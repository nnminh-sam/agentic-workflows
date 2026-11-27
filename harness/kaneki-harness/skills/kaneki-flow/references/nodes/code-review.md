# code-review

**Goal:** an independent verdict on the real diff. Two units run **in parallel** (D3); each writes its own
section, neither waits for the other, and the controller judges both.

1. **technical-review** (routine). Over the real diff
   (`sdlc git --ticket <id> --repo <repo> -- diff <base>`, plus untracked files):
   - run the stack's build, lint, type check and the full test suite (output to a log file; see the
     packet's context budget) and report each with its exit code;
   - start the real app the way the stack adapter says and hit every endpoint/flow the plan changes once,
     including one error path; record request and response (status + body excerpt);
   - judge every code-phase rule id in the rules file against the diff hunks: PASS / N/A (reason) /
     FAIL with `path:line`;
   - verdict **PASS** only when everything builds, every test passes and no rule FAILs.
   Record it with `--verdict PASS|FAIL`.
2. **semantic-review** (deep), at the same time. Does the diff implement the approved plan
   and only the plan?
   - walk each `Condition → negative case` row through the code with concrete values;
   - look for regressions in the callers named by impact-analysis or investigate;
   - **attack the weakest claim on the running app** (`sdlc env up` / `sdlc env exec`, never your own server);
     try the input most likely to break the change (wrong type, missing field, boundary, framework
     default behaviour such as error wrapping or lifecycle hooks) and quote the actual response;
   - do not run the build, lint and rule table; the technical review covers them.
   Record it with `--verdict PASS|FAIL`.

Both append to `code-review.md` under their own heading (`## Technical review (generation N)`,
`## Semantic review (generation N)`); the controller ends the file with one line
`Verdict: APPROVED` or `Verdict: NEEDS CHANGES` (the gate refuses a status that contradicts it).
Severity: Critical/High → NEEDS CHANGES; only Medium/Low → APPROVED with actions. An empty diff is
NEEDS CHANGES ("no change").

**Fix rounds** (generation > 1): both units review only the fix — the hunks that changed since the last
review, named in `implementation.md`'s fix section and in `findingsToFix` — plus the rules and plan
conditions those hunks touch. The technical review still reruns the full test suite (cheap, and it
catches regressions); the semantic review reads only the fix and re-attacks the finding it raised.

The controller closes with `--status needs-changes` (the driver reopens `coding`, counts the round, and
raises G4 at the cap) or `approved`. When code-review has failed `coding` twice, `plan` moves `coding`
from the light tier to routine (`escalated` in the plan) — dispatch it on the model the plan names.
