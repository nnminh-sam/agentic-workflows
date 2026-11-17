# mygit CLI — handoff (2026-11-17)

Spec: `../docs/mygit-cli-plan.html`. Conventions, package map, and build/test commands: `DEVELOPMENT.md`.

## Session 2026-11-17: commit / branch / push / pr / repo

**Branch:** `develop/feat/my-git-cli-commands` (worktree `.claude/worktrees/integration`), based on `12dfc65`. It has one commit per command, and build, vet and test all pass on it:

| Commit | Command | Main files |
|---|---|---|
| 26ffa17 | `push` (order 63), shared `app.PlanPush` | internal/app/push.go, internal/cli/push.go |
| 726f95a | `branch new/cut/prune` (62) | internal/cli/branch.go, internal/workflow/branch_ops.go |
| d3fa546 | `commit` (61) | internal/cli/commit.go, internal/app/commit_ops.go |
| e48ed00 | `repo create/protect/bootstrap` (65) | internal/cli/repo.go, internal/repoops/ |
| 7608fe0 | `pr create/release/run/check/template` (64) | internal/cli/pr.go, internal/pr/ |

**Review:** three Opus reviews ran. The findings, the owner's decisions and the default answers to the open questions are in `docs/review-2026-11-17.md`. Fix those findings before merging into `develop/feat/my-git-cli`. Three fixes block it:
- `pr --apply <id>` usually goes stale, because raw test output is hashed into the PR body.
- `pr release` reads the stale local `develop/base`.
- Every PR is blocked on Windows by GH008 "unreadable".

There are also two High findings in `branch`: `switch -c` sets up tracking of the base branch, and `prune -d` can fail partway through the plan.

**Owner decisions:**
- `delete_branch_on_merge` is off by default, with the config key `github.deleteBranchOnMerge`.
- Failing PR checks give an ask finding; `--allow-failing-checks` overrides it.
- `push` adds `-u` automatically for a new branch.
- `pr release` does not fetch; it uses `<remote>/<head>`.

**Fix status:** every High and Medium review finding is fixed and merged into this branch:
- daedb89: branch/commit fixes
- 8289336: repo fixes
- e9b500e: push/pr fixes, including the three blocking pr bugs, and `pr` now uses `app.PlanPush`

The only finding skipped is L14, a low-priority shared helper for setting up the rules engine. After the merges, `gofmt -s -l .` is clean, `go vet ./...` and `go test ./...` pass, and `GOOS=windows go build ./...` passes.

**Behaviour changes from the fixes:**
- pr: failing test or lint now exits 1 (Ask finding `PR-CHECKS`); `--allow-failing-checks` overrides it.
- pr: a head that is behind its remote branch gets an Ask finding (`PR-SYNC`).
- pr: test and lint commands always run through `sh -c` or `cmd /S /C`.
- push: a new branch gets `-u` automatically.
- push: the ssh key in the push command is always quoted, and a leading `~` is expanded.
- branch prune: exits 2 if the ID005 account check denies.
- repo create: also pushes the long-lived branches (prod and staging).

**Next steps:**
1. Merge or open a PR from `develop/feat/my-git-cli-commands` into `develop/feat/my-git-cli`.
2. Remove the agent worktrees and branches under `.claude/worktrees/`: `agent-*` (branches `worktree-agent-*`), `fix-*` (branches `fix/*`), and `integration` once merged.
3. Continue with the "Not done" list below. Item 1 is done; items 2–6 remain. Also open: L14, the shared engine-deps helper.

**Correction:** the default workflow template is `defaults/workflows/default.json`. `branching.json` is the BR rules contract, whose verdicts the default template must reproduce. The table below is the 2026-11-14 state.

---

## Done

| Area | Packages | Tests |
|---|---|---|
| Core types | verdict, execx (Runner + Fake), policy (+ Substitute) | 100% coverage |
| Config, rule sets, profiles, workflow templates | config, profile, workflow | ≥97%. The default workflow template is `defaults/workflows/default.json`; `branching.json` is the BR rules contract whose verdicts the default template must reproduce (exit criterion); the spec's `rules show --origin` example reproduces exactly |
| Rules engine (35 rules + ID006) | shell (mvdan + PowerShell lexer), rules, engine | 111/118 Python cases match. The 7 skipped test `contracts.validate`, which has no Go port. 112 golden commands in `internal/engine/testdata/cases.json` |
| Plan core, shell renderers, git/gh wrappers, templates | plan, shellfmt, gitx, ghx, tmpl | ≥98.8% |
| Shared CLI plumbing (dry-run → save → apply/manual) | app, cli/plumbing.go | **no tests** |
| Commands: config, profile, rules, workflow, init, doctor, sign, plan | cli/*.go, sign | **no tests** |
| Commands: guard, hook, hooks, agent, lint | agent, agent/githook, cli | partial tests in internal/agent (a tester was stopped mid-way); githook has none |
| Commands: graph, flow | graph | **no tests** |

## Not done (next steps, in order)

1. **`commit`, `branch`, `push`, `pr`, `repo` commands** (not started). Build them on `internal/app` (`Env.NewPlanner`, `Executor.Run`); use `cli/profile.go` and `cli/init.go` as the pattern. They should follow the spec's "Working with gh" ①–⑧ sequence and pin identity and signing with `-c` flags. Register them at orders 60–75.
2. **Dedupe:** replace the private helpers in `cli/guard.go` (guardDeps/guardEnv/guardResolve/guardRunPlan), the private plan runner in `hooks.go`/`agent.go`, and `graphResolve` in `graph.go`/`flow.go` (marked TODO(orchestrator)) with `internal/app`. Route `graph/flow --fetch` through the plan runner.
3. **Tests still missing:** internal/app, internal/sign, internal/cli (e2e via `cli.Main` with temp HOME/XDG dirs), internal/graph, internal/agent/githook, and the rest of internal/agent.
4. **Known bugs:**
   - `internal/rules/identity.go:158` `signFlags`: the value of `-m '-S fix'` is read as the `-S` flag, so ID006 misses it. There is a skipped test in `internal/engine/signing_test.go`.
   - Graph connector rows look wrong on the real repo (`├─┤` between commits, `├─┼─╮` after a merge). Check `Layout` against `git log --graph`.
   - The spec says `git commit -m "Added stuff."` gives CM003 + CM008. Both Python and Go give only CM003, so the spec text needs fixing.
5. **Gaps:**
   - No Go `contracts.validate` (regex/param checking of contract files).
   - `workflow use` doesn't plan branch, default-branch or ruleset changes; `workflow diagram` isn't built.
   - The lint `try:` hint isn't printed.
   - agy user-scope install errors out (its location is unconfirmed).
   - File remove/chmod in hooks install are raw `rm`/`chmod` steps; they should be `mygit file …` primitives.
6. **Release (phase 7):** goreleaser, Homebrew tap, and switching `../hooks/hooks.json` to `mygit guard --agent claude`.

## Notes
- Python-parity generators: `gen_cases.py`, `gen_funcs.py` in `cli/scripts/` (regenerate `testdata/*.json` from `../scripts/gitguard` if needed).
- Open design questions remain as listed in the spec (Codex/agy hook formats, fail-open shims, JSONC, locked default, plan expiry).
