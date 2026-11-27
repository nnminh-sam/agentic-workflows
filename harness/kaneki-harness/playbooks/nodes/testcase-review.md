# testcase-review

**Goal:** every plan condition and every expected value is covered and correct.

Check each case against the plan and its cited source: missing negative cases, wrong expected values,
untestable steps, wrong surface. Wording/format issues ride along as noted actions. Anything that changes an
expected value, an in-scope mark or the case set is NEEDS CHANGES (the driver reopens `testcase`).
Review `testcases.json` (the gate already checked its schema, ids and criterion coverage): is each `expect` what
the requirement says, does each case test the requirement or a weaker proxy, is a `critical` risk marked where a
criterion fails if the case is wrong, does every check observe one thing, does each `steps` file only use kit
helpers? Cite the requirement (`file:line`) for every finding.
Output `testcase-review.md`, ending with `Verdict: APPROVED` or `Verdict: NEEDS CHANGES` (checked by the gate).
