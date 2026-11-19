# P6 · Session supervision and run policy (M3)

**Milestone:** M3. **Output:** `parts/P6-session-supervision.plan.md`

## Problem
Today the node-by-node loop is run by hand from a chat session that spawns Zed panes by keystroke injection (which
once typed into Microsoft Teams). D5/D6: the app spawns `claude` sessions as child processes, supervises them, and
runs a ticket node by node under a policy, stopping at user gates. M3 done-when: "TODO-n runs node by node to
`deliverable-ready` without the chat session". This is the riskiest part; open questions Q1–Q3 block it.

## Plan must cover
1. **How a node is run today**: read `kaneki-harness/skills/kaneki-flow/SKILL.md` (the `/kaneki-flow` driver),
   the session brief, and `zed-spawn-session/` (repo root) to describe the current loop precisely.
2. **Spawning**: the `claude` CLI options that matter (interactive vs. `-p`/print mode, `--output-format
   stream-json`, `--append-system-prompt-file`, `-n`, permission modes, `--allowedTools`); check what the local
   `claude --help` reports (read-only). Interactive terminal vs. headless: can the user still watch and type into
   a session (embedded terminal via ConPTY/xterm.js, or "open in Zed" alongside)? Recommend one with trade-offs.
3. **Q1 waiting on a permission prompt**: options (Claude Code hooks: `Notification`, `PreToolUse`, `Stop`; the
   existing notify-hook at `notify-hook/`; stream-json events; process state). Recommend a mechanism and the
   hook → Host channel (file drop under the ticket dir, named pipe — no open port per D8).
4. **Q2 task in / report out**: prompt via args/file, report via a file the session writes or the harness's own
   `decisions.jsonl` events (prefer the harness trail). **Q3 run policy storage** (ticket dir vs app config) with
   a schema.
5. **Supervisor state machine**: idle → spawning → running node → waiting-permission → node-done → (gate needs
   user → stop) | (auto-continue → next node) | failed | stopped. Stop rules from D6 (never ship/commit/push,
   locked on; stop at `deliverable-ready`, on reject/loop-back). Crash/orphan handling, app restart re-attach, PID
   tracking, Job Objects so children die with the app.
6. **Sessions dock UI** needs and events (`session-started`, `session-state`, `session-exited`).
7. **Safety**: what the guard (`skills/kaneki-flow/scripts/guard_git.py`) already blocks; what the app must add.

## Not in scope
Cost computation (P7), the gate dialog (P5) — P6 only hands off to it.
