# investigate (bug)

**Goal:** a proven root cause with a stated confidence, or the exact questions that block one.

1. Restate the reported and the expected behaviour, each with its source (ticket line, spec section).
2. Reproduce on the cheapest real surface (test, script, query, request). Record the command and output.
3. Trace the failing path: entry point → first wrong point. Cite every hop `path:line`.
4. Hold at least two hypotheses until evidence kills one; record what killed it.
5. Confirm the cause with an executed check (query, log, failing test, runtime value), not by reading alone.
6. List every place the same faulty pattern exists (siblings); they inform the plan's scope.

`investigation.md` ends with `## Confirmed cause` (one paragraph) and `Confidence: High|Medium|Low` plus the
evidence that sets it. Owner/customer questions go to `questions.md`: `Q-n | question | why it matters | status`.

**G1** if a requirement source is unavailable, the cause is not confirmed by execution, or confidence is below High.
