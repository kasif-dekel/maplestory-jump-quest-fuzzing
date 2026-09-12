#!/usr/bin/env bash
# Overnight supervisor: solve jump quests one after another.
#
#   nohup fuzz/night.sh > fuzz/out/night.log 2>&1 &
#
# For each "MAP GOAL" line in MAPS (GOAL empty = farthest warp portal):
#   * start a campaign (adopting one that is already running for that map),
#   * every CHECK_EVERY seconds rank the queue (fuzz/run.sh best, with the
#     current prefix); on a solve: assemble prefix + entry, verify it from the
#     spawn, save it under fuzz/solutions/, write results, commit, next map;
#   * if the best progress has not improved for STALL_AFTER seconds, or grew
#     by RATCHET_PX since the last checkpoint, take a checkpoint from the best
#     input and restart from it (chained prefixes);
#   * give up on a map after GIVE_UP seconds and move on.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CHECK_EVERY=${CHECK_EVERY:-180}
STALL_AFTER=${STALL_AFTER:-300}
# Ratchet: also checkpoint as soon as the best progress has grown by
# RATCHET_PX since the last checkpoint (and RATCHET_MIN seconds passed). On a
# staircase every platform is a precise few bytes; havoc on a long input
# breaks the climb before it finds the next jump, so keep the suffix short.
RATCHET_PX=${RATCHET_PX:-60}
RATCHET_MIN=${RATCHET_MIN:-240}
MARGIN_TICKS=${MARGIN_TICKS:-120}   # slack kept before the frontier at a cut (about two jumps)
GIVE_UP=${GIVE_UP:-18000}
CORES=${CORES:-12}
MAX_TICKS=${MAX_TICKS:-45000}
HOST_HARNESS=${HOST_HARNESS:-build-host/zakum_harness}

