# P5 · Gate approval and other write actions (M2)

Paths are relative to `D:/workspace/HAISE/wp-agentic-workflows/agentic-workflows/` unless noted. `H/` = `harness/kaneki-harness/harness/`.
P1 owns the final shapes of `GateRequest`, `CommandPreview`, `CommandResult`, `AppError`; the shapes below are P5's
proposal and must be reconciled with P1.

## 1. Goal and Done when
**Goal.** Let the user decide a gate with its evidence beside it, see the exact `sdlc` argv before it runs (D2), and
have the result land in `decisions.jsonl` with `actor: user`. The same preview/confirm path serves `invalidate` and
`lane --set full`.

**Done when**
- On a throwaway ticket: approve a user-boundary gate, and reject one with a reopen; `decisions.jsonl` has a `gate`
  line with `actor:"user"` (and an `invalidate` line for the reject), the hash chain still verifies, and the UI
  refreshes without a manual reload.
- Every write shows a copyable command, needs an explicit confirm, and cannot be double-submitted.
- Each harness error (section 4.4) shows as a readable message in the dialog; nothing is swallowed.
- A ticket that changed while the dialog was open is detected and the user must re-confirm.
- Top-bar "N gates need you" count and the 3 toasts fire from Host events and dedupe.

## 2. Scope
**In:** gate dialog (approve / needs-changes / reject-and-reopen / stop), reopen target picker, note field,
`CommandPreview`, stale-node preview, `invalidate` dialog, `lane --set full --reason` dialog, "waiting on user"
detection, toasts for gate / reviewer FAIL / node reopened, safety (confirm, lock, re-validate), test plan.
**Out:** running nodes or sessions (P6), new ticket/onboarding (P8), ship/commit/push (never, D6), `record`,
`handback`, `receipts --reissue`, `status --accept-current-state` (listed as error recovery text only).

## 3. Inputs / outputs
**Inputs**
- P1: ticket reader (`TicketSummary` with nodes, `boundary`, `unitsDone`, `lastUnitVerdict`, `attempts`, `artifacts[]`,
  events; same fields as `H/board.py:23-47`), the graph (`graph/lifecycle.json` loaded by the Host, read-only),
  the profile `gates.loopCap` / `autoSkipPlanApproval`, and the Host `runSdlc(argv) -> CommandResult` primitive
  (spawn python, UTF-8, one JSON line, exit 0/2).
- P2 file watcher events `ticket-changed {ticket}` (state.json, decisions.jsonl).
- P3/P4 shell: rail, palette, top bar, toast host, evidence file opener (open in Zed, D9).

**Outputs (IPC, names provisional; P1 owns names)**
| Call / event | Shape |
|---|---|
| `invoke("gate.request", {ticket, node})` -> `GateRequest` | what the dialog renders (4.1) |
| `invoke("write.preview", {kind, ticket, args})` -> `CommandPreview` | `{argv[], display, steps[], stale[], warnings[], stateToken}` |
| `invoke("write.run", {previewId, stateToken})` -> `CommandResult` | `{ok, steps:[{argv, exit, json}], error?: AppError}` |
| `invoke("gates.waiting")` -> `GateRequest[]` (summary rows) | feeds top-bar count and palette "Review gate" |
| event `gates-changed {count, items[]}` | after any ticket change |
| event `toast {kind: gate-needs-you\|reviewer-fail\|node-reopened, ticket, node, generation}` | to the toast host |
| UI component `CommandPreview` (shared by every write dialog) | P4 hosts it, P5 owns it |

## 4. Design

### 4.1 Exact CLI semantics (what the harness actually does)
Parsers: `gate` `H/cli.py:1062-1069`, `invalidate` `1071-1074`, `lane` `1028-1031`. `main` prints errors as
`{"error": "<text>"}` and exits 2 (`H/cli.py:1190-1192`); `HarnessError` has no code field (`H/errors.py:1-2`).
All writers hold a per-ticket `mkdir` lock (`H/cli.py:1170-1180`, `H/state.py:150-175`) and refresh `STATUS.md` after.

