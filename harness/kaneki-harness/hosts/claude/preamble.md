## Host Tool Binding (Claude Code)

Translate the canonical playbook intents into Claude Code tools as follows:

- **Driver command (`$SDLC`)**: your own launcher, which tells the driver the work is done by Claude Code:
  - Windows (`PowerShell` tool, or cmd): `.sdlc\bin\sdlc-claude.cmd ...`
  - macOS / Linux (`Bash` tool, also Git Bash on Windows): `sh .sdlc/bin/sdlc-claude ...`
  - No `.sdlc/bin` in the project (user-level install in `~/.claude`): `sh "$CLAUDE_SKILL_DIR/scripts/sdlc" ...`.
- **`DRIVER_CALL`**: Run `$SDLC ...` (the command above) via `PowerShell` on Windows or `Bash` elsewhere.
- **`DISPATCH_UNIT`**: Read `execution` and `agentType` from `sdlc plan` / `sdlc packet`:
  - When `execution.dispatchMode == "native"`: call `Agent` with `subagent_type=<agentType>`, `model=<execution.model>`, and `prompt="Read the packet at <path> (sha256 <packetDigest>) and execute it."`. Issue independent parallel `Agent` calls in one message.
  - When `execution.dispatchMode == "subprocess"`: run `$SDLC dispatch --ticket <T> --node <N> --unit <U>`.
- **`RESUME_UNIT`**: On reviewer `FAIL` (`artifact`-class), build the round-2 packet with `findingsToFix` and call `Agent(resume=<agentId>, ...)` when the prior agent ID is available (or launch a fresh `Agent` otherwise).
- **`ASK_USER`**: Use `AskUserQuestion` (at most 4 questions per call, and at most 4 options per question; paginate across calls if `CAND.questions` supplies more options). Place the recommended option first and include brief rationale in `description`.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use `Grep` (with line numbers), `Read` (`offset` / `limit`), and `Bash("<cmd> > <logsDir>/<name>.log 2>&1")`.
