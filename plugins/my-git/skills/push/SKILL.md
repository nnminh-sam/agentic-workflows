---
name: push
description: Push the current branch with the credential the git-guard profile pins (an SSH key, or the gh account over HTTPS), after an explicit confirmation. Use when the user runs /my-git:push or asks to push with their guarded identity. Takes an optional repo path, --commit to commit first, and --upstream to publish a new branch. Never force-pushes.
argument-hint: "[repo-path] [--commit [what to commit / message hint]] [--upstream]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git config:*), Bash(git -c:*), Bash(git -C:*), Bash(git commit:*), Bash(git push:*), Bash(git rev-parse:*), Bash(git remote:*), Bash(git branch:*), Bash(git ls-remote:*), Bash(gh auth status:*), Bash(python3 *setup_profile.py*), Bash(python3 *check_message.py*), AskUserQuestion
---

Push the current branch of a git repo using the credential and account from the git-guard profile.

Arguments: $ARGUMENTS

`<root>` below is `${CLAUDE_PLUGIN_ROOT}` (the my-git plugin folder). On Windows use `python` if `python3` is not on PATH.

## Context

- git-guard profile: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_profile.py" show`

The profile's `git_transport` decides how the push authenticates:

| `git_transport` | Remote | Credential |
|---|---|---|
| `ssh` (also when unset) | `git@github.com:<owner>/<repo>.git` | the profile's `ssh_key`, pinned on the command |
| `gh` | `https://github.com/<owner>/<repo>.git` | the gh credential helper (`gh auth setup-git`), as the active gh account. No SSH key. |

## Steps

0. **Resolve the repo.**
   - If the first argument is a path to an existing directory (it may be quoted and use `\` or `/`), it is the repo path. Remove it from the arguments before parsing flags.
   - Otherwise no repo path was given. Use the current working directory.
   - Resolve the repo root with `git -C "<path>" rev-parse --show-toplevel`. If that fails, stop and tell the user the path is not inside a git repository.
   - From here on, run **every** git command as `git -C "<repo>" ...` (put `-C` before any `-c` options) so the session's working directory never matters.
   - Gather context:
     - Branch: `git -C "<repo>" branch --show-current`
     - Branch and upstream: `git -C "<repo>" status --short --branch`. The first line reads `## <branch>...<remote>/<branch>` when an upstream exists, and `## <branch>` when not.
     - Remotes: `git -C "<repo>" remote -v`
1. **Parse the flags.**
   - `--commit`: commit first. Follow every step of the `my-git:commit` skill (`<root>/skills/commit/SKILL.md`), passing the same repo path. Use the text after `--commit` (with other flags removed) as its request. If that commit stops or fails, don't push.
   - `--upstream` (alias `-u`): the branch has no upstream yet. Publish it to `origin` and set tracking with `push -u origin <branch>`.
   - Force pushing is not supported. If the arguments contain `--force`, `-f`, `--force-with-lease`, `--force-if-includes` or a `+<refspec>`, stop and tell the user this skill never force-pushes. Don't fall back to a normal push in that case.
   - With no `--commit` flag, don't commit or stage anything. If the working tree is dirty, push the existing commits and mention that uncommitted changes were not included.
2. **Check before pushing.** Stop and explain if any of these fail:
   - **Credential**, by transport:
     - `ssh`: `ssh_key` in the profile is set. If it's `(unset)`, tell the user to run `python3 "<root>/scripts/setup_profile.py" init --ssh-key <path>`, or, if they use a single GitHub account over HTTPS with gh, `init --from-current --transport gh`. The remote must be SSH (`git@github.com:...`).
     - `gh`: the remote must be HTTPS (`https://github.com/...`) with no credentials in the URL. `git -C "<repo>" config --get-urlmatch credential.helper https://github.com` must name `gh auth git-credential` (otherwise tell the user to run `gh auth setup-git`). `gh auth status --active --hostname github.com` must show the profile's `github_user` (otherwise tell the user to run `gh auth switch`; don't switch yourself).
   - The remote's owner matches `github_user` or one of `github_orgs` from the profile, when those are set.
   - The branch isn't detached.
   - The branch isn't the production or staging branch (`main` / `develop/base`). Those only change through PRs (rule BR002). Use `/my-git:pr`, or `/my-git:pr --release` for staging into production. The one exception is the very first publish of these branches in a new repository (no `origin/<branch>` yet), which the guard turns into a confirmation.
   - **No upstream and no `--upstream` flag:** stop. Tell the user the branch has no upstream and to run `/my-git:push [repo-path] --upstream` to publish it. Don't add `-u` on your own.
   - **Upstream exists and `--upstream` was given:** ignore the flag, push normally, and say so.
   - There is something to push: `git -C "<repo>" log --oneline @{u}..HEAD` when an upstream exists, or `git -C "<repo>" log --oneline origin/<default-branch>..HEAD` (falling back to `git -C "<repo>" log --oneline -10`) for a new branch. If nothing is ahead, stop.
   - For a new branch, also check the remote doesn't already have `<branch>` (`git -C "<repo>" [<ssh pin>] ls-remote --heads origin <branch>`). If it does, stop and explain. Overwriting it would need a force push, and this skill never does one.
3. **Always ask for confirmation**, every time, using AskUserQuestion. The flags never count as confirmation. The question must show:
   - the repo path, the branch, the remote and remote branch, and whether this will create a new upstream (`-u`),
   - the credential it will use: the SSH key path (never its contents) or "gh account `<login>` over HTTPS",
   - the commits that will be pushed (short hash + subject),
   - the exact `git push` command that will run.
   Offer "Push" and "Cancel". Push only on an explicit "Push". On "Cancel" or any other answer, stop without pushing.
4. **Push**, with the credential from the profile:
   - `ssh`: pin the key on the command:
     `git -C "<repo>" -c core.sshCommand="ssh -i \"<ssh_key>\" -o IdentitiesOnly=yes" push [-u origin <branch>]`
     (on Windows use the full OpenSSH path, for example `C:/WINDOWS/System32/OpenSSH/ssh.exe`, if `ssh` is not the one git should use).
   - `gh`: push plainly; the gh credential helper supplies the active account's token:
     `git -C "<repo>" push [-u origin <branch>]`
   - Never use `--force`, `-f`, `--force-with-lease`, `--force-if-includes`, `--mirror`, `--delete` or `+` refspecs.
   - Don't push through `gh` commands, don't put a token in the URL, and don't use `--no-verify`.
   - If the push is rejected (for example non-fast-forward), report it and stop. Don't retry with force or rebase on your own.
   - If git-guard blocks the command, report the rule ID and message. Don't work around it.
5. **Report the result:** the repo, the commit range that was pushed and the remote branch. Suggest `/my-git:pr` to open the pull request into `develop/base`.
