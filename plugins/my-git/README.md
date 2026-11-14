# my-git

A Claude Code plugin with a customized git workflow:

- **git-guard hooks** check every `Bash` / `PowerShell` command Claude runs against JSON contracts (identity, Conventional Commits, safe `gh` usage).
- **Skills**:
  - `/my-git:commit [repo-path] [hint]`: stage and commit as the profile's identity with a validated Conventional Commits message.
  - `/my-git:push [repo-path] [--commit [hint]] [--upstream]`: push with the profile's credential after an explicit confirmation; never force-pushes.
  - `/my-git:pr [repo-path] [--release] [--draft] [hint]`: run unit tests + lint locally, then open a PR from the current work branch into `develop/base` (or, with `--release`, from `develop/base` into `main`) with a Summary / Detailed changes / Explanation / Test results body.
  - `git-guard`: how agents react to a block, set up the profile, and view or customize rules.

The guard is **account-agnostic**: the shared contracts never name a user. Each person supplies their own account values in a profile.

| Contract | File | Checks |
|---|---|---|
| Identity | `contracts/identity.json` | commit author name/email; the pinned credential for push/pull/fetch/clone (SSH key, or the gh credential helper as the expected account); remotes on allowed hosts/owners using the profile's transport; active `gh` account |
| Commit message | `contracts/commit-message.json` | Conventional Commits 1.0 on `git commit -m/-F`, also applied to `gh pr create/edit --title` |
| Commit message (CM016) | `contracts/commit-message.json` | no AI co-author trailer (`Co-Authored-By: Claude ...`) or "Generated with ..." / 🤖 attribution |
| GitHub CLI | `contracts/github-cli.json` | GitHub only via `gh`; risky `gh` commands denied or confirmed; `gh api` mutations; non-interactive `gh pr create`; repository owner scope; `gh repo create` visibility; PR body sections (GH008) and no AI attribution in PR bodies (GH009) |
| Branching | `contracts/branching.json` | no commits on / pushes to `main` and `develop/base`; work branches `develop/<type>/<name>` started from `develop/base`; PRs target `develop/base`, only `develop/base` → `main` |

## Branching model and repo template

```
develop/feat/<name> ─┐
develop/fix/<name>  ─┼─ PR ─▶ develop/base (staging) ── PR (--release) ─▶ main (production)
develop/chore/...   ─┘
```

- `main` and `develop/base` accept changes **only through PRs**: no direct commits or pushes, no force-push, no deletion.
- Work branches are named `develop/<type>/<name>` (type: feat, fix, chore, docs, refactor, perf, test, build, ci) and start from `develop/base`.
- PR bodies have `## Summary`, `## Detailed changes`, `## Explanation`, `## Test results` (unit tests + lint, run locally).
- Commits and PRs never credit an AI co-author.

These rules are enforced in three layers. They all read their names from `contracts/branching.json`: eject and edit it to rename the branches or change the pattern.

| Layer | Where | Catches |
|---|---|---|
| git-guard hooks | Claude's Bash/PowerShell calls | BR001–BR005, CM016, GH008, GH009 |
| local git hooks | `.githooks/` in the repo (`core.hooksPath`) | commits on protected branches, branch naming, commit header, AI co-author, pushes to protected branches. Applies to you too, outside Claude |
| GitHub ruleset | the GitHub repo | the real enforcement: PR required on both branches, no force-push, no deletion |

Set up a new project in one go:

```sh
python3 <root>/install.py /path/to/project --repo-setup bootstrap   # plugin + PR template + .githooks + git init + develop/base
cd /path/to/project
python3 <root>/scripts/setup_profile.py init --from-current --transport gh
python3 <root>/scripts/setup_repo.py github --repo <owner>/<name> --create private --dry-run   # preview
python3 <root>/scripts/setup_repo.py github --repo <owner>/<name> --create private
```

`setup_repo.py github` pushes `main` and `develop/base` (the first push of each is allowed), makes `develop/base` the default branch, turns on deleting merged branches, and creates or updates the ruleset `my-git: protect main and develop/base`. It defaults to 0 required approvals, because a solo owner can't approve their own PR; pass `--approvals 1` for a team.

**GitHub plan:** rulesets are enforced on private repositories only with GitHub Pro, Team or Enterprise. On a free plan, use a public repository or rely on the local hooks plus the guard.

Template files (`templates/repo/`): `.github/pull_request_template.md`, `.githooks/{pre-commit,commit-msg,pre-push}`, `github/ruleset.json`. `setup_repo.py apply --force` re-renders them after you change the contract.

