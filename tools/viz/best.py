#!/usr/bin/env python3
"""Summarise a fuzzing run: best progress per queue entry, optional plot.

    best.py <harness> <assets-dir> <run-dir> [--map ID] [--top N] [--png out.png] [--json map.json]
            [--instances master|all|name,name] [--prefix checkpoint.bin]

Walks the fuzzer instances' queue/, crashes/ and ijon_max/ under <run-dir>,
replays each input with --trace --keep-going, and reports the best progress
(see progress_of) reached before the first death. Slaves' queues are mostly synced copies, so
--instances master (the default) is usually enough and 12x faster. With
--png, draws all traces over the map (needs the map JSON from
`harness --dump-map`, produced automatically if --json is not given).
Runs on Python 3.6 (the fuzzing container).
"""
import glob
import json
import os
import subprocess
import sys
import tempfile


LAVA_TOP_Y = -168  # default (Zakum maps); overridden from the map JSON's lava_top_y (0 = no lava)
GOAL = None    # set from --goal: passed to the harness (what counts as solved)
GOAL_POS = None  # (x, y) of the goal from the map JSON
SPAWN = None     # (x, y) of the spawn from the map JSON


METRIC = os.environ.get("ZAKUM_RANK_METRIC", "route")  # route (default) | axis | distance
ROUTE = None  # for METRIC=route: build_route(map json)

JUMP_UP = 74    # px the client's standing jump rises
# Jump model fitted to the client: v0 = 4.9 px/tick, g = 0.163 px/tick^2,
# 1.1 px/tick of horizontal speed averaged over a jump. reach(h) is the
# horizontal distance covered when the arc comes back down through a height h
# above the start (h < 0 is a drop): ~76 px level (72 measured), ~57 for a
# 60 px rise (52 measured), ~139 for a 540 px drop.


GRAB_TOL = 10   # Ladder::inrange grabs a rope within 10 px horizontally
ROPE_JUMP_UP = 31  # a jump off a rope rises only 31 px (measured), 1.29 px/tick sideways


def rope_jump_reach(rise):
    """Horizontal reach of a jump off a rope (PlayerClimbState: vspeed =
    -jumpforce / 1.5, hspeed = 8 * walkforce): v0 = 3.18 px/tick up, same
    gravity, and the sideways speed keeps growing while the key is held
    (54 px in 42 ticks level, 133 px over a 391 px drop in the Zakum solution):
    x(t) = 1.12 t + 0.004 t^2."""
    disc = 10.1 - 0.326 * rise
    if disc < 0:
        return 0.0
    t = (3.18 + disc ** 0.5) / 0.163
    return 1.12 * t + 0.004 * t * t + GRAB_TOL


def jump_reach(rise):
    disc = 24.01 - 0.326 * rise
    if disc < 0:
        return 0.0
    t = (4.9 + disc ** 0.5) / 0.163          # ticks until the arc is back at that height
    return 1.1 * t + 10                       # 1.1 px/tick measured over whole jumps; +10 for the edge