**`sdlc gate --ticket T --node N --status {approved|needs-changes|rejected|stopped} [--gate G0] [--actor controller|user] [--reopen NODE|none] [--note TEXT]`** (`H/cli.py:545-657`)
| Status | Behaviour | Mutates |
|---|---|---|
| `approved` | needs every planned unit `ok` at current generation, last reviewer not FAIL, artifacts exist and rewritten, `checks` pass, citations OK, language OK, boundary `user` needs `--actor user` (`:586-605`). Sets `status=done, verdict=approved, closedAt`. | node closes |
| `approved` on `triage-review` | `--reopen <node>` or `--reopen none` is mandatory (`:606-609`); a node reopens via `_invalidate` (`:645-647`). Only node whose spec has `"reopens": true` (`graph/lifecycle.json:668`). | closes + stales |
| `needs-changes` | only nodes with `loopBackTo` (code-review->coding, testcase-review->testcase, quick-review->change, check->change per `graph/lifecycle.json:394,468,799`). Auto-invalidates `loopBackTo` and dependents, `attempts+1`. At `attempts >= loopCap` (default 2) and actor not user: error G4 (`:556-562`). | stales |
| `rejected` / `stopped` | sets `rec["verdict"]` only; **no node status change, no stale, no reopen** (`:648-650`). `--reopen` is ignored. | verdict only |
All four append a `gate` event `{gate, actor, node, generation, verdict, note}` (`:652-653`). `--gate` is a free label
(real data uses G0 and G5, `.../TODO-1-2/decisions.jsonl:70`).

**`sdlc invalidate --ticket T --node N --reason R`** (`H/cli.py:783-790`): `--reason` required (argparse AND
`:785-786`). Stales `N` plus later nodes in trace order that transitively consume a token `N` produces
(`H/graph.py:135-146`), bumps generation, marks `## SUPERSEDED:` into their `.md` artifacts
(`H/cli.py:750-763`), skips nodes that never ran from the generation bump but still lists them in `stale`. Output
`{stale[], kept[], nextPending}`. Valid target = any node in the ticket's trace (else "`N` is not in this ticket's trace").

**`sdlc lane --ticket T [--set LANE --reason R]`** (`H/cli.py:276-299`): no `--set` is read-only. `--set` needs
`--reason`, a lane in the graph, a different lane, and no `families` unless lane is `full`. Appends a `lane` event
`{lane, from, reason}`. Lite -> full is the only app action.

**Real reject flow = two commands.** Reject-and-reopen in the real data is `gate --status rejected --actor user`
followed one second later by `invalidate --node X --reason ...`:
`decisions.jsonl:52` then `:54` and `:70` then `:72` (TODO-1-2, read-only). **Mock bug (two parts):**
`app-mock.html:156-157` previews `gate ... --status rejected --reopen testcase`, but `--reopen` is ignored for
`rejected` (`H/cli.py:648-650`), so nothing would go stale; and the follow-up `invalidate` has no `--reason`
(already noted at `docs/desktop-app/plan.md:55`). The app must therefore model "reject and reopen" as a 2-step
`CommandPreview` (`steps[]`) and make the reason non-empty.

**Failure window between the two steps.** After step 1 the node has `verdict:"rejected"` but is not stale; if step 2
fails (lock, crash, validation) the ticket sits in a half state. Mitigation: run step 2 immediately, and on failure
show "Gate recorded; reopen did not run" with a one-click retry of step 2 only (4.6). ASSUMPTION: a rejected node
with no later invalidate is a valid state the harness tolerates (nothing in `cmd_gate` forbids it).

