# Porting map: wms-flow 1.22.4 → kaneki-flow

Source: `wms-analysis/packages/wms-1.22.4` (`shared/wms/lifecycle.py`, `skills/wms-flow/SKILL.md`,
`resources/policies/node-gates-1.json`, diagram `docs/visuals/wms-ticket-workflow-1.22.4.html`).

## Layers 1–2: kept almost 1:1

| wms-flow node | kaneki-flow node | Change |
| --- | --- | --- |
| preparation | prepare | Backlog fetch / repos.env / doctor → tracker adapter + profile repositories + stack `doctor` |
| investigate | investigate | same (deep tier, G1) |
| fix-plan | fix-plan | same; "16 STACK design rules" → design-phase rules from rulepacks |
| requirement-analysis | requirement-analysis | same (routine + deep reviewer) |
| impact-analysis | impact-analysis | same |
| feature-plan | feature-plan | same |
| coding | coding | `rules-coding.json` self-check → code-phase rules of the rulepacks |
| coding-review | code-review | 2 deep units + controller judgement kept; gate letters BCJS(+I) → rule phases |
| if-registration(-integrity-or-update), if-real-path-verification-import/-export | *(family extension)* | moved out of the core graph to `examples/wms/extensions/if-family.json` at slot `after-code-review` |
| testcase / testcase-update | testcase | the bug+IF Opus variant is dropped; a family can override the tier if it needs to |
| testcase-review | testcase-review | same, loops back to testcase |

## Layer 3: generic nodes only

| wms-flow | kaneki-flow | Change |
| --- | --- | --- |
| execute-and-evidence | execute-and-evidence | Playwright/Flutter/batch hard-wiring → surface adapters per surface |
| handheld-verification | *(surface)* | a real device is surface `device` (adapter `device-manual`, always G6), not a node |
| tech-checklist-review | final-review | full rulepack; receipt per repo with TTL kept |
| delivery-drafts | delivery-drafts | fixed six files (incl. 確認事項, `*-vi.md`) → `profile.delivery.drafts` + adapter templates |
| deliverable-ready | deliverable-ready | receipts → ready → user approves drafts; ship stays outside |
| `wms-ship` skill | delivery adapter `ship` section | always needs the user's go; a separate ship skill can be added later |

## Layer 4 (after PR): 14 nodes → 3 + reopen

| wms-flow (14) | kaneki-flow |
| --- | --- |
| read-pr-review, check-review-is-current | **read-review** |
| reload-ticket-and-spec, verify-review-claim, decide-fix-scope, reopen-earliest-invalidated-node | **triage-review** (`--reopen <node>`) |
| check-sibling-ifs | sibling check folded into triage; IF-specific K gate lives in the IF rulepack |
| apply-review-fix, rerun-affected-evidence, tech-checklist-review, delivery-drafts, reship-ready | *no new nodes*: the reopened part of the **main graph** reruns (coding → … → deliverable-ready) |
| reply-to-pr-review, recheck-pr-review | **respond-review** (recheck = the next handback round) |

## Mechanisms kept

- Controller / worker / reviewer split, fresh subagent per unit, packet rendered by the driver with a digest.
- Routine vs deep tiers (Sonnet C1/C2 vs Opus C3), planned model enforced at `record`.
- Gates G0, G1, G3–G7 with the same triggers (G2 was already retired).
- Token-based `requires`/`produces` invalidation: rerun only what consumes the changed output
  (a coding rerun keeps the test cases).
- Loop cap 2 → G4; append-only decision log; never re-init to recover.
- Subject digest at coding/final-review, per-repo receipt with TTL, receipt must match the checkout at ready.
- HARD-GATE: analyze before code; never commit/push/PR/deploy/delete/print secrets.

## Dropped (or left to a project profile)

- Kai customer policy switch (`kai-requirements/artifacts/delivery@1`) → simply a different profile.
- Graph version migrations @1–@7 and legacy node names.
- xlsx workbook tooling (`kai_workbooks.py`, template safety) → `testcase.format: xlsx` + a project `helper` script.
- Vietnamese progress-table / question-card wording → `locale.chat`.
- `--advice` counsel agent, resume-vs-fresh token heuristics, `diffstat`, `verify-subject` citation checker,
  waivers, `workbook-regen`, finished-ticket trace. Candidates for v0.2 if you miss them.
