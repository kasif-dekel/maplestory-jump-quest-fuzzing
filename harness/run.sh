#!/usr/bin/env bash
# Build the IJON toolchain image, then build the instrumented harness and run
# afl-fuzz inside it.
#
#   fuzz/run.sh                 # fuzz map 280020000 with $CORES instances
#   fuzz/run.sh shell           # drop into the container
#   fuzz/run.sh build           # only build the instrumented harness
#   fuzz/run.sh pull            # copy fuzzer output to $OUT (fuzz/out)
#   fuzz/run.sh best            # rank inputs inside the container, plot to fuzz/out
#   fuzz/run.sh checkpoint      # write /out/prefix-$MAP.bin from the current best
#   fuzz/run.sh solution FILE   # prefix + FILE (a crash/queue entry) -> fuzz/out/solution-$MAP.bin
#   fuzz/run.sh tmin FILE       # afl-tmin a solving input -> FILE-min.bin (fewer ticks)
#   fuzz/run.sh stop            # stop a running campaign
#
# Checkpoints: PREFIX=/out/prefix-280020000.bin makes the harness replay that
# input before the snapshot; the campaign then fuzzes continuations from short
# seeds. `checkpoint` picks the cut from the current best and chains onto the
# current PREFIX, so the prefix file is always itself a valid full input.
#
# Fuzzer output lives in the docker volume zakum-fuzz-out: AFL rewrites the
# test case file on every execution and a /mnt/c bind mount makes that
# 5-10x slower. Use `pull` to copy queue/crashes to the host.
#
# Environment knobs: MAP (280020000), CORES (4), MAX_TICKS (20000; a full
# Zakum run needs ~25000-35000, use 45000),
# DEATH_Y (see harness --death-y), OUT (fuzz/out), INST_RATIO (100),
# NOPNG=1 (skip the plot in `best`; the pure-python renderer takes 40 min on a
# 3700x2400 map),
# SEEDS (container path of a seed dir, e.g. /out/run-280020000-v1/master/queue
# to continue from an earlier campaign after the harness changed), DETACH=1 to
# run the container detached.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IJON_SRC="${IJON_SRC:-$ROOT/../ijon}"
IMAGE="${IMAGE:-zakum-ijon}"
OUT="${OUT:-$ROOT/fuzz/out}"

if [[ ! -f "$IJON_SRC/afl-fuzz.c" ]]; then
    echo "IJON checkout not found at $IJON_SRC (set IJON_SRC)" >&2
    exit 1
fi

