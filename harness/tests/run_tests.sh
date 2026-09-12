#!/usr/bin/env bash
# Smoke tests for the headless harness. Usage: run_tests.sh <harness> [assets-dir]
set -uo pipefail

HARNESS="${1:?path to zakum_harness}"
ASSETS="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/assets}"
MAP=280020000
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail=0
check() {
    if eval "$2"; then
        echo "PASS $1"
    else
        echo "FAIL $1"
        fail=1
    fi
}

mk() { python3 -c "import sys; sys.stdout.buffer.write(bytes($2))" > "$TMP/$1"; }

run() { "$HARNESS" --assets "$ASSETS" --map "$MAP" "$@"; }

# 1. Spawn: the map dump must place the player on portal 0's foothold.
run --dump-map > "$TMP/map.json" 2>/dev/null
check "dump-map is JSON with spawn at portal 0" \
    'python3 - "$TMP/map.json" <<PY
import json,sys
m=json.load(open(sys.argv[1]))
sp=[p for p in m["portals"] if p["id"]==0][0]
assert m["spawn"][0]==sp["x"], m["spawn"]
# spawn is the ground below the portal (portal y is 30px above its foothold anchor)
assert sp["y"]-40 <= m["spawn"][1] <= sp["y"]+120, (m["spawn"], sp)
assert len(m["footholds"]) > 100
PY'

check "map mobs are spawned offline (3 Firebombs on 280020000)" \
    'python3 - "$TMP/map.json" <<PY
import json,sys
m=json.load(open(sys.argv[1]))
assert len(m["mobs"])==3 and all(x["id"]==5100002 for x in m["mobs"]), m["mobs"]
PY'

# 2. Encoding: holding RIGHT moves the player right; a JUMP byte lifts them.
mk right.bin "[0x02]*25"
run --trace "$TMP/right.bin" > "$TMP/right.txt" 2>/dev/null
check "RIGHT increases x" \
    'python3 - "$TMP/right.txt" <<PY
import sys
xs=[int(l.split()[2]) for l in open(sys.argv[1]) if l.startswith("T")]
assert xs[-1] > xs[0] + 50, (xs[0], xs[-1])
PY'

mk jump.bin "[0x00]*5 + [0x04]*2 + [0x00]*40"
run --trace "$TMP/jump.bin" > "$TMP/jump.txt" 2>/dev/null
check "JUMP leaves the ground and lands again" \
    'python3 - "$TMP/jump.txt" <<PY
import sys
rows=[l.split() for l in open(sys.argv[1]) if l.startswith("T")]
ys=[int(r[3]) for r in rows]; ground=[int(r[4]) for r in rows]
assert min(ys) < ys[0] - 40, (ys[0], min(ys))
assert 0 in ground and ground[-1] == 1
PY'

# 3. Determinism: identical input, identical trace.
mk mix.bin "([0x02]*12+[0x06]*3)*20"
run --trace "$TMP/mix.bin" > "$TMP/a.txt" 2>/dev/null
run --trace "$TMP/mix.bin" > "$TMP/b.txt" 2>/dev/null
check "trace is deterministic" 'cmp -s "$TMP/a.txt" "$TMP/b.txt" && [ -s "$TMP/a.txt" ]'

# 4. Death trigger: walking right off the start eventually reaches the lava
#    floor and the run ends with FELL before the tick budget.
mk fall.bin "([0x02]*12+[0x06]*3)*100"
run "$TMP/fall.bin" > /dev/null 2> "$TMP/fall.err"; code=$?
check "falling into the lava is lethal by default (TRAP trap/area)" \
    '[ $code -eq 0 ] && grep -q "^TRAP .*trap/area" "$TMP/fall.err"'
run --lava-hurts --trace "$TMP/fall.bin" > "$TMP/fall.txt" 2> "$TMP/fall2.err"; code=$?
check "with --lava-hurts the lava costs 1 HP per hit and the run goes on" \
    '[ $code -eq 0 ] && grep -q "^END" "$TMP/fall2.err" && grep -q "^G .* 1 99 hit by trap trap/area" "$TMP/fall.txt"'

# 4c. Traps: the lava is covered by trap/area objects; standing in one is lethal.
run --dump-map > "$TMP/map.json" 2>/dev/null
check "map has damage traps (stones, steam, lava areas)" \
    'python3 - "$TMP/map.json" <<PY
import json,sys
m=json.load(open(sys.argv[1])); names=[t["name"] for t in m["traps"]]
assert any("trap/stone" in n for n in names) and any("trap/area" in n for n in names), names[:5]
PY'
mk idle.bin "[0x00]*400"
run --spawn 500,-140 "$TMP/idle.bin" > /dev/null 2> "$TMP/trap.err"; code=$?
check "standing in a lava area exits 0 with TRAP" '[ $code -eq 0 ] && grep -q "^TRAP" "$TMP/trap.err"'
run --lava-hurts --spawn 500,-140 --trace "$TMP/idle.bin" > "$TMP/hpt.txt" 2>/dev/null
check "with --lava-hurts, lava hits are 1 damage each, spaced by the 2 s invincibility" \
    'python3 - "$TMP/hpt.txt" <<PY
import sys
g=[l.split() for l in open(sys.argv[1]) if l.startswith("G ")]
assert len(g)>=5, len(g)
assert all(int(r[4])==1 for r in g), g[0]
gaps=[int(b[1])-int(a[1]) for a,b in zip(g,g[1:])]
assert all(240<=d<=260 for d in gaps), gaps
assert int(g[-1][5])==100-len(g)
PY'

# 4b. Hazards: standing on the first Firebomb's platform gets the player hit,
#     and the run ends with HIT; mobs move on their own.
mk idle.bin "[0x00]*400"
run --spawn 1076,-240 --trace --keep-going "$TMP/idle.bin" > "$TMP/hit.txt" 2>/dev/null
check "Firebomb patrols (mob x changes over time)" \
    'python3 - "$TMP/hit.txt" <<PY
import sys
xs=[int(l.split()[3]) for l in open(sys.argv[1]) if l.startswith("M ") and l.split()[2]=="1000"]
assert len(set(xs))>3, set(xs)
PY'
run --hp 1 --spawn 1076,-240 "$TMP/idle.bin" > /dev/null 2> "$TMP/hit.err"; code=$?
check "with 1 HP, touching a Firebomb exits 0 with HIT" '[ $code -eq 0 ] && grep -q "^HIT" "$TMP/hit.err"'

# 5. No input: run ends cleanly at the budget boundary.
: > "$TMP/empty.bin"
run "$TMP/empty.bin" > /dev/null 2> "$TMP/empty.err"; code=$?
check "empty input exits 0 with END after the 96-tick key-free tail" '[ $code -eq 0 ] && grep -q "^END tick=96" "$TMP/empty.err"'
run --tail-bytes 0 "$TMP/empty.bin" > /dev/null 2> "$TMP/empty0.err"; code=$?
check "--tail-bytes 0 ends at tick 0" '[ $code -eq 0 ] && grep -q "^END tick=0" "$TMP/empty0.err"'

exit $fail
