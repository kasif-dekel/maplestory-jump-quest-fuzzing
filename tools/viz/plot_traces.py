#!/usr/bin/env python3
"""Render map footholds/portals and harness traces to a PNG.

Pure Python (zlib only) so it runs inside the fuzzing container.

    plot_traces.py map.json out.png [--scale S] [--traces-file list.txt] [trace.txt ...]

A trace file is the stdout of `zakum_harness --trace`: lines "T tick x y ...".
Traces are drawn oldest-first in a blue-to-red ramp; the last one is white.
"""
import json
import struct
import sys
import zlib


def parse_trace(path):
    pts = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "T":
                pts.append((int(parts[2]), int(parts[3])))
    return pts


class Canvas:
    def __init__(self, width, height, bg=(18, 18, 24)):
        self.w, self.h = width, height
        self.buf = bytearray(bg * width * height)

    def put(self, x, y, rgb):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = bytes(rgb)

    def line(self, x0, y0, x1, y1, rgb, thick=1):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            for ox in range(thick):
                for oy in range(thick):
                    self.put(x0 + ox, y0 + oy, rgb)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def rect(self, x0, y0, x1, y1, rgb):
        self.line(x0, y0, x1, y0, rgb)
        self.line(x1, y0, x1, y1, rgb)
        self.line(x1, y1, x0, y1, rgb)
        self.line(x0, y1, x0, y0, rgb)

    def save(self, path):
        raw = b"".join(b"\x00" + bytes(self.buf[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(raw, 9))
        png += chunk(b"IEND", b"")
        with open(path, "wb") as f:
            f.write(png)


def ramp(t):
    """0 -> blue, 0.5 -> green, 1 -> red."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        u = t / 0.5
        return (int(40 * (1 - u)), int(80 + 175 * u), int(255 * (1 - u) + 60 * u))
    u = (t - 0.5) / 0.5
    return (int(255 * u + 40 * (1 - u)), int(255 * (1 - u)), int(60 * (1 - u)))


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    map_path, out_path = argv[1], argv[2]
    scale = 0.5
    traces = []
    i = 3
    while i < len(argv):
        if argv[i] == "--scale":
            scale = float(argv[i + 1])
            i += 2
        elif argv[i] == "--traces-file":
            # One trace path per line; avoids argv limits with thousands of traces.
            with open(argv[i + 1]) as f:
                traces += [line.strip() for line in f if line.strip()]
            i += 2
        else:
            traces.append(argv[i])
            i += 1

    m = json.load(open(map_path))
    xs = [f["x1"] for f in m["footholds"]] + [f["x2"] for f in m["footholds"]]
    ys = [f["y1"] for f in m["footholds"]] + [f["y2"] for f in m["footholds"]]
    pad = 40
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad, max(ys) + pad
    w = int((maxx - minx) * scale) + 1
    h = int((maxy - miny) * scale) + 1
    cv = Canvas(w, h)

    def tx(x):
        return int((x - minx) * scale)

    def ty(y):
        return int((y - miny) * scale)

    for fh in m["footholds"]:
        wall = fh["x1"] == fh["x2"]
        cv.line(tx(fh["x1"]), ty(fh["y1"]), tx(fh["x2"]), ty(fh["y2"]), (110, 110, 120) if wall else (200, 200, 210))

    for p in m["portals"]:
        color = (255, 200, 0) if p["valid"] else (120, 200, 255)
        cv.rect(tx(p["x"] - 25), ty(p["y"] - 100), tx(p["x"] + 25), ty(p["y"] + 25), color)

    for trap in m.get("traps", []):
        box = trap.get("box", [0, 0, 0, 0])
        mv = trap.get("move", [0, 0, 0, 0])
        if mv[0] and box[0] != box[2]:
            # moving trap: the area its hit box sweeps (amplitude moveW/moveH)
            w, h = abs(mv[1]), abs(mv[2])
            cv.rect(tx(box[0] - w), ty(box[1] - h), tx(box[2] + w), ty(box[3] + h), (110, 60, 30))
        if box[0] != box[2] or box[1] != box[3]:
            cv.rect(tx(box[0]), ty(box[1]), tx(box[2]), ty(box[3]), (150, 40, 40))
        else:
            cv.rect(tx(trap["x"] - 4), ty(trap["y"] - 4), tx(trap["x"] + 4), ty(trap["y"] + 4), (255, 120, 40))

    for npc in m.get("npcs", []):
        cv.rect(tx(npc["x"] - 8), ty(npc["y"] - 40), tx(npc["x"] + 8), ty(npc["y"]), (120, 255, 120))

    for mob in m.get("mobs", []):
        cv.rect(tx(mob["x"] - 20), ty(mob["y"] - 40), tx(mob["x"] + 20), ty(mob["y"]), (255, 60, 60))

    sx, sy = m["spawn"]
    cv.rect(tx(sx - 6), ty(sy - 12), tx(sx + 6), ty(sy), (0, 255, 0))

    n = len(traces)
    for idx, path in enumerate(traces):
        pts = parse_trace(path)
        color = (255, 255, 255) if idx == n - 1 else ramp(idx / max(1, n - 1))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            cv.line(tx(x0), ty(y0), tx(x1), ty(y1), color)

    cv.save(out_path)
    print(f"wrote {out_path} ({w}x{h}), {len(m['footholds'])} footholds, {n} traces")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
