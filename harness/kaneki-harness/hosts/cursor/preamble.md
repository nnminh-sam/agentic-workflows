## Host Tool Binding (Cursor)

Translate the canonical playbook intents into Cursor capabilities as follows:

- **`DRIVER_CALL`**: Run `sh .sdlc/bin/sdlc ...` via Cursor's terminal tool.
- **`DISPATCH_UNIT`**: Because Cursor runs sub-tasks on the active session model (`inherit-only`), either:
  - Spawn a clean Cursor Agent/sub-task (or configure `execution.dispatchMode == "subprocess"` in `.sdlc/host.local.json` to use `sdlc dispatch`), passing `"Read the packet at <path> (sha256 <packetDigest>) and execute it."`
  - Record the unit via `sdlc record ... --model <active-model>` (`enforcement: "inherit-ok"` logs any tier/model difference transparently in `STATUS.md`).
- **`RESUME_UNIT`**: Build a fresh round-2 packet containing `findingsToFix` and dispatch a fresh turn/sub-task.
- **`ASK_USER`**: Present concise numbered options in chat (recommended first, no raw paths in labels) and wait for the user's reply before closing `--actor user` gates or writing the profile.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use Cursor's codebase/grep search and line-range file read tools instead of loading full large files; redirect long test/lint logs into `<logsDir>/<name>.log`.
