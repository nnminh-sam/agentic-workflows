# testcase

**Goal:** test cases derived from the **approved plan** (not from the code), in the profile's testcase format.

- One case per requirement or plan item, one per `Condition → negative case` row, plus regression cases for
  the callers named in impact-analysis or investigate.
- Each case: id, purpose, surface, preconditions (data/state), steps, expected result (a concrete value),
  in-scope yes/no with reason.
- Format: `testcase.format` from the profile (markdown by default; xlsx/csv/gherkin through the template it names).
- End with `## Coverage matrix` (plan item → case ids) and `## Uncovered` (with reason).

**G5** if an expected value or applicability is ambiguous.
