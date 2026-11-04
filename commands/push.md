---
description: Push the current branch with the git-guard SSH key (--commit to commit first, --upstream for a new branch)
argument-hint: "[--commit [what to commit / message hint]] [--upstream]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git config:*), Bash(git -c:*), Bash(git commit:*), Bash(git push:*), Bash(git rev-parse:*), Bash(git remote:*), Bash(git branch:*), Bash(git ls-remote:*), Bash(python D:/workspace/HAISE/agents/git-guard/scripts/*), AskUserQuestion
---

Push the current branch using the SSH key and account from the git-guard profile.

Arguments: $ARGUMENTS

## Context

- git-guard profile: !`python D:/workspace/HAISE/agents/git-guard/scripts/setup_profile.py show`
- Branch: !`git branch --show-current`
- Branch and upstream (first line reads `## <branch>...<remote>/<branch>` when an upstream exists, `## <branch>` when not): !`git status --short --branch`
- Remotes: !`git remote -v`

## Steps

1. **Parse the flags.**
   - `--commit`: commit first. Follow every step in `.claude/commands/commit.md`. Use the text after `--commit` (with other flags removed) as its request. If that commit stops or fails, don't push.
   - `--upstream` (alias `-u`): the branch has no upstream yet. Publish it to `origin` and set tracking with `git push -u origin <branch>`.
   - Force pushing is not supported. If the arguments contain `--force`, `-f`, `--force-with-lease`, `--force-if-includes` or a `+<refspec>`, stop and tell the user this command never force-pushes. Don't fall back to a normal push in that case.
   - With no `--commit` flag, don't commit or stage anything. If the working tree is dirty, push the existing commits and mention that uncommitted changes were not included.
2. **Check before pushing.** Stop and explain if any of these fail:
   - `ssh_key` in the profile is set. If it's `(unset)`, tell the user to run `setup_profile.py init --ssh-key <path>`.
   - The remote is SSH (`git@github.com:...`) and its owner matches `github_user` from the profile, when that value is set.
   - The branch isn't detached.
   - **No upstream and no `--upstream` flag:** stop. Tell the user the branch has no upstream and to run `/push --upstream` to publish it. Don't add `-u` on your own.
   - **Upstream exists and `--upstream` was given:** ignore the flag, push normally, and say so.
   - There is something to push: `git log @{u}..HEAD` when an upstream exists, or `git log --oneline origin/<default-branch>..HEAD` (falling back to `git log --oneline -10`) for a new branch. If nothing is ahead, stop.
   - For a new branch, also check the remote doesn't already have `<branch>` (`git -c core.sshCommand=... ls-remote --heads origin <branch>`). If it does, stop and explain. Overwriting it would need a force push, and this command never does one.
3. **Always ask for confirmation**, every time, using AskUserQuestion. The flags never count as confirmation. The question must show:
   - the branch, the remote and remote branch, and whether this will create a new upstream (`-u`),
   - the commits that will be pushed (short hash + subject),
   - the exact `git push` command that will run (with the key path, never its contents).
   Offer "Push" and "Cancel". Push only on an explicit "Push". On "Cancel" or any other answer, stop without pushing.
4. **Push with the key pinned on the command**, using the profile's `ssh_key`:
   `git -c core.sshCommand="C:/WINDOWS/System32/OpenSSH/ssh.exe -i \"<ssh_key>\" -o IdentitiesOnly=yes" push [-u origin <branch>]`
   - Never use `--force`, `-f`, `--force-with-lease`, `--force-if-includes`, `--mirror`, `--delete` or `+` refspecs.
   - Never use `gh`.
   - Don't use `--no-verify`.
   - If the push is rejected (for example non-fast-forward), report it and stop. Don't retry with force or rebase on your own.
   - If git-guard blocks the command, report the rule ID and message. Don't work around it.
5. **Report the result:** the commit range that was pushed and the remote branch. If this push set a new upstream, also give the compare URL `https://github.com/<owner>/<repo>/compare/<branch>?expand=1` so the user can open a PR.
