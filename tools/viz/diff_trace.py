#!/usr/bin/env python3
"""diff_trace.py harness_trace.txt browser_console.log

Report the first tick where a browser replay (OfflineTrace=1 console log, lines
"[stdout] T ..." / "[stdout] M ...") diverges from `zakum_harness --trace` output.
"""
import sys, re
def load_harness(path):
    P, M = {}, {}
    for line in open(path):
        p = line.split()
        if not p: continue
        if p[0] == "T": P[int(p[1])] = (int(p[2]), int(p[3]))
        elif p[0] == "M": M.setdefault(int(p[1]), {})[int(p[2])] = (int(p[3]), int(p[4]))
    return P, M
def load_browser(path):
    P, M = {}, {}
    rx = re.compile(r"\[stdout\] (T|M) (-?\d+) (.*)")
    for line in open(path):
        m = rx.search(line)
        if not m: continue
        kind, tick, rest = m.group(1), int(m.group(2)), m.group(3).split()
        if kind == "T": P[tick] = (int(rest[0]), int(rest[1]))
        else: M.setdefault(tick, {})[int(rest[0])] = (int(rest[1]), int(rest[2]))
    return P, M
hP, hM = load_harness(sys.argv[1]); bP, bM = load_browser(sys.argv[2])
print(f"harness ticks {len(hP)}, browser ticks {len(bP)}")
common = sorted(set(hP) & set(bP))
first = None
for t in common:
    if hP[t] != bP[t] or hM.get(t) != bM.get(t):
        first = t; break
if first is None:
    print("no divergence on", len(common), "common ticks"); sys.exit(0)
print("first divergence at tick", first)
for t in range(max(0, first - 3), first + 4):
    print(f"t={t:5d} harness P={hP.get(t)} M={hM.get(t)}")
    print(f"        browser P={bP.get(t)} M={bM.get(t)}")
