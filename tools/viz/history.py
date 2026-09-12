#!/usr/bin/env python3
"""Draw every attempt of every campaign over the map, coloured by campaign.

    history.py <harness> <assets> <map.json> <out.png> <campaign-dir> [<campaign-dir> ...]
               [--map ID] [--scale S] [--prefix-for NAME=prefix.bin] [--sample-ijon N] [--goal G]

Each campaign dir is an AFL output dir (master/queue, master/ijon_max,
*/crashes). Inputs are replayed with the harness under the current rules and
drawn oldest campaign first: blue -> green -> yellow -> red; inputs that reach
the goal are white. Uses PIL for speed (tens of thousands of polylines).
"""
import array
import concurrent.futures
import glob
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import best  # noqa: E402  (shared progress measure)

LAVA_TOP_Y = 0  # set from the map JSON before the workers fork


def replay(args):
    harness, assets, mapid, path, prefix, goal = args
    cmd = [harness, "--assets", assets, "--map", str(mapid), "--max-ticks", "60000", "--trace", "--keep-going", path]
    cmd += os.environ.get("ZAKUM_HARNESS_ARGS", "").split()
    if prefix:
        cmd += ["--prefix", prefix]
    if goal:
        cmd += ["--goal", goal]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    pts = []
    died = None
    solved = False
    best_x = None
    lava = LAVA_TOP_Y
    for line in out.stdout.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "T":
            if died is None:
                x, y, g = int(p[2]), int(p[3]), (int(p[4]) if len(p) > 4 else 1)
                pts.append((x, y))
                if g and (lava == 0 or y <= lava):
                    pr = best.progress_of(x, y)
                    if best_x is None or pr > best_x:
                        best_x = pr
        elif p[0] in ("D", "H", "X", "R") and died is None:
            died = int(p[1])
        elif p[0] == "W" and died is None:
            solved = True
    # every third point, packed as int16 pairs: tens of thousands of long
    # traces held as Python tuples filled 30 GB on Forest of Patience 1
    packed = array.array("h")
    for x, y in pts[::3]:
        packed.append(max(-32768, min(32767, x))); packed.append(max(-32768, min(32767, y)))
    return (packed, solved, best_x)


def ramp(t):
    """0 -> blue, 1/3 -> green, 2/3 -> yellow, 1 -> red."""
    stops = [(0.0, (60, 90, 255)), (0.33, (60, 220, 90)), (0.66, (255, 220, 40)), (1.0, (255, 50, 50))]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            u = (t - a) / (b - a) if b > a else 0
            return tuple(int(ca[i] + (cb[i] - ca[i]) * u) for i in range(3))
    return stops[-1][1]


