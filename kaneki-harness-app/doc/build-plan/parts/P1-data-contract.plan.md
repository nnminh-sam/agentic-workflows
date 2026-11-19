# P1 · Data contract and harness-side changes

Owner of the cross-part names. All paths are relative to `D:/workspace/HAISE/agentic_workspace/agentic-workflows/`
unless absolute. `H` = `kaneki-harness/`. Evidence is cited `path:line`; guesses are marked ASSUMPTION.

## 1. Goal and done-when

**Goal.** One versioned set of JSON types, IPC calls and events so UI (against fixtures) and Host (against the
harness) are built in parallel. The harness computes every derived value (waiting-on, stale preview, gate
choices); the Host and UI only transport and render. Fix N4 and add a few read-only harness commands first.

**Done when**
1. `kaneki-harness-app/contracts/` holds one JSON Schema per type plus `ipc.json` (the invoke/event catalogue), and
   every fixture in `kaneki-harness-app/fixtures/` validates against them in a Node test.
2. `sdlc contract`, `sdlc ticket-detail`, `sdlc gate-request` exist, are read-only, and their output on `TODO-1`
   and `TODO-1-2` validates against the schemas; harness tests (existing 72 + new) pass.
3. `sdlc status --accept-current-state` takes the ticket lock (N4) with a test.
4. P3/P4/P5/P6/P7 can look up every name they use in section 4 without inventing one.

## 2. Scope

**In:** type catalogue, IPC catalogue, `contractVersion` rule, harness changes (with tests), fixtures, schemas,
how Host/UI validate. **Out:** UI layout, stack choice, session spawn mechanics (only `SessionInfo`), cost maths
(only `CostSummary`), the gate dialog flow (P5 owns it; P1 supplies `GateRequest`).

## 3. Inputs / outputs

**Inputs (from other parts):** P3 tells P1 which watcher reasons exist (section 4.2 proposes them); P6 may add
`SessionInfo` fields and `RunPolicy` (additive); P7 may refine `CostSummary` (additive); P5 may ask for more
`GateRequest` fields (additive). Additive changes do not bump `contractVersion` (rule in 4.4).

**Outputs:** `contracts/*.schema.json`, `contracts/ipc.json`, `fixtures/**`, three harness commands, extended
`sdlc tickets`, the N4 fix, and the names in section 4.

## 4. Design

### 4.1 Where each type comes from (recommendation)

| Option | Cost | Coupling |
|---|---|---|
| A. Host parses `state.json`/`decisions.jsonl` itself | no Python | Host re-implements `summarize`, lane/trace, `dependents`, `next_hint` (`H/harness/board.py:17-75`, `H/harness/graph.py:128-146`); drifts from the board |
| B. **Harness emits the contract** (recommended) | ~0.5 s per Python call | none to file layout; one implementation, tested in `H/tests` |
| C. Hybrid | | |

**Recommendation: C, weighted to B.** Derived state always comes from the harness: `sdlc tickets` (list),
`sdlc ticket-detail` (one ticket), `sdlc gate-request` (one gate). The Host reads files directly for exactly three
things that need no derivation: (1) tail new lines of `decisions.jsonl` by byte offset for `decision-added`,
(2) artifact text for the inspector (`read_artifact`), (3) change detection (watcher). The ~0.5 s Python cost is
hidden by caching in the Host (P3): one call per changed ticket on a watcher event, none on re-render. A
`DecisionEvent` read from the file is the same object the harness wrote, so no derivation is duplicated.
Rejected: a persisted `status.json` written by the harness (a second source of truth that goes stale; STATUS.md
is already regenerated after every write command, `H/harness/cli.py:1203-1210`, and nobody should parse it).

Evidence that the shape already exists: `board.summarize` builds the per-ticket dict (`H/harness/board.py:17-60`);
`list_tickets` strips it to a brief plus `strip[]` (`board.py:87-102`); `sdlc tickets` prints it
(`cli.py:956-958`); real output on this machine has `ticket, kind, lane, reviewRound, ticketDir, createdAt,
updatedAt, settled, traceLength, nextPending, next, tokensTotal, questions, shipped, integrity, strip` (run
read-only against `todo-nestjs-backend`, 2 tickets, each `strip` has 12 entries).

### 4.2 Type catalogue

