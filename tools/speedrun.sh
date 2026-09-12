#!/usr/bin/env bash
# Speedrun a solved map: fuzz for a shorter solution.
#
#   fuzz/speedrun.sh MAP [MINUTES] [GOAL or -] [extra harness args...]
#
# Seeds = every solution copy of the map in fuzz/solutions (original,
# -min*.bin), each cut back before the goal until it no longer solves, so
# afl-fuzz accepts it (a seed that solves the map is a "crash" and is dropped). The
# campaign runs with --speed: the IJON value of a hop level is how early the
# run first stands on it, so the fuzzer is rewarded for arriving earlier, not
# farther. Solving inputs land in master/crashes; after MINUTES (default 30)
# the fastest one is replayed from the spawn, minimized with tmin.py and saved
# as fuzz/solutions/<name>-speed.bin when it beats the best existing copy.
# Needs the container harness built with --speed support (fuzz/run.sh build)
# and the host harness in $HOST_HARNESS for the replays.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MAP=$1; MINUTES=${2:-30}; GOAL=${3:-}; [[ "$GOAL" == "-" ]] && GOAL=""; shift $(( $# >= 3 ? 3 : $# )); EXTRA="${*:-}"
HOST_HARNESS=${HOST_HARNESS:-build-host/zakum_harness}
CORES=${CORES:-12}
vol() { docker run --rm -v zakum-fuzz-out:/out -v zakum-fuzz-build:/build -v "$ROOT":/work zakum-ijon bash -c "$1"; }
log() { echo "$(date '+%F %T') $*"; }

solve_tick() {  # FILE -> tick or empty
    # --speed: solving is a normal exit (no abort message); tolerate the status (pipefail)
    { "$HOST_HARNESS" --assets assets --map "$MAP" --max-ticks 60000 ${GOAL:+--goal "$GOAL"} --speed $EXTRA "$1" 2>&1 >/dev/null || true; } \
        | sed -n 's/.*SOLVED tick=\([0-9]*\).*/\1/p' | tail -1
}

orig=$(ls fuzz/solutions/${MAP}-*.bin | grep -v -- "-min\|-speed" | head -1)
[[ -n "$orig" ]] || { echo "no solution for $MAP"; exit 1; }
base=${orig%.bin}
best_file=""; best_tick=999999
seeddir=fuzz/out/speed-seeds-$MAP; rm -rf "$seeddir"; mkdir -p "$seeddir"
for f in fuzz/solutions/${MAP}-*.bin; do
    t=$(solve_tick "$f"); [[ -n "$t" ]] || continue
    (( t < best_tick )) && { best_tick=$t; best_file=$f; }
    cp "$f" "$seeddir/$(basename "$f")"    # whole solutions: in --speed mode solving is a normal exit
done
log "map $MAP: best existing copy $(basename "$best_file") solves at tick $best_tick; $(ls "$seeddir" | wc -l) seeds"
# default seeds too (short variety for the mutator)
vol "cp /out/seeds/* /work/$seeddir/ 2>/dev/null; chown -R $(id -u):$(id -g) /work/$seeddir" || true

run=speed-$MAP
vol "[ -d /out/run-$run ] && mv /out/run-$run /out/run-$run-\$(date +%s)" || true
DETACH=1 MAP="$MAP" GOAL="$GOAL" MAX_TICKS=60000 CORES="$CORES" SEEDS="/work/$seeddir" RUN_NAME="$run" \
    ZAKUM_HARNESS_ARGS="--speed ${EXTRA}" fuzz/run.sh fuzz > /dev/null
log "speedrun campaign started for $MINUTES min"
sleep $(( MINUTES * 60 ))
fuzz/run.sh stop > /dev/null 2>&1 || true
sleep 3

# collect solving inputs
mkdir -p fuzz/out/speed-$MAP; rm -f fuzz/out/speed-$MAP/*
vol "for d in /out/run-$run/*; do [ -f \$d/ijon_max/500 ] && cp \$d/ijon_max/500 /work/fuzz/out/speed-$MAP/\$(basename \$d)-slot500; for f in \$d/crashes/id:*; do [ -f \"\$f\" ] && cp \"\$f\" /work/fuzz/out/speed-$MAP/\$(basename \$d)-\$(basename \$f | cut -c1-9); done; done 2>/dev/null; chown -R $(id -u):$(id -g) /work/fuzz/out/speed-$MAP" || true
n=$(ls fuzz/out/speed-$MAP 2>/dev/null | wc -l)
fastest=""; fastest_tick=$best_tick
for f in fuzz/out/speed-$MAP/*; do
    [[ -f "$f" ]] || continue
    t=$(solve_tick "$f"); [[ -n "$t" ]] || continue
    (( t < fastest_tick )) && { fastest_tick=$t; fastest=$f; }
done
if [[ -z "$fastest" ]]; then
    log "map $MAP: $n solving inputs, none faster than $best_tick ticks"
    exit 0
fi
log "map $MAP: $n solving inputs, fastest $fastest_tick ticks (was $best_tick); minimizing"
python3 fuzz/viz/tmin.py "$HOST_HARNESS" assets "$MAP" "$fastest" "${base}-speed.bin" ${GOAL:+--goal "$GOAL"} --jobs "${TMIN_JOBS:-8}" --speed $EXTRA > "fuzz/results/$MAP/speed.log" 2>&1 || true
tail -1 "fuzz/results/$MAP/speed.log"
