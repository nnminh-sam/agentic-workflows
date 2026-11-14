---
name: git-guard
description: Work with the git-guard hook (part of the my-git plugin) that checks every git/gh command against identity, commit-message, GitHub CLI and branching contracts. Use when a git or gh command was blocked or needs confirmation by git-guard, when the session-start notice says the git-guard profile is unset or identity is wrong, when writing a commit message, PR title or PR body, or creating a branch, in a git-guard project; when setting up a new repository with the main + develop/base branching model, or when the user asks to set up, view, customize or turn off git-guard rules.
---

# git-guard

git-guard is a PreToolUse hook on `Bash` and `PowerShell`. It parses each command, finds the git/gh calls in it (including inside `bash -c`, `&&` chains and PowerShell scripts), and checks them against four JSON contracts:

| Contract | Rule ids | Checks |
|---|---|---|
| `identity` | ID001–ID005 | commit author name/email; the pinned credential on push/pull/fetch/clone (an SSH key with `-i <key>` + `IdentitiesOnly=yes`, or with `git_transport: gh` the gh credential helper as the expected gh account); remotes on allowed hosts and owners using the profile's transport; active `gh` account |
| `commit-message` | CM001–CM016 | Conventional Commits 1.0 on `git commit -m/-F` and on `gh pr create/edit --title`; CM016: no AI co-author or "Generated with" attribution |
| `github-cli` | GH001–GH009 | GitHub only through `gh`, risky `gh` commands denied or confirmed, `gh api` mutations, non-interactive `gh pr create`, repo owner scope, `gh repo create` visibility; GH008: PR body has Summary / Detailed changes / Explanation / Test results (unit + lint); GH009: no AI attribution in PR bodies |
| `branching` | BR001–BR005 | no commits on `main` / `develop/base` (BR001); no pushes to them (BR002); work branches named `develop/<type>/<name>` (BR003) and started from `develop/base` (BR004); PRs target `develop/base`, and only `develop/base` targets `main` (BR005) |

Each finding is `deny`, `ask` or `warn`, and the strictest one wins. The block reason names the rule id and says what to change.

## Locating the scripts

All commands below are relative to the git-guard package root, `<root>`:
- **Plugin install (my-git):** `${CLAUDE_PLUGIN_ROOT}`, two levels above this skill's base directory (`<skill dir>/../..`). The session-start notice also prints the full path.
- **Vendored install:** `.claude/hooks/git-guard/` in the project.

On Windows, use `python` if `python3` is not on PATH.

## When a command is blocked

1. Read the rule id and message in the block reason.
2. Fix the command itself, not the guard. Typical fixes:
   - **CMxxx:** rewrite the message as `type(scope): subject`. Use a lowercase type from the allowed list, an imperative subject, no trailing period, and stay within the header length limit. Before committing, check the message with `python3 <root>/scripts/check_message.py "feat(api): add retry"` (use `--title` for a PR title).
   - **ID003, `ssh` transport:** pin the key, for example `GIT_SSH_COMMAND='ssh -i "<key>" -o IdentitiesOnly=yes' git push`, or check that the repo's `core.sshCommand` already does this.
   - **ID003, `gh` transport:** the user must run `gh auth setup-git` (helper missing) or `gh auth switch` (wrong account), or remove a token from the remote URL. Tell them; don't do it yourself.
   - **ID004:** use a remote of the profile's transport (`git@github.com:<owner>/<repo>.git` for `ssh`, `https://github.com/<owner>/<repo>.git` for `gh`) whose owner is the profile's `github_user` or one of its `github_orgs`.
   - **GH001:** call GitHub through `gh` instead of `curl`/`api.github.com`.
   - **GH004 / GH008:** pass `--title` and `--body-file <file>` to `gh pr create`. The body needs `## Summary`, `## Detailed changes`, `## Explanation` and `## Test results`, with unit test and lint results. Check it first with `python3 <root>/scripts/check_pr.py --title ... --body-file ... --base ... --head ...`. Prefer the `/my-git:pr` skill.
   - **CM016 / GH009:** remove the AI `Co-Authored-By:` trailer, the "Generated with ..." line or the 🤖 from the message or body.
   - **BR001 / BR002:** don't commit on or push to `main` / `develop/base`. Make a work branch, `git switch -c develop/<type>/<name> develop/base`, push it, and open a PR with `/my-git:pr`.
   - **BR003 / BR004:** name new branches `develop/<type>/<name>` (lower case) and start them from `develop/base` or `origin/develop/base`.
   - **BR005:** use `--base develop/base`. Only the release PR (`--head develop/base`) may use `--base main`.
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
| `git_transport` | `GIT_GUARD_GIT_TRANSPORT` | `ssh` (default): SSH remotes + pinned `ssh_key`. `gh`: HTTPS remotes through the gh credential helper, no SSH key |
| `ssh_key` | `GIT_GUARD_SSH_KEY` | private key path, or path suffix, that must be pinned (`ssh` transport only) |

