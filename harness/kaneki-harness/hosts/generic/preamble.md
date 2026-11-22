## Host Tool Binding (Generic / Headless Agent)

Translate the canonical playbook intents into standard shell and file tools as follows:

- **`DRIVER_CALL`**: Execute `sh .sdlc/bin/sdlc ...` (or `python3 <harness-home>/skills/kaneki-flow/scripts/sdlc.py ...`) directly.
- **`DISPATCH_UNIT`**: Run `sh .sdlc/bin/sdlc dispatch --ticket <T> --node <N> --unit <U>` (requires `runner` or `commandTemplate` configured in `.sdlc/host.local.json`), or invoke an isolated session of your host with `"Read the packet at <path> (sha256 <packetDigest>) and execute it."`.
- **`RESUME_UNIT`**: Build a fresh round-2 packet containing `findingsToFix` and re-dispatch cleanly.
- **`ASK_USER`**: For interactive chat hosts, present numbered options with `(Recommended)` on option 1 and pause for input. For headless/desktop orchestrators, inspect `sdlc gate-request` or write a pending prompt payload and pause cleanly until resumed with the user's answer.
- **`INSPECT_CODE` & `RUN_CHECK`**: Locate lines via `rg -n`, read targeted slices via `sed -n '<start>,<end>p'` or ranged reads, and write check outputs to `<logsDir>/<name>.log`.