def build_route(m):
    """Platform/rope graph with hops-to-goal for every node (METRIC=route).

    Nodes are the footholds (platforms and ramps) and the ladders/ropes; an
    edge A->B exists when a jump from A can land on B (see jump_reach), a
    platform reaches a rope
    whose bottom hangs at most 90 px above it (jump + UP) or which passes its
    level, and a rope reaches the platforms near its top. hops[node] is the
    BFS distance to the platform(s) under the goal; unreachable = None.
    """
    from collections import deque
    # segments (x1, x2, y_at_x1, y_at_x2): flat platforms and ramps alike
    segs = []
    seg_ids = []   # foothold id of every seg (for the harness's --route table)
    lava = m.get("lava_top_y", 0) or 0
    for f in m["footholds"]:
        if f["x1"] == f["x2"] or abs(f["x2"] - f["x1"]) < 8:
            continue  # walls and specks
        if lava and max(f["y1"], f["y2"]) > lava:
            continue  # the lava floor is not a platform
        if f["x1"] < f["x2"]:
            segs.append((f["x1"], f["x2"], f["y1"], f["y2"]))
        else:
            segs.append((f["x2"], f["x1"], f["y2"], f["y1"]))
        seg_ids.append(f.get("id", -1))
    ladders = [(l["x"], min(l["y1"], l["y2"]), max(l["y1"], l["y2"])) for l in m.get("ladders", [])]

    def y_at(seg, x):
        x1, x2, ya, yb = seg
        if x <= x1: return ya
        if x >= x2: return yb
        return ya + (yb - ya) * (x - x1) / (x2 - x1)

    n = len(segs)
    adj = [[] for _ in range(n + len(ladders))]
    for i, a in enumerate(segs):
        for j, b in enumerate(segs):
            if i == j:
                continue
            # jump from a's end nearest b to b's end nearest a
            if b[0] > a[1]:
                ay, by, gap = a[3], b[2], b[0] - a[1]
            elif a[0] > b[1]:
                ay, by, gap = a[2], b[3], a[0] - b[1]
            else:  # overlapping in x: from a to the point of b above/below
                mid = (max(a[0], b[0]) + min(a[1], b[1])) / 2
                ay, by, gap = y_at(a, mid), y_at(b, mid), 0
            rise = ay - by  # > 0: b is higher
            if rise > JUMP_UP:
                continue
            if gap <= jump_reach(rise):
                adj[i].append(j)
    for k, (lx, top, bot) in enumerate(ladders):
        r = n + k
        for i, a in enumerate(segs):
            gap = max(0, a[0] - lx, lx - a[1])
            ax = min(max(lx, a[0]), a[1])
            ay = y_at(a, ax)
            # grab: jump from the platform to the rope's lowest point (or any
            # point of it when it passes the platform level)
            rise = ay - bot  # grab the rope at its lowest point (negative: it hangs below)
            if rise <= JUMP_UP and gap <= jump_reach(rise) + GRAB_TOL:
                adj[i].append(r)
            # leave: jump off the rope's top (Zakum's platforms sit beside the
            # ropes, not under them)
            rise = top - ay
            if rise <= ROPE_JUMP_UP and gap <= rope_jump_reach(rise):
                adj[r].append(i)
        # rope to rope: jump off this rope's top and grab the other one at its
        # lowest point within reach (the Zakum rope section)
        for k2, (lx2, top2, bot2) in enumerate(ladders):
            if k2 == k:
                continue
            gap = abs(lx2 - lx)
            rise = top - bot2  # grab the other rope at its lowest point
            if rise <= ROPE_JUMP_UP and gap <= rope_jump_reach(rise):
                adj[r].append(n + k2)
    # in-map portal warps (valid intramap portals, e.g. the Kerning subway's
    # hidden portals that lift the player to the upper platforms): an edge
    # from the platform under the portal to the platform under its target.
    # Under --hidden-portals reset-all these portals reset instead, so skip.
    portal_tx = {}   # (from node, to node) -> x of the portal to enter
    if "reset-all" not in os.environ.get("ZAKUM_HARNESS_ARGS", ""):
        def node_under(px, py):
            for i, a in enumerate(segs):
                if a[0] - 30 <= px <= a[1] + 30 and -10 <= y_at(a, px) - py <= 120:
                    return i
            return None
        by_name = {p["name"]: p for p in m.get("portals", [])}
        for p in m.get("portals", []):
            if p.get("intramap") and p.get("valid") and p.get("toname") in by_name:
                a, b = node_under(p["x"], p["y"]), node_under(by_name[p["toname"]]["x"], by_name[p["toname"]]["y"])
                if a is not None and b is not None and a != b:
                    adj[a].append(b)
                    portal_tx[(a, b)] = p["x"]
    gx, gy = m["goal"]
    goal_nodes = [i for i, a in enumerate(segs) if a[0] - 30 <= gx <= a[1] + 30 and abs(y_at(a, gx) - gy) <= 60]
    rev = [[] for _ in adj]
    for u, vs in enumerate(adj):
        for v in vs:
            rev[v].append(u)
    hops = [None] * len(adj)
    dq = deque()
    for g in goal_nodes:
        hops[g] = 0; dq.append(g)
    while dq:
        u = dq.popleft()
        for v in rev[u]:
            if hops[v] is None:
                hops[v] = hops[u] + 1; dq.append(v)
    return {"segs": segs, "ladders": ladders, "hops": hops, "goal": (gx, gy), "adj": adj,
            "seg_ids": seg_ids, "portal_tx": portal_tx}


