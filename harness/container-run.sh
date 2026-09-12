#!/usr/bin/env bash
# Runs inside the zakum-ijon container (see run.sh).
set -euo pipefail

MODE="${1:-fuzz}"
MAP="${MAP:-280020000}"
CORES="${CORES:-4}"
MAX_TICKS="${MAX_TICKS:-20000}"
INST_RATIO="${INST_RATIO:-100}"
OUT=/out
BUILD=/build

build() {
    export AFL_INST_RATIO="$INST_RATIO"
    cmake -S /work/fuzz -B "$BUILD" \
        -DCMAKE_BUILD_TYPE=Release \
        -DIJON_DIR=/opt/ijon \
        -DCMAKE_C_COMPILER=/opt/ijon/afl-clang-fast \
        -DCMAKE_CXX_COMPILER=/opt/ijon/afl-clang-fast++ > /dev/null
    cmake --build "$BUILD" -j"$(nproc)"
}

harness_args() {
    local args=(--assets /work/assets --map "$MAP" --max-ticks "$MAX_TICKS")
    if [[ -n "${DEATH_Y:-}" ]]; then
        args+=(--death-y "$DEATH_Y")
    fi
    if [[ -n "${GOAL:-}" ]]; then
        args+=(--goal "$GOAL")
    fi
    if [[ -n "${PREFIX:-}" ]]; then
        # Checkpoint: the harness replays this before the forkserver snapshot,
        # so the queue holds continuations and prefix+entry is a full input.
        args+=(--prefix "$PREFIX")
    fi
    if [[ -n "${ZAKUM_HARNESS_ARGS:-}" ]]; then
        # Per-map rules, e.g. --hidden-portals reset-all (also honoured by the
        # ranking tools).
        args+=($ZAKUM_HARNESS_ARGS)
    fi
    if [[ -n "${ROUTE_FILE:-}" ]]; then
        args+=(--route "$ROUTE_FILE")
    fi
    echo "${args[@]}"
}

# Route-aware IJON feedback (ZAKUM_RANK_METRIC=route, the default): dump the
# map, let best.py compute hops-to-goal per platform, hand the table to the
# harness with --route. Falls back to the distance feedback if that fails.
route_table() {
    ROUTE_FILE=""
    [[ "${ZAKUM_RANK_METRIC:-route}" == "route" ]] || return 0
    local mapjson="$OUT/map-$MAP.json" table="$OUT/route-$MAP.txt"
    if "$BUILD/zakum_harness" --assets /work/assets --map "$MAP" ${GOAL:+--goal "$GOAL"} ${ZAKUM_HARNESS_ARGS:-} --dump-map > "$mapjson" 2>/dev/null \
        && python3 /work/fuzz/viz/best.py --export-route "$mapjson" "$table"; then
        ROUTE_FILE="$table"
    else
        echo "route table failed for map $MAP: distance feedback"
    fi
}

