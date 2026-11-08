# coding

**Goal:** the approved plan implemented in the ticket worktree, and nothing more.

1. Write only under the authorized paths (the plan's files). Create new files only where the plan says.
2. Follow the stack adapter's conventions; run its `format`, `lint`, `build` and `unitTest` commands on what you touched.
3. `implementation.md`: `## Changes` (file → what, tied to plan items), `## Checks` (command → exit/result),
   `## Rule self-check` with one line per code-phase rule id in the rules file: `<RULE-ID>: PASS | N/A (reason) | FAIL`
   (the gate checks every id is there). Cite changed code as backticked `path:line`; at this node the gate verifies
   every citation resolves.
4. Never commit or push (the guard and `sdlc git` refuse it; only kaneki-ship commits). Never edit a test to make it pass unless the plan says the test was wrong.

**G6** before any write to shared state (shared DB, queue, external service): the controller asks first and
records a restore value. **G7** if only a read-only checkout is available.
Round two (after code-review NEEDS CHANGES): fix only the listed findings and say what changed for each.