# Current queue: Shumi's Line 3 Construction Site jump quest courses
# (Kerning subway) B1 Area 1-2, B2 Area 1-2, B3 Area 1-3, exit portal as the
# goal; shorter/simpler areas first. B3 Area 2 (907) is a corridor whose
# continuous floor (y 923) runs toward the goal: --floor-y 900 gives IJON no
# credit down there, so falls are not rewarded (the way back up is one ladder
# at the far left). The "Subway Depot" maps 10300090[259]
# fuzzed earlier are the end rooms, not courses. Solved maps are skipped. Goals: the pet trainer NPC at the top
# of each pet park (the maps' farthest portal is the exit next to the spawn).
MAPS=${MAPS:-"103000901
103000907 - --floor-y 900
103000904
103000906
103000908
103000903
103000900
109040001
109040002
109040003
109040004
209000015 npc:2001004
209000010 npc:2001004
209000002 npc:2001004
209000014 npc:2001004
910020200 xy:-181,-4195 --portal 1"}
# 910020200 "Playground of Lupin Leading to Jump Stand" (The Lost Snipe event):
# goal = the Jump Stand reactor on the top platform, start at portal st00
# (--portal 1, where the NPC drops the player; the map's own spawn is a lobby
# floor with no way up). Its siblings 910020100 (Thornbush) and 910020300
# (Jumping Board) climb through springboard portals (type 12) the client does
# not simulate, so they are left out.

log() { echo "$(date '+%F %T') $*"; }

vol() { docker run --rm -v zakum-fuzz-out:/out -v zakum-fuzz-build:/build -v "$ROOT":/work zakum-ijon bash -c "$1"; }

# Containers of the fuzzing campaign for a map: the fuzz image running
# container-run.sh with MAP set (ranking/checkpoint containers run bash -c).
campaign_ids() {
    for id in $(docker ps -q --filter ancestor=zakum-ijon --filter status=running); do
        if docker inspect --format '{{.Path}} {{join .Args " "}}' "$id" | grep -q "container-run.sh" \
           && docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$id" | grep -q "^MAP=$1$"; then
            echo "$id"
        fi
    done
}
campaign_running() { [[ -n "$(campaign_ids "$1")" ]]; }

start_campaign() {  # MAP GOAL PREFIX
    local map=$1 goal=$2 prefix=$3
    if [[ -n "$prefix" ]]; then
        DETACH=1 MAP="$map" GOAL="$goal" PREFIX="$prefix" MAX_TICKS="$MAX_TICKS" CORES="$CORES" fuzz/run.sh fuzz >/dev/null 2>&1
    else
        DETACH=1 MAP="$map" GOAL="$goal" MAX_TICKS="$MAX_TICKS" CORES="$CORES" fuzz/run.sh fuzz >/dev/null 2>&1
    fi
}

stop_map_campaign() {  # MAP
    for id in $(campaign_ids "$1"); do
        docker stop -t 2 "$id" >/dev/null
    done
}

rank() {  # MAP GOAL PREFIX -> prints "best solved firstsolvedfile"
    local map=$1 goal=$2 prefix=$3 out
    local t0=$(date +%s)
    out=$(NOPNG=1 MAP="$map" GOAL="$goal" PREFIX="$prefix" fuzz/run.sh best --top 40 2>/dev/null)
    local dt=$(( $(date +%s) - t0 ))
    (( dt > 120 )) && log "ranking took ${dt}s"
    local best solved sfile
    best=$(echo "$out" | awk '/best_x/ { getline; if ($1 ~ /^-?[0-9]+$/) print $1 }')
    solved=$(echo "$out" | grep -oE "[0-9]+ solve the map" | awk '{print $1}')
    sfile=$(echo "$out" | grep " SOLVED$" | head -1 | awk '{print $4}')
    # NA = the ranking failed (container error, no queue yet); callers must not
    # treat it as a measurement.
    echo "${best:-NA} ${solved:-0} ${sfile:-}"
}

save_solution() {  # MAP GOAL PREFIX ENTRY(relative to run dir)
    local map=$1 goal=$2 prefix=$3 entry=$4 date
    date=$(date +%F-%H%M)
    local out="fuzz/solutions/${map}-${date}.bin"
    vol "cat ${prefix:-/dev/null} '/out/run-$map/$entry' > /work/$out"
    local verdict
    verdict=$("$HOST_HARNESS" --assets assets --map "$map" ${goal:+--goal "$goal"} ${ZAKUM_HARNESS_ARGS:-} --max-ticks 60000 "$out" 2>&1 | grep -E "^(SOLVED|TRAP|HIT|FELL|END)" | tail -1)
    log "solution $out: $verdict"
    if [[ "$verdict" != SOLVED* ]]; then
        log "verification failed, keeping the file but not recording it"
        return 1
    fi
    local R="fuzz/results/$map"
    mkdir -p "$R"
    "$HOST_HARNESS" --assets assets --map "$map" ${goal:+--goal "$goal"} ${ZAKUM_HARNESS_ARGS:-} --dump-map > "$R/map.json" 2>/dev/null
    "$HOST_HARNESS" --assets assets --map "$map" ${goal:+--goal "$goal"} ${ZAKUM_HARNESS_ARGS:-} --max-ticks 60000 --trace "$out" 2>/dev/null | gzip > "$R/solution-trace.txt.gz"
    local hits
    hits=$(zcat "$R/solution-trace.txt.gz" | grep -c "^G ")
    log "solution takes $hits hits"
    zcat "$R/solution-trace.txt.gz" > /tmp/night_trace_$$.txt
    python3 fuzz/viz/plot_traces.py "$R/map.json" "$R/solution-path.png" --scale 0.5 /tmp/night_trace_$$.txt >/dev/null 2>&1
    rm -f /tmp/night_trace_$$.txt
    NOPNG=1 MAP="$map" GOAL="$goal" PREFIX="$prefix" fuzz/run.sh best --top 5 > "$R/ranking.txt" 2>/dev/null
    # every campaign of the map over the map (host, PIL); minutes, so in the background
    (fuzz/viz/map_history.sh "$map" "$goal" > "$R/history.log" 2>&1 &)
    # and a minimized copy of the solution (the original is kept), also in the background
    (python3 fuzz/viz/tmin.py "$HOST_HARNESS" assets "$map" "$out" "${out%.bin}-min.bin" ${goal:+--goal "$goal"} --jobs 6 ${ZAKUM_HARNESS_ARGS:-} > "$R/tmin.log" 2>&1 &)
    [[ -n "$prefix" ]] && vol "cat $prefix > /work/$R/checkpoint-prefix.bin"
    python3 - "$map" "$goal" "$out" "$verdict" "$hits" <<'PY'
import json, sys, os
mapid, goal, out, verdict, hits = sys.argv[1:6]
p = f"fuzz/results/{mapid}/solution-summary.json"
json.dump({"map": int(mapid), "goal": goal or "(farthest warp portal)", "solution_file": os.path.relpath(out, f"fuzz/results/{mapid}"),
           "bytes": os.path.getsize(out), "ticks": 4 * os.path.getsize(out), "hits": int(hits), "verdict": verdict}, open(p, "w"), indent=2)
PY
    # browser config for the morning
    python3 - "$map" "$goal" "$out" <<'PY'
import json, base64, sys
mapid, goal, out = sys.argv[1:4]
cfg = {"AssetsServerIP": None, "AssetsServerPort": None, "AssetsServerProtocol": None, "MapleStoryServerIp": None,
       "MapleStoryServerPort": None, "ProxyIP": None, "ProxyPort": None,
       "OfflineMapId": mapid, "OfflinePortal": "0", "OfflineReplay": base64.b64encode(open(out, "rb").read()).decode(),
       "OfflineDeathY": "", "OfflineTrace": "", "OfflineHp": "100", "OfflineDamagePerHit": "1", "OfflineLavaLethal": "1",
       "OfflineGoal": goal, "OfflineHiddenPortals": "reset"}
import re, os
m = re.search(r"--hidden-portals\s+(\S+)", os.environ.get("ZAKUM_HARNESS_ARGS", ""))
if m: cfg["OfflineHiddenPortals"] = m.group(1)
m = re.search(r"--portal\s+(\d+)", os.environ.get("ZAKUM_HARNESS_ARGS", ""))
if m: cfg["OfflinePortal"] = m.group(1)   # maps fuzzed from a portal other than the spawn
json.dump(cfg, open(f"web/config-{mapid}.json", "w"), indent=2)
PY
    git add "$out" "$R" >/dev/null 2>&1
    git commit -q -m "solutions: map $map solved by AFL+IJON ($verdict)" >/dev/null 2>&1
    return 0
}

while read -r map goal extra; do
    [[ -z "$map" ]] && continue
    [[ "${goal:-}" == "-" ]] && goal=""   # "-" = default goal (farthest warp portal) with extra args
    # third column: extra harness arguments for this map (campaign, ranking,
    # checkpoints and the host verification all get them)
    # a rank=axis|distance token selects the ranking metric (best.py)
    export ZAKUM_RANK_METRIC=route
    if [[ "${extra:-}" == *rank=* ]]; then
        ZAKUM_RANK_METRIC=$(sed -E 's/.*rank=([a-z]+).*/\1/' <<<"$extra")
        extra=$(sed -E 's/rank=[a-z]+//' <<<"$extra")
    fi
    export ZAKUM_HARNESS_ARGS="${extra:-}"
    if ls fuzz/solutions/${map}-*.bin >/dev/null 2>&1; then
        log "map $map already has a solution, skipping"
        continue
    fi
    log "===== map $map goal '${goal:-(farthest warp portal)}' ====="
    prefix=""
    if vol "test -f /out/prefix-$map.bin" 2>/dev/null; then
        prefix="/out/prefix-$map.bin"
        log "using existing checkpoint $prefix"
    fi
    if campaign_running "$map"; then
        log "adopting the running campaign for $map"
    else
        vol "[ -d /out/run-$map ] && mv /out/run-$map /out/run-$map-\$(date +%s)" 2>/dev/null
        start_campaign "$map" "$goal" "$prefix"
        log "campaign started"
    fi
    start_ts=$(date +%s); last_improve=$start_ts; best_seen=""
    last_cp=$start_ts; best_at_cp=""; ratchet_base=""   # best_at_cp: only set by a checkpoint
    solved_this=0
    stall_repeat=0   # stall checkpoints in a row that did not move the best
    take_checkpoint() {  # REASON [--explore]
        log "$1: taking a checkpoint${2:+ ($2)}"
        if MAP="$map" GOAL="$goal" PREFIX="$prefix" fuzz/run.sh checkpoint --margin-ticks "$MARGIN_TICKS" ${2:-} >/dev/null 2>&1; then
            stop_map_campaign "$map"
            vol "mv /out/run-$map /out/run-$map-cp\$(date +%s)"
            prefix="/out/prefix-$map.bin"
            start_campaign "$map" "$goal" "$prefix"
            log "restarted $map from checkpoint $prefix ($(vol "stat -c %s $prefix" 2>/dev/null) bytes)"
        else
            log "checkpoint failed; continuing"
        fi
        last_improve=$(date +%s); last_cp=$last_improve; best_at_cp=$best_seen; ratchet_base=$best_seen
    }
    while true; do
        sleep "$CHECK_EVERY"
        if ! campaign_running "$map"; then
            # afl-fuzz refuses an output directory that was in use less than
            # 25 minutes ago: archive it and continue from its queue.
            stamp=$(date +%s)
            log "campaign container for $map is not running; restarting from /out/run-$map-dead$stamp"
            vol "[ -d /out/run-$map ] && mv /out/run-$map /out/run-$map-dead$stamp" 2>/dev/null
            if vol "test -d /out/run-$map-dead$stamp/master/queue" 2>/dev/null; then
                SEEDS="/out/run-$map-dead$stamp/master/queue" start_campaign "$map" "$goal" "$prefix"
            else
                start_campaign "$map" "$goal" "$prefix"
            fi
            continue
        fi
        read -r best solved sfile <<<"$(rank "$map" "$goal" "$prefix")"
        now=$(date +%s)
        log "map $map: best=$best solved=$solved elapsed=$((now-start_ts))s since-improve=$((now-last_improve))s prefix=${prefix:-none}"
        if [[ "${solved:-0}" != "0" && -n "$sfile" ]]; then
            if save_solution "$map" "$goal" "$prefix" "$sfile"; then
                solved_this=1
                break
            fi
        fi
        if [[ "$best" == "NA" ]]; then
            log "ranking failed for $map (see MAP=$map GOAL=$goal PREFIX=$prefix fuzz/run.sh best); not counted as a stall"
            last_improve=$now
        elif [[ -z "$best_seen" || "$best" -gt "$best_seen" ]]; then
            best_seen=$best; last_improve=$now
        fi
        if (( now - start_ts > GIVE_UP )); then
            log "giving up on $map after $((now-start_ts))s (best $best_seen)"
            break
        fi
        if (( now - last_improve > STALL_AFTER )); then
            # A second stall at the same best means the best-by-height frontier
            # is a dead end: cut where IJON is currently improving instead.
            if [[ -n "$best_at_cp" && "$best_seen" == "$best_at_cp" ]]; then
                stall_repeat=$((stall_repeat + 1))
            else
                stall_repeat=0
            fi
            # alternate: frontier cut, explore cut, frontier cut, ...
            take_checkpoint "stalled for $((now-last_improve))s" $([[ $((stall_repeat % 2)) -eq 1 ]] && echo --explore)
        elif [[ "$best" != "NA" && -n "$ratchet_base" ]] && (( now - last_cp >= RATCHET_MIN )) \
             && (( best_seen - ratchet_base >= RATCHET_PX )); then
            take_checkpoint "ratchet: best $best_seen is $((best_seen - ratchet_base)) px past the last checkpoint"
        fi
        [[ -z "$ratchet_base" && "$best" != "NA" ]] && ratchet_base=$best
    done
    stop_map_campaign "$map"
    log "map $map done (solved=$solved_this)"
done <<< "$MAPS"
log "all maps processed"
