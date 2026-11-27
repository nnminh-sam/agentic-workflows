# execute-and-evidence

**Goal:** every in-scope case run on its real surface, with evidence a stranger could audit.

0. The harness owns the environment and the run. When the profile has `environment.evidence`: start it with
   `sdlc env up --ticket <id>` (database sized, ticket database, migrations, server, seed; reused when already up)
   and never start servers, open database connections or write `.env.local` yourself. Run the cases the repo
   already has (tests written in coding) through `sdlc evidence run --ticket <id> --cases <cases.json>`
   (`{"cases": [{"id": "TC-1", "surface": "web", "command": "npx playwright test -g TC-1"}]}`): smoke first,
   resumable, infra retried with back-off and a breaker, results in `evidence/run.json`. A `STEP-ERROR` case: fix
   its step data once and rerun it with `--rerun TC-n`; an `INFRA` stop or a second step error is an escalation, not
   a reason to write your own scripts. A `FAIL` is a result: record it, never retry it. `sdlc env exec -- <cmd>`
   runs any other command with the env loaded. Stop the server with `sdlc env down` when the node closes
   (`--drop-db` when the ticket is done).
1. For each case, use the **surface adapter** for its surface: prerequisites, run command/tool, what to capture.
2. Capture state **before and after** (DB rows, API response, file, screen). A screenshot alone is not evidence.
3. Mark OK / NG / N/A (with reason). Ambiguous is NG or G6, never OK.
4. Write evidence files under `evidence/` and `run-report.md`: case → surface → command → result → evidence paths.
5. A surface whose adapter is `none` is `N/A` with the profile's reason.

**G6** before mutating shared state, before using a real device, or when runtime config is missing.
The reviewer audits that each OK has a captured expected state.