seeds() {
    if [[ -n "${SEEDS:-}" ]]; then
        # Continuing from an earlier run: IJON keeps its per-slot frontier
        # inputs in ijon_max/<slot> next to the queue, and they are usually
        # not queue entries. Seeding from the queue alone throws the frontier
        # away, so merge the slot files in (finding_* are history, skipped).
        local ijon="$(dirname "$SEEDS")/ijon_max"
        if [[ "$(basename "$SEEDS")" == "queue" && -d "$ijon" ]]; then
            local merged="$OUT/seeds-$MAP-$(date +%s)"
            mkdir -p "$merged"
            cp "$SEEDS"/id:* "$merged"/ 2>/dev/null || true
            for f in "$ijon"/*; do
                [[ -f "$f" && "$(basename "$f")" != finding_* ]] && cp "$f" "$merged/slot_$(basename "$f")"
            done
            echo "$merged"
            return
        fi
        echo "$SEEDS"
        return
    fi
    local dir="$OUT/seeds"
    mkdir -p "$dir"
    # Hold RIGHT / LEFT; the same with a jump every 15 bytes. Enough for AFL
    # to learn the byte alphabet; IJON does the rest. Staircases in the
    # Forest of Patience maps go left as often as right.
    python3 - "$dir" <<'PY'
import sys, os
d = sys.argv[1]
open(os.path.join(d, "right.bin"), "wb").write(bytes([0x02]) * 400)
open(os.path.join(d, "right_jump.bin"), "wb").write(bytes(([0x02] * 12 + [0x06] * 3) * 30))
open(os.path.join(d, "left.bin"), "wb").write(bytes([0x01]) * 400)
open(os.path.join(d, "left_jump.bin"), "wb").write(bytes(([0x01] * 12 + [0x05] * 3) * 30))
# Short hops: after a checkpoint the character often stands a few bytes from
# a platform edge, and the next platform is 20 px over and 60 px up.
open(os.path.join(d, "right_hop.bin"), "wb").write(bytes(([0x02] * 4 + [0x06] * 2) * 60))
open(os.path.join(d, "left_hop.bin"), "wb").write(bytes(([0x01] * 4 + [0x05] * 2) * 60))
# ... and a checkpoint often ends right at an edge: jump first, then walk.
open(os.path.join(d, "right_jump_now.bin"), "wb").write(bytes([0x06] * 2 + [0x02] * 398))
open(os.path.join(d, "left_jump_now.bin"), "wb").write(bytes([0x05] * 2 + [0x01] * 398))
# Hops with UP held: a rope whose bottom hangs above the platform (Deep
# Forest 2) is only grabbed by jumping into it with UP pressed.
open(os.path.join(d, "right_hop_up.bin"), "wb").write(bytes(([0x02] * 4 + [0x0e] * 2) * 60))
open(os.path.join(d, "left_hop_up.bin"), "wb").write(bytes(([0x01] * 4 + [0x0d] * 2) * 60))
# Staggered take-offs: walk k bytes, then jump and keep going. A 34 px gap with
# a 60 px rise leaves a take-off window of a dozen pixels (Forest of Patience).
for k in range(1, 6):
    open(os.path.join(d, "right_walk%d_jump.bin" % k), "wb").write(bytes([0x02] * k + [0x06] * 2 + [0x02] * 40))
    open(os.path.join(d, "left_walk%d_jump.bin" % k), "wb").write(bytes([0x01] * k + [0x05] * 2 + [0x01] * 40))
# Jump in place: the next platform is sometimes straight above (footholds
# are passable from below).
open(os.path.join(d, "jump_in_place.bin"), "wb").write(bytes(([0x00] * 6 + [0x04] * 2) * 50))
# Zig-zag tiers (Deep Forest 3): alternate the hop direction every hop.
open(os.path.join(d, "zigzag_rl.bin"), "wb").write(bytes(([0x02] * 4 + [0x06] * 2 + [0x02] * 10 + [0x01] * 4 + [0x05] * 2 + [0x01] * 10) * 12))
open(os.path.join(d, "zigzag_lr.bin"), "wb").write(bytes(([0x01] * 4 + [0x05] * 2 + [0x01] * 10 + [0x02] * 4 + [0x06] * 2 + [0x02] * 10) * 12))
PY
    echo "$dir"
}

case "$MODE" in
    build)
        build
        ;;
    shell)
        exec bash
        ;;
    fuzz)
        build
        SEEDS="$(seeds)"
        route_table
        ARGS="$(harness_args)"
        RUN="$OUT/run-${RUN_NAME:-$MAP}"   # RUN_NAME: e.g. speed-<map> for fuzz/speedrun.sh
        mkdir -p "$RUN"
        # A seed that already reaches the goal (it happens right after a
        # checkpoint close to the end) makes afl-fuzz refuse to start. Keep
        # such seeds as crashes of this run, where the ranking finds them.
        mkdir -p "$RUN/master/crashes"
        for f in "$SEEDS"/*; do
            [[ -f "$f" ]] || continue
            if ! "$BUILD/zakum_harness" $ARGS "$f" > /dev/null 2>&1; then
                echo "seed $(basename "$f") solves the map: kept as a crash, removed from the seeds"
                cp "$f" "$RUN/master/crashes/id:seed-$(basename "$f")"
                rm -f "$f"
            fi
        done
        echo "harness: $BUILD/zakum_harness $ARGS @@"
        echo "output:  $RUN"
        for ((i = 1; i < CORES; i++)); do
            afl-fuzz -S "slave$i" -i "$SEEDS" -o "$RUN" -m none -t 2000 -- \
                "$BUILD/zakum_harness" $ARGS @@ > "$RUN/slave$i.log" 2>&1 &
        done
        exec afl-fuzz -M master -i "$SEEDS" -o "$RUN" -m none -t 2000 -- \
            "$BUILD/zakum_harness" $ARGS @@
        ;;
    *)
        exec "$@"
        ;;
esac
