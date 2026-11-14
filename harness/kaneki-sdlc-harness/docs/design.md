# Design

## One rule

**The graph is generic; the project is data.** Nothing under `graph/`, `harness/` or
`skills/kaneki-flow/references/nodes/` names a stack, platform, tracker, language or customer.
Those come from one project profile and the adapters it selects.

```
sdlc.profile.json ──► tracker adapter      (where tickets come from, how to reply)
                  ──► stack adapter × repo (commands: doctor/format/lint/build/test, worktree, conventions)
                  ──► surface adapter × surface (how a case is executed and what evidence is captured)
                  ──► delivery adapter     (drafts + templates, PR review fetch, ship/reship steps)
                  ──► rulepacks            (review rules as data, by phase)
                  ──► families             (optional extra nodes at graph slots + extra rules)
```

## Layers and nodes

```
prepare
 ├─ bug:     investigate → fix-plan ─────────────────────────────┐   (user approves the plan)
 └─ feature: requirement-analysis → impact-analysis → feature-plan ┘
coding ⇄ code-review                    (needs-changes loops; cap → G4)
  [@slot after-code-review: family nodes, e.g. IF registration]
testcase ⇄ testcase-review              (from the plan, not the code)
execute-and-evidence                    (one surface adapter per surface)
  [@slot after-evidence: family nodes]
final-review → delivery-drafts → deliverable-ready      (receipts; user approves drafts)
── handback (PR review arrives) ──
read-review → triage-review ─(reopen X)→ X … deliverable-ready → respond-review
```

The after-PR layer adds no fix/rerun nodes: `triage-review` reopens the earliest invalidated node and the
driver's token walk stales only the nodes that consume its output. Their trace order makes them the next
pending nodes, so the normal graph reruns that part, then `respond-review` runs.

## Driver (`harness/`, stdlib Python)

| Command | Does |
| --- | --- |
| `validate` | profile + adapters + rulepacks + extensions resolve (fail-closed) |
| `init` | context (kind, repos, surfaces, families) + trace + state |
| `status` | node states, next pending, missing prerequisites, tokens |
| `plan` | units (model, agent type), boundary, gates, artifacts, card, adapters, rules |
| `packet` | renders the dispatch packet + the node's rules file, prints path + sha256 |
| `record` | appends a unit result (digest, model check, tokens, verdict) |
| `gate` | closes a node: readiness, units, reviewer verdict, artifacts, user boundary, subject digest, receipts, loop-back, reopen |
| `invalidate` | stales a node and its token dependents, keeps the rest |
| `worktree` | binds a repo to the ticket worktree; only `<paths.worktreeRoot>/<repoId>-<ticket>` (or the repo itself) is accepted |
| `handback` | starts a review round |
| `receipts` | shows / re-issues receipts |
| `digest` | sha256 of a file |

State: `<ticketRoot>/<ticket>/state.json` (current view) and `decisions.jsonl` (append-only log).

## Onboarding (`/kaneki-onboarding`)

The profile is produced, not hand-written: deterministic detectors (`harness/onboard.py`) propose every value
with evidence and confidence, the read-only commands found in CI and scripts are run, agents look only at
repositories where detection is weak, and the user answers only what is left. Output:

- `sdlc.profile.json` with a `meta` block;
- `sdlc.profile.evidence.json` (`contracts/profile-evidence.schema.json`): per field, evidence, confidence,
  status (`detected` / `default` / `user`) and the value written, plus the command results and open fields;
- `.sdlc/adapters/stack/<repo>.json`: a project stack adapter that `extends` the bundled one with the commands
  verified in this repo (a `draft` adapter when no bundled stack fits).

Ownership rule on `--refresh`: a field is yours if you answered it, added it by hand, or changed it since it was
written. Yours is never overwritten; the diff reports it as `kept-yours`. Generated adapters follow the same rule
by digest.

## Adding things

- **New stack:** `adapters/stack/<id>.json` (+ optional `rulepacks/stack/<id>.json`, loaded automatically).
- **New platform / verification surface:** `adapters/surface/<id>.json`, then map it in `profile.evidence.surfaces`.
- **New tracker or code host:** `adapters/tracker/<id>.json` / `adapters/delivery/<id>.json`.
- **Project rules:** a rulepack file next to the profile, listed in `profile.rulepacks`.
- **Capability that needs its own steps** (interfaces, migrations, ML model retrain, mobile store release):
  a family with an extension file (`contracts/extension.schema.json`). Use `feeds` so core nodes wait for it.
- **New core node:** only if every project needs it. Otherwise it is a family.

## Open decisions for v0.2

1. JSON Schema validation with `jsonschema` (today: a light built-in check that mirrors the schemas).
2. A `sdlc-ship` skill wrapping the delivery adapter's `ship` steps behind a signed user approval.
3. Citation checker for receipt nodes (port of `verify-subject`).
4. Resume-vs-fresh agent policy for second rounds (token savings measured in wms-flow).
5. Finished-ticket trace (audit existing code, no change).
