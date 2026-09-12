# Reproducing

Everything was run on a 16-core machine under WSL2 with Docker CE; the
fuzzing itself is Linux-only (AFL). Fuzzing times in the results table are wall clock with
12 fuzzer instances.

## 1. Game data

Convert the MapleStory v83 `.wz` files you own to `.nx` (NoLifeNx format;
JourneyClient's wasm port ships a converter under `scripts/wz-converter`) into
an `assets/` directory: `Map.nx`, `Mob.nx`, `Npc.nx`, `String.nx`,
`Character.nx`, `Etc.nx`, `Reactor.nx`, `Sound.nx`, `UI.nx`, `Base.nx`,
`Effect.nx`, `Item.nx`, `Morph.nx`, `Quest.nx`, `Skill.nx`, `TamingMob.nx`.

## 2. The client with the offline mode

```bash
git clone <JourneyClient wasm port> client && cd client
git apply /path/to/patches/journeyclient-offline-mode.patch
git apply /path/to/patches/web-offline-replay.patch     # browser page: ?config=, replay settings
cp -r /path/to/harness fuzz                               # harness.cpp, stubs, CMakeLists, Dockerfile, run scripts
cp -r /path/to/tools/viz fuzz/viz && cp /path/to/tools/*.sh fuzz/
```

The patch was taken against the port's tree as of 2026-09-08; if it does not
apply cleanly, the interesting parts are `src/client/Offline/Offline.{h,cpp}`
(new files) and the hooks in `Stage.cpp` (`apply_warp`, goal check),
`Obj.cpp` (moving map objects, trap hit boxes), `Spawn.cpp`/`Mob.cpp` (fixed
seed spawns), `Randomizer.h` and `Configuration.h`.

## 3. Native harness (tests, tracing, ranking)

```bash
cmake -S fuzz -B build-host && cmake --build build-host -j
fuzz/tests/run_tests.sh build-host/zakum_harness
build-host/zakum_harness --assets assets --map 280020000 --dump-map > map.json
build-host/zakum_harness --assets assets --map 280020000 --trace solutions/280020000-2026-09-08.bin
```

The last command replays a solution and prints `SOLVED tick=...`. Trace lines:
`T tick x y ground byte exactx exacty` (ground 1 = foothold, 2 = rope, 0 =
air), `M` mobs, `G` hits, `D`/`R`/`P`/`W` fall, reset portal, in-map warp,
goal.

## 4. Fuzzing

```bash
git clone https://github.com/RUB-SysSec/ijon ../ijon
(cd ../ijon && git apply /path/to/patches/ijon-dry-run-feeds-max-map.patch)
CORES=12 MAP=280020000 fuzz/run.sh          # builds the image + instrumented harness, starts afl-fuzz
fuzz/run.sh best                            # rank the queue by route hops
fuzz/run.sh checkpoint                      # cut the best input into a prefix
fuzz/run.sh stop
```

`fuzz/night.sh` runs the whole map list unattended (rank, checkpoint, save
and verify solutions, minimize, write `results/<map>/`); `fuzz/speedrun.sh
MAP MINUTES GOAL` fuzzes a solved map for a faster solution. Both expect the
Docker volumes `zakum-fuzz-out` and `zakum-fuzz-build` the first run creates.

## 5. Watching a replay

Build the wasm client (`scripts/docker_build_wasm.sh` in the port), start
its page server and asset server, and put a solution into a config:

```json
{ "OfflineMapId": "280020000", "OfflinePortal": "0",
  "OfflineReplay": "<base64 of the .bin>", "OfflineGoal": "",
  "OfflineHp": "100", "OfflineDamagePerHit": "1", "OfflineLavaLethal": "1",
  "OfflineHiddenPortals": "reset" }
```

`index.html?config=<url>` replays it; the console logs `[offline] SOLVED`.
`tools/gen_min_pages.py` and `tools/gen_all_page.py` generate one page per
solution copy and the all-maps grid.
