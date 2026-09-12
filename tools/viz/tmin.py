#!/usr/bin/env python3
"""Shrink a solving input while it still reaches the goal (parallel).

    tmin.py <harness> <assets> <map> <in.bin> <out.bin> [--goal G] [--jobs N] [harness args...]

Every candidate is a variant of the current input; all candidates of a pass
are replayed in parallel against the same base, then the accepted ones are
applied one at a time (re-verified, since edits interact). A candidate counts
when the harness reports SOLVED no later than the current input does; the
input stays a valid replay from the spawn. Passes repeat until nothing more
comes off.

Candidates come from the replay trace as well as from the bytes:

* loops: the player stands at the same spot at two byte boundaries
  (grounded, not moving) -> the bytes in between are deleted: jumps in place,
  hops up and back down, walking back and forth, waiting;
* flat jumps: a take-off that lands on the same height within a few px ->
  the segment is deleted, or its JUMP bits are cleared (the same time is spent
  standing/walking instead, which later passes shorten);
* walk splices: the player is on the ground at two moments on the same
  height -> the bytes in between become a straight walk between the spots;
* hop templates: from one landing to a later one, the bytes in between become
  "walk n, jump with the direction held, hold k" (wandering and failed
  attempts between two platforms become one direct hop) (wandering, hopping
  along a platform and waiting turn into the walk);
* runs of identical bytes and aligned blocks of 32 .. 1 bytes (the original
  afl-tmin style deletions).

The harness input format is one key mask per byte, held for 4 ticks
(bit 1 left, 2 right, 4 jump, 8 up, 16 down).
"""
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

TICKS_PER_BYTE = 4
KEY_JUMP = 4
MAX_TRIALS = 4000   # candidates replayed per pass


def run(harness, assets, mapid, data, extra, trace=False):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(data)
        path = f.name
    try:
        cmd = [harness, "--assets", assets, "--map", str(mapid), "--max-ticks", "60000"] + extra
        if trace:
            cmd.append("--trace")
        r = subprocess.run(cmd + [path], stdout=subprocess.PIPE if trace else subprocess.DEVNULL,
                           stderr=subprocess.PIPE, universal_newlines=True)
        tick = None
        for line in r.stderr.splitlines():
            if line.startswith("SOLVED"):
                tick = int(line.split("tick=")[1].split()[0])
        solved = tick is not None   # the harness prints SOLVED whether it aborts (default) or exits normally (--speed)
        return solved, tick, (r.stdout if trace else "")
    finally:
        os.unlink(path)


def parse_trace(text):
    """-> list of (x, y, ground) indexed by tick-1 (rows 'T tick x y ground byte ...')."""
    rows = []
    for line in text.splitlines():
        if line.startswith("T "):
            p = line.split()
            rows.append((int(p[2]), int(p[3]), int(p[4])))
    return rows


def boundary_info(rows, nbytes):
    """State after each input byte k (tick 4k+4): (x, y, dx) with dx the
    movement over the last tick, when the player was on the ground for both
    ticks; None in the air."""
    out = []
    for k in range(nbytes):
        i = TICKS_PER_BYTE * k + TICKS_PER_BYTE - 1
        if i >= len(rows) or i < 1:
            out.append(None)
            continue
        x, y, g = rows[i]
        px, py, pg = rows[i - 1]
        out.append((x, y, x - px) if g > 0 and pg > 0 and y == py else None)
    return out


def boundary_states(rows, nbytes):
    """(x, y) after each byte when the player stood still there, else None."""
    return [(s[0], s[1]) if s is not None and s[2] == 0 else None for s in boundary_info(rows, nbytes)]


