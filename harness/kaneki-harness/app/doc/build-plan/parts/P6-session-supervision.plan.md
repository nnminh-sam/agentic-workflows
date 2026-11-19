# P6 Session supervision and run policy (M3)

Status of evidence: **V** = verified in a file or in `claude --help` on this machine (2026-11-18); **A** = ASSUMPTION, to be
settled by spike P6-S0 before the full build. Paths are relative to `D:/workspace/HAISE/wp-agentic-workflows/agentic-workflows/`.
No `claude` session was started and no state-changing `sdlc` command was run.

## 1. Goal and Done when

**Goal.** The Host spawns `claude` sessions as supervised child processes and runs one ticket node by node under a per-ticket
run policy, stopping at user gates, so the hand-run chat loop is no longer needed. The app never ships, commits, pushes or
deletes a branch.

**Done when**
- From the Run button, TODO-n (a fixture or a scratch ticket) runs to `deliverable-ready` with no chat session in the loop;
  each node is a fresh session by default and each close is visible as a `gate` event in `decisions.jsonl`.
- The user can watch and type into any running session from the app.
- A permission prompt or `AskUserQuestion` shows "waiting" within 2 s and a toast fires.
- Killing the app kills every child (Job Object); restarting the app re-attaches to or cleans up each run.
- A node session that tries `git commit/push`, `sdlc ship*` or `git branch -d/-D` is denied by a layer the session cannot disable.

## 2. Scope

**In:** run-policy schema and storage; supervisor state machine (Host); session spawn (PTY), hook channel, node-done
detection, crash/orphan handling; the Sessions dock contract; safety layer; spike P6-S0.
**Out:** cost (P7), the gate dialog (P5; P6 emits `gate-needed` and consumes `gate-closed`), file watching (P1 owns the watcher; P6
subscribes), the board/UI shell (P2-P4), the stack (D10, closed: Go/Wails).

## 3. Inputs and outputs

**Inputs from other parts** (names are proposals; coordinator to reconcile):
- P1 Host: `invoke("sdlc", {args, cwd})` returning the harness JSON line (exit 2 = fail-closed, `error` names gate); event
  `ticket-changed {ticket}` (state.json / decisions.jsonl changed).
- P2/P3 board JSON: `nextPending`, per-node `boundary` (`user|controller`), `executor` (`board.py:38,54`).
- P5: `invoke("gate-open", {ticket,node})` result `{decision: proceed|rerun|stop}`; P6 hands off, never closes a user gate itself.
- P7: session id <-> jsonl path mapping (P6 passes `--session-id <uuid>`, V help line `--session-id`).

**Outputs**
- IPC calls: `run-start {ticket, policy?}`, `run-stop {runId}`, `run-resume {runId}`, `run-policy-get/set {ticket}`,
  `session-focus {sessionId}`, `session-write {sessionId, data}` (user keystrokes into the PTY the app owns),
  `session-resize`.
- Events: `session-started {sessionId, ticket, node, pid, name}`, `session-state {sessionId, state, since, detail}`,
  `session-output {sessionId, bytes}` (PTY stream), `session-exited {sessionId, code, reason}`,
  `run-state {runId, state, node}`, `gate-needed {ticket, node, why}`, `toast {title, body}`.
- Files (Host-owned, under `%LOCALAPPDATA%/kaneki-app/`): `policy/<ticket>.json`, `runs/<runId>/run.json`,
  `runs/<runId>/events.jsonl` (hook drop), `runs/<runId>/brief.txt`, `runs/<runId>/system.txt`, `runs/<runId>/settings.json`.

## 4. Design

### 4.1 How a node runs today (V)
- Driver loop per `/kaneki-flow` (`harness/kaneki-harness/skills/kaneki-flow/SKILL.md:119-159`): orient via `status --compact`, `plan --card`,
  dispatch subagents from packets, `record`, then `gate --status approved`; `boundary: user` nodes ask Proceed/Re-run/Stop with
  `--actor user` (`SKILL.md:149-151`); user doubts use AskUserQuestion (`SKILL.md:145-147`); after `code-review` and
  `execute-and-evidence` it writes `HANDOFF.md` and suggests a fresh session (`SKILL.md:156-158`).
- Resume point is always `status` -> `nextPending` ("Never re-init to recover", `SKILL.md:123-124`), so a fresh session per node
  loses nothing: state is in files (`SKILL.md:20-22`).