Conventions: JSON Schema 2020-12 (the harness validator `H/harness/schema.py:1-8` supports exactly this subset, so
the same schema files can be validated by the harness). camelCase keys (matches existing output). Timestamps are
ISO-8601 UTC strings as written by `state.now()` (`H/harness/state.py:28-29`). Paths are absolute strings as the
harness emits them; the Host normalises `\` to `/` before sending to the UI (ASSUMPTION: the harness itself emits
backslashes on Windows, seen in `ticketDir`). Every object allows unknown keys (`additionalProperties: true`) so
additive change is safe. `NEW` = not produced today.

**`TicketSummary`** (rail; item of `sdlc tickets`) — computed by `board.list_tickets` (`board.py:87-102`)
| Field | Type | Source |
|---|---|---|
| `ticket` | string | `ctx["ticket"]` `board.py:50` |
| `title` NEW | string? | first `# ` line of `ticket.md` minus the `TICKET:` prefix (real file: `# TODO-1-2: Foundation: config, ...`) |
| `kind` | `bug`\|`feature` | `ctx.kind` |
| `lane` | `full`\|`lite` (string) | `graph.lane_of` `graph.py:66` |
| `reviewRound` | int | `ctx.reviewRound` |
| `ticketDir`, `createdAt`, `updatedAt` | string | `board.py:51-52` |
| `settled`, `traceLength` | int | `board.py:53` |
| `nextPending` | string? | `state.next_pending` `state.py:189` |
| `next` | string | `board.next_hint` `board.py:63-75` (display text only; the UI must not parse it) |
| `tokensTotal` | int | harness count (sum of current-generation unit tokens) |
| `questions` | `{path, exists}` | `board.py:57` |
| `shipped` | `{repoId: {commits[], branch, pr?}}` | `state.shipped` |
| `integrity` | string[]? | `verify_log` problems `state.py:79-91` |
| `strip[]` | `StripNode[]` | `{node, status, verdict}` today (`board.py:99`); NEW `generation`, `boundary`, `waiting` |
| `waitingOn` NEW | `user`\|`agent`\|`none` | see `WaitingOn` below |
| `waitingNode`, `waitingReason` NEW | string? / enum? | same |
| `lastEventAt`, `lastEventKind` NEW | string? | last non-hidden event |
| `error` | string? | set instead of everything but `ticket` when `Session()` raises (`board.py:81-83`): integrity, bad JSON, missing profile |
| `contractVersion` NEW | int | wrapper (4.4) |

`StripNode.status` is `pending|in_progress|stale|done` (`state.schema.json:130-137`); `verdict` is
`approved|rejected|stopped|needs-changes|null` (`state.py:182`, `cli.py:649`).

**`WaitingOn`** (computed once, in the harness, `board.waiting(s)`; P5 consumes it, do not recompute elsewhere).
For the next pending node N with spec `S`, rec `R`:
1. `integrity` problems → `none`, reason `integrity` (UI shows an error state, not a gate).
2. N's planned units (workers + reviewer) are not all `ok` at the current generation → `agent`
   (`reason: needs-run` when `pending|stale`, `running` when `in_progress`); from `next_hint` logic `board.py:68-75`.
3. All units recorded and `S.boundary == "user"` (and not the plan-auto-skip case, `cli.py:362`, `cli.py:603`) →
   `user`, reason `user-boundary`.
4. All units recorded, last reviewer verdict `FAIL` → `user`, reason `reviewer-fail` (the gate refuses `approved`,
   `cli.py:578-581`; the choices are needs-changes/rejected).
5. `R.attempts + 1 >= loopCap` for a node with `loopBackTo` and the last verdict is NEEDS CHANGES → `user`, reason
   `loop-cap` (G4 requires `--actor user`, `cli.py:559-565`).