def export_route(m, path):
    """Write the route table the harness reads with --route: one line per
    reachable platform "F <foothold id> <hops> <target x>" (target x = where
    on the platform the next hop starts: the end nearest the next platform,
    the middle of the overlap when the next one is above/below, the portal
    when the hop is a warp, the goal on the goal platforms) and one per
    reachable ladder/rope "L <x> <top y> <bottom y> <hops>". The fuzzer's
    IJON feedback then rewards hops to the goal, not straight-line distance,
    so a floor that runs under the goal or a wrong tower earns nothing."""
    r = build_route(m)
    segs, ladders, hops, adj = r["segs"], r["ladders"], r["hops"], r["adj"]
    n = len(segs)
    gx = r["goal"][0]
    lines = []
    for i, a in enumerate(segs):
        h = hops[i]
        if h is None:
            continue
        if h == 0:
            tx = min(max(gx, a[0]), a[1])
        else:
            best = None
            for b in adj[i]:
                if hops[b] is None or hops[b] != h - 1:
                    continue
                if (i, b) in r["portal_tx"]:
                    cand, gap = r["portal_tx"][(i, b)], 0
                elif b >= n:
                    lx = ladders[b - n][0]
                    cand, gap = min(max(lx, a[0]), a[1]), max(0, a[0] - lx, lx - a[1])
                else:
                    bb = segs[b]
                    if bb[0] > a[1]:
                        cand, gap = a[1], bb[0] - a[1]
                    elif a[0] > bb[1]:
                        cand, gap = a[0], a[0] - bb[1]
                    else:
                        cand, gap = (max(a[0], bb[0]) + min(a[1], bb[1])) // 2, 0
                if best is None or gap < best[1]:
                    best = (cand, gap)
            tx = best[0] if best else (a[0] + a[1]) // 2
        lines.append(f"F {r['seg_ids'][i]} {h} {int(tx)}")
    for k, (lx, top, bot) in enumerate(ladders):
        h = hops[n + k]
        if h is not None:
            lines.append(f"L {lx} {top} {bot} {h}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def route_or_none(m):
    """build_route, or None when the model finds no route from the spawn."""
    global ROUTE
    r = build_route(m)
    ROUTE = r
    sx, sy = m["spawn"]
    node = route_node(sx, sy)
    if node is None:  # the spawn point floats above its foothold
        below = [(i, a) for i, a in enumerate(r["segs"]) if a[0] <= sx <= a[1] and min(a[2], a[3]) >= sy]
        node = min(below, key=lambda t: min(t[1][2], t[1][3]))[0] if below else None
    if node is None or r["hops"][node] is None:
        ROUTE = None
        return None
    r["spawn_hops"] = r["hops"][node]
    return r


def route_node(x, y):
    r = ROUTE
    for i, (x1, x2, ya, yb) in enumerate(r["segs"]):
        if x1 - 4 <= x <= x2 + 4:
            sy = ya if x <= x1 else yb if x >= x2 else ya + (yb - ya) * (x - x1) / (x2 - x1)
            if abs(y - sy) <= 10:
                return i
    for k, (lx, top, bot) in enumerate(r["ladders"]):
        if abs(x - lx) <= 12 and top - 10 <= y <= bot + 10:
            return len(r["segs"]) + k
    return None


def progress_of(x, y):
    """Progress of a position along the quest's dominant axis.

    Height gained on a tower (|dy| > |dx| between spawn and goal), distance
    covered toward the goal's side on a horizontal map. Not the straight-line
    distance to the goal: staircases zig-zag hundreds of pixels sideways, and
    the distance would rank a hop on the floor under the exit above the first
    platforms. Plain x when the map has no goal.

    ZAKUM_RANK_METRIC=distance ranks by straight-line distance to the goal
    instead (negative, so higher is closer): right for quests that first
    travel a long way sideways before climbing (the Henesys pet park).
    """
    if not GOAL_POS or not SPAWN:
        return x
    if METRIC == "distance":
        return -int(((GOAL_POS[0] - x) ** 2 + (GOAL_POS[1] - y) ** 2) ** 0.5)
    if METRIC == "route" and ROUTE:
        # hops to the goal along the platform graph; ties (same platform)
        # broken by the straight-line distance
        node = route_node(x, y)
        hops = ROUTE["hops"][node] if node is not None else None
        if hops is None:
            return -10 ** 9
        return -(hops * 10000 + int(((GOAL_POS[0] - x) ** 2 + (GOAL_POS[1] - y) ** 2) ** 0.5))
    dx = GOAL_POS[0] - SPAWN[0]
    dy = GOAL_POS[1] - SPAWN[1]
    if abs(dy) > abs(dx):
        return SPAWN[1] - y if dy < 0 else y - SPAWN[1]
    return x - SPAWN[0] if dx >= 0 else SPAWN[0] - x


PREFIX = None  # set from --prefix: bytes replayed before every input


def trace(harness, assets, mapid, path, trace_dir=None):
    """Replay one input. Returns (best_x, died_tick, solved, ticks, trace_file).

    best_x only counts positions above the lava (y <= LAVA_TOP_Y): with HP the
    character can wade along the lava floor, which is not jump quest progress.
    The trace file (if trace_dir is given) is cut at the first death so plots
    show the attempt, not the walk along the lava afterwards.
    """
    cmd = [harness, "--assets", assets, "--map", str(mapid), "--max-ticks", "60000", "--trace", "--keep-going", path]
    cmd += os.environ.get("ZAKUM_HARNESS_ARGS", "").split()  # e.g. --hidden-portals reset-all
    if PREFIX:
        cmd += ["--prefix", PREFIX]
    if GOAL:
        cmd += ["--goal", GOAL]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    pts = []
    died = None
    solved = False
    kept = []
    for line in out.stdout.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "T":
            pts.append((int(p[1]), int(p[2]), int(p[3]), int(p[4]) if len(p) > 4 else 1))
            if died is None:
                kept.append(line)
        elif p[0] in ("D", "H", "X", "R") and died is None:
            died = int(p[1])
        elif p[0] == "W" and died is None:
            # Only a portal reached before any death counts as a solve
            # (--keep-going lets a dead character wander on).
            solved = True
    # Like the harness, only grounded positions count (a jump's apex is not a
    # platform reached).
    alive = [q for q in pts if (died is None or q[0] <= died) and (LAVA_TOP_Y == 0 or q[2] <= LAVA_TOP_Y) and q[3]]
    best_x = max((progress_of(q[1], q[2]) for q in alive), default=None)
    tfile = None
    if trace_dir:
        tfile = os.path.join(trace_dir, os.path.basename(os.path.dirname(os.path.dirname(path))) + "_" + os.path.basename(path) + ".txt")
        with open(tfile, "w") as f:
            f.write("\n".join(kept) + "\n")
    return best_x, died, solved, len(pts), tfile


def _job(args):
    harness, assets, mapid, f, trace_dir, prefix, goal, goal_pos, spawn, lava_top, route = args
    global PREFIX, GOAL, GOAL_POS, SPAWN, LAVA_TOP_Y, ROUTE
    PREFIX = prefix
    GOAL = goal
    GOAL_POS = goal_pos
    SPAWN = spawn
    LAVA_TOP_Y = lava_top
    ROUTE = route
    best_x, died, solved, ticks, tfile = trace(harness, assets, mapid, f, trace_dir)
    return (best_x if best_x is not None else -10**9, died, solved, ticks, f, tfile)


def rank(argv):
    """Rank without printing or plotting; returns the sorted result rows."""
    return main(argv, quiet=True)


def load_map_json(harness, assets, mapid, path, goal):
    """Map dump (with --goal so the goal position is resolved the same way)."""
    if not os.path.exists(path):
        cmd = [harness, "--assets", assets, "--map", str(mapid), "--dump-map"]
        cmd += os.environ.get("ZAKUM_HARNESS_ARGS", "").split()
        if goal:
            cmd += ["--goal", goal]
        with open(path, "w") as out:
            subprocess.run(cmd, stdout=out, stderr=subprocess.DEVNULL, check=True)
    return json.load(open(path))


def main(argv, quiet=False):
    global PREFIX, GOAL, GOAL_POS, SPAWN, LAVA_TOP_Y, ROUTE, METRIC
    if len(argv) == 4 and argv[1] == "--export-route":
        # best.py --export-route MAP.json ROUTE.txt  (the harness's --route table)
        m = json.load(open(argv[2]))
        print(f"route table: {export_route(m, argv[3])} lines -> {argv[3]}")
        return 0
    if len(argv) < 4:
        print(__doc__)
        return 2
    harness, assets, run = argv[1:4]
    mapid = 280020000
    top = 15
    png = None
    mapjson = None
    instances = "master"
    i = 4
    while i < len(argv):
        if argv[i] == "--prefix":
            PREFIX = argv[i + 1]; i += 2
        elif argv[i] == "--goal":
            GOAL = argv[i + 1]; i += 2
        elif argv[i] == "--instances":
            instances = argv[i + 1]; i += 2
        elif argv[i] == "--map":
            mapid = int(argv[i + 1]); i += 2
        elif argv[i] == "--top":
            top = int(argv[i + 1]); i += 2
        elif argv[i] == "--png":
            png = argv[i + 1]; i += 2
        elif argv[i] == "--json":
            mapjson = argv[i + 1]; i += 2
        else:
            i += 1

    if instances == "all":
        inst_dirs = sorted(d for d in glob.glob(os.path.join(run, "*")) if os.path.isdir(d))
    else:
        inst_dirs = [os.path.join(run, name) for name in instances.split(",")]
    files = []
    for d in inst_dirs:
        files += glob.glob(os.path.join(d, "queue", "id:*"))
        files += glob.glob(os.path.join(d, "crashes", "id:*"))
        # IJON's per-slot best inputs: the progress frontier, one file per slot.
        # finding_<n>_<time> files are the full update history (tens of
        # thousands after a few hours) -- history.py samples those; skip here.
        files += [f for f in glob.glob(os.path.join(d, "ijon_max", "*"))
                  if os.path.isfile(f) and not os.path.basename(f).startswith("finding_")]
    files = sorted(f for f in files if not f.endswith(".state"))

    import concurrent.futures
    with tempfile.TemporaryDirectory() as tmp:
        trace_dir = os.path.join(tmp, "traces")
        os.makedirs(trace_dir)
        if not mapjson:
            mapjson = os.path.join(tmp, "map.json")
        m = load_map_json(harness, assets, mapid, mapjson, GOAL)
        GOAL_POS = tuple(m["goal"]) if m.get("goal_found") else None
        SPAWN = tuple(m["spawn"]) if m.get("spawn") else None
        LAVA_TOP_Y = m.get("lava_top_y", 0) or 0
        ROUTE = route_or_none(m) if METRIC == "route" and GOAL_POS else None
        if METRIC == "route" and ROUTE is None:
            METRIC = "axis"  # no goal, or the model finds no route from the spawn: fall back
        jobs = [(harness, assets, mapid, f, trace_dir if png else None, PREFIX, GOAL, GOAL_POS, SPAWN, LAVA_TOP_Y, ROUTE) for f in files]
        with concurrent.futures.ProcessPoolExecutor() as pool:
            results = list(pool.map(_job, jobs, chunksize=8))
        # solving inputs first (whatever the metric says), then by progress
        results.sort(key=lambda r: (bool(r[2]), r[0]), reverse=True)
        if quiet:
            return results

        solved = [r for r in results if r[2]]
        if METRIC == "route" and ROUTE:
            metric = "-(hops to goal * 10000 + px to goal); spawn is %d hops away" % ROUTE["spawn_hops"]
        elif METRIC == "distance" and GOAL_POS:
            metric = "-(px to goal)"
        elif GOAL_POS and SPAWN:
            vertical = abs(GOAL_POS[1] - SPAWN[1]) > abs(GOAL_POS[0] - SPAWN[0])
            metric = "height gained (px)" if vertical else "px covered toward the goal"
        else:
            metric = "best x"
        print(f"{len(results)} inputs, {len(solved)} solve the map" + (f" (after prefix {PREFIX})" if PREFIX else "")
              + f"; metric: {metric}" + (f", goal at {GOAL_POS}, spawn at {SPAWN}" if GOAL_POS else ""))
        print(f"{'best_x':>7} {'died@':>6} {'ticks':>6}  file")
        for best_x, died, is_solved, ticks, f, _ in results[:top]:
            tag = " SOLVED" if is_solved else ""
            print(f"{best_x:7d} {str(died) if died is not None else '-':>6} {ticks:6d}  {os.path.relpath(f, run)}{tag}")

        if png and results:
            here = os.path.dirname(os.path.abspath(__file__))
            # oldest first so the colour ramp reads as time; best last (white)
            best = results[0]
            ordered = sorted((r for r in results if r is not best), key=lambda r: os.path.getmtime(r[4])) + [best]
            traces = [r[5] for r in ordered if r[5]]
            listfile = os.path.join(tmp, "traces.txt")
            with open(listfile, "w") as f:
                f.write("\n".join(traces) + "\n")
            subprocess.run([sys.executable, os.path.join(here, "plot_traces.py"), mapjson, png, "--scale", "0.5",
                            "--traces-file", listfile], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
