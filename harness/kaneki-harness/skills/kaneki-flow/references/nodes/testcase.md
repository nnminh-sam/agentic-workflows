# testcase

**Goal:** test cases derived from the **approved plan** (not from the code), as data: `testcases.json`
(`contracts/testcases.schema.json`). Coding implements to these cases and the evidence run records an observed value
for each of their checks, so write what can be checked, not prose.

- `criteria`: the ticket's acceptance criteria (`AC-n`, the text, the requirement ids, `source` as `file:line`).
- `cases`: one per requirement or plan item, one per `Condition → negative case` row, plus regression cases for the
  callers named in impact-analysis or investigate. Each case has:
  - `id` `TC-n`, `title`, `purpose`, `surface`, `criteria` it covers, `preconditions`, `steps` (short sentences);
  - `risk`: `critical` (a criterion fails if it is wrong), `high` or `normal`;
  - `checks`: one per assertion, `TC-n.aN` with `expect` (the concrete expected value or state) and, when it is a
    value, `value` (`404`, `false`, `"feature_disabled"`). One check, one observable thing;
  - `inScope` and `reason` (required when out of scope or manual);
  - `run`: how the evidence run executes it:
    - `repo-test` with `command` (`{case}` is the case id; e.g. `npx playwright test -g "{case}"`): the case is a
      test in the repository, written in coding (unit, integration or e2e);
    - `steps` with `file` (`steps/TC-n.json`, `contracts/steps.schema.json`): an HTTP-level case for the harness
      runner, using only the project's evidence kit helpers and accounts. Write the step file now;
    - `manual`: only when it cannot run (real device). A `critical` case must not be manual.
- `uncovered`: plan items with no case, each with a reason.

Every criterion needs at least one in-scope case. Run `sdlc check --ticket <id> --node testcase` before handing
back: it validates the schema and these rules. `testcases.md` is not written any more; `sdlc report` renders the
cases for people.

**G5** if an expected value or applicability is ambiguous: ask it in the batch (`sdlc ask`), never guess a value.