6. `R.verdict == "rejected"` and status not `done` → `user`, reason `rejected-open` (the user rejected and has not
   yet invalidated; today's TODO-1-2 history shows this, `decisions.jsonl` gates 9 and 12).
7. Units all recorded, controller boundary → `agent`, reason `awaiting-gate` (controller closes it).
8. `nextPending == null` → `none`, reason `done` or `shipped` (`board.py:64-65`).
Gaps the harness cannot see: a session blocked on a permission prompt or a G1/G5/G6 question asked in chat —
that is P6 (Q1) and shows as a session state, not as `waitingOn`. Stated here so the top-bar count is defined:
"gates that need you" = tickets with `waitingOn == "user"`.

**`TicketDetail`** (`sdlc ticket-detail`; extends `board.summarize`, `board.py:17-60`) = `TicketSummary` minus
`strip` plus:
`nodes: NodeCard[]`, `repositories[{id,path,worktree,base}]` (`board.py:55-56`), `receipts`, `events:
DecisionEvent[]` (last 40, hidden `state-write` removed, `board.py:13,46`), `loopBacks: LoopBack[]` NEW,
`trace: string[]` NEW (order, for the strip), `loopCap` NEW (`profile.gates.loopCap`, default 2, `cli.py:555`),
`generationMax` NEW.
`LoopBack` = `{at, node, stale[], reason, byGateVerdict?: "needs-changes"|"rejected"}`: built from `invalidate`
events (`node, stale, reason` required, `decision-event.schema.json:58-71`; real example: reopen `coding`,
stale 6 nodes) so the strip can draw the loop-back line and the `g2` badge; the cause node is only in free text.

**`NodeCard`** (item of `TicketDetail.nodes`) — extends the dict at `board.py:35-43`
| Field | Source |
|---|---|
| `node, layer, purpose, status, generation, verdict, attempts, boundary, executor, units[], unitsDone[], lastUnitVerdict, models[], tokens, closedAt, artifacts[{name,path,exists}]` | existing, unchanged |
| `tier` per unit, `unitRecords: UnitRecord[]` NEW | `state.nodes[n].units[]` at current generation: `{unit, generation, model, tier, status, tokens?, verdict?, failureClass?, at, result, resultDigest}` (`cli.py:534-536`, real record seen) |
| `agents: AgentRow[]` NEW | `usage.node_usage(rec)["agents"]` `usage.py:46-54` → `{unit, model, rounds, tokens?, unreported}` |
| `tokensAllGenerations`, `tokensEarlier` NEW | `usage.py:51-54` |
| `plannedUnits[{id, role, tier, model}]` NEW | `Session.units` + `tier()` incl. escalation `cli.py:100-131` |
| `loopBackTo`, `reopens`, `gates[]` NEW | `spec.loopBackTo`, `spec.reopens`, `spec.gates` (graph) |
| `missing[]` NEW | `missing_prerequisites` `state.py:196-208` (why a node cannot start) |
| `waiting` NEW | `WaitingOn` for this node |
| `resultFiles[]` NEW | `results/<node>.g<gen>.<unit>.r<n>.md` that exist (real dir listing), used for reviewer text |

**`GateRequest`** (`sdlc gate-request --ticket T [--node N]`; default N = `nextPending`) — NEW, what P5's dialog needs:
```
{ contractVersion, ticket, node, generation, attempts, loopCap, boundary, gates[],       // spec.gates (G0..G7)
  question: string,                  // from boundary: "Approve plan?" / "Proceed, re-run or stop?" (graph description of the G-code)
  waitingReason: enum,               // WaitingOn.reason
  readiness: {ok: bool, problems[]}, // dry run of the checks cmd_gate does before closing (artifacts present/rewritten,
                                     //   units ok, reviewer not FAIL, rule/check parsers, citations, language) -> reuse cli.py:552-606
  reviewer: {unit, model, tier, verdict, failureClass?, at, resultFile?} | null,
  units: UnitRecord[],               // current generation
  evidence: [{name, path, exists, source:"artifact"|"result"|"input", headings[]}],  // artifacts + reviewer result + node_inputs (cli.py:694-707)
  ngItems: [{id?, text, file, line?}] // best effort from the reviewer result / checks (ASSUMPTION, see risk R3); [] when none
  choices: Choice[] }
Choice = { id: "approve"|"needs-changes"|"reject"|"stop"|"reopen",
           label, allowed: bool, whyNot?: string,
           argv: string[],             // exact args AFTER `python sdlc.py`, e.g. ["gate","--ticket","T","--node","N","--status","approved","--actor","user"]
           followUp?: string[][],      // e.g. [["invalidate","--ticket","T","--node","coding","--reason","<required>"]]
           reopenTargets: [{node, staleNodes[]}],  // valid --reopen / invalidate targets with their stale preview
           staleIfChosen: string[],    // nodes that go stale (see below)
           needsNote: bool, needsReason: bool }
```
`staleIfChosen` is computed with `graph.dependents` (`graph.py:135-146`) then filtered exactly as `_invalidate` does:
a node that is `pending`, generation 1, with no units is skipped (`cli.py:765-776`). `needs-changes` target =
`spec.loopBackTo` (`cli.py:559-571`); `approved` on a `reopens` node (triage-review) needs `--reopen <node>|none`
(`cli.py:607-609`, `645-647`); `rejected`/`stopped` change only the verdict (`cli.py:648-650`) so a "reject and
reopen X" choice is `followUp: invalidate --reason` (the mock's bug: no `--reason`, plan section 4). The
`argv` the harness returns is a hint; P5/P3 still build argv from the allowlisted `CommandSpec` and compare.

**`DecisionEvent`** — exactly `H/contracts/decision-event.schema.json` (enum of 15 events, `prev` hash, per-event
required fields, lines 4-165). Copied by reference, not duplicated: `contracts/DecisionEvent.schema.json` uses
`$ref` to a vendored copy synced by a test (T09). Real counts in TODO-1-2: `state-write` 49, `record` 27,
`gate` 18, `git` 8, `invalidate` 2, `init` 1, `worktree` 1. UI hides `state-write` (`board.py:13`).
App adds only a wrapper `{ticket, index, event}` for the `decision-added` event; `index` = zero-based line number.

**`CommandPreview` / `CommandResult`** (P5 owns semantics; P3 implements `run`)
```
CommandPreview { previewId, kind: "gate"|"invalidate"|"lane"|"init"|"worktree"|"read",
  argv: string[],            // after python sdlc.py
  display: string,           // shell-quoted for the copy button
  cwd, ticket?, writes: bool, expectedEvents: string[],   // e.g. ["gate"]
  staleIfRun: string[], ticketFingerprint: string,        // sha256 of last decisions.jsonl line: re-validate before run
  expiresAt }
CommandResult { previewId, ok, exitCode, durationMs, stdout: object|null, stderr: string, error: AppError|null, startedAt }
```
`stdout` is the single JSON line the harness prints (`cli.py:31-32`); errors are `{"error": "..."}`, rc 2
(`cli.py:1197-1199`).

**`AppError`** `{code, message, source: "harness"|"host"|"ui", retryable: bool, detail?, command?, exitCode?,
stderr?, ticket?}`. `code` enum and the mapping from harness text (prefixes are in the messages: `G7:`, `G4:`,
`G5:`, `G0:`):
`harness-gate-G0..G7` (message starts `Gn:`), `ticket-locked` (`locked by another session`, `state.py:166`, retryable),
`integrity` (`ticket integrity check failed`, `state.py:124`), `ticket-not-initialised` (`state.py:107`),
`profile-invalid`, `harness-not-found` (`sdlc.py:29-32`), `python-not-found`, `contract-mismatch`,
`command-not-allowed`, `timeout`, `cancelled`, `invalid-args`, `io`, `unknown`. Mapping lives in the Host (P3) and is
unit-tested with the strings above; the harness is not changed for it.

**`SessionInfo`** (P6 owns; names fixed here)
`{sessionId (app uuid), name (the -n value), ticket, node?, pid?, state: "spawning"|"running"|"waiting-permission"|
"idle"|"node-done"|"stopped"|"failed"|"exited", startedAt, lastActivityAt, endedAt?, exitCode?, cwd, mode:
"node"|"run", policyId?, claudeSessionId?, tag (attribution, P7), transcriptPath?, outputTail?: string[], reason?}`.

**`RunPolicy`** (P6) `{ticket, mode: "per-node"|"per-run", autoContinue: bool, stopAtUserGate: true, stopOnReject:
true, stopAtNode: "deliverable-ready", neverShip: true (const), updatedAt}`; stored per ticket (P6 decides where).

**`CostSummary`** (P7 owns maths; shape only)
`{range:{from,to}, generatedAt, pricesVersion, pricesVerified: bool, totals: Usage, byTicket[], byNode[], byModel[],
byDay[], harnessTokens?: int, flags: WasteFlag[]}` with `Usage = {input, output, cacheWrite, cacheRead, usd?}` and
`WasteFlag = {kind: "driver-context"|"agent-start-context"|"loop"|"cache-miss", ticket?, node?, severity, evidence}`.

**`WorkspaceInfo`** `{profilePath, project:{id,name}, ticketRoot, repositories[{id,path,stack}], harness:{home,
python, version, contractVersion}, editor?: string}`. **`ArtifactText`** `{ticket, name, path, text, truncated: bool,
bytes, exists}` (cap 512 KB like `web.py:26,100`).

### 4.3 IPC catalogue

Naming: invoke names are `snake_case` (in Wails the Go method is the PascalCase of
the same name and the one `host-bridge.js` adapter maps it — name-for-name, so UI code never changes). Event names are
`kebab-case`. `contracts/ipc.json` is the machine-readable form of these tables (`{name, kind, args, result|payload,
owner}`); a Node test fails if code uses a name that is not in it.

**Invoke** (`invoke(name, args) -> result`; failure = rejected promise carrying `AppError`)

| Name | Args | Result | Implemented by |
|---|---|---|---|
| `host_info` | `{}` | `{appVersion, contractVersion, harness:{ok,home?,version?,contractVersion?}, workspace: WorkspaceInfo\|null}` | P3 |
| `workspace_open` | `{profilePath}` | `WorkspaceInfo` | P3 (picker UI P8) |
| `workspace_recent` | `{}` | `string[]` | P3 |
| `list_tickets` | `{refresh?: bool}` | `TicketSummary[]` (cache; `refresh` forces `sdlc tickets`) | P3 |
| `get_ticket` | `{ticket, refresh?}` | `TicketDetail` | P3 |
| `get_decisions` | `{ticket, before?: int, limit?: int=100}` | `{events: {index,event}[], total}` | P3 (file read) |
| `read_artifact` | `{ticket, name, maxBytes?}` | `ArtifactText` (name checked against the ticket dir like `web.py:56-61`) | P3 |
| `open_in_editor` | `{path, line?}` | `{ok, opened}` (Zed; path must be under ticket root or a repo, `web.py:63-67`) | P3 |
| `get_gate_request` | `{ticket, node?}` | `GateRequest` | P3 runs `gate-request`; P5 consumes |
| `preview_command` | `{kind, ticket?, params}` | `CommandPreview` | P3 builds argv from allowlist; P5 UI |
| `run_command` | `{previewId, confirm: true}` | `CommandResult` (also emits `command-finished`) | P3; P5 UI |
| `cancel_command` | `{previewId}` | `{cancelled}` | P3 |
| `get_settings` / `set_settings` | `{}` / `{patch}` | `Settings` | P3 store, P8 schema |
| `policy_get` / `policy_set` | `{ticket}` / `{ticket, policy}` | `RunPolicy` | P6 |
| `session_list` | `{}` | `SessionInfo[]` | P6 |
| `session_start` | `{ticket, node?, mode}` | `SessionInfo` | P6 |
| `session_stop` | `{sessionId}` | `SessionInfo` | P6 |
| `session_open_terminal` | `{sessionId}` | `{ok}` | P6 |
| `cost_summary` | `{from, to, ticket?, compare?: string}` | `CostSummary` | P7 |
| `cost_today` | `{}` | `{usd, tokens, pricesVerified}` | P7 |
| `worktree_list`, `new_ticket_preview` | reserved | | P8 |

`preview_command` + `run_command{previewId}` (not "run these args") is deliberate: the argv the user saw is the
argv that runs (D2), the Host stores it keyed by `previewId`, and a stale `ticketFingerprint` makes `run_command`
fail with `AppError{code:"stale-preview"}` (add to the code enum) so P5 can re-validate.

**Events** (`on(name, payload)`)

| Name | Payload | Emitted by |
|---|---|---|
| `workspace-changed` | `WorkspaceInfo\|null` | P3 |
| `tickets-reloaded` | `TicketSummary[]` (startup, watcher overflow, new/removed ticket dir) | P3 |
| `ticket-changed` | `{ticket, summary: TicketSummary, reason: "decisions"\|"state"\|"rescan"}` (detail is pulled via `get_ticket`) | P3 |
| `ticket-removed` | `{ticket}` | P3 |
| `decision-added` | `{ticket, index, event: DecisionEvent}` | P3 (tail by offset) |
| `gate-needed` | `{ticket, node, reason}` — fired on the transition into `waitingOn=user`, deduped by `ticket+node+generation+reason` | P3 (transition) / P5 (toast) |
| `reviewer-failed` | `{ticket, node, generation, verdict}` (a `record` event with reviewer `FAIL`) | P3 / P5 |
| `node-reopened` | `{ticket, node, stale[]}` (an `invalidate` event or needs-changes gate) | P3 / P5 |
| `integrity-error` | `{ticket, problems[]}` | P3 |
| `command-finished` | `CommandResult` | P3 |
| `session-started` / `session-state` / `session-exited` | `SessionInfo` | P6 |
| `cost-updated` | `{usdToday, tokensToday}` | P7 |
| `host-error` | `AppError` | P3 |

### 4.4 Versioning rule

`contractVersion` is an integer (start `1`), declared once in `H/harness/contract.py` and in
`contracts/ipc.json`. Every harness JSON that the app consumes (`tickets`, `ticket-detail`, `gate-request`,
`contract`) carries `"contractVersion": N`. **Additive** (new optional field, new event, new invoke, new enum value
consumers must tolerate) = no bump; consumers ignore unknown keys and treat unknown enum values as "other".
**Breaking** (remove/rename a field, change a type or meaning) = bump. At startup the Host calls `sdlc contract`;
if `harness.contractVersion != app.contractVersion` it emits `host-error{code:"contract-mismatch"}` and the UI
shows the setup/repair state (P8) instead of guessing. The app also records the harness `__version__`
(`H/harness/__init__.py:3`) for the About screen.

### 4.5 Harness changes (small, stdlib, tests in `H/tests/`)

| # | Change | Where | Output / behaviour |
|---|---|---|---|
| H1 | **N4 fix**: `status --accept-current-state` takes the lock | `cli.py:1167-1181`: `READ_ONLY` contains `status` so `_ticket_lock` returns a null context (`cli.py:1175`); make the null-context condition `args.cmd in READ_ONLY and not (args.cmd == "status" and args.accept_current_state)`. The write path is `Ticket.accept_current` `state.py:128-132` | held lock → `{"error":"G7: ticket is locked by another session ..."}` rc 2. Do it before the app ever calls it; the app still does not expose the action in v1 (D6/P5 decide) |
| H2 | `contract.py` + `sdlc contract` | new module, parser near `cli.py:1160`, add to `READ_ONLY` (`cli.py:1167`) | `{contractVersion, harnessVersion, commands:[names]}` |
| H3 | `board.summarize` extensions (additive keys listed in 4.2) | `board.py:17-60` | `ticket-detail` = `summarize` + `contractVersion`; node fields in `NodeCard` |
| H4 | `board.waiting(s)` | new function beside `next_hint` `board.py:63` | `{on, node, reason}`; unit-tested per reason (8 cases) |
| H5 | `list_tickets` strip adds `generation, boundary, waiting`; `tickets` adds `title`, `waitingOn`, `waitingNode`, `waitingReason`, `lastEventAt`, `contractVersion` | `board.py:98-100`, `cli.py:956-958` | **must update the existing assertion** `H/tests/test_lanes_and_board.py:144` (it compares `strip[0]` to an exact 3-key dict) |
| H6 | `sdlc ticket-detail --ticket T [--events N]` | new `cmd_ticket_detail`; parser after `status-of` `cli.py:1146-1148`; add to `READ_ONLY` | `TicketDetail` (replaces the use of `status-of --path`, `cli.py:961-983`, which needs a file path) |
| H7 | `sdlc gate-request --ticket T [--node N]` | new `cmd_gate_request` factored from `cmd_gate` checks (`cli.py:552-606`) into a shared `gate_readiness(s,node,status)` so the dry run cannot drift from the real gate; add to `READ_ONLY` | `GateRequest`; **never writes**, never calls `s.save` or `ticket.log` |
| H8 | `state.schema.json` documents `shipped`, `context.repositories[].startHead`, unit `tier` (N5) | `contracts/state.schema.json:119-180` | additive; lets fixtures of real tickets validate strictly |
| H9 (optional) | `sdlc tickets --detail` returns summary + detail for all tickets in one process | | saves N Python startups at launch; defer unless startup > 2 s with real ticket counts |

`sdlc ticket-detail` and `gate-request` on a ticket that fails integrity return `{ticket, error}` and rc 0 for
the list (as `board.summarize_ticket` does, `board.py:81-83`) but rc 2 `{"error"}` for the single-ticket
commands (same as every other command, `cli.py:1197-1199`); the Host maps both to `AppError{code:"integrity"}`.

**N1-N3 impact on the app:** N1 (`gates.escalateAfter` dead, `cli.py:127`) none; the app shows tier/model from
`plannedUnits` (harness-computed), never from profile. N2 (lite says "no deep tier" but `triage-review` is deep,
`lifecycle.json:658-660` and lane order `:50-59`): the UI must not print "no deep tier" for lite; show real tiers.
N3 (lite cannot re-issue receipts after TTL): the app offers no "reissue receipts" action; `receipts` is not on
the allowlist. N5 is H8. N6 docs only. N4 is H1.

### 4.6 Fixtures (read-only capture + synthetic)

Layout: `kaneki-harness-app/fixtures/`
```
real/TODO-1/{tickets.item.json, ticket-detail.json, gate-request.<node>.json, decisions.jsonl}
real/TODO-1-2/{...same}              # captured, never edited by hand
real/tickets.json                    # whole `sdlc tickets` output
synthetic/<scenario>/{workspace/ (profile + .sdlc/tickets/..), expected/*.json}
```
**Capture (read-only):** `scripts/capture_fixtures.py` runs, with cwd = the real workspace, only
`sdlc tickets`, `sdlc ticket-detail`, `sdlc gate-request` (all READ_ONLY) and copies `decisions.jsonl`; it
writes only under `fixtures/real/`, scrubs absolute paths to `{WORKSPACE}` and refuses any argv not on a
read-only list. Until H6/H7 land, the P4 team can use today's `sdlc tickets` and `board.summarize` output
(already real) to start. Note the real workspace has 2 tickets, both fully done (TODO-1 shipped,
TODO-1-2 `deliverable-ready`), so **no real fixture shows a pending gate**.

**Synthetic scenarios** are generated, not hand-written JSON: `scripts/make_synthetic.py` builds a throwaway
workspace in a temp dir with the harness's own test helpers (`Base`, `run`, `write_artifacts` in
`H/tests/test_lifecycle.py:23-75`) and drives real `sdlc` commands to reach each state, then captures the output.
Because the harness is the generator, the fixtures are valid by construction and break when the harness changes.

