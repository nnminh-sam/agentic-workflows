# Kaneki desktop app: design plan

Status: **draft for design** · 2026-11-17 · owner: nnminh-sam
Mockup: [`../mock/desktop-ui.html`](../mock/desktop-ui.html) (commit `686a5c7`, branch `feat/learning-kit`)

## 1. Goal

One local desktop window to **watch, decide and drive** kaneki harness tickets, replacing today's mix of
external editor panes, the read-only board (`sdlc web`), the chat session that spawns sessions, and hand-run cost scripts.

What it must make easy:
1. See every ticket, which node it is on, and **who it is waiting on** (you, an agent, nothing).
2. **Decide a gate** with its evidence next to it, without switching to the session that runs the node.
3. **Run a ticket node by node** under a policy, with spawned sessions supervised via an agent-agnostic `AgentBackend`.
4. See the **real cost** (tokens and dollars from agent session logs + `sdlc dispatch` telemetry), not only the harness count.

Out of scope for v1: editing artifacts (open them in the configured editor), multi-user or remote access, running on a server,
shipping/committing/pushing from the app.

## 2. Why now (evidence from the TODO-1-2 run, 2026-11-17)

- Gate decisions happened inside whatever Zed pane ran the node; the evidence was in other files.
- The node-by-node loop (spawn a session → watch `decisions.jsonl` → stop it at the gate → spawn the next) was
  done by hand from a chat session, with idle notices and `Stop-Process`.
- Spawning by keystroke injection (`zed-spawn.ps1`) once sent keys to Microsoft Teams when Windows refused to
  switch focus (diagnostic logs downloaded, pasted command possibly posted). Fixed with a foreground check, but
  it shows keystroke automation is the wrong long-term mechanism.
- The harness's `token-usage.md` counted ~1.4M tokens where the session logs showed ~20M+ processed; cost had to
  be computed with throwaway scripts.

## 3. Decisions

| Id | Decision | Status | Why |
|---|---|---|---|
| D1 | **The harness CLI stays the only writer.** The app reads ticket files directly; every state change (gate, invalidate, record, lane) runs `python <skill>/scripts/sdlc.py …`. | Decided | Keeps `decisions.jsonl` the single audit trail and the hash chain intact; the app can't corrupt state. |
| D2 | **Show the exact command before any state change**, and confirm. | Decided | Same pattern as `/push`; the user sees what will be logged. |
| D3 | **Gate approval is the core screen**: question, evidence summary (NG list, reviewer verdict), choices (approve / reject + reopen node X), optional note, command preview, which nodes go stale. | Decided | The decision that most needs context. Notes optional because typing with Telex is awkward. |
| D4 | **React to file changes, don't poll.** Watch `.sdlc/tickets/*/decisions.jsonl` and `state.json`. | Decided | The board polls every 2.5 s; a watcher is cheaper and instant. |
| D5 | **The app supervises sessions through a Go `AgentBackend` interface** (`antigravity`, `claude`, `codex`, `gemini`, `opencode`, or `sdlc dispatch`), tracks PIDs, detects idle/waiting-on-user/exit, and stops them. No keystroke injection. | Decided | Lesson from the Teams incident; keeps supervision decoupled from any single agent CLI. |
| D6 | **Run policy per ticket**: one node per fresh session (default) or one session per run; auto-continue after a pass; stop at gates needing the user, on reject/loop-back, at `deliverable-ready`; never ship/commit/push (locked on). | Decided | Mirrors the instructions pasted into every session brief today. One node per session keeps driver context small. |
| D7 | **Pluggable usage/cost collectors** (Claude session JSONL `~/.claude/projects/**/*.jsonl`, `sdlc dispatch` logs, and harness `token-usage.md`), deduplicated by message id, priced per model/tier, shown next to the harness count; flag waste. | Decided | The harness count misses real API usage (see `kaneki-todo1-vs-todo12`); collectors implement one Go interface per backend. |
| D8 | **Local only.** No listening server, or `127.0.0.1` only with a per-run token if one is needed. | Decided | Same stance as `sdlc web`. |
| D9 | **Documents open in the configured editor** (`editor` setting: VS Code, Cursor, Antigravity, Zed, or OS default); the app shows previews/summaries, not a full Markdown/diff viewer in v1. | Decided | Keeps v1 small without tying users to one IDE. |
| D10 | **UI stack**: Tauri 2 (Rust), Wails (Go) or C++ + Dear ImGui. | **Open** → decided by spike S1 | Priorities: low memory, performance, small toolchain. See §6. |
| D11 | **Where the app lives**: new folder `agentic-workflows/kaneki-desktop/` (separate from the Python harness). | Proposed | The harness stays stdlib-only Python; the app is a client of it. |

## 4. Mockup reference

[`docs/mock/desktop-ui.html`](../mock/desktop-ui.html) is a clickable, standalone mock with sample TODO-1-2 data. It
runs no commands. Screens it covers:

| Screen | In the mock | Notes for the real app |
|---|---|---|
| Main window | ticket rail · pipeline strip (generation `g2`, loop-back line) · node card (units, tier/model, verdict, tokens, rounds) · inspector · sessions dock · live decision feed | Top bar: Ctrl+K, "N gates need you", today's cost |
| Gate approval | Review gate / amber pill | **Fix:** the mock's reject preview runs `gate` then `invalidate` without `--reason`. Real CLI: `sdlc gate … --status rejected --actor user [--reopen NODE] [--note …]`; `sdlc invalidate --ticket T --node N --reason R` (reason required). Use `--reopen` if it covers the case. |
| Run policy | ⚙ Run policy drawer | Policy must be stored per ticket (file under the ticket dir or app config, see Q3) |
| Cost | Cost tab | Real vs harness count, by node, by model, waste flags, compare two runs |
| Decisions | Decisions tab | Newest first, who decided (controller / user) |
| Command palette | Ctrl+K | Run next node, review gate, reopen, stop session, compare, policy, theme |
| Toasts | bottom right | Gate needs you, reviewer FAIL, node reopened, session waiting on a permission prompt |

