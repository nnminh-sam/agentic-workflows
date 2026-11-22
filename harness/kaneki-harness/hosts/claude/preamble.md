## Host Tool Binding (Claude Code)

Translate the canonical playbook intents into Claude Code tools as follows:

- **`DRIVER_CALL`**: Run `sh .sdlc/bin/sdlc ...` (or `sh "$CLAUDE_SKILL_DIR/scripts/sdlc" ...` when present) via `Bash`.
- **`DISPATCH_UNIT`**: Read `execution` and `agentType` from `sdlc plan` / `sdlc packet`:
  - When `execution.dispatchMode == "native"`: call `Agent` with `subagent_type=<agentType>`, `model=<execution.model>`, and `prompt="Read the packet at <path> (sha256 <packetDigest>) and execute it."`. Issue independent parallel `Agent` calls in one message.
  - When `execution.dispatchMode == "subprocess"`: run `sh .sdlc/bin/sdlc dispatch --ticket <T> --node <N> --unit <U>` via `Bash`.
- **`RESUME_UNIT`**: On reviewer `FAIL` (`artifact`-class), build the round-2 packet with `findingsToFix` and call `Agent(resume=<agentId>, ...)` when the prior agent ID is available (or launch a fresh `Agent` otherwise).
- **`ASK_USER`**: Use `AskUserQuestion` (at most 4 questions per call, and at most 4 options per question; paginate across calls if `CAND.questions` supplies more options). Place the recommended option first and include brief rationale in `description`.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use `Grep` (with line numbers), `Read` (`offset` / `limit`), and `Bash("<cmd> > <logsDir>/<name>.log 2>&1")`.