| Scenario | How it is reached | Expected |
|---|---|---|
| `gate-user-pending` | feature ticket, finish `feature-plan` units, stop | `waitingOn=user`, reason `user-boundary`, choices approve/needs-changes? (none: no loopBackTo) /reject/stop |
| `reviewer-fail` | record `reviewer` with `--verdict FAIL` on `requirement-analysis` | reason `reviewer-fail`, `approve.allowed=false` |
| `loopback-g2` | `code-review` needs-changes then rerun `coding` | `coding.generation=2`, `loopBacks[0]`, strip `generation:2` |
| `loop-cap` | needs-changes twice with `loopCap=2` | reason `loop-cap`, argv includes `--actor user` |
| `rejected-open` | `gate --status rejected --actor user` without invalidate | reason `rejected-open` |
| `lite-lane` | `init --lane lite` | trace `scope,change,check,...`; inline nodes |
| `bug-kind` | `init --kind bug` | `investigate`, `fix-plan` in trace |
| `integrity-error` | edit a `decisions.jsonl` line (as `test_hardening.py:48-58` does) | list item `{ticket, error}`; `integrity` text |
| `bad-state-json` | truncate `state.json` | `error: "G7: state.json is not valid JSON"` (`state.py:111`) |
| `empty-workspace` | profile, no ticket dir | `tickets: []` (`board.py:92`) |
| `no-profile` | missing `sdlc.profile.json` | `{"error": ...}` rc 2 → `AppError profile-invalid` |
| `locked` | create `.lock/owner.json` fresh (`test_hardening.py:59-67`) | write commands → `ticket-locked` |
| `review-round` | `handback` path (after-PR nodes) | `reviewRound=1`, reopens node `triage-review` (needs `--reopen`) |

