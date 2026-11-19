# P8 · Setup, settings, worktrees, packaging and quality

Paths are relative to `D:/workspace/HAISE/wp-agentic-workflows/agentic-workflows/` unless absolute. `H` = `harness/kaneki-harness/`.

## 1. Goal and Done when
**Goal.** Everything around the core board: first run and settings, new ticket, repo onboarding, worktree list and
cleanup, an installable build, and one quality story (tests, CI, versioning) shared by all parts.

**Done when**
- A fresh Windows user profile installs the app, completes first run (workspace, harness home, Python, editor) and sees
  the board for a copied workspace, with no manual file editing.
- "New ticket" runs `sdlc init` after a command preview and shows the `trace`/`warnings` it returns; init errors (G7) are shown verbatim.
- Worktree screen lists worktrees; the only removal action runs `git worktree remove`; no UI, IPC call or Host code path can delete a branch (asserted by a test).
- `npm run ci` (or `just ci`) runs every test layer locally; GitHub Actions runs it on `windows-latest` and builds the installer artifact.
- `doc/` holds user guide, dev guide, release checklist, changelog.

## 2. Scope
**In:** config file and settings screen; first-run wizard; new-ticket form; onboarding (read-only scan/preview in app, writing via Claude session in v1); worktrees screen; installer, WebView2, signing/update decisions, harness discovery; test pyramid, CI, lint, versioning; docs.
**Out:** stack choice (P2), IPC contract/types (P1), price table and cost math (P7, only the settings field is here), session spawning (P4), run policy (P5/other), shipping (never from the app, D6).

## 3. Inputs and outputs
**Needs from other parts**
- P1: `invoke(name,args)` envelope, error shape (harness `{"error": "G7: ..."}` passes through, see `H/skills/kaneki-flow/scripts/sdlc.py:26`), `runSdlc(args[])` Host primitive with command preview (D2), a Host `runGit` primitive limited to an allow-list.
- P2: the Wails build config; the Go toolchain install story (`D:\toolchains`).
- P7: price table schema; P8 only stores the user override path and the "prices last updated" display.
- P4: the "spawn Claude session" primitive, used by onboarding handoff.

**Provides (IPC)**

| Call | Args | Returns | Mutates |
|---|---|---|---|
| `config.get` / `config.set` | patch | `AppConfig` (below) | app config only |
| `env.probe` | none | `{python:{path,version,ok}, harness:{home,ok,version?}, git, claude, zed, webview2}` | no |
| `workspace.open` | `profilePath` | runs `sdlc validate`; returns validation + repos/trackers summary | no |
| `ticket.initPreview` | form | `{argv[], cmdline, warnings[]}` (no execution) | no |
| `ticket.init` | form | `sdlc init` JSON (`ticketDir, lane, trace, nextPending, warnings`) | yes (harness writes) |
| `onboard.scan` / `onboard.verify` | root, repos, allowTests | `sdlc onboard-scan` / `onboard-verify` JSON | scan writes only the candidates file in app data |
| `onboard.previewWrite` | candidates, answers | `onboard-write --dry-run` JSON (`diff`, `validation`) | no |
| `onboard.write` | same | `onboard-write` JSON | yes (profile, evidence, adapters) |
| `worktree.list` | ticket? | `Worktree[]` (below) | no |
| `worktree.remove` | `repoId, ticket, path` | `{removed:true}` or git's refusal | yes: `git worktree remove` only |

Events: `config:changed`, `env:changed` (after probe re-run). Files: `%APPDATA%\kaneki-harness-app\config.json`, `logs\app.log`.

## 4. Design

### 4.1 Config file and first run
Location (ASSUMPTION, Wails default): `%APPDATA%\KanekiHarnessApp\config.json` (Wails: `os.UserConfigDir`). Logs next to it. Never inside a workspace (no secrets, nothing for git to pick up).

