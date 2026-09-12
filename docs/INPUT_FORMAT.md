# Input format

A solution is a raw byte file. Each byte is a key mask held for four physics
ticks of the client (the client ticks at 125 Hz, so one byte is 32 ms):

| Bit | Key |
|---|---|
| 1 | LEFT |
| 2 | RIGHT |
| 4 | JUMP |
| 8 | UP (climb a rope, enter a portal) |
| 16 | DOWN |

Other bits are ignored. Key transitions are delivered to the client exactly
as key presses and releases; holding JUMP does not re-jump, a new jump needs
a release and a press.

After the last byte the harness and the browser keep simulating without keys
(the harness for 96 ticks, the browser until the character comes to rest), so
a jump started by the final byte lands.

The replay is deterministic from the map's spawn (or the portal given by
`OfflinePortal` / `--portal`): the same bytes produce the same positions in
the native harness (gcc or clang), in the IJON-instrumented build and in the
WebAssembly client. `SOLVED tick=N` in the harness output is the tick the
goal was reached; the browser reports N+1 because it checks after the frame.

Files:

* `<map>-<date>.bin`: the fuzzer's original solution;
* `<map>-<date>-min.bin`: first minimization;
* `<map>-<date>-min2.bin`, `-min3.bin`: later minimizer generations;
* `<map>-<date>-speed.bin`: speedrun fuzzing result.

`solutions/README.md` lists the tick counts of every copy.
