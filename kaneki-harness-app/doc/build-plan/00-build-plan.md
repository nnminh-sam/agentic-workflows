# Kaneki desktop app: build plan (aggregate)

Status: **decided** · 2026-11-18 · owner: nnminh-sam · all recommendations in section 5 (1–12) accepted by the
owner on 2026-11-18; decision 13 (macOS) is new and open. **D10 (Host stack) is closed: Go + Wails only** (owner, 2026-11-18).
Inputs: design plan `kaneki-harness/docs/desktop-app/plan.md` (D1–D11, M0–M5), design system
`kaneki-harness-app/design-system/`, eight part plans written by Sonnet sub-agents from the specs in `specs/`, then
reviewed and reconciled here. **Where this file and a part plan disagree, this file wins** (section 6 lists every
correction). Paths are relative to `D:/workspace/HAISE/agentic_workspace/agentic-workflows/`.

| Part | Plan | Milestone | One line |
|---|---|---|---|
| P1 | [Data contract](parts/P1-data-contract.plan.md) | M0 (first) | Harness emits every derived value; schemas + IPC catalogue + fixtures |
| P2 | [Wails setup + scaffold](parts/P2-stack-spike-scaffold.plan.md) | M0 | Install Go + Wails on `D:`, small Wails spike slice measured on this machine, scaffold the app |
| P3 | [Host core](parts/P3-host-core.plan.md) | M1 | Discovery, allow-listed runner, watcher + tail, cache, IPC envelope |
| P4 | [UI shell, read-only](parts/P4-ui-shell-readonly.plan.md) | M1 | Preact + htm, fixture/host sources, 7 screens, parity with `sdlc web` |
| P5 | [Gate + writes](parts/P5-gate-and-writes.plan.md) | M2 | Gate dialog, command preview/confirm, invalidate, lane, toasts |
| P6 | [Session supervision](parts/P6-session-supervision.plan.md) | M3 | Spawn `claude` in an embedded terminal, hooks → file drop, run policy |
| P7 | [Real cost](parts/P7-cost-panel.plan.md) | M4 | `sdlc cost` engine over session logs, attribution, waste flags |
| P8 | [Setup, packaging, quality](parts/P8-setup-packaging-quality.plan.md) | M5 + all | First run, new ticket, worktrees, installer, test pyramid, CI |

## 1. The architecture the parts agree on

```
 UI  (WebView2: HTML/CSS + Preact/htm, design system .k-*)      renders, never derives
  │  invoke(name,args) / on(event,payload)    ← only ui/host-bridge.js knows the stack
 Host (Go + Wails)                                              thin: transport, cache, watch, spawn
  │  argv (allow-listed, no shell) · file watch · tail decisions.jsonl · ConPTY for claude
 Harness (Python CLI `sdlc`, stdlib, 3.9+)                      computes everything, only writer of tickets
```

**The one rule that settled most conflicts: logic lives in the harness.** Three parts drifted toward re-deriving
harness logic in the Host (P5 wanted the Host to compute "waiting on user", stale previews and the gate request;
P3 used `status-of --path`; P7 debated a native log parser). Resolved: the harness gains a few **read-only**
commands and the Host only transports their JSON.

| New / changed harness command (all read-only unless noted) | Owner | Replaces |
|---|---|---|
| `sdlc contract` → `{contractVersion, harnessVersion, commands[]}` | P1 H2 | – |
| `sdlc tickets` + `title, waitingOn, waitingNode, waitingReason, lastEventAt`; strip items + `generation, boundary, waiting` | P1 H4/H5 | parsing the `next` sentence (P4 gap) |
| `sdlc ticket-detail --ticket T` → `TicketDetail` (node cards, unit records, loop-backs, trace) | P1 H6 | P3's `status-of --path` refresh |
| `sdlc gate-request --ticket T [--node N]` → `GateRequest` (readiness dry run, evidence, choices with argv + stale preview) | P1 H7 | P5's Host-side `waiting()`, `staleOf()`, `gate.request` |
| `sdlc cost --workspace W [--ticket T] --json` (new `harness/cost.py`) | P7 | native log parser |
| **Write:** N4 fix: `status --accept-current-state` takes the ticket lock | P1 H1 | – |
| **Write (proposed, decision A2):** `gate --status rejected --reopen X` invalidates atomically | new H10 | the two-step reject (P5) |