- A session running the skill carries a `PreToolUse` guard for `Bash|PowerShell` (`SKILL.md:7-14`) that runs `guard_git.py`.
- Spawning today: `zed-spawn.ps1` pastes a command into a new Zed terminal by SendKeys; the launcher runs
  `claude -n <name> --append-system-prompt-file <sys> [-w <name>] [--permission-mode <m>] <task>`
  (`skills/zed-spawn-session/scripts/zed-spawn.ps1:93-101`); the system file tells the child to check in with the parent over
  `ListAgents`/`SendMessage` and report at the end (`zed-spawn.ps1:77-86`). Peer messages cannot approve permission prompts
  (`skills/zed-spawn-session/SKILL.md:61`). Foreground-window check exists because keys once landed in Teams (`SKILL.md:47`).
- The app replaces SendKeys with a PTY it owns, and replaces peer messaging with files (Q2).

### 4.2 Spawning: interactive in an embedded PTY (recommended) vs headless
`claude --help` (V) shows: `-n/--name`, `--session-id <uuid>`, `--append-system-prompt[-file]` (the `-file` form is used by
`zed-spawn.ps1:93` and named in `--bare` help text), `--permission-mode acceptEdits|auto|bypassPermissions|manual|dontAsk|plan`,
`--allowedTools`/`--disallowedTools`, `--settings <file-or-json>`, `--setting-sources`, `-w/--worktree`, `-r/--resume`,
`--bg`/`attach`/`logs`/`stop`, and print-only flags: `-p`, `--output-format stream-json`, `--input-format stream-json`,
`--include-hook-events`, `--permission-prompts host|none`, `--permission-prompt-tool`.

| | A. Interactive in embedded ConPTY + xterm.js (recommended) | B. Headless `-p --output-format stream-json` | C. "Open in Zed" tab |
|---|---|---|---|
| User can watch and type | yes, in-app (same UX as today, no foreign window) | no live typing; transcript view only | yes, but needs Zed keystrokes: banned |
| Permission prompts | shown in the terminal; app learns via hooks (4.3) | structured via `--permission-prompts host` (V flag exists; behaviour A) | as A |
| AskUserQuestion (user-boundary nodes) | works as today (A: unverified under PTY) | A: likely unsupported in `-p`; would break user nodes | as A |
| Node-done detection | harness trail + Stop hook | `result` event | as A |
| Cost | Go ConPTY lib + xterm.js (npm, available) | smallest | spawn without owning handle: loses supervision |
| Child lifetime | Job Object, PID known | Job Object, PID known | cannot guarantee (terminal owns it) |

Recommendation: **A as default; keep B behind a flag for later "fully unattended coding nodes"** once spike shows AskUserQuestion
behaviour. C stays a manual "reveal in Zed" for documents only (D9), never for sessions.
Stack notes (Wails/Go): `github.com/UserExistsError/conpty` for ConPTY on Windows (`creack/pty` is Unix-only; use it for the macOS build) and
`golang.org/x/sys/windows` for the Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`). No Go toolchain is installed today (context file), so the PTY layer cannot be
prototyped until P2 installs it; spike P6-S0 below is therefore split into a part that needs no toolchain and a part that does.

Spawn command (Host builds; `cwd` = ticket worktree or repo from `sdlc status`, A on which):
```
claude -n kaneki-<ticket>-<node> --session-id <uuid> --permission-mode <mode>
       --append-system-prompt-file runs/<id>/system.txt
       --settings runs/<id>/settings.json --disallowedTools <deny list, 4.7>
       "<one-line task: run /kaneki-flow <ticket> --node <node> ... then stop>"