case "${1:-}" in
    pull)
        mkdir -p "$OUT"
        docker run --rm -v zakum-fuzz-out:/out -v "$OUT":/host "$IMAGE" \
            bash -c 'cp -r /out/. /host/ && du -sh /host/run-* 2>/dev/null'
        exit 0
        ;;
    stop)
        ids="$(docker ps -q --filter "ancestor=$IMAGE")"
        [[ -n "$ids" ]] && docker stop -t 2 $ids
        exit 0
        ;;
    best)
        # Replays run where the queue lives (the volume), with the instrumented
        # harness already in the build volume; only the plot comes back.
        shift
        mkdir -p "$OUT"
        MAP="${MAP:-280020000}"
        docker run --rm \
            -v "$ROOT":/work \
            -v zakum-fuzz-out:/out \
            -v zakum-fuzz-build:/build \
            -e MAP="$MAP" \
            -e PREFIX="${PREFIX:-}" \
            -e GOAL="${GOAL:-}" -e ZAKUM_HARNESS_ARGS="${ZAKUM_HARNESS_ARGS:-}" -e ZAKUM_RANK_METRIC="${ZAKUM_RANK_METRIC:-route}" \
            -e NOPNG="${NOPNG:-}" \
            "$IMAGE" bash -c "python3 /work/fuzz/viz/best.py /build/zakum_harness /work/assets /out/run-\$MAP --map \$MAP \${NOPNG:---png /work/fuzz/out/progress-\$MAP.png} \${PREFIX:+--prefix \$PREFIX} \${GOAL:+--goal \$GOAL} $*"
        exit 0
        ;;
    checkpoint)
        shift
        MAP="${MAP:-280020000}"
        docker run --rm \
            -v "$ROOT":/work \
            -v zakum-fuzz-out:/out \
            -v zakum-fuzz-build:/build \
            -e MAP="$MAP" \
            -e PREFIX="${PREFIX:-}" \
            -e GOAL="${GOAL:-}" -e ZAKUM_HARNESS_ARGS="${ZAKUM_HARNESS_ARGS:-}" -e ZAKUM_RANK_METRIC="${ZAKUM_RANK_METRIC:-route}" \
            "$IMAGE" bash -c "python3 /work/fuzz/viz/checkpoint.py /build/zakum_harness /work/assets /out/run-\$MAP /out/prefix-\$MAP.new --map \$MAP \${PREFIX:+--prefix \$PREFIX} \${GOAL:+--goal \$GOAL} $* && mv /out/prefix-\$MAP.new /out/prefix-\$MAP.bin && cp /out/prefix-\$MAP.bin /work/fuzz/out/prefix-\$MAP.bin && ls -la /out/prefix-\$MAP.bin"
        exit 0
        ;;
    tmin)
        # Shrink a solving input with afl-tmin: the harness aborts on SOLVED,
        # so tmin's crash mode keeps only the bytes still needed to reach the
        # goal (walking about, waiting on ropes and detours fall away).
        #   MAP=105040310 GOAL= fuzz/run.sh tmin fuzz/solutions/<file>.bin
        shift
        MAP="${MAP:-280020000}"
        FILE="${1:?solution file, relative to the repo root}"
        docker run --rm \
            -v "$ROOT":/work \
            -v zakum-fuzz-build:/build \
            -e MAP="$MAP" -e GOAL="${GOAL:-}" -e ZAKUM_HARNESS_ARGS="${ZAKUM_HARNESS_ARGS:-}" \
            "$IMAGE" bash -c "/opt/ijon/afl-tmin -i /work/$FILE -o /work/${FILE%.bin}-min.bin -t 5000 -m none -- /build/zakum_harness --assets /work/assets --map \$MAP --max-ticks 60000 \${GOAL:+--goal \$GOAL} \$ZAKUM_HARNESS_ARGS @@"
        exit 0
        ;;
    solution)
        shift
        MAP="${MAP:-280020000}"
        ENTRY="${1:?container path of a queue/crash entry}"
        mkdir -p "$OUT"
        docker run --rm \
            -v "$ROOT":/work \
            -v zakum-fuzz-out:/out \
            -e MAP="$MAP" \
            -e PREFIX="${PREFIX:-}" \
            "$IMAGE" bash -c "cat \${PREFIX:-/dev/null} '$ENTRY' > /work/fuzz/out/solution-\$MAP.bin && ls -la /work/fuzz/out/solution-\$MAP.bin"
        exit 0
        ;;
esac

docker build -t "$IMAGE" -f "$ROOT/fuzz/Dockerfile" "$IJON_SRC"

TTY_FLAGS=""
if [[ -t 0 && -t 1 ]]; then
    TTY_FLAGS="-it"
fi

# DETACH=1 runs the campaign container detached (docker run -d) so it survives
# the calling shell being killed; follow it with `docker logs -f <id>`.
RUN_FLAGS="--rm $TTY_FLAGS"
if [[ "${DETACH:-0}" == "1" ]]; then
    RUN_FLAGS="--rm -d"
fi

# The build directory lives in a named volume: the repo is usually on a slow
# bind mount and the object files never need to leave the container.
exec docker run $RUN_FLAGS \
    -v "$ROOT":/work \
    -v zakum-fuzz-out:/out \
    -v zakum-fuzz-build:/build \
    -e MAP="${MAP:-280020000}" \
    -e CORES="${CORES:-4}" \
    -e MAX_TICKS="${MAX_TICKS:-20000}" \
    -e DEATH_Y="${DEATH_Y:-}" \
    -e INST_RATIO="${INST_RATIO:-100}" \
    -e SEEDS="${SEEDS:-}" -e RUN_NAME="${RUN_NAME:-}" \
    -e PREFIX="${PREFIX:-}" \
    -e GOAL="${GOAL:-}" -e ZAKUM_HARNESS_ARGS="${ZAKUM_HARNESS_ARGS:-}" -e ZAKUM_RANK_METRIC="${ZAKUM_RANK_METRIC:-route}" \
    -e AFL_SKIP_CPUFREQ=1 \
    -e AFL_NO_AFFINITY=1 \
    -e AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 \
    "$IMAGE" /work/fuzz/container-run.sh "$@"
