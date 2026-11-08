# git-guard

A Claude Code plugin that checks every `Bash` / `PowerShell` command Claude runs. It is **account-agnostic**: the shared contracts never name a user. Each person supplies their own account values in a profile.

| Contract | File | Checks |
|---|---|---|
| Identity | `contracts/identity.json` | commit author name/email, pinned SSH key for push/pull/fetch/clone, SSH-only remotes on allowed hosts/owners, active `gh` account |
| Commit message | `contracts/commit-message.json` | Conventional Commits 1.0 on `git commit -m/-F`, also applied to `gh pr create/edit --title` |
| GitHub CLI | `contracts/github-cli.json` | GitHub only via `gh`; risky `gh` commands denied or confirmed; `gh api` mutations; non-interactive `gh pr create`; repository owner scope; `gh repo create` visibility |

What it ships:
- **Hooks** (`hooks/hooks.json`):
  - PreToolUse (`Bash|PowerShell`) → `scripts/pre_tool_use.py`
  - SessionStart → `scripts/session_start.py`. It re-renders `rules.html` and tells Claude about identity problems and unset profile values.
- **Skill** `git-guard` (`skills/git-guard/SKILL.md`): tells agents how to react to a block, set up the profile (after asking the user), check messages, and view or customize rules.
- **CLI scripts** in `scripts/`: `setup_profile.py`, `render_rules.py`, `check_message.py`.

Requires Python 3.10+ on PATH as `python3`. If you only have `python`, edit `hooks/hooks.json`. There are no third-party dependencies.

## Layout

```
git-guard/
  .claude-plugin/plugin.json        plugin manifest
  .claude-plugin/marketplace.json   local marketplace "git-guard-local" (this folder)
  hooks/hooks.json                  hook registration (uses ${CLAUDE_PLUGIN_ROOT})
  skills/git-guard/SKILL.md         agent skill
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
python D:/workspace/HAISE/agents/git-guard/install.py D:/path/to/project
```

This adds the following to `<project>/.claude/settings.json`; commit it so teammates get the same setup:

```json
{
  "extraKnownMarketplaces": {
    "git-guard-local": { "source": { "source": "directory", "path": "D:/workspace/HAISE/agents/git-guard" } }
  },
  "enabledPlugins": { "git-guard@git-guard-local": true }
}
```

Restart Claude Code in the project and accept the marketplace trust prompt.

You can also do the same from inside Claude Code:
```
/plugin marketplace add D:/workspace/HAISE/agents/git-guard
/plugin install git-guard@git-guard-local
```

To use it for every project, run `install.py --scope user`. This writes `~/.claude/settings.json`. After you change this folder, run `/plugin marketplace update git-guard-local` to pick up the changes.

### Option B: vendored copy

Use this when the project must not depend on this folder, for example when it is cloned on other machines.

```sh
python D:/workspace/HAISE/agents/git-guard/install.py D:/path/to/project --mode vendor
```

It copies the hook to `<project>/.claude/hooks/git-guard/` and the skill to `<project>/.claude/skills/git-guard/`, and merges the hooks into `<project>/.claude/settings.json`. To upgrade, run it again. Existing contracts and `profile.local.json` are kept unless you pass `--force`.

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
| `ssh_key` | `GIT_GUARD_SSH_KEY` | ID003 pinned key (full path or path suffix) |

Each value is resolved from the first source that has it:
1. the environment variable
2. the project profile (see the table above)
3. `~/.claude/git-guard/profile.json` (per user, all projects)

Run these from the project root. `<root>` is this folder, or `.claude/hooks/git-guard` when vendored:

```sh
python3 <root>/scripts/setup_profile.py init --from-current   # capture current git/gh identity
python3 <root>/scripts/setup_profile.py init --github-user octocat --ssh-key ~/.ssh/id_gh
python3 <root>/scripts/setup_profile.py init --scope user ...  # for all projects
python3 <root>/scripts/setup_profile.py show                   # values, sources, active contract files
```

**Unset variables** make the rule fall back to a presence check:
- an author name must be set, and the author email must be a valid address
- gh must be logged in, as any account
- remotes must be SSH on an allowed host, with any owner
- `-i <some key>` plus `IdentitiesOnly=yes` must be pinned
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
cd <root>/tests && python3 -m unittest
claude plugin validate <root>                                   # check the plugin manifest
```

The tests use a fictional `octocat` profile. They never read your real profile files.