```jsonc
{ "schemaVersion": 1,
  "workspaces": [ {"id":"todo","profile":"D:/.../sdlc.profile.json","label":"todo-nestjs","lastOpened":"..."} ],
  "activeWorkspace": "todo",
  "harness": {"home": null, "python": null},     // null = auto-discover (4.7)
  "editor": {"zedPath": null, "args": ["{file}"]},
  "claude": {"path": null},
  "prices": {"overrideFile": null},               // P7 owns schema
  "theme": "system",                              // light|dark|system, maps to design-system tokens
  "notifications": {"needsYou":true,"nodeDone":false,"sound":false},
  "ui": {"windowState":{}} }
```
Rules: app writes atomically (tmp + rename), validates on load, unknown keys kept, parse failure -> back up as `config.json.bad` and start first run. Settings that belong to a ticket (run policy) do NOT live here (plan Q3, other part).

First-run wizard (4 steps, each re-runnable from Settings; uses `.k-` stepper/form components of the design system, ASSUMPTION they exist in `harness/kaneki-harness/app/design-system/components.css`; P8-T01 verifies):
1. Environment check via `env.probe`: Python >=3.9 (`python --version`, same set the launcher tries: `SDLC_PYTHON`, python3, python, `py -3`, see `H/skills/kaneki-flow/scripts/sdlc:3`), harness home found, git, claude, zed, WebView2. Each failing row has a one-line fix and "Browse...".
2. Harness home (4.7).
3. Workspace: browse to `sdlc.profile.json` -> `workspace.open` -> `sdlc validate` (cli.py:1016). No profile? offer "Onboard a repo" (4.3).
4. Editor and notifications, then land on the board.

Multi-workspace (plan Q6): **recommend one window per workspace, one active workspace at a time in v1.** Config keeps a list for a switcher; the Host holds one watcher set and one `SDLC_PROFILE` env per window. Simpler file watching, no cross-workspace id collisions (`TODO-1` can exist in two workspaces). Second window = same binary with `--workspace <id>`.

Settings screen sections: Workspaces, Harness and Python, Editor, Claude, Prices (link P7), Appearance, Notifications, About (app version, harness version, WebView2 version, log folder, "copy diagnostics"). Diagnostics excludes tokens, keys, ticket text.

### 4.2 New ticket
Facts: `sdlc init` requires `--ticket` (from `ticketed`), `--kind {bug,feature}`; optional `--repo` (repeat), `--family` (repeat), `--surface` (repeat), `--lang k=v` (repeat), `--lane {full,lite}` default full, `--force` (H/harness/cli.py:1018-1026). Handler: refuses if ticket exists unless `--force` (cli.py:230-233); validates repos/families/surfaces from the profile (`build_context`, H/harness/profile.py:251-267); `lite` lane rejects families (cli.py:236-237); resolves adapters fail-closed (cli.py:243); requires a git checkout with a commit per repo (cli.py:256-259); on `full` lane needs a valid `paths.worktreeRoot` (cli.py:246-248, profile.py:120); returns `ticketDir, lane, trace, nextPending, warnings` (cli.py:279-281). **`--force` is never offered**: init over an existing ticket destroys state; the form instead links to the existing ticket.

Form fields and sources:

| Field | Control | Source |
|---|---|---|
| Ticket id | text, required; live check against `sdlc tickets` list for duplicates | user; id format is tracker specific, not validated by the harness (ASSUMPTION: no regex found in `harness/*.py` grep of ticket validation) |
| Kind | segmented bug/feature | fixed |
| Lane | segmented full/lite with the lane descriptions | `sdlc lane --ticket X` shows descriptions only for an existing ticket, so descriptions come from `H/graph/lifecycle.json` lanes (read directly) |
| Repositories | multi-checkbox, default all | profile `repositories[]` (id, stack, surfaces) |
| Families | multi-select, disabled when lane=lite | profile `families[]` |
| Extra surfaces | chips, only values in `evidence.surfaces` | profile |
| Languages | collapsed "Override output language" (off by default; only when user asks, per cli.py:1024 help) | free `key=value` rows |

Tracker adapters (`H/adapters/tracker/*.json`) hold **fetch** instructions, not init arguments: github-issues has a `fetch.command` with `{ticket}` and `{repo}` (github-issues.json:7), backlog needs env `BACKLOG_API_KEY` (backlog.json:5), jira uses connector/REST, local-file reads `{dir}/{ticket}.md` (local-file.json:5-7). So the app does not call trackers at init; it shows a **tracker hint** under the id field from the profile's `tracker.adapter`/`config` (schema profile `tracker` at project-profile.schema.json:167-180): e.g. for local-file "expects `tickets/<id>.md`" and checks the file exists (warning, not block: G7 asks later); for github-issues "number only"; for backlog/jira "KEY-123" (hint only, ASSUMPTION from `projectKey`/`project` config). App never stores or prints tracker keys.

