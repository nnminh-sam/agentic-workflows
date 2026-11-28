# coding

**Goal:** the approved plan implemented in the ticket worktree, and nothing more.

1. Write only under the authorized paths (the plan's files). Create new files only where the plan says.
2. Follow the stack adapter's conventions; run its `format`, `lint`, `build` and `unitTest` commands on what you touched.
3. `implementation.md`: `## Changes` (file → what, tied to plan items), `## Checks` (command → exit/result),
   `## Rule self-check` with one line per code-phase rule id in the rules file: `<RULE-ID>: PASS | N/A (reason) | FAIL`
   (the gate checks every id is there). Cite changed code as backticked `path:line`; at this node the gate verifies
   every citation resolves.
4. Implement to the reviewed cases in `testcases.json`: every `repo-test` case is a test in the repository, its
   name or tag carries the case id, and each assertion records its check with the `evidence()` helper
   (`sdlc evidence helper --ticket <id> --repo <repo>` copies it into the repo once; it does nothing outside an
   evidence run, so the tests stay ordinary regression tests in CI). Live cases (`surface` web or api) are e2e
   tests next to the unit tests (Playwright test, supertest ...), written here and code-reviewed; the evidence node
   runs them and never writes scripts. A case's `run.command` must select exactly its tests.
5. For every `critical` check of a `repo-test` case, write `mutations.json` in the ticket folder: the line that
   implements the check, which `sdlc mutate` breaks to prove the test is not vacuous (J3):
   `{"TC-1.a1": {"file": "src/lib/flags.ts", "line": 41, "find": "if (!enabled) return notFound()", "replace": ""}}`,
   or `{"TC-1.a1": {"none": "<why no single line implements it>"}}`. Run `sdlc mutate --ticket <id>`: a check whose
   test still passes with its line broken needs a stronger test before you hand back.
6. Never commit or push (the guard and `sdlc git` refuse it; only kaneki-ship commits). Never edit a test to make it pass unless the plan says the test was wrong.

**G6** before any write to shared state (shared DB, queue, external service): the controller asks first and
records a restore value. **G7** if only a read-only checkout is available.
Fix round (after code-review NEEDS CHANGES): fix only the listed findings. Write the new round (below the
`## SUPERSEDED:` line) with a `## Fix` section: per finding, what changed and the exact `path:line` hunks, so the
reviewers can review only those hunks.
