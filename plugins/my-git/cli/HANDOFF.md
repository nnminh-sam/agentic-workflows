# mygit CLI — handoff (2026-11-17)

Spec: `../docs/mygit-cli-plan.html`. Conventions, package map, and build/test commands: `DEVELOPMENT.md`.

## Session 2026-11-17: commit / branch / push / pr / repo

**Branch:** `develop/feat/my-git-cli-commands` (worktree `<repo root>/.claude/worktrees/integration`), based on `12dfc65`. It has one commit per command, then review fixes and handoff docs (`git log 12dfc65..`). Build, vet and test all pass on it.

| Commit | Command | Main files |
|---|---|---|
| 26ffa17 | `push` (order 63), shared `app.PlanPush` | internal/app/push.go, internal/cli/push.go |
| 726f95a | `branch new/cut/prune` (62) | internal/cli/branch.go, internal/workflow/branch_ops.go |
| d3fa546 | `commit` (61) | internal/cli/commit.go, internal/app/commit_ops.go |
| e48ed00 | `repo create/protect/bootstrap` (65) | internal/cli/repo.go, internal/repoops/ |
| 7608fe0 | `pr create/release/run/check/template` (64) | internal/cli/pr.go, internal/pr/ |

**Review:** three Opus reviews ran. The findings, the owner's decisions and the default answers to the open questions are in `docs/review-2026-11-17.md`. Every finding is fixed except L14 (see **Fix status**).

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
- graph / flow: `--fetch` now runs through the shared plan runner (dry-run prints the fetch plan and stops; `--yes` or `--apply <id>` runs `git fetch --prune <remote>` and then renders the view).
- hooks / agent: `install` and `uninstall` now accept `--out` for saving manual-mode scripts.

**Next steps:**
1. ~~Merge or open a PR from `develop/feat/my-git-cli-commands` into `develop/feat/my-git-cli`.~~ **Done.**
2. ~~Remove the agent worktrees and branches under `.claude/worktrees/`: `agent-*` (branches `worktree-agent-*`), `fix-*` (branches `fix/*`), and `integration` once merged.~~ **Done.**
3. ~~Continue with the "Not done" list below, from item 2 (Dedupe, missing tests, known bugs).~~ **Done.**

**Correction:** the default workflow template is `defaults/workflows/default.json`. `branching.json` is the BR rules contract, whose verdicts the default template must reproduce.

---

## Done

| Area | Packages | Tests |
|---|---|---|
| Core types | verdict, execx (Runner + Fake), policy (+ Substitute) | 100% coverage |
| Config, rule sets, profiles, workflow templates | config, profile, workflow | ≥97%. The default workflow template is `defaults/workflows/default.json`; `branching.json` is the BR rules contract whose verdicts the default template must reproduce (exit criterion); the spec's `rules show --origin` example reproduces exactly |
| Rules engine (35 rules + ID006) | shell (mvdan + PowerShell lexer), rules, engine | 111/118 Python cases match. The 7 skipped test `contracts.validate`, which has no Go port. 112 golden commands in `internal/engine/testdata/cases.json` |
| Plan core, shell renderers, git/gh wrappers, templates | plan, shellfmt, gitx, ghx, tmpl | ≥98.8% |
| Shared CLI plumbing (dry-run → save → apply/manual) | app, cli/plumbing.go | tested (`internal/app/app_test.go`, `internal/cli/commands_test.go`) |
| Commands: config, profile, rules, workflow, init, doctor, sign, plan | cli/*.go, sign | tested (`internal/sign/sign_test.go`, `internal/cli/commands_test.go`) |
| Commands: guard, hook, hooks, agent, lint | agent, agent/githook, cli | 100% in `internal/agent` and `internal/agent/githook`; e2e tested in `internal/cli` |
| Commands: graph, flow | graph | ~90% in `internal/graph`; e2e tested in `internal/cli` |
| Commands: commit, branch, push, pr, repo | cli, app, pr, repoops, workflow | unit + e2e tested (`internal/pr` at 83.7%, `internal/cli` at 76.4%, `internal/app` at 74.5%) |

## Not done (next steps, in order)

1. ~~`commit`, `branch`, `push`, `pr`, `repo` commands~~ **Done 2026-11-17** (see above).
2. ~~**Dedupe:** replace private helpers in `guard.go`, `hooks.go`, `agent.go`, `graph.go`, `flow.go` with `internal/app` (`Env.EngineDeps()`, `newSession`, shared plan runner). Route `graph/flow --fetch` through the plan runner.~~ **Done 2026-11-17.**
3. ~~**Tests still missing:** `internal/app`, `internal/sign`, `internal/cli` e2e for older commands, `internal/graph`, `internal/agent/githook`, `internal/pr`.~~ **Done 2026-11-17.**
4. **Known bugs:**
   - ~~`internal/rules/identity.go:158` `signFlags`: the value of `-m '-S fix'` is read as the `-S` flag, so ID006 misses it.~~ **Fixed** (`m_separate_value_dash_S`, `-u -S`, `--untracked-files -S`, and `-S<keyid>` tested and passing).
   - ~~`internal/graph/layout.go` `placeMergeParents`: reused a lane freed by `placeFirstParent`'s pull-left in the same row.~~ **Fixed** (`TestLayoutNoLaneRecycledInSameRow` unskipped and passing).
   - **Still open:** `internal/graph/render_text.go` `connectorLine` (`TestConnectorJunctionVsCrossing` in `internal/graph/layout_test.go:490`): when a node has edges into two lanes on the same side, the nearer lane is both an endpoint and crossed by the farther edge; `connectorLine` draws `┼` there (the same glyph as a plain crossing), hiding the nearer edge.
   - ~~The spec says `git commit -m "Added stuff."` gives CM003 + CM008. Both Python and Go give only CM003, so the spec text needs fixing.~~ **Fixed** in `../docs/mygit-cli-plan.html`.
5. **Gaps:**
   - No Go `contracts.validate` (regex/param checking of contract files).
   - `workflow use` doesn't plan branch, default-branch or ruleset changes; `workflow diagram` isn't built.
   - The lint `try:` hint isn't printed.
   - agy user-scope install errors out (its location is unconfirmed).
   - File remove/chmod in hooks install are raw `rm`/`chmod` steps; they should be `mygit file …` primitives.
   - `defaults/workflows/default.json` lists `work.types` literally. The spec uses `"{commit.types}"`, so a commit type added in config is rejected as a branch type by `branch new` and BR003.
6. **Release (phase 7):** goreleaser, Homebrew tap, and switching `../hooks/hooks.json` to `mygit guard --agent claude`.

## Notes
- Python-parity generators: `gen_cases.py`, `gen_funcs.py` in `cli/scripts/` (regenerate `testdata/*.json` from `../scripts/gitguard` if needed).
- Open design questions remain as listed in the spec (Codex/agy hook formats, fail-open shims, JSONC, locked default, plan expiry).
