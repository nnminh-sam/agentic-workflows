# Handoff: implement the harness fix plan

Written 2026-11-27 at the end of the analysis session. Start the next session by reading this file, then the plan.

## Goal

Implement the plan in `docs/plans/harness-fixes-and-tester-notebook-plan.html` (open it in a browser; its title is "The Kaneki harness before and after the fix"). Target: a feature the size of feature-flags goes from 8 h 18 m to about 3 h, with at most 2 generations per node. Review nodes become trusted judges, evidence runs without throwaway scripts, and cost is measured correctly.

## Rules from the user (binding)

1. **Review authority:** the testcase-review and evidence-review nodes are trusted agent judges that decide whether tests and code are valid, from the ticket's requirements and the evidence. The user is asked only about business, infra, or important logic with no citation to decide on. **Never add a user approval gate for test validity.** (Also saved as memory `review-authority`.)
2. **The tester notebook is a read-only view** for looking back. It is not a gate and has no decision buttons.
3. **Evidence must not depend on per-ticket throwaway scripts.** Use repo tests written in coding, declarative steps run by a harness runner, and a harness-owned environment.
4. Workspace rules: never delete branches; GitHub over SSH only, with the pinned `sam-github` key (`core.sshCommand` is already set in this repo); commit or push only when the user asks; my-git PR mode is browser (no `gh`).

## Where things are