Values are resolved in this order: env var, then the project profile, then the user profile (`~/.claude/git-guard/profile.json`). The project profile is `<project>/.claude/git-guard/profile.local.json` for a plugin install, or `.claude/hooks/git-guard/profile.local.json` for a vendored install. Both are git-ignored. When a value is unset, its rule only checks that something is present.

Run these from the project root:

```sh
python3 <root>/scripts/setup_profile.py show                      # values, their sources, active contract files
python3 <root>/scripts/setup_profile.py init --from-current       # copy current git config + gh login (+ detect transport)
python3 <root>/scripts/setup_profile.py init --from-current --transport gh   # single GitHub account: HTTPS + gh, no SSH key
python3 <root>/scripts/setup_profile.py init --github-user <login> --ssh-key "<path>" --git-email a@b.c
python3 <root>/scripts/setup_profile.py init --scope user ...     # applies to all projects
python3 <root>/scripts/setup_profile.py unset github_orgs
```

`init` writes the user's identity to disk, so confirm the values with the user before running it. If the project's `CLAUDE.md` names the required account or key, propose those values.

Picking the transport: if the user has a single GitHub account, HTTPS remotes and gh set up as git's credential helper (`git config --get-urlmatch credential.helper https://github.com` prints `... gh auth git-credential`), propose `--transport gh`; `--from-current` detects it. Commits are still checked against `git_name`/`git_email`, and every push/pull/fetch is checked to go through gh as `github_user`. If the helper isn't set up yet, the user runs `gh auth setup-git` once. With several accounts or SSH remotes, use `ssh` with an `ssh_key`.

## Setting up a repository (branching model template)

`<root>/templates/repo/` holds the reusable setup. It contains:
- a PR template (`.github/pull_request_template.md`)
- local git hooks (`.githooks/`): `pre-commit` (no commits on protected branches, branch naming), `commit-msg` (Conventional Commits, no AI co-author) and `pre-push` (no pushes to protected branches)
- the GitHub ruleset that protects both branches (`github/ruleset.json`)

`<root>/scripts/setup_repo.py` applies it. Branch names come from the `branching` contract.

```sh
python3 <root>/scripts/setup_repo.py apply              # PR template + .githooks + core.hooksPath (local only)
python3 <root>/scripts/setup_repo.py bootstrap          # git init -b main, initial commit, create develop/base
python3 <root>/scripts/setup_repo.py github --repo <owner>/<name> [--create private|public] [--approvals N]
python3 <root>/scripts/setup_repo.py all ...            # all three; add --dry-run to preview
```

`github` pushes both branches, sets the default branch to `develop/base`, turns off auto-delete of merged branches (so long-lived branches such as `develop/infra` survive their PRs; delete finished work branches yourself), and creates or updates the ruleset. Those are changes on GitHub, so ask the user before running it, and show `--dry-run` output first. Rulesets on **private** repositories need a paid GitHub plan; the script says so when GitHub refuses.

## Viewing and customizing rules

- `python3 <root>/scripts/render_rules.py --open` builds and opens `rules.html`, a searchable page with every rule, its severity, examples and the resolved profile.
- **Plugin install:** `python3 <root>/scripts/setup_profile.py eject <identity|commit-message|github-cli|branching|all>` copies a shipped contract to `<project>/.claude/git-guard/contracts/`. The project copy overrides the shipped one; commit it to share it with the team.
- **Vendored install:** edit `.claude/hooks/git-guard/contracts/*.json` in place.
- Change a rule's `severity` (`deny`/`ask`/`warn`/`off`) or its `params`. Don't delete rules or add new ids. An invalid contract makes every git/gh command ask for confirmation until it is fixed. Only edit contracts when the user asks you to.

## Tests

```sh
cd <root>/tests && python3 -m unittest
```