Python start-up (~0.5 s per call, measured by `sdlc tickets`) is hidden by the Host cache (P3 4.4): one harness
call per changed ticket on a watcher event, none on re-render.

## 2. Build order and critical path

```
Week →    1        2        3        4        5        6        7        8       9+
P1 harness cmds + schemas + fixtures ███████
P4 UI on fixtures (browser, no toolchain)  ████████████████
P2 Wails setup + spike (needs installs) ████ → scaffold ██
P3 Host core                          ██████████████
                                 M1 parity ▲
P5 gate + writes                                     ██████████
                                                           M2 ▲
P6-S0a hook spike (you run claude once) ██     P6 full build ──────────────────────▶ M3
P7 cost.py (harness, Python only) ········ can start any time after P1 H2 ──▶ UI at M4
P8 quality (fixtures/CI) from week 1; setup/packaging at M5
```

1. **Start now, no installs needed:** P1 harness changes (Python + tests), P1 schemas/fixtures, P4 UI against
   fixtures in Edge (`?source=fixture`), P7 `cost.py` (Python), P6-S0a (one hook-capture session you run).
2. **Needs your install approval:** P2 (Go + Wails CLI only, toolchains on `D:\toolchains`).
3. **M1** = P3 + P4 wired; done when `parity.mjs` shows zero diffs vs `sdlc web` for both real tickets.
4. **M2** = P5 on a throwaway ticket made by `make_synthetic.py` (never the real tickets).
5. **M3** = P6 (riskiest; spikes S0a–S0c first). **M4** = P7 UI. **M5** = P8 setup and installer.