**Error cases and their JSON** (all `{"error": "..."}`, exit 2; the app maps by prefix/regex, ASSUMPTION that
prefixes stay stable; ask the harness owner for a `code` field later):
| Case | Message source | App mapping |
|---|---|---|
| node already closed | `already closed at generation N` `H/cli.py:550` | `stale-view`: refresh, close dialog |
| prerequisites not settled | `G7: ... is not ready; missing [...]` `:553` | `not-at-gate` |
| units not all ok | `G7: ... has no ok record for units [...]` `:575` | `not-at-gate`, show units |
| reviewer FAIL | `last reviewer verdict is FAIL (...)` `:579` | `reviewer-fail` (hides Approve) |
| artifact missing / not rewritten / `checks` fail / citations | `G7: ...` `:582-596` | `artifact-problem`, list the files |
| user boundary without `--actor user` | `G0: ... closes on the user's approval` `:605` | bug in app (always sets user) |
| loop cap | `G4: ... after N rounds (cap C); ... --actor user` `:560` | offer rerun with user |
| wrong language | `G5: ...` `:600` | show, offer "confirm text is right" (user) |
| `reopens` node without `--reopen` | `:609` | picker must be set |
| unknown/invalid node | `Session.spec` KeyError path; `invalidate` "not in this ticket's trace" `H/graph.py:138` | `bad-node` |
| lock held | `G7: ticket is locked by another session (...)` `H/state.py:166` | `locked`: show command + pid + age, Retry button |
| integrity failure | `G7: ticket integrity check failed: ...` `H/state.py:124`, hash chain `:87-88`, state digest `:119-121` | `integrity`: block all writes, show recovery text (`sdlc status --accept-current-state --reason ...` is the user's call, not the app's) |
| `--reason` missing | `:786`, `:281` (argparse error is plain text on stderr, exit 2, not JSON) | UI validation prevents it |
Non-JSON failure (argparse usage error on stderr, Python missing, traceback) -> `AppError{kind:"spawn"|"parse",stderr}`.

### 4.2 When is a gate "waiting on the user"
There is no single field. Derive in the Host from state + graph (all read-only), per ticket, for the **next pending
node** (`nextPending`, `H/state.py:189-193`) only:
1. `ready`: `missing_prerequisites == []` (same rule as `H/state.py:196-208`) and all planned units have an `ok`
   record at the current generation (`board.py:34,41` already gives `unitsDone` vs `units`).
2. Not blocked by a reviewer: `lastUnitVerdict != "FAIL"`; if FAIL the item is a **reviewer-FAIL** attention item,
   not an approvable gate (CLI refuses, `:577-579`).
3. Needs the user if ANY: (a) spec `boundary == "user"` (and not auto-skipped: `fix-plan`/`feature-plan` with
   profile `gates.autoSkipPlanApproval`, `:603-604`, `:363`); (b) `attempts >= loopCap` on a `loopBackTo` node (G4);
   (c) node already has `verdict ∈ {rejected, stopped}` and is not done (user decision half-applied);
   (d) controller-boundary node where the controller needs a user decision (see below).
User-boundary nodes in the graph (`boundary:"user"`, `graph/lifecycle.json:169,289,603,661,699,720`): `fix-plan`,
`feature-plan`, `deliverable-ready`, `triage-review`, `respond-review`, `scope` (the lite lane). The fuller list is in
the table at the end of this section.

**Gap (d), the TODO-1-2 case.** `execute-and-evidence` is `boundary:"controller"` (`lifecycle.json:506`) yet the
user rejected it with `actor:user` twice (`decisions.jsonl:52,70`), because the controller found NG test cases and
asked. That is a controller-initiated question the files do not record until the gate runs. The mock shows exactly
this ("2 NG, reviewer PASS", `app-mock.html:112-124`). Heuristic (ASSUMPTION, needs validation with the user):
treat a controller-boundary node as "needs you" when its reviewer verdict is PASS and its result document reports
NG > 0, or when node is `execute-and-evidence`/`final-review` with `G5/G6` listed in `spec.gates`
(`lifecycle.json:507-508,539-541`) and an NG count > 0. Otherwise it stays "ready to close" and does not count
toward the badge. Recommend: surface two tiers in the UI: **Needs you** (a/b/c/d) and **Ready to close**
(the controller will do it, no badge). Decision Q1 below.
`approved` also requires `--actor user` for G5 language overrides, not a separate waiting state.
`gates.waiting` count = items in tier "Needs you" across all tickets; recomputed on each `ticket-changed`.