Fixtures are for UI/Host unit tests, never touch the real workspace, and are regenerated by one command
(`npm run fixtures`); the diff is reviewed when the harness changes.

### 4.7 Schema files and how consumers use them

`kaneki-harness-app/contracts/`: `TicketSummary`, `TicketDetail`, `NodeCard`, `UnitRecord`, `GateRequest`,
`Choice`, `DecisionEvent` (ref to harness copy), `CommandPreview`, `CommandResult`, `AppError`, `SessionInfo`,
`RunPolicy`, `CostSummary`, `WorkspaceInfo`, `ArtifactText`, `Settings` (P8) — `*.schema.json`, `$id:
"kaneki-app/<Type>@1"`, plus `ipc.json` and `README.md` (the version rule).
- **Source of truth = the schema files.** Harness: the same files validate `sdlc` output in tests via
  `harness.schema.validate` (`schema.py`, no new dependency).
- **UI (JS):** dev-only. `ajv` (ASSUMPTION: a Node dev dependency is acceptable) validates every fixture in `npm test`;
  `json-schema-to-typescript` or a small generator emits `contracts/types.d.ts` + JSDoc for editor hints; no
  validation at runtime in release (the Host already validated).
- **Host:** Go (Wails): structs with `json` tags and `encoding/json` (unknown fields tolerated when reading harness output); `go test` loads every fixture and
  deserialises it; schema validation with a Go JSON Schema package (ASSUMPTION: package name). At runtime the Host validates only
  `contractVersion` (cheap) and logs, not rejects, on shape mismatch.
