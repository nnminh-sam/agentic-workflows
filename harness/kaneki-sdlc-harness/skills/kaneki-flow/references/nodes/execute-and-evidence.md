# execute-and-evidence

**Goal:** every in-scope case run on its real surface, with evidence a stranger could audit.

1. For each case, use the **surface adapter** for its surface: prerequisites, run command/tool, what to capture.
2. Capture state **before and after** (DB rows, API response, file, screen). A screenshot alone is not evidence.
3. Mark OK / NG / N/A (with reason). Ambiguous is NG or G6, never OK.
4. Write evidence files under `evidence/` and `run-report.md`: case → surface → command → result → evidence paths.
5. A surface whose adapter is `none` is `N/A` with the profile's reason.

**G6** before mutating shared state, before using a real device, or when runtime config is missing.
The deep reviewer audits that each OK has a captured expected state.