## Quick start: single GitHub account (git config + gh, no SSH key)

If you use one GitHub account, HTTPS remotes and `gh` (`gh auth login`), you don't need an SSH key:

```sh
gh auth setup-git                                   # once: git authenticates to github.com through gh
python3 <root>/scripts/setup_profile.py init --from-current --transport gh   # captures git user.name/email + gh login
python3 <root>/scripts/setup_profile.py show
```

The guard still enforces:
- **commit**: author name/email must equal the profile's `git_name` / `git_email` (ID001/ID002), and the message must be Conventional Commits.
- **push / pull / fetch / clone**: the remote must be HTTPS on an allowed host, owned by `github_user` or `github_orgs` (ID004); git must use the gh credential helper, no token may be embedded in the URL, and gh's active account must be `github_user` (ID003). A `-c credential.helper=...` override is caught too.
- **gh**: the active account must be `github_user` (ID005).

With several accounts, or SSH remotes, use `--transport ssh` with an `ssh_key` (see [Profile](#profile-who-you-are)).

What it ships:
- **Hooks** (`hooks/hooks.json`):
  - PreToolUse (`Bash|PowerShell`) → `scripts/pre_tool_use.py`
  - SessionStart → `scripts/session_start.py`. It re-renders `rules.html` and tells Claude about identity problems and unset profile values.
- **Skills** in `skills/`: `git-guard`, `commit`, `push`.
- **CLI scripts** in `scripts/`: `setup_profile.py`, `render_rules.py`, `check_message.py`.

Requires Python 3.10+ on PATH as `python3`. If you only have `python`, edit `hooks/hooks.json`. There are no third-party dependencies.

## Layout

```
my-git/
  .claude-plugin/plugin.json        plugin manifest ("my-git")
  .claude-plugin/marketplace.json   local marketplace "my-git-local" (this folder)
  hooks/hooks.json                  hook registration (uses ${CLAUDE_PLUGIN_ROOT})
  skills/git-guard/SKILL.md         agent skill: working with the guard
  skills/commit/SKILL.md            /my-git:commit
  skills/push/SKILL.md              /my-git:push
  skills/pr/SKILL.md                /my-git:pr
  templates/repo/                   repo template: PR template, .githooks, GitHub ruleset
  scripts/                          hook entry points, CLIs, gitguard/ package
  contracts/                        shipped default rules
  viewer/shell.html                 template for rules.html
  tests/                            python -m unittest
  install.py                        one-command install into a project
```

## Install into a project

### Option A: plugin (recommended)

The code stays in this folder and every project uses it. To update all projects at once, update this folder.

```sh
python3 <root>/install.py /path/to/project
```

This adds the following to `<project>/.claude/settings.json`; commit it so teammates get the same setup:

```json
{
  "extraKnownMarketplaces": {
    "my-git-local": { "source": { "source": "directory", "path": "/path/to/agentic-workflows/my-git" } }
  },
  "enabledPlugins": { "my-git@my-git-local": true }
}
```

Restart Claude Code in the project and accept the marketplace trust prompt.

You can also do the same from inside Claude Code:
```
/plugin marketplace add /path/to/agentic-workflows/my-git
/plugin install my-git@my-git-local
```

To use it for every project, run `install.py --scope user`. This writes `~/.claude/settings.json`. After you change this folder, run `/plugin marketplace update my-git-local` to pick up the changes.

**Upgrading from `git-guard@git-guard-local`:** run `install.py` again (same scope). It removes the old plugin and marketplace entries so the hooks don't run twice. Profiles, contract overrides and env vars (`GIT_GUARD_*`, `.claude/git-guard/`) are unchanged.

### Option B: vendored copy

Use this when the project must not depend on this folder, for example when it is cloned on other machines.

```sh
python3 <root>/install.py /path/to/project --mode vendor
```

It copies the hook to `<project>/.claude/hooks/git-guard/` and the skills to `<project>/.claude/skills/` (`git-guard`, `my-git-commit`, `my-git-push`), and merges the hooks into `<project>/.claude/settings.json`. To upgrade, run it again. Existing contracts and `profile.local.json` are kept unless you pass `--force`.

Both modes support `--uninstall` and `--dry-run`. Each install mode removes the other mode's hooks from the project's settings, so the guard never runs twice.

## Per-project state

| File | Plugin mode | Vendored mode | Commit it? |
|---|---|---|---|
| project profile | `<project>/.claude/git-guard/profile.local.json` | `.claude/hooks/git-guard/profile.local.json` | no (git-ignored) |
| rules viewer | `<project>/.claude/git-guard/rules.html` | `.claude/hooks/git-guard/rules.html` | no (git-ignored) |
| contract overrides | `<project>/.claude/git-guard/contracts/*.json` | edit `.claude/hooks/git-guard/contracts/` in place | yes |

The project is `$GIT_GUARD_PROJECT_DIR`. If that isn't set, it's `$CLAUDE_PROJECT_DIR`, which hooks receive, and otherwise the current directory. In plugin mode the first run creates `.claude/git-guard/` with a `.gitignore` for the personal files.

## Profile (who you are)

Contracts refer to account values as variables, for example `"expected_logins": ["${github_user}"]`. These variables are declared in `identity.json` under `profile_variables`:

| Variable | Env override | Used by |
|---|---|---|
| `github_user` | `GIT_GUARD_GITHUB_USER` | ID005 gh account, ID004 / GH006 allowed owners |
| `github_orgs` | `GIT_GUARD_GITHUB_ORGS` (comma-separated) | ID004 / GH006 allowed owners |
| `git_name` | `GIT_GUARD_GIT_NAME` | ID001 author name |
| `git_email` | `GIT_GUARD_GIT_EMAIL` (comma-separated) | ID002 author email |
| `git_transport` | `GIT_GUARD_GIT_TRANSPORT` | ID003 / ID004: `ssh` (default) or `gh` |
| `ssh_key` | `GIT_GUARD_SSH_KEY` | ID003 pinned key (full path or path suffix), `ssh` transport only |

Each value is resolved from the first source that has it:
1. the environment variable
2. the project profile (see the table above)
3. `~/.claude/git-guard/profile.json` (per user, all projects)

Run these from the project root. `<root>` is this folder, or `.claude/hooks/git-guard` when vendored.

`--from-current` sets `git_transport` to `ssh` when the repo pins a key in `core.sshCommand`, and to `gh` when git uses `gh auth git-credential` for github.com and gh is logged in. Otherwise it leaves it unset (= `ssh`).

```sh
python3 <root>/scripts/setup_profile.py init --from-current   # capture current git/gh identity (+ detect transport)
python3 <root>/scripts/setup_profile.py init --from-current --transport gh   # single account over HTTPS + gh
python3 <root>/scripts/setup_profile.py init --github-user octocat --ssh-key ~/.ssh/id_gh
python3 <root>/scripts/setup_profile.py init --scope user ...  # for all projects
python3 <root>/scripts/setup_profile.py show                   # values, sources, active contract files
```

**Unset variables** make the rule fall back to a presence check:
- an author name must be set, and the author email must be a valid address
- gh must be logged in, as any account
- remotes must be SSH on an allowed host, with any owner
- `-i <some key>` plus `IdentitiesOnly=yes` must be pinned (`ssh` transport); `git_transport` unset means `ssh`
- GH006 (owner scope) is skipped

## Configure the rules

In plugin mode, copy a shipped contract into the project first, then edit the copy:

```sh
python3 <root>/scripts/setup_profile.py eject commit-message   # or identity | github-cli | all
```

Every rule has a `severity`:

- `deny` blocks the command.
- `ask` asks you to confirm it.
- `warn` lets it run and shows a warning.
- `off` disables the rule.

The strictest finding wins. Params may reference `${variable}`:
- A list item that is exactly `${x}` is replaced by the value; list values are spliced in; the item is dropped when `x` is unset.
- Inside regex params, values are escaped before they are substituted in.

For SSH host aliases (`git@github-work:org/repo.git`), add the alias to ID004 `hosts`, or add a regex to `extra_patterns`.

Don't delete rules (set them to `off` instead) and don't add new ids. An invalid contract or profile makes the hook ask before every git/gh command until it is fixed.

## View the rules

```sh
python3 <root>/scripts/render_rules.py --open
```

`rules.html` is a self-contained page that shows your resolved profile and every rule, with search, severity filters, examples, and light/dark themes. It is git-ignored because it embeds your profile values.

## Tools and tests

```sh
python3 <root>/scripts/check_message.py "feat(api): add retry"   # also: --title, --file (commit-msg hook)
python3 <root>/scripts/check_pr.py --title "feat: x" --body-file pr.md --base develop/base --head develop/feat/x
python3 <root>/scripts/setup_repo.py render-ruleset              # the ruleset JSON for the current contract
cd <root>/tests && python3 -m unittest
claude plugin validate <root>                                   # check the plugin manifest
```

The tests use a fictional `octocat` profile. They never read your real profile files.