- A `contracts/ipc.json` linter checks every invoke/event has args/payload schemas that resolve.

## 5. Tasks

| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P1-T01 | H1: N4 lock fix + test | S | | new test: held lock blocks `--accept-current-state`; `test_hardening.py:45` still passes |
| P1-T02 | H2: `contract.py`, `sdlc contract` | S | | prints version; test |
| P1-T03 | H4: `board.waiting()` with 8 reasons | M | | one unit test per reason on throwaway tickets |
| P1-T04 | H3+H5: extend `summarize`/`list_tickets` (additive) and update `test_lanes_and_board.py:144` | M | T03 | old tests green, new keys asserted |
| P1-T05 | H6: `sdlc ticket-detail` | S | T04 | output validates against schema on both real tickets |
| P1-T06 | H7: `gate_readiness` extraction + `sdlc gate-request` | L | T03 | dry run agrees with `cmd_gate` on every error case in a test matrix (same messages); writes nothing (digest of ticket dir unchanged) |
| P1-T07 | H8: update `state.schema.json` (N5) | S | | real states validate |
| P1-T08 | Write all schema files + `ipc.json` + README | L | T04, T06 | each type has a schema; linter passes |
| P1-T09 | Vendored `DecisionEvent` sync test | S | T08 | test fails when the harness schema differs |
| P1-T10 | `capture_fixtures.py` (read-only) + real fixtures | S | T05, T06 | fixtures/real committed, scrubbed |
| P1-T11 | `make_synthetic.py` + 13 scenarios | L | T06 | each scenario's expected JSON matches |
| P1-T12 | Node fixture-validation test (`npm test`) | S | T08, T10 | all fixtures pass; wrong fixture fails |
| P1-T13 | Types generation (`types.d.ts`) | S | T08 | UI imports it |
| P1-T14 | Hand the catalogue to P3/P4/P5/P6/P7; agree additive-change process | S | T08 | each part lists names used = subset of `ipc.json` |

