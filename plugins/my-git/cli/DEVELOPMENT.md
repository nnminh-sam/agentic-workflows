# mygit — developer guide

Architecture source of truth: `../docs/mygit-cli-plan.html` (plain-text copy for
agents: ask the orchestrator). Python reference implementation being ported:
`../scripts/gitguard/*.py`, tests in `../tests/*.py`, contracts in `../contracts/`.

## Commands

| Task | Command |
|---|---|
| Build `bin/mygit` | `make build` |
| All tests | `make test` |
| Unit tests only (no real git) | `make test-short` |
| Race detector | `make test-race` |
| Coverage | `make cover` |
| vet + gofmt check | `make lint` |
| Format | `make fmt` |
| Cross-compile (darwin/linux/windows × amd64/arm64) | `make cross` |
| Everything CI runs | `make ci` |

## Layout and dependency direction

```
cmd/mygit            → internal/cli
internal/cli         → every package below (thin: flags → call → print)
internal/agent       → engine, rules, policy, shell, verdict (adapters: claude, codex, agy, json; githook)
internal/engine      → shell, rules, policy, verdict
internal/rules       → policy, verdict, shell    (pure: repo facts come through rules.Repo)
internal/shell       → (mvdan.cc/sh)             (parse command lines → []Invocation)
internal/config      → policy, workflow, profile, defaults
internal/workflow    → defaults
internal/profile     → (config types only via small structs)
internal/plan        → execx, shellfmt, verdict
internal/shellfmt    → (none; pure string functions)
internal/gitx, ghx   → execx
internal/sign        → gitx, ghx, plan
internal/graph       → gitx, ghx, workflow
internal/tmpl        → defaults
internal/repoops     → workflow                  (pure: slug, ruleset, bootstrap steps for `mygit repo`)
internal/pr          → app, config, engine, execx, ghx, policy, profile, rules, shell, shellfmt, sign, tmpl, verdict, workflow
internal/policy      → verdict                   (resolved contracts: read-only engine input)
internal/verdict     → (none)                    (Finding, Verdict, Severity, Decision)
internal/execx       → (os/exec)                 (Runner interface + Fake: the ONLY os/exec user)
defaults/            embedded contracts/*.json, templates/*, workflows/*.json
```

No import cycles; lower packages never import `internal/cli`.

## Shared contracts (owned by the orchestrator — do not change signatures without asking)

- `verdict.Severity` (Off < Warn < Ask < Deny), `verdict.Verdict`, `Decision.ExitCode()`
  (0 allow/warn, 1 ask, 2 deny).
- `execx.Runner` / `execx.Cmd` / `execx.Result` / `execx.Fake`. A non-zero exit is not an
  error. **Nothing outside execx calls os/exec.**
- `policy.Policy` / `policy.Contract` / `policy.Rule` + typed param accessors. Settings and
  params keep the Python snake_case keys from `defaults/contracts/*.json`.
- `cli.App` (IO + GlobalOptions), `cli.register(order, factory)`, `cli.ExitError`.

## Conventions

- Go 1.23+, standard library first. Third-party: `cobra`, `mvdan.cc/sh/v3` only (ask before adding).
- Every side effect behind an interface: processes via `execx.Runner`, files via plain
  paths passed in (tests use `t.TempDir()`), env via `func(string) string`, time via `func() time.Time`.
- No package-level mutable state (except the cli command registry). No `init()` except `register`.
- Errors: wrap with `fmt.Errorf("context: %w", err)`; user-facing messages lower-case, no trailing period.
- Mutating commands build a `plan.Plan` and never mutate in dry-run. Only `plan.Apply` executes writes.
- `--json` output: one JSON object on stdout, stable field names (lowerCamelCase).
- Tests: table-driven, `package x` for internals, `package x_test` for API; tests needing a real
  `git` binary call `testutil`-style helpers that `t.Skip` under `-short`. Golden files live in
  `testdata/` of the package; regenerate with `go test ./... -update` where supported.
- gofmt -s clean, go vet clean, doc comment on every exported identifier.