| What | Path |
|---|---|
| Plan (HTML, the source of truth) | `harness/kaneki-harness/docs/plans/harness-fixes-and-tester-notebook-plan.html` |
| Notebook prototype (becomes `sdlc report`) | `harness/kaneki-harness/docs/prototypes/tester-notebook/build.py`, `template.html` |
| Token audit script (counts every API message once) | `harness/kaneki-harness/docs/prototypes/run-audit/tok.py` (hard-codes the 3 feature-flags session ids; generalise when used in tests) |
| Test-bed ticket | `D:/workspace/HAISE/wp-kaneki/.sdlc/tickets/feature-flags/` (shipped, PR nnminh-sam/my-kaneki#2) |
| Test-bed worktree | `D:/workspace/HAISE/wp-kaneki/worktrees/my-kaneki-feature-flags` (base `e469f50`, head `05c1a77`) |
| Session logs used as ground truth | `~/.claude/projects/D--workspace-HAISE-wp-kaneki/{17e83121…,a3c4afd6…,38462dbc…}.jsonl` + `*/subagents/` |
| Harness tests | `harness/kaneki-harness/tests/` (33 files); run `python -m unittest discover -s tests -v` from `harness/kaneki-harness` |

Repo state: branch `develop/feat/kz-cost-accuracy` (from `origin/develop/base` 96cf7fe, no upstream set). The plan and prototype docs go in the same branch (user, 2026-11-27).

## Progress

**M1 implemented (2026-11-27), all verified on feature-flags:**

| Task | Result |
|---|---|
| A1 | `SDLC_RE` matches `sdlc-<host>(.cmd)` and `invalidate`; only state-changing commands attribute (read-only `check`/`status`/`cost` do not; a session debugging the ticket after it shipped had added 19.6 M); `--node $n` (shell variable) yields no node; file-edit text (`new_string`, `content` …) is no longer read as a call. `sdlc cost --ticket feature-flags` = **113,735,785 tokens, $38.68**; controller 62,173,828 (exact) |
| A2 | `record` measures the dispatch from its log(s) (`cost.dispatch_usage`): `tokens` (measured), `tokensSource`, `tokensMeasured` by class + USD, `contextAtEnd` (= `--tokens`), `dispatchId`. prepare g1 = 658,424 |
| A3 | `usage.ticket_tokens` is the one total for STATUS.md, token-usage.md and `status --compact` (per node, per model, fresh / cache write / cache read / output + USD); all three show 113,735,785 |
| A5 | `record --status abandoned --reason` (no result file); a packet run in several sessions lists the extras in `abandonedAttempts` (execute-and-evidence g1 r1: 2 sessions, 14.72 M); `sdlc dispatch` records its timeouts/failures as abandoned; the gate refuses a node with a unit log that has no record |
| B1 | `sdlc --profile P <cmd>` works (leading `--profile`/`--host` are moved behind the command; this form was broken for every command); `sdlc digest <path>`; packets have a Driver section with the absolute sh form (Git Bash too) and the PowerShell form; role cards and agent files check the packet digest with `sha256sum` / `Get-FileHash` |
| B2 | `sdlc check --ticket T --node N [--unit U]`: the gate's content checks as a read-only dry run; the packet's return contract requires it; citations resolve relative to the project folder too, and a bare file name / partial path unique in the worktree resolves (ambiguous ones are named) |

Not done in M1: the project copies under `wp-kaneki/.claude/` (agents, node cards) are refreshed only by `sdlc install`, which the user runs. The full test suite takes ~10 min.

M1 is committed (`786554b`, not pushed).

**M2 committed (`71c3d2c`, 2026-11-27, not pushed):**

| Task | Result |
|---|---|
| D5 | Node records carry `reopens` (review loop-backs and manual `invalidate` of that node; reset per PR review round). G4 uses max(review attempts, reopens of the loop-back target); `invalidate` past the cap needs `--actor user`; gate-request reopen targets carry `needsUser`, `sdlc decide` adds `--actor user` |
| A4 | `budget.DEFAULTS` apply when a profile has no `budgets` block (ticket 60 M tokens; node 15 M / 120 min; evidence 20 M / 60 min); `{}` turns budgets off. Node caps take `minutes` (wall clock from the round's first packet, `startedAt` in state); packet refuses past it (G8), the packet names the deadline, the runner stops, `dispatch` caps its timeout |
| D7 | `harness/questions.py`: `sdlc ask` (class business / infra / critical-logic; `judgeable` refused), `sdlc questions` (one batch), `sdlc answer` (user; LOW `--adopt` by controller). Plan nodes refuse to close over an open HIGH question (G1); business questions after the plan are `late` and reach reflect. `questions.md` is generated |
| B5 | delivery-drafts gate + `sdlc check`: commit message, PR body (my-git `check_pr.py`: GH005/GH008/GH009) and my-git registration (`mygit.readiness`). final-review refuses untracked files its review does not name |
| E3 | `harness/env.py`: `sdlc env check|up|status|down|exec` from profile `environment.evidence` (DB floor via ALTER SYSTEM + one restart, per-ticket DB, migrate, background server + health wait, seed, `env.json` without secrets). wp-kaneki profile has the block (max_connections floor 100, DB `kaneki_{ticket}`, port 3100) and the web `baseUrl` now points at `http://localhost:3100` (was production). Profile backup: none in the repo; the previous file was copied to the session scratchpad |
| B4 | Preflight = `sdlc env check` (run by prepare); breaker = the runner's infra rule below |
| B3 | `sdlc baseline` → `baseline.json` (install, lint, unit tests, build on the found checkout); prepare's artifacts include it; the gate refuses a failed install |
| E4 | `harness/evidence.py`: `sdlc evidence run|status` (smoke first, resume, infra retried 2× with back-off, breaker after 2 infra cases in a row, step error fixed once, product FAIL never retried, stop at the node's time cap) |

Open from M2:
- `sdlc env up` was not run on the real machine: it restarts `my-kaneki-postgres-1` to raise max_connections 20 → 100 (the user runs it, or approves it).
- The my-git guard reads `git -C "$R"` literally (ship detour 38462dbc:813). The fix (expand variables assigned on the same command line in `plugins/my-git/scripts/gitguard/commands.py`) was blocked by auto mode as a change to a safety control: the user decides.
- Project copies need `sdlc install --target . --host claude` again (node cards, packet template).

**M3 committed (`3834dd3`, 2026-11-27, not pushed):**

| Task | Result |
|---|---|
| C1 | `contracts/testcases.schema.json` (criteria, cases with risk, checks `TC-n.aN` with `expect`/`value`, `run`: repo-test / steps / manual) and `contracts/evidence.schema.json` (per check: PASS, FAIL, UNOBSERVED, NOT-RUN, MANUAL with observed value and source). Gate parsers `testcases` (schema, ids, every criterion covered, critical cases not manual) and `evidence` (every check present). The testcase node now writes `testcases.json` (no `testcases.md`); execute-and-evidence adds `evidence.json`. Superseded data files move to `superseded/<name>.g<n>.json` |
| C3 | `adapters/evidence/node/kaneki-evidence.mjs` and `python/kaneki_evidence.py`: `evidence(check, {observed, expected})` appends a JSON line with the calling test line to `$KANEKI_EVIDENCE_FILE` (no-op outside a run). Stack adapters name it in `evidenceHelper`; `sdlc evidence helper --repo R` copies it into the checkout |
| E1 / C4 | Cards: testcase writes the JSON; coding implements `repo-test` cases as repo tests (e2e for live surfaces) that record checks through `evidence()`; testcase-review reviews the JSON with requirement citations; execute-and-evidence only runs (`sdlc evidence run`), never authors; reflect proposes missing kit helpers |
| E2 | `harness/steps.py` + `contracts/steps.schema.json`: declarative HTTP-level cases (login as a kit account, http, page goto, kit helpers, capture; expect status / textPresent / textAbsent / json / header; params), validated against schema, kit and testcases.json before the run; browser interactions refused (write an e2e test) |
| E5 | `contracts/evidence-kit.schema.json`; kit at `.sdlc/adapters/evidence/<project id>/kit.json`. wp-kaneki has a starter kit (accounts A/B and the login from `seed.mjs`); no `flag` helper yet (toggle is a server action on `feature_flags`; add it tested when a case needs it) |
| C2 | `sdlc report` (`harness/report.py`, `harness/report_template.html`): the tester notebook from testcases.json, evidence.json, run.json, questions.json and the git diff; no review state or decision buttons (only the theme is stored) |
| runner | `sdlc evidence run` without `--cases` runs testcases.json (step files run in-process, a fresh observation file per attempt) and writes evidence.json; a passing test that never recorded a check leaves it UNOBSERVED |

Open from M3: the report was checked by tests and a JS syntax check, not opened in a browser against a real ticket (feature-flags has no testcases.json; the prototype backfill stays for it).

**M4 implemented (2026-11-27, uncommitted on the same branch). Judge pair: Opus + Sonnet (user).**

| Task | Result |
|---|---|
| D1 | Graph order: plan → testcase → testcase-review → coding; coding requires `reviewed-testcases` |
| D2 | feature-plan worker on deep; feature-plan and fix-plan get a deep `reviewer` (plan review before the user sees it) |
| D3 | code-review's technical and semantic units run in parallel (no `after`; pattern `parallel-units-controller-judged`) |
| J1/J4 | execute-and-evidence `reviewer` = judge A (deep, every check) and `reviewer-2` = judge B (routine, critical checks) via `reviewer.second` in the graph; testcase-review is a fresh deep judge |
| J2 | `harness/judge.py` `facts()` (`sdlc judge-facts`): EVD-A observed, EVD-B observed = expected, EVD-C case ran, EVD-D critical check has a source, EVD-M critical mutation killed, EVD-E changed files observed. `contracts/judgement.schema.json`; verdicts need requirement + evidence citations whose quotes are found verbatim at the cited line; FAIL needs `routeTo`; Low confidence must ESCALATE; ESCALATE must be asked (`sdlc ask`). Gate: both judgements valid, A and B agree on critical checks (else G1 until the user answers a critical-logic question naming the check), FAILs routed, escalations answered |
| J3 | `sdlc mutate`: per critical check in `mutations.json` (written in coding) break the guarded line, run the case command, restore the file (backup restored first if a previous run died); `evidence/mutations.json` killed / survived / error |
| J5 | Approved verdicts → `<project>/.sdlc/judge-calibration.jsonl`; `sdlc judge-miss`; miss rate > 5 % over the last 200 verdicts → only High-confidence PASS stands |
| D4 | `sdlc invalidate --node coding --small tests|docs`: keeps code-review (and, docs, the evidence run); the coding gate refuses changes outside test files (one shared test-file pattern incl. Python/Go/JVM conventions) or docs since the reopen |
| D6 | `state.ready_nodes`; `ready` in `status --compact` and the board summary; flow.md starts every ready node |

Not done in M4: the runner (`harness/runner.py`) still starts one node per pass; D6 gives it and the controller the ready list. The test-case judge keeps its markdown verdict (its determinable checks are the `testcases` parser).

## Milestone M1 (done; kept for reference)

| Task | Change | Where | Verify |
|---|---|---|---|
| A1 | `SDLC_RE` must also match `sdlc-claude(.cmd)`, `sdlc-<host>`, quoted or not | `harness/sessions.py:44-45` | New test fed with real controller lines; `.sdlc/bin/sdlc cost --ticket feature-flags` (run in `wp-kaneki`) shows controller ≈ 62.2 M and total ≈ 113.7 M |
| A2 | At `record`, the driver measures unit tokens from the unit's own log (packet match already exists in `sessions.py`) and stores `tokensMeasured` by class; the reported value becomes `contextAtEnd` | `harness/cli.py` (`cmd_record`), `harness/cost.py` | prepare g1 = 658,424 (not 41,704) |
| A3 | STATUS.md, `token-usage.md` and `status --compact` all read `cost.measured()`; show fresh / cache write / cache read / output + USD | `harness/board.py`, `harness/usage.py` | All three views agree |
| A5 | Record every dispatch, including abandoned ones (feature-flags: `execute-and-evidence.g1.worker.r1` dispatched 3 times, 2 unrecorded, 14.7 M tokens) | `harness/cli.py` dispatch/record | No unit log without a record |
| B1 | Packets carry the absolute launcher path; `sdlc digest <path>` accepts a positional path; the Bash launcher does not hang via `cmd.exe` | packet template, `cli.py` digest, `.sdlc/bin` launchers | No `command not found` (was 40×) or digest errors (26×) |
| B2 | `sdlc check --ticket T --node N [--unit U]` = the node's G7 checks as a dry run (sections, `Verdict:` line, citations, `proposals.json` schema, repo commit/PR rules); node cards require it before hand-back; the gate accepts a bare filename that is unique in the worktree | `harness/checks.py`, `harness/cli.py`, `playbooks/nodes/*.md` | No G7 refusal after hand-back |

Then M2 → M5 as listed in the plan's "Work plan" section: A4, B3–B5, E1–E5, C1–C4, J1–J5, D1–D9, then a pilot ticket compared with the plan's "How we will know it worked" table.

## Key facts already established (don't re-derive)

- Real cost of feature-flags: **113,735,785 tokens** (units 51.56 M + controllers 62.17 M; 96% cache reads), $38.68 by Claude Code's own cost rows. Recorded values: `token-usage.md` 2,284,355; STATUS 997,808; `sdlc cost` 51,561,957 with controller 0.
- `record --tokens` stores the unit's **last-turn context size**, not what it consumed.
- Timeline: 26 questions to the user, 129 min waiting. 2 abandoned evidence runs (07:36–09:35 UTC). Coding and code review each ran 5 generations. Context ran out at `a3c4afd6:2751`.
- Evidence scripts: 12 files (~1,000 lines). 30 run failures: 20 environment (12 `too many clients`, 8 Neon control plane), 9 UI step (6 selector, 3 timeout), 1 login.
- Notebook findings classes for feature-flags: determinable 5, judgeable 20, infra 8, business 0, recorded 4.

## Open questions for the user (ask when the related milestone starts)

1. ~~Evidence database for my-kaneki~~ **Answered 2026-11-27: raise `max_connections`.** `sdlc env up` configures the local `my-kaneki-postgres-1` container with a higher `max_connections` (the Neon proxy exhausted 20) and a per-ticket throwaway DB; no pooler; live checks stay on.
2. ~~Judge model pair (M4)~~ **Answered 2026-11-27: Opus + Sonnet** (deep tier + routine tier; independent models, lower cost than two Opus).
3. Run the controller on Sonnet (D9, M5)?
4. Not checked yet: can `harness/runner.py` (`run`/`tick`) dispatch units headlessly?

## Working notes

- On this machine `python -I` ignores `PYTHONIOENCODING`; use `python -X utf8 -I` for scripts that print transcript text.
- The haise-doc `check_doc.py` imports a sibling module, so run it without `-I`.
- After changing the plan doc, re-run `check_doc.py`. After changing the notebook template, rebuild with `build.py --ticket-dir .sdlc/tickets/feature-flags --worktree worktrees/my-kaneki-feature-flags --project . --base e469f50 --head 05c1a77 --out .sdlc/tickets/feature-flags/tester-notebook.html` from `D:/workspace/HAISE/wp-kaneki`.