Order: T01, T02 first (unblock everything), then T03-T06; T08 can start from section 4.2 immediately in parallel.

## 6. Tests / verification

- **Harness unit** (`python -m unittest discover -s kaneki-harness/tests`, currently 72 tests per the review guide):
  waiting reasons; lock fix; `gate-request` equals `gate` errors; read-only commands leave `decisions.jsonl` and
  `state.json` byte-identical (hash before/after).
- **Contract:** every real and synthetic fixture validates against the schemas (Node + Python); a deliberate break
  (rename a field) fails the build.
- **Real, read-only:** `sdlc tickets`, `ticket-detail --ticket TODO-1-2`, `gate-request --ticket TODO-1-2 --node
  deliverable-ready` run against `todo-nestjs-backend`; compare with `STATUS.md` (settled 12/12, tokens 1112295,
  `coding` gen 2, `execute-and-evidence` gen 3, as seen) and with `sdlc web` `/api/ticket`.
- **Parity (M1):** a script diffs `ticket-detail` against `board.summarize_ticket` for the shared keys (must be equal).
- **Timing:** record the cost of `tickets` with 2 and with ~30 synthetic tickets to decide on H9.

## 7. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Harness change breaks `sdlc web`/existing tests (`strip` shape) | additive keys only; fix `test_lanes_and_board.py:144`; web page ignores unknown keys (ASSUMPTION: check `web_page.html` before merging) |
| R2 | `gate-request` readiness drifts from `cmd_gate` | single shared function (T06), matrix test |
| R3 | NG list / reviewer findings are free Markdown (`Verdict: **FAIL**`, numbered "Blocking findings"), no structure | `ngItems` best effort: parse the `## Blocking findings` numbered list; fall back to `[]` plus the result file for the inspector. Longer term: have the reviewer packet ask for a `## NG` table (harness skill change, out of P1) |
| R4 | 0.5 s per Python call, N tickets | cache in Host; `ticket-detail` only for the selected/changed ticket; H9 if needed |
| R5 | Windows backslashes and cp1252 | Host normalises paths; the CLI already forces UTF-8 stdout (`cli.py:1185-1190`) |
| R6 | A write command is running while the watcher fires → half-written view | Host debounces and re-reads after `command-finished`; state is written atomically via tmp+replace (`state.py:134-146`) |
| R7 | Contract grows ad hoc as P3-P7 ask | additive rule + `ipc.json` linter + single owner (this part) |
| R8 | `waitingOn` misses chat-side questions (G1/G5/G6) | documented gap; P6 session state covers it |

