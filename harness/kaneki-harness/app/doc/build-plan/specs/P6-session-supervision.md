# P6 · Session supervision and run policy (M3)

**Milestone:** M3. **Output:** `parts/P6-session-supervision.plan.md`

## Problem
Today the node-by-node loop can be driven via host workflows or `sdlc dispatch`, whereas older manual scripts spawned
editor panes by keystroke injection (which once typed into Microsoft Teams). D5/D6: the app supervises sessions through
an agent-agnostic Go `AgentBackend` interface (supporting Antigravity, Claude Code, Codex CLI, Gemini CLI, OpenCode,
or direct `sdlc dispatch`), runs a ticket node by node under a policy, and pauses cleanly at `ASK_USER` / gate boundaries.
M3 done-when: "TODO-n runs node by node to `deliverable-ready` without a manual chat loop". This is the riskiest part;
open questions Q1–Q3 block it.

## Plan must cover
1. **Go `AgentBackend` interface**: define the contract (`Start(ctx, LaunchSpec)`, `WatchEvents()`, `Stop()`,
   `UsageLogSource()`) backed by host adapters selected from `.sdlc/host.local.json` and `sdlc plan` (`execution` block),
   reading `playbooks/flow.md` and `hosts/<host>/preamble.md`.
2. **Spawning & interactive visibility**: CLI options per backend (`sdlc dispatch` vs `claude -p --output-format stream-json`
   vs `codex exec` vs `gemini -p`) plus PTY/xterm.js vs "open in configured editor" trade-offs.
3. **Q1 headless `ASK_USER` & permission pause/resume**: standardize on `<ticketDir>/pending-prompt.json` (plus
   `sdlc gate-request`) and optional backend hooks (`Notification`/`PreToolUse` on Claude, `BeforeTool` on Gemini,
   process/stream status) feeding the Host event watcher over file drop / IPC — no open port per D8.
4. **Q2 task in / report out**: prompt via packet path/digest or args, report via `decisions.jsonl` + result files
   (prefer the harness trail). **Q3 run policy storage** (ticket dir vs app config) with a schema.
5. **Supervisor state machine**: idle → spawning → running node → waiting-user (`pending-prompt.json` / gate) →
   waiting-permission → node-done → (gate needs user → stop) | (auto-continue → next node) | failed | stopped.
   Stop rules from D6 (never ship/commit/push, locked on; stop at `deliverable-ready`, on reject/loop-back). Crash/orphan
   handling, app restart re-attach, PID tracking, Job Objects / process groups so children die with the app.
6. **Sessions dock UI** needs and events (`session-started`, `session-state`, `session-exited`, `prompt-pending`).
7. **Safety**: what `.sdlc/hooks` + `guard_git.py` + `sdlc git` already block; what the app must enforce per backend.

## Not in scope
Cost computation (P7), the gate dialog (P5) — P6 only hands off to it.
