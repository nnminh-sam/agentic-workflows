# testcase-review

**Goal:** every plan condition and every expected value is covered and correct.

Check each case against the plan and its cited source: missing negative cases, wrong expected values,
untestable steps, wrong surface. Wording/format issues ride along as noted actions. Anything that changes an
expected value, an in-scope mark or the case set is NEEDS CHANGES (the driver reopens `testcase`).
Output `testcase-review.md`, ending with `Verdict: APPROVED` or `Verdict: NEEDS CHANGES` (checked by the gate).
