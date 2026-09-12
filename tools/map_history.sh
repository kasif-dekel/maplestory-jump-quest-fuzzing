#!/usr/bin/env bash
# Draw every campaign of one map (all archived runs in the zakum-fuzz-out
# volume plus the live one) over the map: fuzz/results/<map>/progress-everything*.png.
# Checkpoint campaigns get their prefix back by truncating the final prefix to
# the sizes night.log recorded ("restarted <map> from checkpoint ... (N bytes)").
#
#   fuzz/viz/map_history.sh MAP [GOAL] [HOST_HARNESS]
set -euo pipefail
MAP=$1; GOAL=${2:-}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
H=${3:-${HOST_HARNESS:-build-host/zakum_harness}}
W=${WORK:-/tmp/map-history-$MAP}
R="$ROOT/fuzz/results/$MAP"
mkdir -p "$W" "$R"
cd "$ROOT"
# 1. copy the runs out of the volume (queue, crashes, slot files, every 10th finding)
docker run --rm -v zakum-fuzz-out:/out -v "$W":/dst zakum-ijon bash -c '
  cd /out; for r in run-'"$MAP"'*; do
    [ -d /dst/$r/master/queue ] && [ "$r" != run-'"$MAP"' ] && continue
    rm -rf /dst/$r; mkdir -p /dst/$r/master/ijon_max
    cp -r $r/master/queue /dst/$r/master/; [ -d $r/master/crashes ] && cp -r $r/master/crashes /dst/$r/master/
    cd $r/master/ijon_max; ls | grep -v "^finding_" | xargs -r cp -t /dst/$r/master/ijon_max/
    ls | grep "^finding_" | awk "NR%10==0" | xargs -r cp -t /dst/$r/master/ijon_max/; cd /out
  done
  for r in run-'"$MAP"'*; do echo "$r $(grep -h ^start_time $r/master/fuzzer_stats | awk "{print \$3}")"; done > /dst/starts.txt
  [ -f /out/prefix-'"$MAP"'.bin ] && cp /out/prefix-'"$MAP"'.bin /dst/final-prefix.bin
  chown -R '"$(id -u):$(id -g)"' /dst'
[ -f "$R/checkpoint-prefix.bin" ] && cp "$R/checkpoint-prefix.bin" "$W/final-prefix.bin"
# 2. prefixes: sizes in log order; campaigns in start order (pre-checkpoint ones first, no prefix)
python3 - "$W" "$MAP" "$ROOT/fuzz/out/night.log" <<'PY'
import sys, os, re
W, mapid, log = sys.argv[1:4]
sizes = [int(m.group(1)) for m in re.finditer(r"restarted %s from checkpoint \S+ \((\d+) bytes\)" % mapid, open(log).read())]
runs = sorted([l.split() for l in open(os.path.join(W, "starts.txt")) if len(l.split()) == 2], key=lambda r: int(r[1]))  # skip dirs without fuzzer_stats
names = [r[0] for r in runs]
n_pre = len(names) - len(sizes)
if n_pre < 1:
    # more checkpoint restarts logged than runs kept (some were deleted or never wrote stats):
    # attribute the LAST len(names)-1 restarts to the runs after the first
    sizes = sizes[len(sizes) - (len(names) - 1):] if len(names) > 1 else []
    n_pre = len(names) - len(sizes)
args = []
if sizes and not os.path.exists(os.path.join(W, "final-prefix.bin")):
    sizes = []  # checkpoints logged, but the map was solved without a prefix in the end
    n_pre = len(names)
if sizes:
    data = open(os.path.join(W, "final-prefix.bin"), "rb").read()
    for name, n in zip(names[n_pre:], sizes):
        p = os.path.join(W, f"prefix-{n}.bin"); open(p, "wb").write(data[:n]); args.append(f"--prefix-for {name}={p}")
open(os.path.join(W, "history-args.txt"), "w").write(" ".join(os.path.join(W, n) for n in names) + " " + " ".join(args))
print(f"{n_pre} campaigns without checkpoint, {len(sizes)} with")
PY
[ -f "$R/map.json" ] || "$H" --assets assets --map "$MAP" ${GOAL:+--goal "$GOAL"} --dump-map > "$R/map.json" 2>/dev/null
ARGS=$(cat "$W/history-args.txt")
for s in 0.5 1.0; do
    out="$R/progress-everything.png"; [ "$s" = "1.0" ] && out="$R/progress-everything-2x.png"
    nice -n 10 python3 fuzz/viz/history.py "$H" assets "$R/map.json" "$out" $ARGS --map "$MAP" ${GOAL:+--goal "$GOAL"} --scale "$s" --sample-ijon 200 | tail -n +2
done
