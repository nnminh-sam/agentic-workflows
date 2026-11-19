# Shared context for every part plan (read first)

Date: 2026-11-18. Owner: nnminh-sam (first desktop app). Coordinator: the main Claude session, which reviews and
aggregates every part plan into `../00-build-plan.md`.

## What we are building
A local Windows desktop app to **watch, decide and drive** kaneki-harness tickets (the harness is a stdlib-only
Python CLI, `sdlc`). Read these before planning anything:

| What | Where |
|---|---|
| Design plan (goals, decisions D1–D11, interfaces §5, spike §6, milestones §7, open questions Q1–Q6) | `D:/workspace/HAISE/agentic_workspace/agentic-workflows/kaneki-harness/docs/desktop-app/plan.md` |
| Harness CLI (all subcommands) | `kaneki-harness/harness/cli.py` (parsers from line ~1010) |
| Board summary functions (the JSON shape the app should reuse) | `kaneki-harness/harness/board.py` (`summarize`, `summarize_ticket`, `list_tickets`) |
| Contracts (JSON schemas) | `kaneki-harness/contracts/*.schema.json` (state, decision-event, graph, project-profile, …) |
| Token graph (20 nodes, lanes, tiers) | `kaneki-harness/graph/lifecycle.json` |
| Harness architecture | `kaneki-harness/docs/design.md`; review guide with known code issues N1–N6: `kaneki-harness/docs/harness-guide.html` |
| Design system (tokens, ~45 components, icons, gallery) | `kaneki-harness-app/design-system/` (`tokens.json`, `components.css`, `icons.js`, `gallery-data.js`, `gallery.html`) |
| Assembled window mock built from the design system | `kaneki-harness-app/design-system/app-mock.html` |
| Older clickable mock with TODO-1-2 sample data | `kaneki-harness/docs/mock/desktop-ui.html` |

All repo paths are relative to `D:/workspace/HAISE/agentic_workspace/agentic-workflows/`.

## Real data (READ-ONLY)
Workspace `D:/workspace/HAISE/testing/todo-nestjs-backend` (profile `sdlc.profile.json`, tickets `TODO-1`,
`TODO-1-2` under `.sdlc/tickets/`). Read-only commands you MAY run from that folder:

```
python D:/workspace/HAISE/agentic_workspace/agentic-workflows/kaneki-harness/skills/kaneki-flow/scripts/sdlc.py tickets
python …/sdlc.py status --ticket TODO-1-2
python …/sdlc.py plan --ticket TODO-1-2
```
(`sdlc tickets` takes ~0.5 s and returns `{"tickets":[{ticket, kind, lane, strip[], nextPending, next, tokensTotal, …}]}`.)

## Hard rules for sub-agents
- **Research and plan only.** The only file you create is your own plan file. Do not edit any other file, do not
  scaffold code, do not install toolchains or packages, do not commit.
- **Never run a state-changing `sdlc` command** (`init, record, gate, invalidate, lane --set, worktree, handback,
  ship*, review-fetch, receipts --reissue, onboard-write, install, zed-setup, status --accept-current-state`) and
  never touch git branches.
- Use `rg` (not grep/find) in the shell. Shell is Windows (Git Bash / PowerShell).
- Cite evidence as `path:line` for every claim about existing code or data. Mark guesses `ASSUMPTION`.

## Fixed decisions you must plan within
- D1 the harness CLI is the only writer; the app reads ticket files directly and runs `sdlc.py …` to change state.
- D2 show the exact command and confirm before any state change. D4 watch files, do not poll.
- D5 the app spawns `claude` sessions as child processes; no keystroke injection. D6 run policy per ticket; never
  ship/commit/push from the app. D7 real cost from `~/.claude/projects/**/*.jsonl`. D8 local only, no open port
  (or 127.0.0.1 + per-run token). D9 documents open in Zed.
- **App lives in `kaneki-harness-app/`** (user decision 2026-11-18; supersedes D11's `kaneki-desktop/`). Source code
  and the design system live there; docs in `kaneki-harness-app/doc/`.
- **D10 is closed: Host stack = Go + Wails** (owner decision 2026-11-18; no Rust, no Tauri, no ImGui, no comparison
  spike). Name the Wails-specific bits explicitly; the UI still reaches the Host only through `ui/host-bridge.js`.
- The UI is HTML/CSS/JS in WebView2, built on the existing design system (`.k-*` classes, tokens). Machine has Node
  22, WebView2; Go and Wails are not installed yet (P2 installs them under `D:\toolchains`).
- Architecture vocabulary used across all parts: **UI** (WebView JS) ↔ **Host** (Go/Wails process: runs commands,
  watches files, spawns sessions, toasts) ↔ **Harness** (Python CLI + ticket files). UI↔Host = IPC calls
  (`invoke(name, args)`) and events (`on(name, payload)`). Keep logic in the harness; keep the Host thin.

## Required plan format (`../parts/Pn-<slug>.plan.md`)
1. **Goal** (2–3 lines) and **Done when** (testable).
2. **Scope**: in / out.
3. **Inputs it needs from other parts** and **outputs it provides** (name the IPC calls, events, JSON types, files).
4. **Design**: the approach, data shapes, file/module layout, key algorithms. Stack-specific notes where relevant.
5. **Tasks**: table `ID (Pn-Tnn) | Task | Size (S ≤2h, M ≤1d, L ≤3d) | Depends on | Done when`.
6. **Tests / verification** (unit, fixture-based, manual against the real workspace).
7. **Risks** with mitigation.
8. **Decisions needed from the user** (numbered, each with a recommendation).
9. **Evidence**: files and lines you read.
Keep it under ~350 lines. Be concrete; no filler.
