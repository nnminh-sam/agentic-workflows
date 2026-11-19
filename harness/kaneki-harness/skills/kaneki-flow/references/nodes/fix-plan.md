# fix-plan (bug)

**Goal:** the smallest change that removes the proven cause, approved by the user.

`plan.md` sections:
- `## Change`: per file, what changes and why (tie each change to the confirmed cause).
- `## Condition → negative case`: every predicate, branch or query condition the fix adds or changes, one row
  each: condition | positive case | negative case. If no condition changes, say so explicitly (the controller
  then asks the user to confirm).
- `## Data access & side effects`: reads/writes, transactions, locking, migrations, external calls.
- `## Alternatives`: options considered and why rejected (G3 if they differ materially).
- `## Test intent`: what the testcase node must prove.
- `## Design rules`: each design-phase rule id from the rules file → PASS / N/A (reason) / FAIL.

Boundary is **user**: the controller summarises and asks Proceed / Re-run / Stop.