| Node | Boundary | Special |
|---|---|---|
| fix-plan, feature-plan | user (controller if autoSkip) | `:603-604` |
| deliverable-ready | user | closes the ticket for shipping |
| triage-review | user + `reopens` | needs `--reopen` target or `none` |
| respond-review, scope | user | |
| code-review, testcase-review, quick-review | controller + `loopBackTo` | G4 at cap |
| execute-and-evidence, final-review | controller | NG-driven user decision (gap d) |

### 4.3 Evidence panel (`GateRequest`)
`GateRequest` (P5 proposal, P1 owns):
```
{ ticket, node, generation, askedAt,            // askedAt = last unit/record time, ASSUMPTION
  tier: "user-boundary"|"loop-cap"|"ng-decision"|"half-applied",
  purpose, boundary, loopBackTo?, reopens: bool, gate: "G0",
  reviewer: {verdict, failureClass, resultFile}|null,
  units: [{unit, status, verdict, tokens, model, resultFile}],
  ng: [{id, summary, class, sourceFile, line?}],   // best effort parse, may be []
  artifacts: [{name, path, exists, excerpt?}],
  options: [ {id:"approve", status:"approved", enabled, why?},
             {id:"needs-changes", enabled: !!loopBackTo, target: loopBackTo},
             {id:"reject-reopen", enabled, targets:[node...]},
             {id:"reject-only"}, {id:"stop"} ],
  stateToken, warnings: [...] }
```
- Evidence comes from files: reviewer result `results/<node>.g<N>.reviewer.r<R>.md` (path is in the unit record's
  `result` field, real example `state.json` units with `verdict:"PASS"`), node artifacts (`board.py:30-33`),
  token usage, `STATUS.md`. NG list: parse the evidence/testcase-review markdown for NG rows; ASSUMPTION about row
  format, so show the raw excerpt plus "Open in Zed" (D9) when parsing yields nothing. Never block the dialog on parse
  failure.
- Reviewer FAIL: `approve` disabled with reason (CLI would refuse). Controller-boundary G4: `needs-changes` offered
  only as "actor user" after cap.
- Options available per node: `approve` always; `needs-changes` only when `loopBackTo`; `reject-reopen` targets =
  nodes of the trace at or before the current node in `order` that have been settled or are the producer of a token
  the current node requires (earliest invalidated node; stale list shown). `triage-review` additionally shows the
  mandatory `--reopen` picker (`none` default is NOT preselected; user must choose, matching `:606-609`).

### 4.4 Dialog flow and preview
1. Open from rail pill, palette ("Review gate"), toast click, or top-bar count. All call `gate.request`.
2. Layout reuses the mock (`app-mock.html:115-140`): evidence strip, NG table, choices, note, command box, footer
   "Logged as actor: user". Add: reopen target `<select>` (only valid targets), stale chips, warnings area, reason
   field for reject-and-reopen (required, prefilled from note).
3. Preview is rebuilt on every input change by the Host (`write.preview`), never in JS. UI renders `display`
   (shell-quoted, copyable) and `steps[]`:
   - approve: `sdlc gate --ticket T --node N --status approved --actor user [--reopen X] [--note "..."]`
   - needs-changes: same with `--status needs-changes --actor user` (adds `--actor user` only past the cap, else the
     app still passes `--actor user` because the user is the actor; no harm, `:556`)
   - reject-and-reopen: step 1 `gate ... --status rejected --actor user --note ...`, step 2
     `invalidate --ticket T --node TARGET --reason "<reason>"`
   - stop: `gate ... --status stopped --actor user`
   `display` shows `python <abs>/sdlc.py` collapsed to `sdlc` with a "show full path" toggle.
4. Confirm button label names the action ("Reject and reopen testcase"); disabled until required fields valid;
   `Ctrl+Enter` confirms; `Esc` cancels. Focus lands on the first choice, not the destructive button.
