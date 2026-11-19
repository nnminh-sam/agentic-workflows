# P1 · Data contract and harness-side changes

**Milestone:** before M0/M1 (everything else builds against it). **Output:** `parts/P1-data-contract.plan.md`

## Problem
The UI and the Host need one agreed set of JSON types, IPC calls and events, so they can be built in parallel
(UI against fixture files, Host against the harness). Today the shape lives implicitly in `board.py` and
`sdlc tickets`. Plan Q5 asks whether the harness should emit a stable machine-readable status so the app does not
depend on internal file layouts; N4 (`status --accept-current-state` writes without the lock) must be fixed first.

## Plan must cover
1. **Type catalogue** with field-level definitions and source of each field (which file / function computes it):
   `TicketSummary` (rail), `TicketDetail` (strip + node cards + generation/loop-back), `NodeCard` (units,
   tier/model, verdict, tokens, rounds, artifacts), `GateRequest` (what a gate dialog needs: question, evidence
   summary, reviewer verdict, NG list, allowed statuses, reopen targets, nodes that go stale), `DecisionEvent`
   (from `contracts/decision-event.schema.json`), `SessionInfo` (for P6), `CostSummary` (for P7, shape only),
   `CommandPreview`/`CommandResult` (for P5), `AppError`.
2. **IPC catalogue**: every `invoke` name with args → result type, and every event name with payload, including
   which part implements it. Versioning rule (`contractVersion`).
3. **Where each type comes from**: reuse `sdlc tickets` / `board.summarize` vs. a new read-only harness command
   (e.g. `sdlc ticket-detail --json`, `sdlc gate-request --ticket T --node N`) vs. the Host reading files directly.
   Recommend one, with the trade-off (startup cost ~0.5 s per Python call vs. coupling to file layout).
4. **Harness changes** needed (small, stdlib Python, with tests in `kaneki-harness/tests/`): list each new or
   changed command, its JSON output, and the N4 lock fix. Note anything in N1–N3 that the app would trip over.
5. **Fixtures**: how to capture fixture JSON from `TODO-1` and `TODO-1-2` (read-only), plus synthetic fixtures for
   states the real tickets don't show (gate pending on user, reviewer FAIL, loop-back g2, lite lane, bug kind,
   corrupt/integrity error, empty workspace). Where they live (`kaneki-harness-app/fixtures/`).
6. **Schema files**: JSON Schema for each type in `kaneki-harness-app/contracts/` and how both UI (JS) and Host
   validate or generate types from them.

## Not in scope
UI layout, stack choice, session spawning mechanics (only the `SessionInfo` shape), cost computation (only shape).
