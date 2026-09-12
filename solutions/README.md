# Solutions

Input files (one byte = key mask held for 4 ticks, see `../docs/INPUT_FORMAT.md`) that
take the character from the spawn to the exit portal. Replay with
`zakum_harness --assets assets --map <id> <file>` (prints `SOLVED`) or by
putting the base64 of the file into `OfflineReplay` in `web/config.json`.

| File | Map | Found | Length | Notes |
|---|---|---|---|---|
| `280020000-2026-09-08.bin` | Breath of Lava 1 | 2026-09-08, AFL+IJON, 12 cores, ~4 h of fuzzing across campaigns | 5092 bytes, 20352 ticks (162.8 s) | 100 HP / 1 damage per hit / lethal lava rules; 13 hits taken (2 Firebomb touches on the first two platforms, 6 falling rocks, 5 touches on the third Firebomb), 87 HP left; climbs all 25 ropes; last quarter found from a checkpoint at tick 17731 |
| `280020001-2026-09-09-0301.bin` | Breath of Lava 2 | 2026-09-09 02:23, AFL+IJON, 12 cores, 1 h 39 min, one campaign, no checkpoint | 4484 bytes, 17930 ticks (143.4 s) | goal = NPC Lira (2032003); same HP/damage/lava rules; 15 hits taken (7 fireballs, 3 rocks, 5 vents), 85 HP left; 32 solving inputs were found within 40 min of the first, fastest 17411 ticks |
| `105040310-2026-09-09-1606.bin` | Deep Forest of Patience 1 | 2026-09-09 16:06, AFL+IJON, 12 cores; 4 h without checkpoints got nowhere, then 2 h with 13 chained checkpoints | 3119 bytes, 12184 ticks (97.5 s) | goal = portal in00 at the top left (3120 px climb); 4 hits from the swinging spiked balls, 96 HP left |
| `105040311-2026-09-09-1746.bin` | Deep Forest of Patience 2 | 2026-09-09 17:46, AFL+IJON, 12 cores, 1 h 39 min, 11 chained checkpoints | 3340 bytes, 12800 ticks (102.4 s) | goal = NPC 1063000 at the top (2760 px climb) via two ropes; 10 hits, 90 HP left |
| `100000202-2026-09-09-2353.bin` | Pet-Walking Road (Henesys pet park) | 2026-09-09 23:53, AFL+IJON, 12 cores; 4 h 20 min including the rule and ranking changes it forced, ~20 chained checkpoints | 4529 bytes, 17244 ticks (137.9 s) | goal = pet trainer NPC 1012007 at the top left; hidden portals reset (`--hidden-portals reset-all`); no hazards, 100 HP |
| `220000006-2026-09-10-0155.bin` | Ludibrium Pet Walkway | 2026-09-10 01:55, AFL+IJON, 12 cores, 2 h from a cold start, 14 chained checkpoints | 2661 bytes, 9257 ticks (74.1 s) | goal = pet trainer NPC 2040033 at the top right; hidden portals reset; no hazards, 100 HP |
| `105040312-2026-09-10-0301.bin` | Deep Forest of Patience 3 | 2026-09-10 03:01, AFL+IJON, 12 cores; 1 h 45 min with the height metric got stuck at a dead-end peak, 65 min with the route metric solved it | 3653 bytes, 14360 ticks (114.9 s) | goal = portal in00 at the top right; a 540 px steered drop between the two towers; 11 hits (javelins), 89 HP |
| `101000100-2026-09-10-0722.bin` | Forest of Patience 1 (Ellinia) | 2026-09-10 07:22, AFL+IJON, 12 cores, 4 h 20 min cold, 38 chained checkpoints | 4605 bytes, 17328 ticks (138.6 s) | goal = portal in00 at the top (3960 px climb); no hits |
| `103000901-2026-09-10-1455.bin` | Kerning Subway B1 Area 2 (Shumi's Line 3 Construction Site) | 2026-09-10 14:55, AFL+IJON, 12 cores, 17 min, 2 chained checkpoints | 1159 bytes, SOLVED at tick 3216 (25.7 s) | goal = exit portal out01 to the depot (1263 px climb); 6 hits (5 Stirges, 1 laser), 94 HP |
| `103000907-2026-09-10-1543.bin` | Kerning Subway B3 Area 2 (Shumi's Line 3 Construction Site) | 2026-09-10 15:43, AFL+IJON, 12 cores, 47 min (18 stalled before `--floor-y 900`), 4 chained checkpoints | 1617 bytes, SOLVED at tick 4928 (39.4 s) | goal = exit portal out00 to B3 Area 3 (3519 px corridor of 65 px segments under 16 lasers); 8 hits, 92 HP |
| `103000904-2026-09-10-1702.bin` | Kerning Subway B2 Area 2 (Shumi's Line 3 Construction Site) | 2026-09-10 17:02, AFL+IJON, 12 cores, 78 min, 9 chained checkpoints | 2294 bytes, SOLVED at tick 8564 (68.5 s) | goal = exit portal in00 to the B2 depot; down the left side, through the floor's cross00/cross01 warp, 1630 px up the right side under 258 lasers; 17 hits, 83 HP |
| `103000906-2026-09-10-1928.bin` | Kerning Subway B3 Area 1 (Shumi's Line 3 Construction Site) | 2026-09-10 19:28, AFL+IJON, 12 cores, 2 h 26 min, 13 chained checkpoints | 4103 bytes, SOLVED at tick 15156 (121 s) | goal = exit portal in00 to B3 Area 2 (2890 px climb); 24 hits (13 electric arcs, 6 lasers, 5 mob touches), 76 HP |
| `103000908-2026-09-10-2121.bin` | Kerning Subway B3 Area 3 (Shumi's Line 3 Construction Site) | 2026-09-10 21:21, AFL+IJON, 12 cores, 1 h 51 min, 15 chained checkpoints | 3077 bytes, SOLVED at tick 11716 (93.7 s) | goal = exit portal in00 to the B3 depot (3180 px climb); the hidden h001/h002 warp lifts the run out of the spawn pit; 24 laser hits, 76 HP |
| `103000903-2026-09-10-2157.bin` | Kerning Subway B2 Area 1 (Shumi's Line 3 Construction Site) | 2026-09-10 21:57, AFL+IJON, 12 cores, 35 min, 4 chained checkpoints | 1667 bytes, SOLVED at tick 5696 (45.6 s) | goal = exit portal in00 to B2 Area 2 (2590 px climb) through the go00x door puzzle; 12 laser hits, 88 HP |
| `103000900-2026-09-10-2352.bin` | Kerning Subway B1 Area 1 (Shumi's Line 3 Construction Site) | 2026-09-10 23:52, AFL+IJON, 12 cores, 1 h 34 min with the route feedback (20 min stalled without), 13 chained checkpoints | 2279 bytes, SOLVED at tick 8460 (67.7 s) | goal = exit portal end00 to B1 Area 2, 3850 px to the right through a five-storey grid of 65 px boxes; 12 hits, 88 HP |
| `109040003-2026-09-11-0329.bin` | MapleStory Physical Fitness Challenge, Level 3 | 2026-09-11 03:29, AFL+IJON, 12 cores, 2 h 17 min, 19 chained checkpoints | 4385 bytes, SOLVED at tick 15984 (128 s) | goal = exit portal in00 to Level 4; 174 hops of 70 px platforms in a full-width zig-zag; 6 thorn hits, 94 HP |
| `109040004-2026-09-11-0447.bin` | MapleStory Physical Fitness Challenge, Level 4 | 2026-09-11 04:47, AFL+IJON, 12 cores, 1 h 17 min, 10 chained checkpoints | 2453 bytes, SOLVED at tick 8496 (68 s) | goal = exit portal in00 to the finish map (2075 px climb); 10 hits (5 mobs, 2 moving animals, 3 thorns), 90 HP |
| `910020200-2026-09-11-0549.bin` | Playground of Lupin Leading to Jump Stand (The Lost Snipe event) | 2026-09-11 05:49, AFL+IJON, 12 cores, 36 min, 5 chained checkpoints | 1899 bytes, SOLVED at tick 7594 (60.8 s) | goal = the Jump Stand reactor on the top platform (xy:-181,-4195), start at portal st00 (3000 px of ladders); 4 moving-animal hits, 96 HP |

## Minimized solutions

`<name>-min.bin` next to a solution is the same run with every byte removed
that was not needed to reach the goal (`fuzz/viz/tmin.py`, a parallel
delete-and-replay minimizer; `fuzz/run.sh tmin` does the same with afl-tmin
in about six times the wall clock). The original is always kept: it is what
the fuzzer found, the minimized one is the shortest replay we know.

| Original | Minimized | Ticks before -> after |
|---|---|---|
| `105040310-2026-09-09-1606.bin` (3119 B) | `105040310-2026-09-09-1606-min.bin` (2904 B, afl-tmin) | 12184 -> 11612 |
| `280020000-2026-09-08.bin` (5092 B) | `280020000-2026-09-08-min.bin` (5043 B) | 20352 -> 20168 |
| `280020001-2026-09-09-0301.bin` (4484 B) | `280020001-2026-09-09-0301-min.bin` (4323 B) | 17930 -> 17356 |
| `105040311-2026-09-09-1746.bin` (3340 B) | `105040311-2026-09-09-1746-min.bin` (3028 B) | 12800 -> 12160 |
| `220000006-2026-09-10-0155.bin` (2661 B) | `220000006-2026-09-10-0155-min.bin` (2082 B) | 9257 -> 8388 |
| `100000202-2026-09-09-2353.bin` (4529 B) | `100000202-2026-09-09-2353-min.bin` (3256 B) | 17244 -> 13082 |
| `105040312-2026-09-10-0301.bin` (3653 B) | `105040312-2026-09-10-0301-min.bin` (3479 B) | 14360 -> 13912 |
| `101000100-2026-09-10-0722.bin` (4605 B) | `101000100-2026-09-10-0722-min.bin` (3888 B) | 17328 -> 15548 |
| `103000901-2026-09-10-1455.bin` (1159 B) | `103000901-2026-09-10-1455-min.bin` (711 B) | 3216 -> 2840 |
| `103000907-2026-09-10-1543.bin` (1617 B) | `103000907-2026-09-10-1543-min.bin` (1225 B) | 4928 -> 4896 |
| `103000900-2026-09-10-2352.bin` (2279 B) | `103000900-2026-09-10-2352-min.bin` (2005 B, trace-guided tmin) | 8460 -> 8016 |
| `109040003-2026-09-11-0329.bin` (4385 B) | `109040003-2026-09-11-0329-min.bin` (trace-guided tmin) | 15984 -> 15004 |
| `109040004-2026-09-11-0447.bin` (2453 B) | `109040004-2026-09-11-0447-min.bin` (trace-guided tmin) | 8496 -> 7560 |
| `910020200-2026-09-11-0549.bin` (1899 B) | `910020200-2026-09-11-0549-min.bin` (trace-guided tmin) | 7594 -> 6385 |
| `103000906-2026-09-10-1928.bin` (4103 B) | `103000906-2026-09-10-1928-min.bin` (trace-guided tmin) | 15156 -> 14280 |
| `103000908-2026-09-10-2121.bin` (3077 B) | `103000908-2026-09-10-2121-min.bin` (trace-guided tmin) | 11716 -> 11216 |

## Trace-guided minimizer (`-min2.bin`, pages `index<N>-min-new.html`)

The second version of `fuzz/viz/tmin.py` reads the replay trace and deletes
whole loops (the stretch between two moments the player stands at the same
spot: jumps in place, hops up and back, wandering, waiting), deletes flat
jumps as a unit, clears jump bits, and splices a straight walk between two
grounded moments on the same height, before the old byte-block deletions.
Both minimized copies are kept next to the original.

| Original | First minimizer | Trace-guided | Ticks original -> first -> trace-guided |
|---|---|---|---|
| `100000202-2026-09-09-2353.bin` (4529 B) | `-min.bin` (3256 B) | `-min2.bin` (1709 B) | 17244 -> 13082 -> 6903 |
| `101000100-2026-09-10-0722.bin` (4605 B) | `-min.bin` (3888 B) | `-min2.bin` (2609 B) | 17328 -> 15548 -> 10432 |
| `103000907-2026-09-10-1543.bin` (1617 B) | `-min.bin` (1225 B) | `-min2.bin` (975 B) | 4928 -> 4896 -> 3896 |
| `105040310-2026-09-09-1606.bin` (3119 B) | `-min.bin` (2904 B) | `-min2.bin` (2879 B) | 12184 -> 11612 -> 11512 (moving spiked balls fix the timing) |
| `105040311-2026-09-09-1746.bin` (3340 B) | `-min.bin` (3028 B) | `-min2.bin` (2985 B) | 12800 -> 12160 -> 11984 |
| `105040312-2026-09-10-0301.bin` (3653 B) | `-min.bin` (3479 B) | `-min2.bin` (3479 B) | 14360 -> 13912 -> 13912 (javelins fix the timing) |
| `220000006-2026-09-10-0155.bin` (2661 B) | `-min.bin` (2082 B) | `-min2.bin` (1693 B) | 9257 -> 8388 -> 6841 |
| `280020001-2026-09-09-0301.bin` (4484 B) | `-min.bin` (4323 B) | `-min2.bin` (4157 B) | 17930 -> 17356 -> 16700 |
| `103000901-2026-09-10-1455.bin` (1159 B) | `-min.bin` (711 B) | `-min2.bin` (736 B) | 3216 -> 2840 -> 2940 (the first minimizer's copy is the shorter one here) |
| `103000903-2026-09-10-2157.bin` (1667 B) | `-min.bin` (trace-guided too) | `-min2.bin` (1221 B) | 5696 -> 4880 |
| `103000904-2026-09-10-1702.bin` (2294 B) | `-min.bin` (trace-guided too) | `-min2.bin` (1941 B) | 8564 -> 7768 -> 7760 |
| `280020000-2026-09-08.bin` (5092 B) | `-min.bin` (5043 B) | `-min2.bin` (5042 B) | 20352 -> 20168 -> 20164 (falling rocks fix the timing) |
| `103000906-2026-09-10-1928.bin` (4103 B) | `-min.bin` (3571 B, trace-guided too) | `-min2.bin` (3540 B) | 15156 -> 14280 -> 14156 |
| `103000908-2026-09-10-2121.bin` (3077 B) | `-min.bin` (2805 B, trace-guided too) | `-min2.bin` (2805 B) | 11716 -> 11216 -> 11216 |

## Third pass (`-min3.bin`): hop templates, starting from the shortest copy

Same minimizer plus "hop templates" (the stretch between two landings becomes
walk n, jump with the direction held, hold k), started from the shorter of the
two earlier copies. The `-min-new` pages take the newest generation.

| Map | Original | Best before | `-min3.bin` |
|---|---|---|---|
| Henesys pet park | 17244 | 6903 | 6159 |
| Forest of Patience 1 | 17328 | 10432 | 9928 |
| Subway B1 Area 1 | 8460 | 8016 | 8016 |
| Subway B1 Area 2 | 3216 | 2840 | 2596 |
| Ludibrium Pet Walkway | 9257 | 6841 | 6622 |
| Zakum 1 / 2, Deep Forest 1 / 2 / 3, Subway B2 Area 1 / 2, B3 Area 1 / 2 / 3 | | | unchanged: at the minimizer's floor |
| Physical Fitness 3 / 4 (run after the batch) | 15984 / 8496 | 15004 / 7560 | 15004 / 7560 |

## Speedrun fuzzing (`-speed.bin`)

`fuzz/speedrun.sh MAP MINUTES GOAL` fuzzes a solved map with the harness in
`--speed` mode: the IJON value of a hop level is how early the run first
stands on it, solving is a normal exit recorded in slot 500, and every
solution copy is a seed. The fastest solving input is minimized and kept as
`-speed.bin` when it beats the best copy. One pass over all maps on 12 cores
(30 to 8 minutes each, 2026-09-11 07:37 to 14:45):

| Map | Best before | `-speed.bin` |
|---|---|---|
| Zakum 2 | 16700 | 16684 |
| Ludibrium Pet Walkway | 6622 | 6515 |
| Henesys pet park | 6159 | 6148 |
| Playground of Lupin | 6385 | 6362 |
| Zakum 1, Deep Forest 1-3, Forest of Patience 1, all subway courses, Physical Fitness 3-4 | | no faster solution found: mutations of a long input rarely stay solving and get faster; these are at the minimizer's floor |
