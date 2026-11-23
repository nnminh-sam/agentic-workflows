## Host Tool Binding (Antigravity)

Translate the canonical playbook intents into Antigravity tools as follows:

- **Driver command (`$SDLC`)**: your own launcher, which tells the driver the work is done by Antigravity:
  - Windows (PowerShell or cmd): `.sdlc\bin\sdlc-antigravity.cmd ...`
  - macOS / Linux: `sh .sdlc/bin/sdlc-antigravity ...`
- **`DRIVER_CALL`**: Execute `$SDLC ...` (the command above) via `run_command`.
- **`DISPATCH_UNIT`**: Read the `execution` block from `sdlc plan` / `sdlc packet`:
  - When `execution.dispatchMode == "native"`: call `invoke_subagent` with `TypeName="self"`, `Model=<execution.model>` (one of `inherit`, `flash_lite`, `flash`, `pro`), `Role="<node> <unit>"`, and `Prompt="Read the packet at <path> (sha256 <packetDigest>) and execute it."` (use `swarm` only when parallel code writers need `workspace="share"`). Record each returned `conversationId`.
  - Units run **only** on your own subagents (`invoke_subagent`); `sdlc dispatch` refuses host `antigravity`. Never run `$SDLC dispatch` (or any other command that starts an agent as a subprocess), whatever `execution.dispatchMode`, `runner` or a node override says. If `execution.dispatchMode` is not `native`, stop and tell the user to fix the binding in `.sdlc/host.local.json` or to run that node with another agent.
  - Parallel calls to `invoke_subagent` may share a turn when packet outputs are disjoint; wait for subagent completion notifications before recording.
- **`RESUME_UNIT`**: On reviewer `FAIL` (`artifact`-class), build the round-2 packet with `findingsToFix`. If the prior unit's `conversationId` is still idle, send the new packet path via `send_message(Recipient=<conversationId>, Message="Read the round-2 packet at <path> (sha256 <packetDigest>) and fix every item in Findings to fix.")`; otherwise launch a fresh `invoke_subagent`.
- **`ASK_USER`**:
  - Use `ask_question` for multi-option choices: pass plain string options (no object wrapper, no manual numbering or `Other` option), and **prefix the recommended first option with `"(Recommended) "`** (if more than 5 options exist in `CAND.questions`, paginate across consecutive questions/calls).
  - For simple Yes/No or approval confirmations (e.g. scope/plan Proceed, G6 shared-state mutations, ship confirmation), ask concisely in plain chat text and end your turn to wait for the user's answer.
- **`INSPECT_CODE` & `RUN_CHECK`**: Use `run_command("rg -n ...")` to locate line numbers, `read_file(Path, StartLine, EndLine)` for targeted ranges, and `run_command("<cmd> > <logsDir>/<name>.log 2>&1")` for builds and tests.