**Effort (sum of the part plans' task ceilings: S ≤ 2 h, M ≤ 1 d, L ≤ 3 d; 43 S + 59 M + 15 L):**

| Scope | Ceiling | Realistic for a first desktop app (~60 %) |
|---|---|---|
| M0–M2 (P1–P5): an app that watches every ticket and decides gates | ~61 working days | ~37 days |
| M3–M5 (P6–P8) | ~54 working days | ~32 days |
| Total | ~115 working days | ~69 days |

The ceilings double-count some shared work (section 6, "duplicates"), so the realistic figure is the planning
number. *Recomputed after D10 (Go/Wails only):* the old P2 tasks T07 (L, Rust + Tauri slice) and T11 (S, loser
uninstall) are gone, so 44 S + 59 M + 16 L becomes 43 S + 59 M + 15 L; at 8 h per day that removes 3 d + 0.25 d =
3.25 d, so M0–M2 goes from ~64 to ~61 d (60.75), the total from ~118 to ~115 d (114.75); realistic = 60 % of the
ceiling (~37 and ~69 days; M3–M5 is unchanged at ~54 / ~32). Old T08 (measure Tauri + Telex) was kept as a smaller
Telex-only S task, so it does not reduce the count. M2 is the first point where the app replaces daily work (gates no longer decided inside Zed panes).

## 3. Cross-part conventions (fixed here)

| Topic | Decision | Was |
|---|---|---|
| IPC names | P1 catalogue (`parts/P1…` 4.3): `snake_case` invokes, `kebab-case` events, camelCase JSON; machine form `contracts/ipc.json`, a test fails on unknown names | P3 `tickets.list`, P5 `write.run`, P8 `config.get`, P2 `run_sdlc` |
| Additions to the catalogue (additive, no version bump) | P6: `session_write`, `session_resize`, `run_resume`, events `session-output`, `run-state`. P7: `cost_compare`, `cost_prices_get/set`, `cost_rescan`. P8: `env_check`, `worktree_list`, `worktree_remove`; ticket creation via `preview_command{kind:"init"}` | – |
| Writes | always `preview_command` → `run_command{previewId}`; Host rebuilds argv from the allowlist and checks `ticketFingerprint` (P1) — P5's `stateToken` is the same idea, use P1's name | P5 `write.preview/run` |
| Toast/"gate needed" source | P3 emits `gate-needed` / `reviewer-failed` / `node-reopened` from `waitingOn` transitions and new events; P5 owns toast UI + dedupe; P6 emits only session states | P5, P6 and P3 each emitted them |
| Stack seam | `ui/host-bridge.js` is the only stack-specific UI file | P2 `ui/src/host.js` |
| UI tech | Preact 10 + htm vendored in `ui/vendor/` (versions pinned in `VERSIONS.txt`), plain ES modules, **no bundler** | – (P2 and P4 agree) |
| Design system in the app | `scripts/sync-ds.mjs` copies `tokens.css`, `components.css`, `icons.js` into `ui/ds/` (git-ignored) after `build_tokens.py --check`; `gallery.html` stays the source of truth; new classes go into the gallery first (P4 4.5) | P4 linked `../design-system/` directly (breaks in release builds) |
| Tests | Python `unittest` (harness); `go test` (Host); **`node:test` + `linkedom`** (UI DOM) + `ajv` (contract); headless Edge screenshots; e2e few, nightly | P8 proposed Vitest + jsdom |
| App folders | Config: `%APPDATA%\kaneki-harness-app\config.json`. Runtime data: `%LOCALAPPDATA%\kaneki-harness-app\` → `logs\`, `runs\<runId>\`, `policy\<ticket>.json`, `cost-cache\`, `toasts.json`. **Nothing app-written inside a workspace or ticket folder** | 4 different names across P3, P6, P8; P7 wrote `<ticketDir>/app/sessions.jsonl` |
| Python | ≥ **3.9** (harness uses `from __future__ import annotations`, `README.md:15`); auto-detect `py -3` → `python` → `python3`, cache the absolute path, override in Settings | P3 said 3.10 |
| Spawned session identity | `claude -n kaneki:<ticket>:<node>:<runId> --session-id <uuid>`; `runs/<runId>/run.json` records both, so P7 attribution is exact | P6 `kaneki-<ticket>-<node>`, P7 colon form + ledger in ticket dir |
| Fixtures | One generator: P1 `scripts/make_synthetic.py` (drives real `sdlc` in a temp workspace) + `capture_fixtures.py` (read-only, real tickets). P5-T01/T11 and P8-T15 use it instead of their own | three fixture builders |

**Host allowlist v1 (P3 4.2, adjusted):**

| Class | Subcommands |
|---|---|
| read | `contract, tickets, ticket-detail, gate-request, status, plan, validate, digest, status-of, cost, lang` (no `--set`), `receipts` (no `--reissue`) |
| write, M2 | `gate` (always `--actor user`), `invalidate` (reason required), `lane --set full` |
| write, M5 | `init` (never `--force`), `onboard-scan`, `onboard-verify` (no `--allow-tests`), `onboard-write` (first run only) |
| **never** | `ship`, `ship-request`, `review-fetch`, `git`, `install`, `zed-setup`, `web`, `record` (sessions record, not the app), `handback`, `receipts --reissue`, `status --accept-current-state` |
| git (Host `runGit`, separate allowlist, P8) | `worktree list --porcelain`, `worktree remove <path>` (no `--force`), `status --porcelain`, `rev-parse`, `log`. No `branch`, `push`, `commit`, `reset`, `clean`, `prune`; a test iterates the deny list |

## 4. What the review verified (and what it found)

| Claim | Verdict | Evidence |
|---|---|---|
| `gate --status rejected --reopen X` ignores `--reopen`; the mock's reject would stale nothing | **Confirmed** (P5) | `kaneki-harness/harness/cli.py:648-650` (`else: rec["verdict"] = args.status` only) |
| Real rejects were two commands: `gate rejected` then `invalidate --reason` | Confirmed (P5) | `TODO-1-2/decisions.jsonl:52/54, 70/72` |
| `install`, `onboard-write`, `zed-setup`, `web` are in `READ_ONLY` (no lock) though they write or open a port | Confirmed (P3) | `cli.py:1167-1168` |
| The git guard does not stop a session from running `sdlc ship-request` + `ship` itself | Confirmed (P6) | `guard_git.py` checks shell git only; ship is gated by the digest flow `cli.py:850-876` → the app adds a deny hook + `--disallowedTools` |
| No harness field says "waiting on you" (only the `next` sentence) | Confirmed (P4, P1) | `board.py:63-75` |
| Extra `strip` keys are safe for the board page; one test needs updating | Confirmed (P1) | `web_page.html:126` uses only `node/status`; `tests/test_lanes_and_board.py:144` |
| Harness version source exists | Confirmed (resolves P8 decision 6) | `harness/__init__.py:3` `__version__ = "0.1.0"` |
| Real cost ≈ **29× the harness count**; the design plan's "1.4M vs 20M+" is stale | Measured (P7) | TODO-1: 50,857,722 tokens / 722 calls vs 1,763,370; TODO-1-2: ~59.45M net / 778 calls vs 2,066,547; matches the earlier comparison report within 0.3 % |
| Python 3.10 required | **Wrong** (P3) | `from __future__ import annotations` in `cli.py:3`, `state.py:9`, `board.py:4`; README says 3.9+ |
| Both real tickets are finished (12/12), so no real data shows a pending gate or a loop-back in progress | Confirmed (P1, P4) | `sdlc tickets` output; M1/M2 proofs use synthetic fixtures + one throwaway ticket |
| Dialog classes P5 needs exist (`.k-dialog .k-choice .k-command .k-stale-list .k-evidence`) | Confirmed | `design-system/components.css` |
| Disk: C: 9.9 GB free, D: 14.4 GB | Reported (P2) | Go + Wails is about 0.5 GB and goes under `D:\toolchains`; fits on D: |

Still unverified (each has a task that verifies it first): current Wails version, Go toast library and single-instance support (P2-T01), Claude Code hook payloads and `--settings` hook merging, `--disallowedTools`
pattern syntax, AskUserQuestion under `-p` (P6-S0a/S0c), model prices (P7, you enter them), NG-row format in
reviewer results (P1 R3: best effort), WebView2 Telex behaviour (P2, you type it).

## 5. Decisions

**Decisions 1–12 were accepted as recommended on 2026-11-18.** The text below keeps each recommendation as the
record; the part plan section has the detail. Decision 13 (macOS) is new.

**A. Before any code (blocking)**
1. **Harness emits the contract**: add the read-only `contract`, `ticket-detail`, `gate-request` (and later `cost`)
   commands to `kaneki-harness`, extend `tickets` additively, fix N4 and N5. → **Yes** (P1 §8.1, 8.6).
2. **Reject in one atomic command** (new H10: `gate --status rejected --reopen X` also invalidates, reason = note,
   required) instead of P5's two-step with a half-applied state on failure. → **Atomic** (small harness change; until
   it lands, `GateRequest.choices[].followUp` carries the `invalidate` step).
3. **Host stack: Go + Wails only** (D10 closed by the owner, 2026-11-18; no Rust, no Tauri, no ImGui, no two-stack
   spike). Toolchain installed under `D:\toolchains`; memory < 80 MB is a goal, not a gate (hard gates: Telex, toast,
   cold start < 1 s). Was: "spike both stacks, Wails first". → **Decided** (P2 §12.1–4).
4. **UI: Preact + htm, no bundler**; dev-only npm deps `linkedom`, `ajv`. → **Yes** (P4 §8.1, 8.4; P1 §8.7).
5. **Fixtures committed** (scrubbed real captures + generated synthetics); review the real ones before commit. → **Yes**.

**B. Before M2 (gate dialog)**
6. **"Needs you" = harness `waitingOn == user`** with P1's deterministic reasons (user boundary, reviewer FAIL, loop
   cap, rejected-open). P5's heuristic (NG > 0 on a controller node, the TODO-1-2 case) is **not** adopted: that
   question is asked by the driver in the session and shows up as a P6 session state "waiting on you". → **Yes**.
7. Minor defaults, accepted unless you object: `--gate` label `G0` (auto `G4` at the loop cap); note pre-fills the
   reason; `needs-changes` offered as a secondary action on `loopBackTo` nodes; failed second step = manual retry;
   Decisions tab shows the last 200 with "load older"; dock log workspace-wide; inspector stays plain text (D9).

**C. Before M3 (sessions)**
8. **Q1/Q2/Q3**: hooks (`Notification`, `Stop`, `SessionEnd`) append to `runs/<runId>/events.jsonl` (no port, no
   pipe); the task goes in as a short prompt pointing at a brief file, the report is the harness trail
   (`decisions.jsonl` gate event + `HANDOFF.md`); run policy in app data with ship/commit/push/branch-delete locked
   off in code. → **Yes** (P6 §8.1–3).
9. **Interactive sessions in an embedded terminal** (ConPTY + xterm.js), the Host writes only *your* keystrokes and
   Ctrl+C into its own child terminal; default permission mode `acceptEdits`, never bypass. → **Yes** (P6 §8.4–6).
10. **You run spike S0a once** (one `claude` session in a scratch folder with a logging hook) — agents must not.

**D. Before M4 (cost)**
11. Prices ship empty and you enter them once (marked unverified until you tick them); headline = net of the
    previous session's leftover context, gross one click away; "list-price equivalent" wording; local day. → **Yes**
    (P7 §8.3–6).

**E. Before M5 (setup, packaging)**
12. One window per workspace; no code signing and no auto-update in v1; onboarding = scan + dry-run preview in the
    app, the rest via "Continue in Claude session"; the app may run `git worktree remove` itself (clean, delivered
    tickets only, no `--force`, branches always kept); require a harness checkout (no bundled snapshot); add a
    `kaneki-harness` CI job (none exists today; CI never pushes or releases). → **Yes to all** (P8 §8).

**F. Platforms (new, open)**
13. **macOS.** The app can be installable on a Mac: Wails builds macOS apps (WebKit instead of
    WebView2), and the harness, Claude Code and Zed all run there. But the plan as written is Windows-first in about
    ten places (table below), Mac builds have to be made on a Mac (or a macOS CI runner), and an unsigned app is
    blocked by Gatekeeper until you allow it (proper fix: Apple Developer ID + notarization, paid yearly).
    → **Recommend: ship Windows first, keep the code portable from day one, add a Mac build as milestone M6** when
    there is a Mac to test on. Cost now is small (everything OS-specific behind P3's `platform` module, paths from the
    stack's path API, build scripts in Node not PowerShell). D10 is decided (Wails), so this is no
    longer a stack-choice factor. Framework facts here are from knowledge of Wails, not re-checked against current docs;
    P2-T01 verifies them.

| Windows-specific in the plan | macOS equivalent | Part |
|---|---|---|
| WebView2 (Chromium) | WKWebView (Safari engine): test UI + Vietnamese Telex in both | P2, P4 |
| ConPTY for the embedded terminal | Unix PTY (`creack/pty`) | P6 |
| Job Objects kill the session tree | process groups (`setpgid`, kill the group) | P3, P6 |
| `CREATE_NO_WINDOW` | not needed | P3 |
| `%APPDATA%` / `%LOCALAPPDATA%` | `~/Library/Application Support/kaneki-harness-app/`, `~/Library/Logs/` | P3, P8 |
| Python via `py -3` | `python3` (Homebrew or Xcode CLT) | P3, P8 |
| Windows toasts | macOS notifications (need a bundle id; may not show from dev builds) | P5 |
| NSIS installer | `.app` in a `.dmg`; Gatekeeper / notarization | P8 |
| `measure.ps1` | a shell or Node twin | P2 |
| CI `windows-latest` | add `macos-latest` build job (unsigned) once M1 runs | P8 |

## 6. Corrections applied to the part plans

| Part | Correction |
|---|---|
| P2 | After D10 closed (Go/Wails only) the part was renamed "Wails setup + scaffold" and its tasks renumbered (map below). Decision record (now T08): write it; commit only when you ask. `measure.ps1`: `$ids | Get-Unique` needs sorted input → `Sort-Object -Unique`. Spike IPC names (`list_tickets`, `run_sdlc`, `decisions-changed`) are spike-only; the scaffold uses P1's catalogue. Seam file renamed `ui/host-bridge.js`. Old → new task ids: T01→T01, T02→T02, T03→T03, T04→T04, T05→T05, T06→T06, T07 (Rust + Tauri slice) removed, T08→T07 (Telex test, Wails only), T09→T08, T10→T09, T11 (loser uninstall) removed. |
| P3 | Python ≥ 3.9, not 3.10. Refresh uses `sdlc ticket-detail`, not `status-of --path`. `record` removed from the app allowlist; `init`/`onboard-*` allowed from M5 (section 3). Config folder per section 3. Its `Job Object` + `CREATE_NO_WINDOW` helpers become a shared `platform` module that P6 reuses. |
| P4 | Design system via `sync-ds.mjs` copy, not `../design-system/` links. `waitingOn` comes from P1 (H4); the `next`-parsing fallback is fixture-only and must be gone before M1. Its IPC names → P1's. |
| P5 | Host-side `waiting()`, `staleOf()`, `gate.request` dropped: consume `GateRequest` from `sdlc gate-request` (P1 H7); P5-T02/T03/T04 shrink to UI mapping + tests. `write.preview/run` → `preview_command/run_command`; `stateToken` → `ticketFingerprint`. Watcher is P3's, not "P2". NG heuristic not adopted (decision 6). Fixtures from P1's generator. |
| P6 | File watching is P3's, not P1's. Hook names `Stop`, `SessionStart`, `SessionEnd`, `PostToolUse`, `UserPromptSubmit` are standard Claude Code hook events; S0a still captures the real payloads. Run data under `%LOCALAPPDATA%\kaneki-harness-app\runs\`. Emits session states only; `gate-needed` comes from P3. Session name per section 3. |
| P7 | No `<ticketDir>/app/sessions.jsonl`: attribution reads `runs/<runId>/run.json` in app data (D1: only the harness writes ticket folders). `cost.py` will depend on Claude Code's log format: keep the defensive parse and the start-up canary. Update the design plan's "1.4M vs 20M+" figure to the measured ~29×. |
| P8 | Part references: "P4 spawn" → P6, "gaps for P3" → P4, "run policy (P5/other)" → P6, "P1 `runSdlc`" → P3 runner. Test stack per section 3 (no Vitest). Harness version = `harness.__version__` + `contractVersion` (decision 6 resolved). Config folder per section 3. |

**Duplicates removed:** fixtures (P1 owns the generator), workspace/harness/Python discovery (P3 owns the logic, P8
the first-run screens), config load/save (P3) vs schema + settings UI (P8), process-tree kill (P3 `platform`
module, reused by P6), toast triggers (P3 events, P5 UI), measurement script (P2's `measure.ps1`, reused by
P4-T19 and P8).

## 7. Top risks across the build

| Risk | Where | Mitigation |
|---|---|---|
| Session supervision does not work as hoped (hooks, permission prompts, AskUserQuestion under a PTY) | P6 | Spikes S0a–S0c before any M3 code; M0–M2 deliver value without it |
| Harness changes break the CLI users or `sdlc web` | P1 | Additive keys only, `contractVersion`, existing 72 tests + new ones, one assertion update |
| Disk on D: (14.4 GB free) | P2 | Go + Wails is about 0.5 GB under `D:\toolchains`; check free space before the install |
| Real data has no pending gate | P1, P4, P5 | Synthetic fixtures generated by the harness itself; one throwaway ticket for M2 |
| A spawned session ships, commits, pushes or deletes a branch | P6, P8 | Locked policy in code, `--disallowedTools`, an app-injected deny hook, Host never runs those commands, post-node `git log` check, tests for every deny case |
| Log-format drift in Claude Code breaks cost | P7 | Defensive parse, schema version on the cache, canary at start-up |

## 8. Next steps

1. ~~You answer section 5 **A** (1–5).~~ Done 2026-11-18 (all accepted). Open: decision 13 (macOS timing).
2. Now, in parallel and without installs: P1-T01…T06 (harness), P4-T01…T05 (UI on fixtures), P7-T01 (`cost.py`).
3. With install approval: P2-T01…T09 (Go + Wails setup, spike, decision record, scaffold). No second stack.
4. Update the design plan (`plan.md`): D11 → `kaneki-harness-app/`, §2 cost numbers, §6 link to this file.