Not in the mock yet: new ticket / onboarding form, worktree list and cleanup, settings (paths, editor, prices),
first-run setup.

## 5. Interfaces the app uses (verified 2026-11-17 against `kaneki-harness` @ `487f5e8`)

**Read (direct file access):**
- Profile & host binding: `<workspace>/sdlc.profile.json` and `.sdlc/host.local.json` (ticket root, worktree root, repositories, tier/model bindings).
- Per ticket, under `.sdlc/tickets/<id>/`: `state.json`, `decisions.jsonl`, `STATUS.md`, `token-usage.md`,
  `packets/`, `results/`, `facts/`, `logs/*.dispatch.log`, node artifacts (`plan.md`, `code-review.md`, `run-report.md`, …), `HANDOFF.md`.
- Ready-made JSON: `sdlc tickets` (list with `nextPending`, `next`, `strip`, `tokensTotal`, `questions`, …), `sdlc gate-request`, and `sdlc plan` (includes resolved `execution` block per unit).
- Usage telemetry: pluggable backend collectors (e.g. Claude JSONL `~/.claude/projects/<cwd-slug>/*.jsonl`, `logs/*.dispatch.log`, and harness `token-usage.md`).

**Write (always via the CLI, D1):**
- `sdlc gate --ticket T --node N --status approved|needs-changes|rejected|stopped [--actor user] [--reopen NODE] [--note …]`
- `sdlc invalidate --ticket T --node N --reason R`
- `sdlc lane --ticket T --set full --reason R`, `sdlc init …`, `sdlc worktree …`, `sdlc dispatch …`
- Node sessions run via the configured `AgentBackend` (`antigravity`, `claude`, `codex`, `gemini`, `opencode`, or `sdlc dispatch`); the app starts, monitors, and stops them.

## 6. Stack decision (D10): spike S1

Build the same thin slice in each candidate: window + ticket list from `sdlc tickets` + watch `decisions.jsonl`
+ one toast + one button that runs `sdlc gate --help`. Measure, on this machine:

| Criterion | How | Target |
|---|---|---|
| Idle memory, all processes (incl. WebView2) | Task Manager / `Get-Process` sum after 5 min | < 80 MB |
| Cold start | stopwatch to first paint | < 1 s |
| CPU idle | 5 min average | ≈ 0 % (no per-frame redraw) |
| Toolchain added to disk | before/after | report |
| Release binary | size | report |
| Vietnamese Telex input in a text field | type a note | must work |
| Effort to port `web_page.html` UI | hours | report |

Already on this machine: Node 22, WebView2 runtime, VS 2019 Build Tools (MSVC 14.29). Not installed: Rust, Go, CMake.
Rough expectations: Wails ≈ smallest toolchain; ImGui ≈ smallest runtime footprint but weak rich text and IME
risk; Tauri ≈ best plugin set (notifications, single instance, updater) but the largest toolchain and `target/`.

## 7. Milestones

| M | Scope | Done when |
|---|---|---|
| M0 | Spike S1, pick stack (D10), scaffold `kaneki-desktop/` | Decision recorded here with measurements |
| M1 | Read-only: ticket rail, pipeline strip, node card, inspector preview, decision feed, file watching | Matches `sdlc web` data for every ticket in `todo-nestjs-backend` |
| M2 | Gate approval screen + command preview + toasts | Approve/reject a real gate; `decisions.jsonl` shows `actor: user` |
| M3 | Session supervision + run policy ("Run next node", auto-continue, stop rules) | TODO-n runs node by node to `deliverable-ready` without the chat session |
| M4 | Cost panel (real vs harness), waste flags, compare runs | Reproduces the TODO-1 vs TODO-1-2 numbers |
| M5 | New ticket / onboarding, worktree cleanup (`git worktree remove` only; never delete branches), settings, packaging | Installer builds in CI |

## 8. Open questions

- **Q1** How does each `AgentBackend` report **waiting on permission or `ASK_USER` input**? (Standardize on `pending-prompt.json` + `sdlc gate-request` alongside backend-specific hooks/stream events.) Needed for M3.
- **Q2** How does the app **send the task** to a spawned session and get its **report** back: command-line prompt + cross-session messaging (as now), or a file the session writes?
- **Q3** Where is **run policy** stored: in the ticket folder (visible to the harness) or app config only?
- **Q4** Prices for cost: hard-coded table, user setting, or fetched? (Haiku 5.5 cache-read price was assumed 0.1×.)
- **Q5** Should the harness emit a small **machine-readable status file** (or reuse `sdlc tickets`) so the app does not depend on internal file layouts? Also fix N4 (`status --accept-current-state` writes without the ticket lock) before the app calls it.
- **Q6** Multiple workspaces in one app window, or one window per workspace?

## 9. Next steps

1. Answer Q1–Q3 (they shape M3 and the app↔harness contract).
2. Fix the mock's reject command preview (see §4).
3. Run spike S1 for the stack candidates you want to compare, record the numbers in §6 and close D10.
4. Write the M1 contract: the exact JSON the UI consumes (start from `sdlc tickets` / `board.summarize`).
5. Finish TODO-1-2 and its comparison report, then use the TODO-1 vs TODO-1-2 numbers as the M4 test fixture.

Related: harness guide `../harness-guide.html` (rev 4, §"Token usage and live tracking"), code inconsistencies N1–N6 there.