## 8. Decisions needed from the user

1. **Harness emits the contract** (new read-only `ticket-detail`, `gate-request`, extended `tickets`) rather than the
   Host re-deriving it from files. Recommend yes: touches `kaneki-harness` (a separate repo area) but only additive.
2. **`contractVersion` as an integer, additive-without-bump** rule. Recommend yes.
3. **Naming:** snake_case invokes, kebab-case events, camelCase JSON. Recommend yes (matches existing harness output).
4. **Ticket `title`** taken from the first `#` line of `ticket.md` (the harness has no title field). Recommend yes;
   alternative is to add `--title` to `sdlc init` later.
5. **Fixtures committed to git** (scrubbed real captures + generated synthetics). Recommend yes; real captures
   contain only ticket text from your test project, review before commit.
6. **Fix N4 now** and also do N5 (state schema). Recommend yes, both small.
7. **Node dev dependency (`ajv`) allowed** for contract tests. Recommend yes (dev-only, not shipped).
8. Whether v1 exposes `--accept-current-state` at all. Recommend no (integrity failure shows as an error with the
   exact command to run by hand), but H1 is done anyway.

## 9. Evidence

- `kaneki-harness-app/doc/build-plan/specs/_context.md`, `P1-data-contract.md`, and specs P3-P8 (names each will consume).
- `kaneki-harness/docs/desktop-app/plan.md` 1-129 (D1-D11, interfaces section 5, Q5).
- `kaneki-harness/harness/board.py:13-170`; `cli.py:25-152` (Session), `:276-327` (lane, status), `:337-375` (plan),
  `:520-657` (record, gate), `:665-707`, `:746-790` (artifact checks, node inputs, `_invalidate`, `invalidate`),
  `:956-983`, `:1008-1164` (parsers), `:1167-1210` (READ_ONLY, lock, main, status refresh).
- `kaneki-harness/harness/state.py:24-286`, `graph.py:63-146`, `usage.py:14-70`, `web.py:26-145`, `schema.py:1-60`,
  `__init__.py:3`, `checks.py:14,97-106`.
- `kaneki-harness/contracts/decision-event.schema.json:1-165`, `state.schema.json:1-194`;
  `graph/lifecycle.json:23-66` (order, lanes), `:317-409` (coding, code-review loopBackTo), `:638-677` (triage-review reopens), `:822-830` (gates).
- `kaneki-harness/tests/test_lifecycle.py:23-75`, `test_hardening.py:40-69`, `test_lanes_and_board.py:77,141-144`.
- `kaneki-harness/docs/harness-guide.html:1393-1398` (N1-N6).
- Real data, read-only (`sdlc tickets`; `plan --ticket TODO-1-2` returned `{"done": true}`): `todo-nestjs-backend`
  `.sdlc/tickets/TODO-1-2/{state.json, decisions.jsonl (event counts, 2 invalidates, rejected gates), STATUS.md,
  ticket.md line 1, results/ listing}`; `sdlc.profile.json` (`gates.loopCap 2`, tier models).
- Not verified: `web_page.html` consumption of `strip`; any Windows backslash behaviour beyond `ticketDir`;
  whether `jsonschema` is installed for the harness (the bundled validator is the fallback, `schema.py:1-8`).
