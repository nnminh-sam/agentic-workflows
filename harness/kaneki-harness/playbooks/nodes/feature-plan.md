# feature-plan (feature)

**Goal:** a design and task plan the user approves.

`plan.md` sections: `## Design` (components, data model changes, interfaces/contracts, sequence),
`## Tasks` (ordered, each with files and acceptance), `## Condition → negative case` (as in fix-plan),
`## Data access & side effects`, `## Alternatives` (G3 if material), `## Test intent`,
`## Design rules` (each design-phase rule id → PASS / N/A / FAIL).

Boundary is **user**: Proceed / Re-run / Stop.

**Plan reviewer** (unit `reviewer`, deep tier, D2): a fresh session that did not write the plan. Check every task
against the requirements it cites (`file:line`), every `Condition → negative case` row for a missing negative, and
the `## Test intent` against what the testcase node must prove. A gap that would only surface in code review (a
page the requirement covers but the plan does not) is a FAIL with the requirement quote. Record `--verdict PASS|FAIL`;
on FAIL the worker fixes the plan before the user sees it. Business questions go to the batch (`sdlc ask`).
