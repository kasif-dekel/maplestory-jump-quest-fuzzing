#!/usr/bin/env python3
"""Pick a checkpoint from the best input of a run and write it as a prefix.

    checkpoint.py <harness> <assets-dir> <run-dir> <out-prefix.bin>
                  [--map ID] [--prefix current.bin] [--margin-ticks N] [--explore]

The best input (best progress along the quest's axis, see best.py) is replayed with
the harness; the cut is placed at the last tick, at least --margin-ticks
before its frontier (the tick of that best progress, before any death),
where the character has been standing on ground for a few ticks and is above
the lava. The new prefix is <current prefix> + <best input
bytes up to the cut>, so it is itself a valid input that replays from the
spawn. Python 3.6 compatible.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import best  # noqa: E402

LAVA_TOP_Y = best.LAVA_TOP_Y  # updated from the map JSON in main()


def replay_trace(harness, assets, mapid, path, prefix):
    cmd = [harness, "--assets", assets, "--map", str(mapid), "--max-ticks", "60000",
           "--trace", "--keep-going", path]
    cmd += os.environ.get("ZAKUM_HARNESS_ARGS", "").split()
    if prefix:
        cmd += ["--prefix", prefix]
    if best.GOAL:
        cmd += ["--goal", best.GOAL]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    rows = []
    death = None
    for line in out.stdout.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "T" and len(p) >= 5:
            rows.append((int(p[1]), int(p[2]), int(p[3]), int(p[4])))
        elif p[0] in ("D", "H", "X", "R") and death is None:
            death = int(p[1])
    return rows, death, out.stderr


def main(argv):
    if len(argv) < 5:
        print(__doc__)
        return 2
    harness, assets, run, out_path = argv[1:5]
    mapid = 280020000
    prefix = None
    margin = 400
    explore = False
    i = 5
    while i < len(argv):
        if argv[i] == "--explore":
            explore = True; i += 1
        elif argv[i] == "--map":
            mapid = int(argv[i + 1]); i += 2
        elif argv[i] == "--prefix":
            prefix = argv[i + 1]; i += 2
        elif argv[i] == "--goal":
            best.GOAL = argv[i + 1]; i += 2
        elif argv[i] == "--margin-ticks":
            margin = int(argv[i + 1]); i += 2
        else:
            i += 1

    # Rank with best.py's machinery (master queue + ijon_max).
    rank_argv = ["best.py", harness, assets, run, "--map", str(mapid), "--top", "40"]
    if prefix:
        rank_argv += ["--prefix", prefix]
    if best.GOAL:
        rank_argv += ["--goal", best.GOAL]
    top = best.rank(rank_argv)
    global LAVA_TOP_Y
    LAVA_TOP_Y = best.LAVA_TOP_Y
    if not top:
        print("no inputs to rank")
        return 1
    def alive_rows(path):
        """Grounded rows before the first death (and above the lava)."""
        rows, death, err = replay_trace(harness, assets, mapid, path, prefix)
        alive = [r for r in rows if (death is None or r[0] < death) and r[3] >= 1]  # 1 foothold, 2 rope
        if LAVA_TOP_Y != 0:
            above = [r for r in alive if r[2] <= LAVA_TOP_Y]
            alive = above or alive
        return alive, rows, err

    # The frontier is where the search should continue from: the best
    # progress reached before any death (not the input's end: where a fall is
    # not lethal the character wanders on long after its best moment). Among
    # the inputs within a platform's height of the best, prefer the one that
    # explored that platform furthest from where it landed on it, and take
    # its latest tick there: on a long platform every position has the same
    # height, and IJON trims inputs to end right at the landing.
    best_p = top[0][0]
    candidates = [t for t in top[:40] if t[0] >= best_p - 8]
    prefix_ticks = 4 * os.path.getsize(prefix) if prefix else 0
    if explore:
        # The best-by-progress frontier is a dead end (a checkpoint there did
        # not move it). Cut instead where the fuzzer is currently advancing:
        # IJON rewrites a slot's file whenever that column improves, so the
        # most recently written slot files are the live edge of the search,
        # wherever on the map it is.
        slots = [t for t in top if "/ijon_max/" in t[4] and os.path.basename(t[4]).isdigit()]
        # ... but only slots whose best position is near the route frontier
        # (within two hops under the route metric, 60 px otherwise): the
        # newest slots are often in a side area the fuzzer is idly exploring.
        near = 25000 if best.METRIC == "route" else 60
        slots = [t for t in slots if t[0] >= best_p - near] or slots
        slots.sort(key=lambda t: os.path.getmtime(t[4]), reverse=True)
        candidates = slots[:6] or candidates
        print("explore: cutting at the most recently improved slots:",
              ", ".join(os.path.relpath(t[4], run) for t in candidates))
        # The slot number is the harness's band index (see setup_metric): the
        # frontier of a slot input is its best position inside that band, not
        # wherever it ended (it may have fallen to the floor afterwards).
        mj = best.load_map_json(harness, assets, mapid, "/tmp/checkpoint-map-%d.json" % mapid, best.GOAL)
        sx, sy = mj["spawn"]; gx, gy = mj["goal"] if mj.get("goal_found") else (mj["walls"][1], sy)
        bands_by_y = abs(gx - sx) >= abs(gy - sy)
        phases = 8 if mj.get("rock_cycle_ticks") else 1
        span_px = (mj["borders"][1] - mj["borders"][0]) if bands_by_y else (mj["walls"][1] - mj["walls"][0])
        band_size = max(16, (span_px * phases + 479) // 480)
        band_origin = mj["borders"][0] if bands_by_y else mj["walls"][0]

        def band_of(row):
            along = row[2] if bands_by_y else row[1]
            return (along - band_origin) // band_size
    chosen = None  # (span, tick, path, rows, frontier_row)
    for t in candidates:
        alive, rows, err = alive_rows(t[4])
        if not alive:
            continue
        if explore:
            # frontier = this input's best grounded position inside its slot's
            # band (latest tick among equals)
            slot_band = int(os.path.basename(t[4])) // phases
            # only what this input did after the current prefix can be cut at
            in_band = [r for r in alive if band_of(r) == slot_band and r[0] > prefix_ticks] \
                or [r for r in alive if r[0] > prefix_ticks]
            if not in_band:
                continue
            p_top = max(best.progress_of(r[1], r[2]) for r in in_band)
            last = max((r for r in in_band if best.progress_of(r[1], r[2]) >= p_top - 8), key=lambda r: r[0])
            first = last
            span = 0
            key = (os.path.getmtime(t[4]), last[0])
        else:
            p_top = max(best.progress_of(r[1], r[2]) for r in alive)
            at_top = [r for r in alive if best.progress_of(r[1], r[2]) >= p_top - 8]
            first, last = at_top[0], max(at_top, key=lambda r: r[0])
            span = abs(last[1] - first[1]) + abs(last[2] - first[2])
            key = (span, last[0])
        if chosen is None or key > chosen[:2]:
            chosen = (span, last[0], t[4], rows, last)
    if chosen is None:
        print("no live trace among the best inputs")
        return 1
    span, _, path, rows, frontier = chosen
    best_x, died, solved, ticks = next(t[:4] for t in top if t[4] == path)
    death = died
    print("best input: {} (progress {}, died@{}, {} ticks; explored {} px along its platform)".format(
        os.path.relpath(path, run), best_x, died, ticks, span))
    end = frontier[0]
    limit = end - margin

    # Candidate cut: on a foothold or a rope for >= 8 consecutive ticks, above
    # the lava, after the current prefix, as late as allowed. (Cutting below a
    # rope made every restart redo the jump-and-grab.)
    # If the frontier lies inside the current prefix (an explore cut went past
    # the best point), roll the prefix back: cut before the frontier even
    # though that is inside the prefix; the result is still prefix+input[:cut].
    floor_tick = prefix_ticks if limit > prefix_ticks else 0
    if floor_tick == 0 and prefix_ticks:
        print("frontier at tick {} is inside the prefix ({} ticks): rolling the prefix back".format(end, prefix_ticks))
    cut = None
    streak = 0
    for tick, x, y, onground in rows:
        streak = streak + 1 if onground >= 1 and (LAVA_TOP_Y == 0 or y <= LAVA_TOP_Y) else 0
        if tick <= limit and tick > floor_tick and streak >= 8:
            cut = (tick, x, y)
    if cut is None:
        print("no standing moment found between tick {} and {}".format(floor_tick, limit))
        return 1

    cut_tick, cx, cy = cut
    old = open(prefix, "rb").read() if prefix else b""
    suffix = open(path, "rb").read()
    full = old + suffix
    cut_bytes = cut_tick // 4  # ticks 1..4*cut_bytes are covered by the first cut_bytes bytes
    new_prefix = full[:cut_bytes]
    with open(out_path, "wb") as f:
        f.write(new_prefix)
    print("checkpoint at tick {} ({:.1f} s), player at ({}, {}); prefix {} bytes -> {}".format(
        cut_tick, cut_tick * 8 / 1000.0, cx, cy, len(new_prefix), out_path))
    print("frontier of that input: ({}, {}) at tick {}{}; {} ticks of slack kept before it".format(
        frontier[1], frontier[2], end, " (died at {})".format(death) if death is not None else "", end - cut_tick))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
