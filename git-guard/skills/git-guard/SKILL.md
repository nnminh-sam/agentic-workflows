---
name: git-guard
description: Work with the git-guard hook that checks every git/gh command against identity, commit-message and GitHub CLI contracts. Use when a git or gh command was blocked or needs confirmation by git-guard, when the session-start notice says the git-guard profile is unset or identity is wrong, when writing a commit message or PR title in a git-guard project, or when the user asks to set up, view, customize or turn off git-guard rules.
---

# git-guard

git-guard is a PreToolUse hook on `Bash` and `PowerShell`. It parses each command, finds the git/gh calls in it (including inside `bash -c`, `&&` chains and PowerShell scripts), and checks them against three JSON contracts:

| Contract | Rule ids | Checks |
|---|---|---|
| `identity` | ID001–ID005 | commit author name/email, pinned SSH key (`-i <key>` + `IdentitiesOnly=yes`) on push/pull/fetch/clone, SSH-only remotes on allowed hosts and owners, active `gh` account |
| `commit-message` | CM001–CM015 | Conventional Commits 1.0 on `git commit -m/-F` and on `gh pr create/edit --title` |
| `github-cli` | GH001–GH007 | GitHub only through `gh`, risky `gh` commands denied or confirmed, `gh api` mutations, non-interactive `gh pr create`, repo owner scope, `gh repo create` visibility |

Each finding is `deny`, `ask` or `warn`, and the strictest one wins. The block reason names the rule id and says what to change.

## Locating the scripts

All commands below are relative to the git-guard package root, `<root>`:
- **Plugin install:** two levels above this skill's base directory (`<skill dir>/../..`). The session-start notice also prints the full path.
- **Vendored install:** `.claude/hooks/git-guard/` in the project.

On Windows, use `python` if `python3` is not on PATH.

## When a command is blocked

1. Read the rule id and message in the block reason.
2. Fix the command itself, not the guard. Typical fixes:
   - **CMxxx:** rewrite the message as `type(scope): subject`. Use a lowercase type from the allowed list, an imperative subject, no trailing period, and stay within the header length limit. Before committing, check the message with `python3 <root>/scripts/check_message.py "feat(api): add retry"` (use `--title` for a PR title).
   - **ID003:** pin the key, for example `GIT_SSH_COMMAND='ssh -i "<key>" -o IdentitiesOnly=yes' git push`, or check that the repo's `core.sshCommand` already does this.
   - **ID004:** use an SSH remote (`git@github.com:<owner>/<repo>.git`) whose owner is the profile's `github_user` or one of its `github_orgs`.
   - **GH001:** call GitHub through `gh` instead of `curl`/`api.github.com`.
   - **GH004:** pass `--title` and `--body` (or `--fill`) to `gh pr create`.
3. Never do the following yourself: change the git author, the `gh` account, SSH keys, or remote URLs; edit contracts; set a rule to `off`; or unset profile values just to get a command through. Identity is the user's decision. Tell the user what was blocked and why, and let them choose.
4. If a command got `ask`, the user is shown a confirmation prompt. Don't try to route around it with a different command form.

## Setting up the profile (ask the user first)

The contracts never name an account. The values come from a profile:

| Variable | Env override | Meaning |
|---|---|---|
| `github_user` | `GIT_GUARD_GITHUB_USER` | the `gh` login, also an allowed repo owner |
| `github_orgs` | `GIT_GUARD_GITHUB_ORGS` | extra allowed owners (comma-separated) |
| `git_name` | `GIT_GUARD_GIT_NAME` | commit author name |
| `git_email` | `GIT_GUARD_GIT_EMAIL` | allowed author emails (comma-separated) |
| `ssh_key` | `GIT_GUARD_SSH_KEY` | private key path, or path suffix, that must be pinned |

Values are resolved in this order: env var, then the project profile, then the user profile (`~/.claude/git-guard/profile.json`). The project profile is `<project>/.claude/git-guard/profile.local.json` for a plugin install, or `.claude/hooks/git-guard/profile.local.json` for a vendored install. Both are git-ignored. When a value is unset, its rule only checks that something is present.

Run these from the project root:

```sh
python3 <root>/scripts/setup_profile.py show                      # values, their sources, active contract files
python3 <root>/scripts/setup_profile.py init --from-current       # copy current git config + gh login
python3 <root>/scripts/setup_profile.py init --github-user <login> --ssh-key "<path>" --git-email a@b.c
python3 <root>/scripts/setup_profile.py init --scope user ...     # applies to all projects
python3 <root>/scripts/setup_profile.py unset github_orgs
```

`init` writes the user's identity to disk, so confirm the values with the user before running it. If the project's `CLAUDE.md` names the required account or key, propose those values.

## Viewing and customizing rules

- `python3 <root>/scripts/render_rules.py --open` builds and opens `rules.html`, a searchable page with every rule, its severity, examples and the resolved profile.
- **Plugin install:** `python3 <root>/scripts/setup_profile.py eject <identity|commit-message|github-cli|all>` copies a shipped contract to `<project>/.claude/git-guard/contracts/`. The project copy overrides the shipped one; commit it to share it with the team.
- **Vendored install:** edit `.claude/hooks/git-guard/contracts/*.json` in place.
- Change a rule's `severity` (`deny`/`ask`/`warn`/`off`) or its `params`. Don't delete rules or add new ids. An invalid contract makes every git/gh command ask for confirmation until it is fixed. Only edit contracts when the user asks you to.

## Tests

```sh
cd <root>/tests && python3 -m unittest
```
