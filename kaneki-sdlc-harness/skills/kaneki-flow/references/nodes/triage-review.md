# triage-review (after PR)

**Goal:** a decision for every comment, and the earliest node the accepted ones invalidate.

For each `RC-n`, verify the claim against the code at the current head and against the spec (reproduce it if it
is behavioural), then mark it `ACCEPT`, `REJECT (evidence)` or `CLARIFY (question)`.

Group the accepted findings by the earliest node whose output they make wrong:

| Finding is about | Reopen |
| --- | --- |
| the plan or design | fix-plan / feature-plan |
| code only | coding |
| a test gap | testcase |
| evidence only | execute-and-evidence |
| wording only | delivery-drafts |

Look for the same issue elsewhere (siblings). `triage.md` ends with `Reopen: <node>` or `Reopen: none`.

Boundary **user**: show the table and ask for confirmation, then `gate --actor user --reopen <node>`.