5. Run -> spinner -> `CommandResult`. Success: show the harness output (`reopened`, `stale`, `nextPending`) for ~2 s
   with a "Next: <nextPending>" link, close dialog, rail refreshes via watcher. Error: keep dialog open, show
   `AppError.kind` + message + the command, offer Retry / Copy command / Refresh.
6. **Note and Vietnamese input.** Plain `<textarea>`, no key interception, UTF-8 end to end. Telex composition needs
   `compositionstart/end` awareness: never rebuild or rewrite the textarea value while `isComposing` (debounce the
   preview on `compositionend`). Pass note via argv array (no shell, so no quoting loss); the Host sets
   `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` (harness also reconfigures stdout, `H/cli.py:1199-1203`). Windows
   command-line argv is UTF-16, so Vietnamese survives Python's `sys.argv`; verify in T12. The mock's
   `.replace(/"/g,"'")` is display-only and must not be copied into the real argv.
   Dialog can also be driven with a Vietnamese IME prefix test (P5-T12).

### 4.5 "Which nodes go stale" preview: recommendation
Options: (A) a new harness read-only flag, e.g. `sdlc invalidate --dry-run` / `gate --dry-run`, (B) Host computes from
`lifecycle.json`. **Recommend B now, with a parity test, and ask for A as a later harness change.** Reasons: the
rule is 10 lines (`H/graph.py:135-146`: start node + later nodes in `order` whose `requires` intersects the growing
`produces` set); `trace(order)` (lane/families) is loaded from the same graph file the app already reads; harness
changes need harness review (N1-N6 backlog) and the lock logic (`H/cli.py:1170`: any non-READ_ONLY command locks)
would need a READ_ONLY exemption. Parity guard: fixture test replays the two real `invalidate` events
(`decisions.jsonl:54,72`) and asserts the Host's list equals the logged `stale` list; at run time the Host compares
the preview's `stale` with the CLI's returned `stale` and shows a warning banner if they differ (the harness answer
wins). Output of Host: `stale[] = [{node, status, willBump: bool}]` where `willBump=false` for nodes with
`status=pending, generation=1, units=[]` (`H/cli.py:752-753`).
Trace order for the Host: lane `order` (`lifecycle.json:44-` `lanes.lite.order`) or the full `order` key, plus review
round nodes (`do_handback`); P1 should expose `trace(ticket)` so P5 does not re-derive it. ASSUMPTION: P1 provides it;
otherwise P5 reads `STATUS.md`/`sdlc status --compact` (read-only).

### 4.6 Other write actions
All reuse `write.preview` / `write.run` and the same `CommandPreview` component.
- **Invalidate node** (palette "Reopen a node…", node-card menu): pick node (any in trace that has run), reason
  textarea (required, min 1 non-space char, `invalidate` argv `--reason`), stale chips, command box. One step.
  Warning if the node is `pending` (nothing to stale). Never preselect a node.
- **Lane to full** (visible only when `lane == "lite"` and no `families`, `:289-292`): reason required, shows the
  new trace and `repositories stay bound ... prepare keeps it` note (`:298`). One step: `sdlc lane --ticket T --set full --reason "..."`.
- **Retry step 2 of a reject**: opens the invalidate dialog prefilled with node and reason from the interrupted gate.
Out for M2: `worktree`, `handback`, `receipts`, `ship*`.

### 4.7 Safety
- **No write without confirm.** `write.run` takes a `previewId` the Host issued in `write.preview`; Host refuses
  unknown ids, and expires them after 10 min. The UI cannot send argv; only structured args that the Host validates and
  turns into argv. Never a shell string, never `shell=true`.
- **Re-validation (stateToken).** `write.preview` returns `stateToken = sha256(state.json bytes) + last decisions.jsonl
  line hash` (the same chain the harness uses, `H/state.py:80-96`). On confirm the Host recomputes it; mismatch ->
  `AppError{kind:"stale-view"}`, dialog reloads `GateRequest` and shows "The ticket changed (<what>). Review again."
  with the previous choice preserved. Also recheck that the node is still the one asked (`nextPending == node`, status
  not `done`). This is advisory (TOCTOU remains); the harness's own checks and lock are the authority.