def loop_candidates(rows, nbytes):
    """Delete bytes (a, b]: the player stands at the same spot after byte a and
    after byte b. For every spot: first->last, and the consecutive pairs."""
    states = boundary_states(rows, nbytes)
    exact, near = {}, {}
    for k, s in enumerate(states):
        if s is not None:
            exact.setdefault(s, []).append(k)
            # near-loops: the same height, x within the same 16 px cell (a jump
            # in place that drifted a little; the replay decides whether the
            # drift matters for what follows)
            near.setdefault((s[0] // 16, s[1]), []).append(k)
    cands = set()
    for ks in list(exact.values()) + list(near.values()):
        if len(ks) < 2:
            continue
        cands.add((ks[0] + 1, ks[-1] + 1))
        prev = ks[0]
        for k in ks[1:]:
            if k - prev >= 2:          # (prev, k]: at least one byte between the two stands
                cands.add((prev + 1, k + 1))
            prev = k
        # the longest stretch that ends anywhere later, from each stand
        for a in ks[1:-1]:
            cands.add((a + 1, ks[-1] + 1))
    return [c for c in cands if c[1] > c[0]]


def jump_segments(rows):
    """(takeoff_tick_index, landing_tick_index, dx, dy) for every jump."""
    out = []
    i, n = 0, len(rows)
    while i < n - 1:
        if rows[i][2] > 0 and rows[i + 1][2] == 0:
            x0, y0 = rows[i][0], rows[i][1]
            j = i + 1
            while j < n and rows[j][2] == 0:
                j += 1
            if j < n:
                out.append((i, j, rows[j][0] - x0, rows[j][1] - y0))
            i = j
        else:
            i += 1
    return out


def flat_jump_candidates(rows, data):
    """Flat jumps (land within 6 px of the height they left): delete the
    segment, or clear its JUMP bits."""
    dels, zeros = set(), set()
    for i, j, dx, dy in jump_segments(rows):
        if abs(dy) > 6:
            continue
        a = i // TICKS_PER_BYTE               # the byte holding the take-off tick
        b = j // TICKS_PER_BYTE + 1           # one past the byte holding the landing tick
        b = min(b, len(data))
        if b <= a:
            continue
        if abs(dx) <= 12:
            dels.add((a, b))
        if any(data[k] & KEY_JUMP for k in range(a, b)):
            zeros.add((a, b))
    return sorted(dels), sorted(zeros)


KEY_LEFT, KEY_RIGHT = 1, 2


def walk_speed(rows, data):
    """Median px per tick while walking on the ground with a single direction key."""
    ds = []
    for t in range(1, len(rows)):
        k = t // TICKS_PER_BYTE
        if k >= len(data):
            break
        if rows[t][2] > 0 and rows[t - 1][2] > 0 and rows[t][1] == rows[t - 1][1] and data[k] in (KEY_LEFT, KEY_RIGHT):
            ds.append(abs(rows[t][0] - rows[t - 1][0]))
    ds = sorted(d for d in ds if d > 0)
    return ds[len(ds) // 2] if ds else 1.25


def walk_splice_candidates(rows, data):
    """The player stands at (xa, y) after byte a and at (xb, y) after a later
    byte b, on the same height: replace the bytes in between by a straight
    walk of n bytes (n around |xb - xa| / speed, a few variants). This
    replaces wandering, hopping along a platform and waiting with a walk."""
    info = boundary_info(rows, len(data))
    speed = walk_speed(rows, data) * TICKS_PER_BYTE
    by_y = {}
    for k, s in enumerate(info):
        if s is not None:
            by_y.setdefault(s[1], []).append((k, s[0], s[2]))
    cands = set()
    for grounded in by_y.values():
        for i, (a, xa, _) in enumerate(grounded):
            seen = 0
            for b, xb, vb in grounded[i + 1:]:
                gap = b - a
                if gap < 3:
                    continue
                dx = xb - xa
                # the splice ends walking towards xb: the original must not
                # have been moving the other way there
                if vb * dx < 0:
                    continue
                seen += 1
                if seen > 5:
                    break
                n0 = int(round(abs(dx) / speed))
                for n in (n0, n0 + 1, n0 + 2):
                    if n < gap - 1:
                        cands.add(("walk", a + 1, b + 1, KEY_RIGHT if dx > 0 else KEY_LEFT, n))
    return sorted(cands, key=lambda c: -(c[2] - c[1] - c[4]))


def landings(rows):
    """Ticks where the player lands (ground after air), with position."""
    out = []
    for t in range(1, len(rows)):
        if rows[t][2] > 0 and rows[t - 1][2] == 0:
            out.append((t, rows[t][0], rows[t][1]))
    return out


def hop_template_candidates(rows, data):
    """Between landing on one platform and landing on a later one (up to three
    landings ahead), replace everything by a synthesized hop: walk n bytes
    towards the target, jump (with the direction held), hold k more bytes,
    then let go. Wandering, waiting and failed attempts between two
    platforms collapse into one direct hop; the replay decides which
    template lands where the rest of the input expects."""
    lands = landings(rows)
    cands = []
    for i, (ti, xi, yi) in enumerate(lands):
        a = ti // TICKS_PER_BYTE + 1            # first byte after the landing
        for tj, xj, yj in lands[i + 1:i + 4]:
            if (xj, yj) == (xi, yi):
                continue
            b = tj // TICKS_PER_BYTE + 1        # first byte after the target landing
            if b - a < 6:
                continue
            d = KEY_RIGHT if xj > xi else KEY_LEFT
            for n in (0, 2, 4, 6, 8, 12, 16):
                for k in (0, 4, 8, 16):
                    body = bytes([d] * n + [d | KEY_JUMP] + [d] * k)
                    if len(body) < b - a - 1:
                        cands.append(("splice", a, b, body))
    return cands


def byte_blocks(data):
    """Runs of identical bytes, then aligned blocks of 32..1."""
    out = []
    i = 0
    while i < len(data):
        j = i
        while j < len(data) and data[j] == data[i]:
            j += 1
        if j - i >= 2:
            out.append((i, j))
        i = j
    for size in (32, 16, 8, 4, 2, 1):
        for i in range(0, len(data), size):
            out.append((i, min(len(data), i + size)))
    return out


def apply_edit(data, edit):
    kind, a, b = edit[:3]
    if kind == "del":
        return data[:a] + data[b:]
    if kind == "walk":
        return data[:a] + bytes([edit[3]]) * edit[4] + data[b:]
    if kind == "splice":
        return data[:a] + edit[3] + data[b:]
    return data[:a] + bytes(c & ~KEY_JUMP for c in data[a:b]) + data[b:]


def main(argv):
    if len(argv) < 6:
        print(__doc__); return 2
    harness, assets, mapid, inp, outp = argv[1:6]
    extra, jobs, i = [], os.cpu_count() or 4, 6
    while i < len(argv):
        if argv[i] == "--goal":
            extra += ["--goal", argv[i + 1]]; i += 2
        elif argv[i] == "--jobs":
            jobs = int(argv[i + 1]); i += 2
        else:
            extra.append(argv[i]); i += 1
    data = open(inp, "rb").read()
    solved, t0, text = run(harness, assets, mapid, data, extra, trace=True)
    if not solved:
        print("the input does not solve the map"); return 1
    rows = parse_trace(text)
    segs = jump_segments(rows)
    print(f"start: {len(data)} bytes, SOLVED at tick {t0}; {len(segs)} jumps, "
          f"{sum(j - i for i, j, _, _ in segs)} ticks in the air", flush=True)
    total_execs = 1
    t_cur = t0   # an edit is accepted only if the input still solves no later than this
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        stage = 0   # 0: trace-guided edits, 1: byte blocks; a stage repeats while it helps
        passes = 0
        while True:
            if stage == 0:
                loops = loop_candidates(rows, len(data))
                dels, zeros = flat_jump_candidates(rows, data)
                walks = walk_splice_candidates(rows, data)
                hops = hop_template_candidates(rows, data)
                edits = ([("del",) + c for c in set(loops) | set(dels)] + walks + hops
                         + [("zero",) + c for c in zeros])
                label = (f"trace: {len(loops)} loops, {len(dels)} flat jumps, {len(walks)} walk splices, "
                         f"{len(hops)} hop templates, {len(zeros)} jump clears")
            else:
                edits = [("del",) + c for c in byte_blocks(data)]
                label = f"blocks: {len(edits)} candidates"
            # biggest deletions first so they win the sequential apply below;
            # long replays with many grounded moments produce tens of thousands
            # of walk splices, keep a pass to MAX_TRIALS candidates
            def saved(e):
                if e[0] == "walk": return e[2] - e[1] - e[4]
                if e[0] == "splice": return e[2] - e[1] - len(e[3])
                return e[2] - e[1]
            edits.sort(key=lambda e: (e[0] == "zero", -saved(e)))
            if len(edits) > MAX_TRIALS:
                keep = [e for e in edits if e[0] not in ("walk", "splice")]
                rest = [e for e in edits if e[0] in ("walk", "splice")][:max(0, MAX_TRIALS - len(keep))]
                edits = sorted(keep + rest, key=lambda e: (e[0] == "zero", -saved(e)))
            trials = [apply_edit(data, e) for e in edits]
            def good(d):
                solved, tick, _ = run(harness, assets, mapid, d, extra)
                return solved and tick is not None and tick <= t_cur
            ok = list(pool.map(good, trials))
            total_execs += len(trials)
            accepted = [e for e, good in zip(edits, ok) if good]
            # apply from the end so earlier indices stay valid; jump clears
            # (same length) go first
            accepted.sort(key=lambda e: (e[0] != "zero", -e[1]))
            base = data
            applied = 0
            for e in accepted:
                cand = apply_edit(base, e)
                if cand != base:
                    solved, tick, _ = run(harness, assets, mapid, cand, extra)
                    if solved and tick is not None and tick <= t_cur:
                        base = cand; applied += 1; t_cur = tick
                total_execs += 1
            solved, t1, text = run(harness, assets, mapid, base, extra, trace=True)
            total_execs += 1
            if not solved:   # should not happen: every applied edit was verified
                print("verification of the combined result failed; keeping the previous input")
                base, t1 = data, t0
                solved, _, text = run(harness, assets, mapid, base, extra, trace=True)
            rows = parse_trace(text)
            segs = jump_segments(rows)
            t_cur = t1
            print(f"pass ({label}): {len(accepted)} accepted, {applied} applied -> {len(base)} bytes, "
                  f"SOLVED at tick {t1}, {len(segs)} jumps", flush=True)
            changed = base != data
            removed = len(data) - len(base)
            data = base
            passes += 1
            if stage == 0:
                if removed >= 8 and passes < 40:
                    continue           # the trace changed a lot: look for loops again
                stage = 1              # trace edits are (nearly) exhausted: byte blocks
            elif removed == 0:
                break                  # blocks took nothing more off: done
            elif removed >= 8 and passes < 40:
                stage = 0              # blocks moved a lot: one more trace round
            # else: repeat the block pass (it keeps finding small deletions)
    solved, t1, _ = run(harness, assets, mapid, data, extra)
    open(outp, "wb").write(data)
    print(f"done: {len(data)} bytes, SOLVED at tick {t1} (was {t0}); {total_execs} replays -> {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
