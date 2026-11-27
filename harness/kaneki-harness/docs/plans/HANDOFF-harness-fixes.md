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
2. Judge model pair for the two-judge rule (M4).
3. Run the controller on Sonnet (D9, M5)?
4. Not checked yet: can `harness/runner.py` (`run`/`tick`) dispatch units headlessly?

## Working notes

- On this machine `python -I` ignores `PYTHONIOENCODING`; use `python -X utf8 -I` for scripts that print transcript text.
- The haise-doc `check_doc.py` imports a sibling module, so run it without `-I`.
- After changing the plan doc, re-run `check_doc.py`. After changing the notebook template, rebuild with `build.py --ticket-dir .sdlc/tickets/feature-flags --worktree worktrees/my-kaneki-feature-flags --project . --base e469f50 --head 05c1a77 --out .sdlc/tickets/feature-flags/tester-notebook.html` from `D:/workspace/HAISE/wp-kaneki`.