def main(argv):
    if len(argv) < 6:
        print(__doc__)
        return 2
    harness, assets, mapjson, out_png = argv[1:5]
    mapid = 280020000
    scale = 0.5
    prefixes = {}
    sample_ijon = 300
    goal = None
    campaigns = []
    i = 5
    while i < len(argv):
        a = argv[i]
        if a == "--map":
            mapid = int(argv[i + 1]); i += 2
        elif a == "--scale":
            scale = float(argv[i + 1]); i += 2
        elif a == "--prefix-for":
            name, pfx = argv[i + 1].split("=", 1); prefixes[name] = pfx; i += 2
        elif a == "--sample-ijon":
            sample_ijon = int(argv[i + 1]); i += 2
        elif a == "--goal":
            goal = argv[i + 1]; i += 2
        else:
            campaigns.append(a); i += 1

    jobs = []
    labels = []
    for ci, cdir in enumerate(campaigns):
        name = os.path.basename(cdir.rstrip("/"))
        files = sorted(glob.glob(os.path.join(cdir, "master", "queue", "id:*")))
        files = [f for f in files if not f.endswith(".state")]
        ijon = sorted((f for f in glob.glob(os.path.join(cdir, "master", "ijon_max", "*")) if os.path.isfile(f)),
                      key=os.path.getmtime)
        if len(ijon) > sample_ijon:
            step = len(ijon) / float(sample_ijon)
            ijon = [ijon[int(k * step)] for k in range(sample_ijon)]
        crashes = sorted(glob.glob(os.path.join(cdir, "*", "crashes", "id:*")))
        allfiles = files + ijon + crashes
        labels.append((name, len(allfiles)))
        for f in allfiles:
            jobs.append((ci, harness, assets, mapid, f, prefixes.get(name), goal))
    print("replaying {} inputs from {} campaigns".format(len(jobs), len(campaigns)), flush=True)

    global LAVA_TOP_Y
    m = json.load(open(mapjson))
    best.GOAL_POS = tuple(m["goal"]) if m.get("goal_found") else None
    best.SPAWN = tuple(m["spawn"]) if m.get("spawn") else None
    best.ROUTE = best.route_or_none(m) if best.METRIC == "route" and best.GOAL_POS else None
    if best.METRIC == "route" and best.ROUTE is None:
        best.METRIC = "axis"
    LAVA_TOP_Y = m.get("lava_top_y", 0) or 0
    xs = [f["x1"] for f in m["footholds"]] + [f["x2"] for f in m["footholds"]]
    ys = [f["y1"] for f in m["footholds"]] + [f["y2"] for f in m["footholds"]]
    pad = 40
    minx, maxx = min(xs) - pad, max(xs) + pad
    w = int((maxx - minx) * scale) + 1
    # legend height from the label lengths (the stats add a few digits)
    rows, x0 = 1, 6
    for name, n in labels:
        width = 14 + 6 * (len(name.replace("run-{}-".format(mapid), "")) + 32) + 12
        if x0 + width > w and x0 > 6:
            rows += 1; x0 = 6
        x0 += width
    header = 14 * rows + 22
    miny, maxy = min(ys) - pad - int(header / scale), max(ys) + pad  # room for the legend at the top
    h = int((maxy - miny) * scale) + 1
    img = Image.new("RGB", (w, h), (18, 18, 24))
    d = ImageDraw.Draw(img)

    def tx(x): return int((x - minx) * scale)
    def ty(y): return int((y - miny) * scale)

    for trap in m.get("traps", []):
        b = trap.get("box", [0, 0, 0, 0])
        if b[0] != b[2] or b[1] != b[3]:
            d.rectangle([tx(b[0]), ty(b[1]), tx(b[2]), ty(b[3])], outline=(150, 40, 40))
        else:
            d.rectangle([tx(trap["x"] - 4), ty(trap["y"] - 4), tx(trap["x"] + 4), ty(trap["y"] + 4)], outline=(255, 120, 40))

    # Stream: draw each replay as it arrives and keep nothing but the stats and
    # the (few) solving lines, which go on top at the end.
    ncamp = max(1, len(campaigns) - 1)
    solved_lines = []
    stats = {}
    with concurrent.futures.ProcessPoolExecutor() as pool:
        for (ci, *_), res in zip(jobs, pool.map(replay, [j[1:] for j in jobs], chunksize=16)):
            packed, solved, best_x = res
            st = stats.setdefault(ci, [0, 0, None])
            st[0] += 1
            if solved:
                st[1] += 1
            if best_x is not None and (st[2] is None or best_x > st[2]):
                st[2] = best_x
            if len(packed) < 4:
                continue
            line = [(tx(packed[i]), ty(packed[i + 1])) for i in range(0, len(packed), 2)]
            if solved:
                solved_lines.append(line)
            else:
                d.line(line, fill=ramp(ci / ncamp), width=1)
    for line in solved_lines:
        d.line(line, fill=(255, 255, 255), width=1)
    legend = []
    for ci, (name, n) in enumerate(labels):
        st = stats.get(ci, [0, 0, None])
        legend.append((ci, "{} ({} inputs, best {}{})".format(name.replace("run-{}-".format(mapid), ""), st[0],
                                                             st[2] if st[2] is not None else "-",
                                                             ", {} solved".format(st[1]) if st[1] else "")))

    for fh in m["footholds"]:
        wall = fh["x1"] == fh["x2"]
        d.line([(tx(fh["x1"]), ty(fh["y1"])), (tx(fh["x2"]), ty(fh["y2"]))],
               fill=(110, 110, 120) if wall else (215, 215, 225), width=1)
    for p in m["portals"]:
        color = (255, 200, 0) if p["valid"] else (120, 200, 255)
        d.rectangle([tx(p["x"] - 25), ty(p["y"] - 100), tx(p["x"] + 25), ty(p["y"] + 25)], outline=color)
    for npc in m.get("npcs", []):
        d.rectangle([tx(npc["x"] - 8), ty(npc["y"] - 40), tx(npc["x"] + 8), ty(npc["y"])], outline=(120, 255, 120))
    for mob in m.get("mobs", []):
        d.rectangle([tx(mob["x"] - 20), ty(mob["y"] - 40), tx(mob["x"] + 20), ty(mob["y"])], outline=(255, 60, 60))
    sx, sy = m["spawn"]
    d.rectangle([tx(sx - 6), ty(sy - 12), tx(sx + 6), ty(sy)], outline=(0, 255, 0))

    # Legend: one entry per campaign, wrapped into rows.
    x0, row = 6, 0
    for ci, text in legend:
        width = 14 + 6 * len(text) + 12
        if x0 + width > w and x0 > 6:
            row += 1; x0 = 6
        y0 = 4 + 14 * row
        d.rectangle([x0, y0, x0 + 10, y0 + 10], fill=ramp(ci / ncamp))
        d.text((x0 + 14, y0 - 1), text, fill=(230, 230, 230))
        x0 += width
    d.text((6, 4 + 14 * rows), "white = reaches the goal; traces cut at the first death; drawn under the final rules (100 HP, 1 dmg/hit, lethal lava)",
           fill=(180, 180, 180))

    img.save(out_png)
    print("wrote", out_png, img.size)
    for ci, (name, n) in enumerate(labels):
        s = stats.get(ci, [0, 0, None])
        print("  {:28s} inputs {:5d}  best_x {}  solved {}".format(name, s[0], s[2], s[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
