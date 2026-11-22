## Host Tool Binding (OpenCode)

Translate the canonical playbook intents into OpenCode tools as follows:

- **`DRIVER_CALL`**: Run `sh .sdlc/bin/sdlc ...` via your shell/bash tool.
- **`DISPATCH_UNIT`**: If `execution.dispatchMode == "subprocess"`, run `sh .sdlc/bin/sdlc dispatch --ticket <T> --node <N> --unit <U>`. Otherwise invoke OpenCode's native subagent tool with an isolated context (`Prompt="Read the packet at <path> (sha256 <packetDigest>) and execute it."`) and the model bound in `.sdlc/host.local.json`.
- **`RESUME_UNIT`**: Resume the open subagent session if supported, or launch a fresh subagent/dispatch with the round-2 packet (`findingsToFix` included).
- **`ASK_USER`**: Print concise numbered options in chat with `(Recommended)` on the first option and wait for the user's answer before closing user gates or writing `sdlc.profile.json`.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use grep/search and ranged file reading (`offset`/`limit`), and redirect test/lint output into `<logsDir>/<name>.log`.