- **Double-submit.** Host holds an in-flight set keyed by `ticket`; second `write.run` for the same ticket returns
  `busy` immediately; UI disables the button and ignores Ctrl+Enter while pending.
- **Lock held.** Map to `locked`: the harness lock is a directory with owner json (`H/state.py:156-174`); the app does
  no retry loop by default; shows who holds it (command, pid, seconds) and a Retry button. A running P6 session holding
  the lock is the common cause. Locks older than the stale window are broken by the harness itself.
- **Integrity.** If `Ticket.verify_log` problems exist (`board.py:63`, `integrity` field), show a banner and disable
  all write buttons; no auto-recovery.
- **Actor.** Host always passes `--actor user` on user-driven writes (nothing else is allowed from this UI) and never
  sets `controller`. Never `ship`/`commit`/`push`.
- **Audit.** After success, Host re-reads the tail of `decisions.jsonl` and checks the new `gate` line has
  `actor:"user"` and the expected `node/verdict`; mismatch is shown as a warning (also the M2 acceptance check).

### 4.8 Toasts (Host events -> toast host)
Source: Host diffing consecutive ticket snapshots on `ticket-changed` (no polling, D4).
| Toast | Trigger | Dedupe key | Click |
|---|---|---|---|
| Gate needs you | a ticket enters tier "Needs you" (4.2) | `ticket:node:generation:tier` | open gate dialog |
| Reviewer FAIL | new unit record `unit=reviewer, verdict=FAIL` at current generation | `ticket:node:generation:unit-id/at` | open ticket on node |
| Node reopened | new `invalidate` event, or `gate` with `reopened` (needs-changes) | event `prev` hash | open ticket, highlight stale chips |
Rules: dedupe key persisted in the Host for the session (and in `%APPDATA%` ledger so restart does not re-toast);
coalesce >3 toasts within 2 s into one "N tickets need you"; suppress when the dialog for that key is already open;
Windows native toast only when the window is unfocused, in-app toast always; the "session waiting on a permission
prompt" toast belongs to P6.

### 4.9 Layout
- Stack-neutral (UI): `src/ui/gate/{GateDialog,ReopenPicker,StaleChips,EvidencePanel,NgTable}.js`,
  `src/ui/shared/CommandPreview.js`, `src/ui/writes/{InvalidateDialog,LaneDialog}.js`, styles reuse `.k-dialog`, `.k-choice`,
  `.k-command`, `.k-stale-list`, `.k-evidence` from `harness/kaneki-harness/app/design-system/components.css`.
- Host: `gate` module with `gateRequest()`, `waiting()`, `preview()`, `run()`, `staleOf()`, `errorMap()`, toast diff. Keep
  it thin: pure functions over data returned by P1 readers; spawn only through P1's `runSdlc`.
- **Stack notes:** Wails: Go bound methods + `runtime.EventsEmit`. The error map is table-driven from fixtures so a
  later port is mechanical. Windows toast: a Go toast library or Wails v3 notifications (ASSUMPTION; P2-T01
  verifies, decide with P3).

