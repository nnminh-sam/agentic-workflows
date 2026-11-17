# mygit CLI — handoff (2026-11-14)

Spec: `../docs/mygit-cli-plan.html`. Conventions, package map, and build/test commands: `DEVELOPMENT.md`.
State at handoff: `go build ./...`, `go vet ./...` and `go test ./...` all pass.

## Done

| Area | Packages | Tests |
|---|---|---|
| Core types | verdict, execx (Runner + Fake), policy (+ Substitute) | 100% coverage |
| Config, rule sets, profiles, workflow templates | config, profile, workflow | ≥97%. Default template = `branching.json` (exit criterion); the spec's `rules show --origin` example reproduces exactly |
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
