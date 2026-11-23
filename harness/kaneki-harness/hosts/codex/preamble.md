## Host Tool Binding (OpenAI Codex CLI)

Translate the canonical playbook intents into Codex CLI actions as follows:

- **Driver command (`$SDLC`)**: your own launcher, which tells the driver the work is done by Codex:
  - Windows (PowerShell or cmd): `.sdlc\bin\sdlc-codex.cmd ...`
  - macOS / Linux: `sh .sdlc/bin/sdlc-codex ...`
- **`DRIVER_CALL`**: Run `$SDLC ...` (the command above) via the shell execution tool.
- **`DISPATCH_UNIT`**: Execute `$SDLC dispatch --ticket <T> --node <N> --unit <U>` (or include `--facts <f>`) via shell. `sdlc dispatch` builds the packet, spawns a fresh `codex exec` subprocess with the resolved `model` and `reasoning_effort`, verifies the result file, and calls `sdlc record` automatically. Dispatch independent units in parallel if your host supports multi-agent or parallel shell calls.
- **`RESUME_UNIT`**: Always build a fresh round-2 packet (it already contains `findingsToFix` and live-only inputs) and dispatch via `sdlc dispatch`.
- **`ASK_USER`**: Present concise numbered options in your response (mark the recommended first choice with `(Recommended)`), summarize evidence briefly without pasting file paths into option labels, and end your turn to wait for the user's reply before closing `--actor user` gates or writing the profile.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use `rg -n` and `sed -n '<start>,<end>p'` (or file reader tools) for targeted reads, and redirect check output with `<cmd> > <logsDir>/<name>.log 2>&1`.