Preview/confirm (D2): the "Create" button opens the standard confirm dialog showing the exact `cmdline` from `ticket.initPreview` plus "what it does": creates `<ticketRoot>/<id>/state.json` + trace, no git change on `full` (worktree is created later by the prepare node, adapters' `worktree` template, H/adapters/stack/node-ts.json:14) and on `lite` the change node creates `<branchPrefix><id>` in place (cli.py:268-272). Warnings from init (dirty checkout, on base branch, cli.py:263-271) are shown as an amber banner after success, they are not errors. After success: select the ticket on the board and offer "Start work" (P4/P5 owns that).

### 4.3 Onboarding a repo (v1 scope)
Flow in the skill: scan -> show table -> interpret weak spots with `kaneki-worker` subagents -> verify -> ask -> write (`--refresh --dry-run` then `--refresh`) -> report (H/skills/kaneki-onboarding/SKILL.md:30-67). HARD-GATE: never read `.env`, tests only after user yes, write only profile/evidence/adapters/.gitignore, never commit, never change git config, never read SSH keys (SKILL.md:21-29).

| Step | App drives? | Notes |
|---|---|---|
| 1 scan `onboard-scan --root --repo --out` | **Yes**, read-only (cli.py:188-195, parser 1104-1108) | Host picks `--out` in app data (the skill says never inside the project, SKILL.md:15-16). Show repos, stacks, command counts, `lowConfidence`, `questions`, `gaps` (cli.py:192-195). |
| 2 table | Yes | render `proposal` + `evidence` |
| 3 interpret weak repos | **No (Claude session)** | needs `kaneki-worker` subagents |
| 4 verify `onboard-verify` | Yes, **without** `--allow-tests` only; tests/build toggles are off and labelled "run in a Claude session" in v1 | cli.py:198-206, G6 |
| 5 ask | **Partly**: app can render `CAND.questions` as a form (git identity, language, tracker); the two always-ask groups (git account, language) require explicit choice, no default pre-selected for identity (SKILL.md:50-60) | writes answers JSON to app data |
| 6 write | `onboard-write --dry-run` preview: yes. Real write: yes only for **first run** (no profile), after confirm dialog. `--refresh` of an existing profile stays in a Claude session in v1 | cli.py:209-226 refuses existing profile without `--refresh`/`--dry-run` (cli.py:213) |
| 7 report | Yes | show `validation.ok`; `false` is shown as failure with the error |

**Recommendation v1:** ship scan + table + dry-run preview in the app, and a **"Continue in Claude session"** button (P4 spawn) that runs `/kaneki-onboarding <dir>` for interpret/verify-with-tests/refresh. Full in-app question UI = v1.1 (the question schema in `CAND.questions` is not read by me beyond SKILL.md; ASSUMPTION its shape is `{id, why, options[], followUp?}`, P8-T10 confirms from `H/harness/onboard.py`). Reason: the interpretation step needs an LLM, and writing a profile with a wrong identity affects every later commit.

### 4.4 Worktrees (never delete a branch)
Facts: worktree path is `<worktreeRoot>/<repoId>-<ticket>` (profile.py:116-117); branch is `<branchPrefix><ticket>` created by `git worktree add ... -b` (stack adapter, node-ts.json:14; README.md:88); `sdlc worktree` only **binds** an existing folder to the ticket state and requires it to be the expected slot or the repo itself (cli.py:793-815), so it is a state write, not a list/clean tool. `sdlc git` runs git as the project identity and prints git's own output (parser cli.py:1125). Board summaries already expose `repositories[].worktree` and `base` per ticket (H/harness/board.py:55).

`Worktree` shape (built by Host, UI renders):
```json
{ "ticket":"TODO-1-2","repoId":"api","path":"D:/.../worktrees/api-TODO-1-2",
  "branch":"ticket/TODO-1-2","branchStatus":"kept","exists":true,"dirty":false,
  "ticketStatus":"in-progress|delivered|shipped|unknown","bound":true,"inPlace":false,
  "removable":false,"whyNot":"ticket not delivered" }
```
List algorithm: (1) tickets from `sdlc tickets`/ticket files (read-only); (2) for each repo `git worktree list --porcelain` via `sdlc git --repo <id> -- worktree list --porcelain` (ASSUMPTION about exact `sdlc git` argument form, P8-T11 reads `cmd_git`; fall back to Host `runGit` with the same identity env); (3) join by path to `state.context.repositories[].worktree` (cli.py:283-285 init stores it; cli.py:807-809 bind); (4) `git status --porcelain` for `dirty`. Items in the folder but not in a ticket = "orphan" (shown, removable with the same rule). `inPlace` (worktree == repo path, lite lane, cli.py:268-272) is **never removable** (it is the user's checkout).

Cleanup rule: `removable` only if ticket state is delivered/shipped (or user ticks "abandoned"), `dirty` is false, not `inPlace`. Action = confirm dialog showing exactly `git worktree remove "<path>"` (no `--force`; a dirty tree is refused by git and we show git's message). Branch row always shows chip **"branch kept"** with tooltip "kaneki-harness-app never deletes branches; delete it yourself in git if you want". Consequences designed in:
- No button, menu item, keyboard shortcut or IPC named `branch.*`; no `git branch -d/-D`, `git push --delete`, `git push origin :x` string exists in the Host.
- Host `runGit` is an **allow-list** of argv shapes (`worktree list --porcelain`, `worktree remove <path>`, `status --porcelain`, `rev-parse`, `log`), anything else returns `ERR_NOT_ALLOWED`. Unit test iterates a deny list (`branch`, `push`, `commit`, `reset`, `clean`, `worktree remove --force`, `worktree prune`) and asserts rejection. `worktree prune` is excluded: it is not branch deletion but changes metadata silently.
- After removal, state still references the worktree path (state is harness-owned, D1). The app does not edit state; board shows "worktree removed" chip because `exists:false`. ASSUMPTION: later harness commands tolerate the missing path only on a delivered ticket; verify in P8-T11 by reading `Session` usage of `repo["worktree"]` before enabling removal for non-delivered tickets (until then only delivered/shipped).
- The project guard (`git-guard`, H/skills/kaneki-flow/scripts/guard_git.py) may block `git worktree remove` when run inside a Claude session; the app runs git itself, outside the guard. Whether that is acceptable is Decision 5.

### 4.5 Packaging (Wails)
| | Wails 3/2 |
|---|---|
| Installers | NSIS `.exe` via `wails build -nsis` (needs NSIS installed) |
| WebView2 | NSIS template downloads the bootstrapper if the runtime is missing |
| Toolchain to add | Go + NSIS + `wails` CLI (Go and the CLI come from P2) |
| Updater | none official in v2 (manual); v3 has an updater service (ASSUMPTION, verify) |
| Size | ~8-12 MB |
Facts about Wails are from memory (no network research done); P8-T14 verifies against the current docs of Wails before wiring CI.

Recommendations (personal use): **no code signing in v1** (SmartScreen will warn once; document "More info -> Run anyway"); per-user NSIS install (no admin, writes `%LOCALAPPDATA%`); **no auto-update in v1** (single user: rebuild + reinstall; updater needs a signing key and hosting). `embedBootstrapper` mode; Windows 11 already ships WebView2 so risk is low. Reconsider signing if the app is shared outside the team.

### 4.6 What ships with the app
The harness is Python and must not be vendored inside the installer by default (it changes often and `sdlc install` already copies skills into a target `.claude`, install.py:37). Options: (a) app **discovers** an existing harness checkout, (b) installer bundles a snapshot of `harness/kaneki-harness/` as a resource (about the size of the repo folder; also needs Python on the machine either way). **Recommend (a) + optional (b) snapshot as fallback** ("Use bundled harness"), because Python 3.9+ is required in both cases and the app cannot make Python appear (stdlib-only harness, H/README.md:15).

### 4.7 Finding the harness on another machine
Discovery order, same as the harness itself (`sdlc.py:14-26`) so behaviour matches the CLI: (1) `config.harness.home`; (2) env `SDLC_HOME`; (3) `.harness-home` marker next to an installed `sdlc.py` in the workspace's `.claude/skills/kaneki-flow/scripts/` (written by `sdlc install`); (4) walk up from known paths looking for `graph/lifecycle.json` + `harness/`; (5) bundled snapshot; (6) first-run Browse. Validation of a candidate = both `graph/lifecycle.json` and `harness/` exist, then run `python <home>/skills/kaneki-flow/scripts/sdlc.py --help` (read-only) and record the harness version (ASSUMPTION: a version source is needed; `sdlc` has no `--version`, so P8-T04 compares a file hash of `graph/lifecycle.json` + `harness/` mtime, or asks harness part for a `version` field in `lifecycle.json`, Decision 6). Host always sets `SDLC_HOME` and `SDLC_PROFILE` and `SDLC_PYTHON` explicitly for child processes, so what the app validated is what runs.

### 4.8 Quality strategy (all parts)
Existing: stdlib `unittest` suites in `H/tests/` (9 files, e.g. test_lifecycle.py, test_worktrees.py, test_onboarding.py); `test_worktrees.py` creates temp repos with `git init` (test_worktrees.py:75-95), so fixtures are self-contained. CI today: only `.github/workflows/learning-kit.yml` (matrix ubuntu/macos/windows x py3.9/3.12/3.13, `python -B -m unittest discover -s tests -v`, `working-directory`, path-filtered, lines 1-27). **There is no kaneki-harness CI**, so P8 adds one.

Pyramid (cheapest at the bottom):
1. **Harness Python** (`unittest`): exists; add any harness changes other parts request (e.g. N4 lock fix, machine-readable status) with their own tests.
2. **Host unit tests** (`go test`): argv builders, command preview text, allow-list `runGit` deny tests (4.4), config load/save/corrupt-recovery, harness discovery with fake dirs, watcher debounce, cost parser on small jsonl samples (P7).
3. **UI unit/DOM tests** (Vitest + jsdom, Node 22 available): components render from fixture JSON; confirm dialog shows exact cmdline; worktree screen has no element matching `/delete.*branch/i` (snapshot + query test).
4. **Contract/fixture tests**: golden JSON captured once from the real workspace (`sdlc tickets`, `status`, `plan`) into `harness/kaneki-harness/app/tests/fixtures/` (sanitised, small); a schema check validates fixtures against the P1 types; Host and UI both load the same fixtures. A "drift" test runs the real `sdlc` against a **generated** mini-workspace (see 5) and compares key sets to the fixtures, so a harness change that breaks the app shows up.
5. **E2E, few** (3-5): against a **temp copy** of a generated workspace (git init + `sdlc onboard-write` + `sdlc init`, no real repo, never `D:/workspace/HAISE/testing/...`): launch app (Playwright against the Wails dev server, ASSUMPTION; or Playwright on the built UI with a mocked Host in v1), flows: first run, new ticket, worktree list, remove a clean worktree, attempt-branch-delete impossible. Marked `@slow`, run on main/nightly, not on every push.

Commands (one entry point, ASSUMPTION `npm` scripts since Node exists): `npm run test:harness`, `test:host`, `test:ui`, `test:contract`, `test:e2e`, `lint`, `ci` (all but e2e). Lint/format: `ruff` is **not** used by the harness today (ASSUMPTION; stdlib-only, no config found), so v1 adds none for Python except `python -m py_compile`; Go `gofmt -l` + `go vet`; UI `eslint` + `prettier --check`.

CI (`.github/workflows/kaneki-harness-app.yml`, new, path filter `harness/kaneki-harness/app/**`, `harness/kaneki-harness/**`): job `harness` (windows-latest + ubuntu, Python 3.9 and 3.12, `unittest discover` from `harness/kaneki-harness/tests`); job `app` on `windows-latest`: Node 22, `actions/setup-go`, `npm ci`, `npm run ci`, then `wails build -nsis` and `upload-artifact` for the installer on tags and manual dispatch. Pin action versions as learning-kit does (`@v4`/`@v5`). **CI never pushes, publishes releases or tags automatically**; a release is a manual GitHub Release the user creates (D6 spirit). GitHub access from this machine goes through SSH as `nnminh-sam` with the pinned key (D:/workspace/HAISE/CLAUDE.md); this plan adds no credentials or secrets to CI (no signing key in v1).

Versioning/changelog: SemVer in one place (`harness/kaneki-harness/app/VERSION`, synced into `wails.json` by a script, test checks they match); `CHANGELOG.md` in Keep a Changelog format; conventional-commit subjects as already used in the repo (git log: `docs(kaneki): ...`, `fix(zed-spawn-session): ...`); scope `kaneki-app`. Compatibility: app records the min harness version it supports in `config`/`about`; `env.probe` shows a warning on mismatch.

### 4.9 Docs (`harness/kaneki-harness/app/doc/`)
- `user-guide.md`: install, first run, board basics, new ticket, onboarding, worktrees ("branches are kept; delete them yourself"), what the app never does (ship/commit/push/delete branches), troubleshooting (Python not found, harness not found, SmartScreen).
- `dev-guide.md`: layout, how to run dev, IPC list (generated from P1 types), test commands, fixtures refresh procedure, adding a settings key.
- `release.md`: checklist (bump VERSION, changelog, `npm run ci`, build, smoke on a clean Windows user, manual GitHub Release).
- `CHANGELOG.md` at app root; `doc/build-plan/` stays as history.
- Short `harness/kaneki-harness/app/README.md` pointing to the above.

## 5. Tasks
| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P8-T01 | Confirm design-system has stepper, form, dialog, chip, table components; list gaps for P3 | S | none | gap list in plan comment |
| P8-T02 | `AppConfig` schema + load/save (atomic, backup on corrupt) + `config.get/set` | M | P1 | unit tests: roundtrip, corrupt file, unknown keys kept |
| P8-T03 | `env.probe` (python, git, claude, zed, webview2) | M | P1 | fake PATH tests; real machine shows all green |
| P8-T04 | Harness discovery (4.7) + validation via `sdlc --help` + version source | M | T03 | finds repo checkout, rejects empty dir, honours `SDLC_HOME` |
| P8-T05 | First-run wizard UI + Settings screen | L | T02-T04, P3 | fresh config reaches board; each setting persists |
| P8-T06 | `workspace.open` (validate profile) and workspace switcher / `--workspace` window arg | M | T02 | invalid profile shows error text from harness |
| P8-T07 | `ticket.initPreview` + `ticket.init` + form UI + tracker hints | L | P1, T06 | init against generated workspace creates ticket; duplicate id blocked; no `--force` anywhere |
| P8-T08 | Onboarding scan/verify/preview screens (no tests/build flags) | L | T06 | scan on a generated repo shows table; preview shows diff |
| P8-T09 | "Continue in Claude session" handoff for onboarding | S | P4 | spawns `/kaneki-onboarding <dir>` visibly |
| P8-T10 | Real first-run `onboard.write` with confirm + question form for the shapes confirmed in `onboard.py` | M | T08 | writes profile in temp dir; validation.ok shown |
| P8-T11 | Read `cmd_git`, `Session` worktree use; build `worktree.list` | M | T06 | lists real `TODO-1-2` worktree read-only; orphan detection test |
| P8-T12 | Host `runGit` allow-list + deny tests | M | P1 | all deny cases rejected, test in CI |
| P8-T13 | `worktree.remove` + screen with "branch kept" chip, no branch UI | M | T11, T12 | removes clean temp worktree; dirty refused with git message; DOM test finds no branch-delete control |
| P8-T14 | Installer config for Wails (NSIS, WebView2 mode), per-user install | M | P2 | installer installs/uninstalls on clean VM or fresh user |
| P8-T15 | Fixtures from real workspace (sanitised) + contract tests + generated mini-workspace helper | M | P1 | `npm run test:contract` green |
| P8-T16 | Lint/format setup and `npm run ci` script | S | P2 | one command runs all but e2e |
| P8-T17 | GitHub Actions `kaneki-harness-app.yml` (harness job + app job + installer artifact) | M | T14, T16 | green on a branch push; artifact downloadable |
| P8-T18 | E2E set (3-5 flows) on temp workspace | L | T07, T13 | passes locally and in nightly |
| P8-T19 | Versioning script, CHANGELOG, release checklist | S | T14 | version mismatch test fails when out of sync |
| P8-T20 | User guide, dev guide, README | M | T05-T13 | reviewed against a fresh install |

## 6. Tests and verification
- Unit: config, discovery, argv/preview builders, allow-list, tracker-hint selection, worktree join logic (porcelain parser with fixtures including detached HEAD, prunable, bare, locked entries).
- Fixture: golden `sdlc tickets/status/plan` JSON; `git worktree list --porcelain` samples from a temp repo made in the test.
- Manual against the real workspace `D:/workspace/HAISE/testing/todo-nestjs-backend` (read-only): first run picks its profile, `sdlc validate` ok, board shows `TODO-1`, `TODO-1-2`, worktree list shows their folders; **do not** init, onboard-write or remove anything there. State-changing flows run only on a generated temp workspace.
- Packaging: install on a clean Windows 11 user; uninstall leaves `%APPDATA%` config only if user opts to keep; app starts without Python and shows the fix row.
- Negative: grep of Host source and built UI bundle for `branch -d`, `branch -D`, `--delete`, `push` in CI (`rg`), failing the build.

## 7. Risks
| Risk | Mitigation |
|---|---|
| Removing a worktree leaves ticket state pointing to a missing path | removal only for delivered/shipped tickets until T11 proves tolerance; "removed" chip |
| App-level `git worktree remove` bypasses the harness git guard | allow-list in Host, confirm dialog with exact command, Decision 5 |
| Python missing/old on another machine | env probe + fix row + `SDLC_PYTHON` browse; cannot bundle (decided) |
| Stack undecided delays CI/installer | keep T14/T17 behind P2; build everything else stack-neutral (UI + Node scripts) |
| E2E flakiness | few tests, nightly, generated workspace, retries off |
| Fixture drift vs harness changes | drift test against generated workspace on every `harness/kaneki-harness/**` change |
| SmartScreen warning scares others | documented; sign only if shared (Decision 2) |
| Onboarding writes a wrong git identity | identity question never pre-selected; first-run write only; dry-run preview first |
| Config written inside a workspace by mistake | fixed `%APPDATA%` location, test asserts path not under any workspace |

## 8. Decisions needed from the user
1. Multi-workspace: one window per workspace, one active at a time (recommended) or one window with a switcher?
2. Code signing: skip in v1 for personal use (recommended)? Will the installer be shared outside your machine?
3. Auto-update: none in v1, manual reinstall (recommended)?
4. Onboarding v1: scan + preview in app, interpret/tests/refresh via "Continue in Claude session" (recommended), or full in-app flow now?
5. Is the app allowed to run `git worktree remove` itself (outside the Claude git guard), after a confirm dialog and only for delivered/clean worktrees (recommended)? Or should removal only copy the command for you to run?
6. Harness version source: ask the harness to add a `version` field (recommended, small harness change) or compare file hashes?
7. Bundle a harness snapshot in the installer as fallback, or require a harness checkout (recommended: checkout only, snapshot later)?
8. Add a kaneki-harness CI job now (recommended; none exists today)?

## 9. Evidence
- H/harness/cli.py:188-226 (onboard handlers), 229-282 (`init`), 793-815 (`worktree`), 1016-1026 (validate/init parser), 1076-1079 (worktree parser), 1104-1123 (onboard parsers), 1125 (git), 1143 (tickets).
- H/harness/profile.py:116-129 (worktree path and root errors), 251-279 (`build_context`).
- H/harness/board.py:55 (repositories/worktree in board summary).
- H/adapters/tracker/{backlog,github-issues,jira,local-file}.json (config and fetch blocks).
- H/adapters/stack/node-ts.json:14, H/harness/onboard.py:821 (worktree add template with `-b`), H/README.md:15,88,156,166.
- H/contracts/project-profile.schema.json:7-18 (required), 125-146 (paths), 167-180 (tracker).
- H/skills/kaneki-onboarding/SKILL.md:1-89 (flow, HARD-GATE, detected fields).
- H/skills/kaneki-flow/scripts/sdlc, sdlc.py:10-26 (Python and home discovery).
- H/tests/ (9 test files), H/tests/test_worktrees.py:1-95 (temp git repos, unittest).
- `.github/workflows/learning-kit.yml:1-27` (only existing CI; none for kaneki-harness).
- H/docs/desktop-app/plan.md:110 (M5), 114-119 (Q1-Q6).
- Not read: `cmd_git`, `onboard.py` question schema, `Session` worktree use, design-system component list, git-guard rules.
