# P3 · Host core (the "backend")

**Milestones:** M1 (read side) and the runner used by M2/M3. **Output:** `parts/P3-host-core.plan.md`

## Problem
The Host is the native process that owns the window. It must stay thin: find the workspace and harness, run
`sdlc.py` commands safely, watch ticket files and push events to the UI, and report errors cleanly. Everything
else (state rules, graph logic) stays in the Python harness.

## Plan must cover
1. **Workspace & harness discovery**: how the app finds `sdlc.profile.json` (setting, recent list, `$SDLC_PROFILE`),
   the harness home (`$SDLC_HOME`, `.harness-home`, see `skills/kaneki-flow/scripts/sdlc.py`), and the Python
   interpreter (`py -3`, `python`, setting); validation and the error shown when any is missing.
2. **Command runner**: run `python sdlc.py <args>` as a child process with an **allowlist** of subcommands and
   argument validation (no shell string building), cwd = workspace, env, timeout, cancellation, stdout JSON
   parsing, stderr capture, exit-code mapping to `AppError`; serialise writes per ticket (the harness has its own
   ticket lock: say how the Host behaves when the lock is held). Windows specifics: no console window flashing
   (`CREATE_NO_WINDOW`), UTF-8 (Vietnamese text in `--note`), quoting.
3. **File watcher**: watch `.sdlc/tickets/*/decisions.jsonl` and `state.json` (D4); debounce; read only appended
   lines of `decisions.jsonl` (track offsets); handle new tickets, file replace/rename (atomic writes), watcher
   overflow → full rescan; map to the events defined by P1 (`ticket-changed`, `decision-added`, …).
4. **Caching**: in-memory ticket summaries refreshed on events, so the UI never waits on a 0.5 s Python call for
   a re-render; when to call `sdlc tickets` vs. read files.
5. **IPC layer**: how invoke handlers and events are registered in Wails (Go), payload
   validation against P1 schemas, error envelope.
6. **Cross-cutting**: logging (where, rotation), single instance, app config file location (`%APPDATA%`),
   security (D8: no listening port; command allowlist; never pass untrusted text to a shell).
7. Module layout in the Host source (Go packages).

## Not in scope
Session spawning (P6), cost log parsing (P7), UI (P4), the gate dialog flow (P5) beyond providing `run_command`.
