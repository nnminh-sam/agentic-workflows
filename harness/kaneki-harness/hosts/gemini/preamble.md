## Host Tool Binding (Gemini CLI)

Translate the canonical playbook intents into Gemini CLI tools as follows:

- **`DRIVER_CALL`**: Run `sh .sdlc/bin/sdlc ...` via your shell tool.
- **`DISPATCH_UNIT`**: When native subagents are unavailable for per-unit model binding, run `sh .sdlc/bin/sdlc dispatch --ticket <T> --node <N> --unit <U>` via shell so a fresh `gemini -m <execution.model>` subprocess runs the packet and records the result.
- **`RESUME_UNIT`**: Build a fresh round-2 packet with `findingsToFix` and re-run `sdlc dispatch` (or send to an active subagent if your host session has one).
- **`ASK_USER`**: Print numbered choices directly in chat with the recommended choice listed first and marked `(Recommended)`, then stop your turn and wait for the user's answer before closing user gates or committing.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use your grep/search and ranged file-read tools (or `rg -n` / `sed -n`) rather than dumping whole files; redirect check commands into `<logsDir>/<name>.log`.
