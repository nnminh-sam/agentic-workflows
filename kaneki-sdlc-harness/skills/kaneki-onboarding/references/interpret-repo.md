# interpret-repo (onboarding unit)

**Goal:** settle what the deterministic scan could not decide for one repository, with evidence.
You suggest; the user decides. Read only; write only your output file.

Read `CAND.repos.<id>` and the `repositories.<id>.*` entries of `CAND.proposal` / `CAND.evidence`. Then, inside
the repository:

1. **Stack** (only if `unknown` or `stackAlternatives` is not empty): which language/framework holds the
   product code (not tooling)? Cite the manifest and one source file.
2. **Surfaces**: how would a tester exercise this repo? Pick from `web`, `api`, `cli`, `batch`, `db`, `mobile`,
   `device`, `file`, `report_pdf`. Cite an entry point for each (router file, `main`, a scheduled job, a
   migration folder). Drop a detected surface you cannot cite.
3. **Commands** (only if none were detected): the documented way to lint and test (README, CONTRIBUTING,
   Makefile, `justfile`). Cite the line. Do not run anything.
4. **Conventions**: up to five rules from CONTRIBUTING / style docs that a reviewer would check, each cited.

Never open `.env` or files that look like secrets. Do not guess: no citation, no suggestion.

Write `interpret-<id>.json`:

```json
{
  "repo": "<id>",
  "suggestions": [
    {"field": "repositories.<id>.surfaces", "value": ["api", "batch"],
     "evidence": ["routes/api.php:1", "app/Console/Kernel.php:18 schedules ImportOrders"], "confidence": "high"}
  ],
  "conventions": [{"rule": "Controllers stay thin; logic in services", "evidence": "CONTRIBUTING.md:22"}],
  "doubts": ["Is the `tools/` folder a separate deliverable?"]
}
```

Reply in ≤ 100 words: the path of the file and one line per suggestion.
