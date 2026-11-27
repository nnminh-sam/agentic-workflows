# execute-and-evidence

**Goal:** every in-scope case of `testcases.json` run on its real surface, with an observed value per check that a
stranger could audit. You run what exists; you never author tests or scripts here.

1. The harness owns the environment. When the profile has `environment.evidence`, start it with
   `sdlc env up --ticket <id>` (database sized, ticket database, migrations, server, seed; reused when already up).
   Never start servers, open database connections or write `.env.local` yourself; `sdlc env exec -- <cmd>` runs any
   other command with the env loaded.
2. Run the cases: `sdlc evidence run --ticket <id>`. It reads `testcases.json`: `repo-test` cases run their
   `command` (the tests coding wrote; they record each check through `evidence()`), `steps` cases run their step file
   through the harness runner (validated first: a bad file refuses the run before anything is spent). Smoke cases
   first, resumable, infra retried with back-off and a breaker. It writes `evidence/run.json` and `evidence.json`
   (per check: PASS, FAIL, UNOBSERVED, NOT-RUN or MANUAL, with the observed value and the test line or step that
   observed it).
3. By status:
   - `STEP-ERROR` (a selector, a wait, a helper or a step file): fix the step data once, then
     `sdlc evidence run --ticket <id> --rerun TC-n`; a second step error is an escalation;
   - an `INFRA` stop: an infra escalation (`sdlc ask --class infra`), not a reason to write your own scripts;
   - `FAIL`: a result. Record it, never retry it; the evidence judge routes it to the node that owns it;
   - `UNOBSERVED`: the test passed but never recorded that check. It is not a pass: say so in the report; the fix
     belongs to coding (the test must observe the check).
4. `MANUAL` cases (real device, `surface` adapter `none`): capture state before and after with the surface adapter
   and record the observation in `run-report.md`. A screenshot alone is not evidence. Ambiguous is NG or G6, never OK.
5. Write `run-report.md`: the command(s) you ran, the check summary from `evidence.json`, each FAIL / UNOBSERVED /
   NOT-RUN check with one line of why, and the manual observations. Stop the server with `sdlc env down` (add
   `--drop-db` when the ticket is done). `sdlc report --ticket <id>` renders the read-only notebook.

**G6** before mutating shared state, before using a real device, or when runtime config is missing.

## Judges (units `reviewer` = judge A, deep; `reviewer-2` = judge B, routine, critical checks only)

You never wrote or ran these tests: you decide whether they prove the ticket (J1). Start from the driver's facts:
`sdlc judge-facts --ticket <id>` writes `evidence/judge-facts.json` (per check: status, observed value, source,
mutation result, and the EVD rules it breaks). You cannot overrule them: a driver FAIL is FAIL; a check with any
rule listed cannot be PASS (J2).

Write `evidence-judgement.json` (judge A) or `evidence-judgement-2.json` (judge B), `contracts/judgement.schema.json`:
per check `verdict` PASS | FAIL | ESCALATE, `confidence` High | Medium | Low, `reason`, and for PASS / FAIL a
`requirement` and an `evidence` citation, each `{"ref": "file:line", "quote": "<verbatim text at that line>"}`
(the gate checks the quote is there). FAIL names `routeTo`, the node that owns the fix (coding for a weak test or
wrong code, testcase for a wrong expected value). Low confidence is ESCALATE, and so is anything you cannot decide from
the requirements and the evidence: class business / infra / critical-logic, asked with
`sdlc ask --class <class> --severity HIGH --question '<check id>: ...'`. Judge A also gives a reason, with a citation,
for each file in the facts' `uncoveredFiles` (EVD-E). Run `sdlc check --ticket <id> --node execute-and-evidence
--unit <reviewer|reviewer-2>` before you hand back. Where A and B disagree on a critical check, the gate asks the user
(J4). Approved verdicts go to the calibration log; a later stage that overturns one records `sdlc judge-miss` (J5).