## 5. Tasks
| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P5-T01 | Write `fixtures/` for gate states: copy-minimal ticket dirs (user-boundary ready, reviewer FAIL, loop cap, half-rejected, triage-review, lite scope, locked, integrity broken) built by a script in a temp dir, not from real tickets | M | P1 reader | fixtures load in P1 reader tests |
| P5-T02 | `waiting()` classifier: tiers per 4.2 + unit tests | M | T01, P1 | table-driven tests pass for all fixtures; TODO-1-2 snapshot at `execute-and-evidence g2` is "ng-decision" |
| P5-T03 | `staleOf()` + parity test against `decisions.jsonl:54,72` copies | S | P1 trace | lists equal; never-ran nodes marked `willBump:false` |
| P5-T04 | `gate.request`: assemble `GateRequest` (units, reviewer, artifacts, excerpt, NG parse best-effort, options matrix) | M | T02 | request correct for each fixture; NG parse failure degrades to raw excerpt |
| P5-T05 | `write.preview`: validate args -> argv builders for gate/invalidate/lane, 2-step reject, `stateToken`, previewId store | M | T03,T04 | argv equals expected strings; `--reason` required; Vietnamese note preserved in argv |
| P5-T06 | `write.run`: in-flight guard, token recheck, spawn via `runSdlc`, error map (4.1 table), post-write audit check | L | T05, P1 runSdlc | all error rows mapped; reject step-2 failure returns partial result |
| P5-T07 | `CommandPreview` UI component (argv display, copy, steps, full-path toggle, stale chips, warnings) | S | T05 | gallery entry + fixture states render |
| P5-T08 | Gate dialog UI: evidence, options, reopen picker, reason/note, confirm flow, error/stale-view states, keyboard | L | T04,T07 | all dialog states (below) render; confirm disabled correctly |
| P5-T09 | Invalidate dialog and Lane dialog + palette entries | M | T05,T07 | both work against a temp ticket |
| P5-T10 | Toast diff engine + dedupe ledger + top-bar count event | M | T02, P2 watcher | dedupe/coalesce unit tests; no duplicate after restart |
| P5-T11 | Throwaway-ticket script `scripts/make-test-ticket.*`: copy a minimal workspace to a temp dir, `sdlc init` + `record` units there (state-changing commands allowed ONLY in the temp copy, run by the user or by the app's test harness, never by planning agents) | M | none | script yields a ticket at a user-boundary gate in a temp dir |
| P5-T12 | Telex/IME check + UTF-8 round trip through argv to `decisions.jsonl` | S | T06 | Vietnamese note identical in the log line |
| P5-T13 | End-to-end acceptance on temp ticket: approve, reject+reopen, lock, stale-view; write results in doc | M | T08,T09,T11 | M2 done-when satisfied |

## 6. Tests and verification
- **Unit (Host, table-driven):** `waiting()`, `staleOf()`, argv builders, error mapper (feed the exact strings from 4.1),
  stateToken change detection, dedupe ledger.
- **Fixture UI states for the dialog:** loading, approve default, reviewer FAIL (approve disabled), loop cap, reject
  with no target selected, reason empty, triage-review with `--reopen` unset, stale-view banner, locked, integrity
  broken, spawn error, partial reject (step 2 failed), success. Snapshot with the gallery pattern
  (`design-system/gallery-data.js`).
- **Parity:** replay `decisions.jsonl` events (copied into fixtures) and compare `stale` lists; run
  `python .../sdlc.py invalidate --help` and `gate --help` in CI to detect flag drift (read-only).
- **Manual against the real workspace (read-only):** open the app on `todo-nestjs-backend`, confirm `waiting()` for TODO-1-2
  (currently all gates closed through `deliverable-ready`, `decisions.jsonl:105`) is empty and the preview text for a
  hypothetical gate on a closed node shows "already closed" without executing.
- **Manual write test on a temp copy** (T11/T13): approve a `feature-plan`-like gate; reject `execute-and-evidence`
  and reopen `testcase` (compare resulting stale list with `decisions.jsonl:72`); hold the lock (`mkdir .lock`
  equivalent via a running `sdlc` in another shell) and see `locked`; edit `state.json` by hand and see `integrity`.
  Confirm last log line `actor:"user"` and that `verify_log` still passes.
- **Never** run gate/invalidate/lane --set against `D:/workspace/HAISE/testing/todo-nestjs-backend` tickets.

## 7. Risks
| Risk | Mitigation |
|---|---|
| Two-step reject leaves a half state if step 2 fails | run back-to-back; partial-result UI with retry-step-2; log both in Host ledger |
| "Needs you" for controller-boundary nodes (gap d) is a heuristic and may mis-badge | two tiers; user confirms rule (Q1); never auto-act on it |
| Host `staleOf` drifts from `H/graph.py:dependents` | fixture parity; runtime compare with CLI output; ask for `--dry-run` later |
| Error mapping by message text breaks when messages change | regex table in one file; unknown text falls back to raw `error` string; request a `code` field |
| Windows argv/encoding corrupts Vietnamese | UTF-8 env vars, argv array not shell, T12 test; fall back to `--note` from a temp file is not possible (CLI has no `--note-file`), so report to harness owner if it fails |
| Advisory TOCTOU between token check and CLI run | harness validates again and holds the lock; surface its error verbatim |
| NG parse of evidence markdown is brittle | best-effort + raw excerpt + open-in-Zed; never gate approval on parse |
| Lock held by a running P6 session | explicit `locked` UX with owner info; P6 should release between nodes |
| Mock's evidence numbers (136 OK / 2 NG) are hard-coded | take from files only; show "unknown" when absent |

## 8. Decisions needed from the user
1. **Q1 NG-driven gates on controller nodes.** Show "Needs you" for `execute-and-evidence`/`final-review` when NG > 0 with
   reviewer PASS (matches how you decided TODO-1-2), or only on user-boundary nodes + G4? Recommend the NG rule, with a
   separate "Ready to close" tier for everything else.
2. **Q2 Stale preview source.** Host-computed from the graph with parity test (recommended) vs. add `--dry-run` to the harness.
3. **Q3 Reject semantics.** Keep two steps (`gate rejected` then `invalidate --reason`) as real data does (recommended), or ask
   harness to make `--reopen` work for `rejected` so it is one atomic command (needs harness change, avoids the half state).
4. **Q4 Reason field.** Use the note as the `invalidate --reason` default (editable) or require a separate reason? Recommend
   prefilled from the note, required non-empty.
5. **Q5 `--gate` label.** Always `G0` or let the dialog pick (G4/G5 appear in real data)? Recommend `G0` default, auto `G4` when
   loop-cap, `G5` for NG/ambiguous-result gates, editable under "advanced".
6. **Q6 `needs-changes` from the app.** Expose it (only on `loopBackTo` nodes) or hide in M2? Recommend expose behind a secondary
   action; most user decisions are approve/reject.
7. **Q7 Failure of step 2.** Auto-retry once or manual only? Recommend manual only; show clearly.

## 9. Evidence (files and lines read)
- `harness/kaneki-harness/harness/cli.py`: `Session` 54-95; `cmd_lane` 276-299; `cmd_plan` 335-376; `cmd_gate` 545-657;
  `artifact_problems` 665-675; `mark_superseded` 733-763; `_invalidate` 766-780; `cmd_invalidate` 783-790; parsers 1010-1075;
  `READ_ONLY`/lock/`main` 1161-1203.
- `harness/kaneki-harness/harness/errors.py:1-2`; `state.py:80-96,119-131,150-175,181-208`; `graph.py:135-146`; `board.py:20-66`.
- `harness/kaneki-harness/graph/lifecycle.json`: node boundaries (169, 289, 394, 468, 506, 603, 661-668, 699, 720, 799), lanes 44,
  gates 822-830; decision-event schema `contracts/decision-event.schema.json` (gate/invalidate/lane required fields).
- Real data (read-only): `testing/todo-nestjs-backend/.sdlc/tickets/TODO-1-2/decisions.jsonl` lines 21, 52, 54, 70, 72, 105;
  `state.json` unit record shape (reviewer `verdict:"PASS"`, `result`, `tokens`).
- `harness/kaneki-harness/app/design-system/app-mock.html:112-175` (toast, gate dialog, preview script); `components.css` classes named only.
- `harness/kaneki-harness/docs/desktop-app/plan.md:13,35-40,54-60,77,107`.
- Ran only `sdlc.py gate|invalidate|lane --help`.
- Unverified: NG row format in evidence markdown; whether the harness accepts `rejected` with no follow-up invalidate in all
  later steps (`nextPending`/`status` behaviour not exercised); real Windows argv UTF-8 behavior.
