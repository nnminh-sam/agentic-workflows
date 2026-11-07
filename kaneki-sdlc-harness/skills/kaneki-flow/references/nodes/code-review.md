# code-review

**Goal:** an independent verdict on the real diff. Two deep units run in parallel; the controller judges.

- **mechanical-review:** judge every code-phase rule id in the rules file against the diff hunks
  (`sdlc git --ticket <id> --repo <repo> -- diff <base>...`): PASS / N/A (reason) / FAIL with `path:line`.
- **semantic-review:** does the diff implement the approved plan and only the plan? Walk each
  `Condition → negative case` row through the code. Look for regressions in the callers named by
  impact-analysis or investigate.

Both append to `code-review.md` under their own heading; the controller ends the file with one line
`Verdict: APPROVED` or `Verdict: NEEDS CHANGES` (the gate refuses a status that contradicts it). Severity: Critical/High → NEEDS CHANGES;
only Medium/Low → APPROVED with actions. An empty diff is NEEDS CHANGES ("no change").

The controller closes with `--status needs-changes` (the driver reopens `coding`, counts the round, and raises
G4 at the cap) or `approved`. Round two reviews only the fix hunks plus the rules they touch.