```
(`--setting-sources` left default so project guard hooks still load; A: that `--settings` hooks merge with, not replace, skill and
user hooks. Spike checks.)

### 4.3 Q1 waiting-on-permission: hooks write to a file drop (recommended)
Evidence: the installed notify-hook is a `Notification` hook whose stdin JSON carries `message`, `notification_type`
(`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `auth_success`), `cwd`, `session_id`, `transcript_path`
(`notify-hook/notify.py:5-6`, `notify-hook/README.md:26`) (V from repo docs). Other event names (`Stop`, `SessionStart`,
`SessionEnd`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit`) are **A**: the SKILL already uses `PreToolUse` with a `matcher`
(`SKILL.md:7-9`, V); no docs.claude.com check was possible from here (no web fetch used), so the others need spike confirmation.

Mechanism:
1. Per-run `settings.json` registers one command hook, `kaneki-hook.py <runId>` (stdlib, ~40 lines, ships with the app), for
   `Notification`, `Stop`, `SessionStart`, `SessionEnd`. It appends `{at, runId, event, notification_type, message, session_id}`
   as one line to `runs/<id>/events.jsonl` (single `write` in append mode; Windows append of <4 KB lines is effectively atomic,
   A) and always exits 0 (like `notify.py`, never blocks Claude).
2. Host watches `runs/<id>/events.jsonl` (P1 watcher, no polling per D4) and maps: `Notification/permission_prompt` ->
   `waiting-permission`; `elicitation_dialog` -> `waiting-question`; `idle_prompt` -> `idle`; `Stop` -> turn finished (candidate
   node-done); `SessionEnd`/process exit -> exited.
3. Leave the global notify-hook alone (it keeps toasting); the app suppresses its own duplicate toast when the global hook is
   installed (check `~/.claude/settings.json` for `notify-hook`). ASSUMPTION: both hooks fire; order irrelevant.
4. Fallback if `Notification` does not fire reliably under a PTY: heuristic on PTY output silence plus last `PreToolUse`
   without matching `PostToolUse` (needs those hooks added; A). Show as "possibly waiting" so it is never trusted for control.
Channel choice: file drop, not a named pipe (works with a Python hook, survives Host restart so re-attach can replay the file,
no port per D8). The hook dir is outside the ticket dir so the harness integrity check never sees it.

### 4.4 Q2 task in, report out (recommended: argument + files; report = harness trail)
- **In:** brief written to `runs/<id>/brief.txt` and passed as the positional prompt (single line pointing to the file, to avoid
  the quoting/newline flattening that `skills/zed-spawn-session/SKILL.md:41` warns about). Content: `/kaneki-flow <ticket> --node <n>`
  plus the stop rules from 4.6 and "do not message other sessions". The system file replaces the zed protocol: no `ListAgents`,
  no check-in message.
- **Out:** no report file the session must write. Truth is the harness: node is done when `decisions.jsonl` gains a `gate` event
  for that node with status approved/needs-changes (event type exists, `contracts/decision-event.schema.json:18-30`, V), read
  via `sdlc status --ticket <id> --compact` after `ticket-changed`. The Stop hook only says "the session went quiet", which
  triggers the status check. `HANDOFF.md` written by the node (`SKILL.md:157`) is shown to the user as the narrative.
- Why not messaging: needs `crossSessionInbound` and a parent session (`skills/zed-spawn-session/SKILL.md:60-62`); the app is not a session.

### 4.5 Q3 run policy storage (recommended: app config, never in the ticket dir)
`policy/<ticket>.json` in app data (survives `sdlc init`, never trips ticket integrity, which covers `state.json` and
`decisions.jsonl`, `harness/kaneki-harness/harness/state.py:80-90` V). The harness stays unaware (D1: it is the only writer of ticket state).
Trade-off: policy is invisible to a CLI user; acceptable since the app is the only orchestrator.
```json
{ "schema": "kaneki-run-policy@1",
  "ticket": "TODO-1-2",
  "sessionMode": "per-node",            // "per-node" | "per-run"
  "autoContinue": true,                  // continue after a node passes
  "permissionMode": "acceptEdits",       // one of claude --permission-mode values; "bypassPermissions" rejected by Host
  "stopAt": ["deliverable-ready"],       // nodes to always stop at (always includes deliverable-ready)
  "stopOn": ["boundary-user","needs-changes","loop-back","gate-fired","session-failed"],
  "maxNodesPerRun": 20, "nodeTimeoutMin": 45, "idleWarnMin": 10,
  "model": null,                         // null = harness plan decides
  "locked": { "neverShip": true, "neverCommit": true, "neverPush": true, "neverDeleteBranch": true } }
```
`locked` is not read from the file: the Host hard-codes it and rejects any file that sets a value to false. Defaults are the D6
defaults. `--auto` from the SKILL (`SKILL.md:152`) is never passed by the app.

### 4.6 Supervisor state machine (Host, one per ticket; one active run per ticket)
```
idle -> spawning -> running(node) -> waiting-permission | waiting-question  (each returns to running on PostToolUse/UserPromptSubmit or output)
running -> node-done -> decide:
    boundary:user or fired gate or needs-changes/loop-back or at stopAt/nextPending==null -> stopped(gate-needed)  [emit gate-needed -> P5]
    else if autoContinue and under caps -> spawning(next node)
running -> failed (nonzero exit before gate event, spawn error, nodeTimeout) ; any -> stopped (user)
```
- Before each spawn: `sdlc status --ticket T --compact` (read-only); refuse if `.lock/owner.json` has a live pid and age <600 s
  (`state.py:156-168` V); read `nextPending`, `boundary`.
- Node-done = gate event for the node seen AND (Stop hook or process exit). If the session stays alive after the node, Host
  terminates the process tree (conversation persists on disk; `--resume` possible). Host, not the session, decides continue/stop.
- User gate: Host never runs `gate --actor user`; P5 does after the dialog. After `gate-closed {proceed}` Host resumes at next node.
- `needs-changes`: seen as the gate event status; stop per D6 and show reason (round count from driver).
- Failure: no gate event + exit -> `failed`, keep PTY scrollback, offer Re-run (new session, same node) or Stop. Never re-init.
- App restart: scan `runs/*/run.json` with `state != finished`; for each, check PID alive and start time matches (guard against
  PID reuse); alive -> re-attach read-only to `events.jsonl` (PTY cannot be re-attached; show "detached session", offer Stop);
  dead -> mark `failed(orphan)` and, if the node has no gate event, offer Re-run. Job Object `KILL_ON_JOB_CLOSE` makes "alive
  orphan after crash" the rare case (only if the Host is hard-killed before the Job closes; handle anyway).
- Stop: send Ctrl+C to the PTY, wait 3 s, then `TerminateJobObject`. Child's grandchildren (node, python) die with the job.

### 4.7 Safety (what exists, what the app adds)
Already blocked (V): `guard_git.py` refuses, for `Bash|PowerShell` tool calls, history writes, pushes, deleting work, identity changes
and plain remote access via `harness.gitpolicy.shell_refusals` (`kaneki-flow/scripts/guard_git.py:36-58`, `SKILL.md:104-118`); the
`sdlc git` wrapper refuses commit/push/`branch -D` etc. (`SKILL.md:109-112`); fail-closed on error (`guard_git.py:66-68`).
Gaps for an app-spawned session:
1. The guard does not mention `sdlc ship*`/`ship-request`; protection is the digest approval flow (`cli.py:850-865`), which a session
   could run itself. **App adds** `--disallowedTools` entries (A on exact pattern syntax): `Bash(*sdlc*ship*)`, `Bash(git commit*)`,
   `Bash(git push*)`, `Bash(git branch -d*)`, `Bash(git branch -D*)`, `Bash(git branch --delete*)`, `Bash(git push*--delete*)`, and the
   PowerShell equivalents. Because deny patterns are bypassable by shell tricks, add a second layer: a Host-injected `PreToolUse`
   hook (`kaneki-deny.py`, same shape as `guard_git.py`) that normalises the command and exits 2 on commit/push/ship/branch-delete,
   independent of the skill hook (the skill hook only exists while the skill is active, `SKILL.md:7-14`, and `$HOME/.claude` fallback
   may be missing). Spike verifies that it blocks.
2. Host itself never runs `ship*`, `git`, or `branch` commands; its `sdlc` allow-list (P1) excludes them. `invoke("sdlc")` takes a
   command enum, not free text.
3. Never `bypassPermissions` / `--dangerously-skip-permissions`; Host rejects them in policy. Default mode `acceptEdits`.
4. Treat ticket text as untrusted (`SKILL.md:24-25`): brief is built by the Host from fixed templates plus ticket id and node name
   validated against `^[A-Za-z0-9._-]+$`; no ticket text goes in args.
5. Path confinement: spawn `cwd` only inside the workspace profile root; `--add-dir` not used.
6. Do not run `claude` with `--bare` (it skips the hooks we rely on; V help text).

### 4.8 Sessions dock contract
Row per session: name, ticket, node, state chip (`spawning|running|waiting-permission|waiting-question|idle|done|failed|stopped`),
elapsed, PID, last event. Actions: focus (opens terminal pane bound to PTY), Stop, Re-run, open `HANDOFF.md` in Zed (D9). State
changes arrive as `session-state`; terminal bytes as `session-output` (coalesced, 16 ms). Toasts on `waiting-*`, `failed`,
`gate-needed`, `deliverable-ready`.

### 4.9 Module layout (names only; Go per D10)
`host/supervisor` (state machine, caps), `host/spawn` (PTY + Job Object + arg builder), `host/hooks` (writes settings.json,
copies hook scripts, tails events.jsonl), `host/policy` (schema, validation, locked fields), `hooks/kaneki-hook.py`,
`hooks/kaneki-deny.py`, `ui/sessions-dock` + `ui/terminal` (xterm.js, `@xterm/addon-fit`).

## 5. Tasks

| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P6-S0a | Spike, no toolchain: in a scratch dir with a throwaway settings file, capture which hook events fire and their JSON (Notification, Stop, SessionStart, SessionEnd, PreToolUse) using a logging hook; test `--settings` merge with skill hooks and `--disallowedTools` patterns for commit/push/ship. Needs one real `claude` run by the user (not by an agent) | M | none | Table of event -> payload -> observed, written to this plan's section 8 follow-up |
| P6-S0b | Spike, after P2 (Go installed): PTY + Job Object hello world (spawn `claude --version` then interactive `claude` in ConPTY, resize, Ctrl+C, kill tree on app exit) | M | P2-T03 | child dies when app is killed from Task Manager; typing works |
| P6-S0c | Spike: does `/kaneki-flow <fixture> --node X` run to a gate under `-p` and under PTY; does AskUserQuestion work in each; what ends the session | M | S0a | Written verdict; picks interactive vs headless for coding nodes |
| P6-T01 | Policy schema JSON file + validator + defaults + locked fields (Host) | S | none | Invalid/unsafe files rejected in unit tests |
| P6-T02 | Run-dir builder: brief, system.txt, settings.json, uuid, names | S | T01, S0a | Dry-run prints the exact command and files; no process started |
| P6-T03 | `kaneki-hook.py` + `kaneki-deny.py` with tests (stdlib; mirror `guard_git.py` structure) | M | S0a | Table-driven tests: deny list passes/blocks; hook appends valid JSON lines |
| P6-T04 | Spawn layer: PTY + Job Object + exit capture + Ctrl+C/terminate | L | S0b | Child tree dies with Host; exit code reported |
| P6-T05 | Events tailer: parse `events.jsonl` -> `session-state` | S | T03, P1 watcher | Fixture file produces expected state sequence |
| P6-T06 | Node-done detector: gate event via `status --compact` after `ticket-changed` | M | P1 | On a copied fixture ticket, detects node close and `nextPending` |
| P6-T07 | Supervisor state machine + caps + stop rules | L | T01,T04,T05,T06 | Simulated-session tests cover every arrow in 4.6 |
| P6-T08 | Re-attach/orphan scan on startup, PID+start-time check | M | T07 | Kill Host mid-run: restart shows correct state |
| P6-T09 | IPC + events surface (3) and Sessions dock wiring + xterm.js pane | L | T07, P2 | Dock shows live rows; typing reaches PTY |
| P6-T10 | Toasts and global notify-hook de-duplication | S | T05 | One toast per waiting event |
| P6-T11 | Hand-off to P5 (`gate-needed` / `gate-closed`) and resume | S | T07, P5 | User gate stops and resumes the run |
| P6-T12 | End-to-end manual run on a scratch ticket in a scratch repo copy to `deliverable-ready` | M | all | M3 done-when, no commit/push observed in `git log` and reflog |

## 6. Tests / verification
- Unit: policy validation (reject `bypassPermissions`, `locked` false, bad ticket id); arg builder (golden command lines);
  deny hook (commands: `git commit -m x`, `git push`, `git branch -D x`, `python sdlc.py ship ...`, `git -C x push`, PowerShell
  `& git push`; must block; `git status`, `sdlc status` must pass); state machine with a fake spawner and fake `status` JSON.
- Fixture: copy `D:/workspace/HAISE/testing/todo-nestjs-backend/.sdlc/tickets/TODO-1-2/` (read-only copy to a scratch dir) to test node-done
  detection from recorded `decisions.jsonl`.
- Manual (needs the user): S0a/S0c runs; T12 on a scratch ticket in a scratch clone, never the real `todo-nestjs-backend` checkout.
  Compare `git reflog` and `git branch -a` before/after.
- Never test against real workspace state with a state-changing command.

## 7. Risks

| Risk | Mitigation |
|---|---|
| `Notification` hook does not fire for every prompt type under PTY | S0a captures real payloads; fallback heuristic marked "possibly waiting"; never used for control decisions |
| AskUserQuestion unusable under `-p`, so headless blocks user nodes | Default to interactive PTY; S0c verdict decides if B is ever used |
| `--settings` hooks replace instead of merge (guard lost) or skill hooks absent | S0a checks merge; app's own deny hook is independent of the skill hook |
| Deny patterns bypassed by shell quoting | Two layers (tool deny + PreToolUse script); gates on `coding`, `final-review`, `deliverable-ready` already refuse foreign commits (`SKILL.md:116-117`); post-node check of `git log` since startHead (read-only, via `sdlc git`) flags any unexpected commit and stops the run |
| Session finishes a node but never exits | Host ends it after gate event + Stop; conversation persisted |
| Session keeps running after node (does the next node itself, ignoring one-node rule) | Brief says stop; Host checks `nextPending` changes with no spawn and flags "ran ahead"; per-node mode terminates on first gate event |
| PID reuse / stale locks (600 s, `state.py:25`) | PID + start time; lock-broken is logged by harness; Host waits for lock age < 600 s |
| PTY libs and xterm.js add toolchain weight; no Go installed yet | S0b only after P2-T03; xterm.js via npm (Node 22 present) |
| Quoting/encoding on Windows (Vietnamese input, long briefs) | Brief by file, positional prompt is one short ASCII line |
| `claude` CLI flags change between versions | Pin-check `claude --version` at start; golden arg-builder test; fail with a clear message |
| Costs: unattended loops burn tokens | `maxNodesPerRun`, `nodeTimeoutMin`; P7 shows live cost; stop on loop-back by default |

## 8. Decisions needed from the user (with recommendations)

1. **Q1 mechanism**: hooks (`Notification`, `Stop`, `SessionEnd`) writing to a per-run file drop. Recommend yes, pending S0a.
2. **Q2**: prompt by file + short argument; report = harness `decisions.jsonl` gate event (plus `HANDOFF.md`). Recommend yes, no peer messaging.
3. **Q3**: run policy in app data, not the ticket dir; locked safety flags hard-coded in the Host. Recommend yes.
4. **Interactive embedded PTY vs headless** for node sessions. Recommend interactive PTY; headless later if S0c allows.
5. **Allow the Host to write keystrokes into its own child's PTY** (user typing and Ctrl+C)? D5 bans injection into foreign windows;
   this is the app's own pipe. Recommend yes, limited to user input and Ctrl+C; Host never types commands itself.
6. **Default permission mode** for app-spawned nodes: `acceptEdits` (prompts for Bash). Recommend `acceptEdits`; never bypass.
7. **Who runs spike S0a/S0c**: it needs a real `claude` session (agents were told not to start one). Recommend the user runs it once,
   on a scratch dir, guided by a script I would write in M3.
8. **Per-node fresh session** stays default (D6). Recommend yes.

## 9. Evidence read
- `harness/kaneki-harness/skills/kaneki-flow/SKILL.md:7-14, 20-22, 24-25, 104-118, 119-159, 161-175, 187-194`
- `harness/kaneki-harness/skills/kaneki-flow/scripts/guard_git.py:1-69`
- `skills/zed-spawn-session/SKILL.md:41, 47, 56-65`; `skills/zed-spawn-session/scripts/zed-spawn.ps1:70-101`
- `notify-hook/README.md:3-26`; `notify-hook/notify.py:5-6, 54-72`
- `harness/kaneki-harness/harness/state.py:25, 67-90, 150-175` (lock dir, owner.json, 600 s stale, hash chain)
- `harness/kaneki-harness/harness/cli.py:572-589, 850-865` (gate approve, ship-request/ship digest); `harness/board.py:38,54`
- `harness/kaneki-harness/contracts/decision-event.schema.json:1-40`
- `harness/kaneki-harness/docs/desktop-app/plan.md` D5, D6, Q1-Q3
- `claude --help` (local, read-only): options listed in 4.2
- Not verified: Claude Code hook event names beyond `PreToolUse` and `Notification`, `--disallowedTools` pattern syntax, behaviour
  of `--settings` hook merging, AskUserQuestion under `-p`, skill invocation via positional prompt, ConPTY behaviour.
