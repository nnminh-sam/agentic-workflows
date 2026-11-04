---
description: Commit changes using the identity from the git-guard profile
argument-hint: "[what to commit / message hint]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git config:*), Bash(git -c:*), Bash(git commit:*), Bash(python D:/workspace/HAISE/agents/git-guard/scripts/*)
---

Commit changes in this repo as the identity configured in the git-guard profile.

Request: $ARGUMENTS

## Context

- git-guard profile: !`python D:/workspace/HAISE/agents/git-guard/scripts/setup_profile.py show`
- Repo-local identity: !`git config --local user.name; git config --local user.email`
- Branch: !`git branch --show-current`
- Status: !`git status --short --untracked-files=all`
- Recent commits (match their style): !`git log --oneline -8`

## Steps

1. **Resolve the author.**
   - Use `git_name` and `git_email` from the git-guard profile above. If `git_email` is a comma-separated list, use the first entry.
   - If either value is `(unset)`, fall back to the **repo-local** value (`git config --local`), never the global one.
   - If a value is still empty, stop. Tell the user how to set it:
     `python D:/workspace/HAISE/agents/git-guard/scripts/setup_profile.py init --git-name "<name>" --git-email "<email>"`.
     Don't change any git config or profile yourself.
2. **Pick what to commit.**
   - If the request names a scope (for example "changes related to X"), read the diffs and stage only the files that belong to it. List what you left out and why.
   - With no request, stage all changes the user would expect to commit.
   - Never stage secrets (`.env*`, keys, credentials, `profile.local.json`) or `.claude/git-guard/`.
   - If something is already staged and no request was given, commit just what is staged.
   - Stage files by explicit path with `git add -- <paths>`. Don't use `git add -A` or `git add .`.
3. **Write the message.**
   - Use Conventional Commits (`type(scope): summary`, imperative, header ≤ 72 chars) with a short body explaining why.
   - Validate it before committing:
     `python D:/workspace/HAISE/agents/git-guard/scripts/check_message.py --file <msgfile>`.
     Write `<msgfile>` to the session scratchpad. Fix it if the check exits non-zero.
4. **Commit with the identity pinned on the command**, so global config can never leak in:
   `git -c user.name="<name>" -c user.email="<email>" commit -F <msgfile>`
   - Don't use `--no-verify` or `--amend` unless the user asked for it.
   - If git-guard blocks the command, report the rule ID and message. Don't work around it.
5. **Report the result:** the short hash, the author (`git log -1 --format='%an <%ae>'`), the files committed, and anything left uncommitted.
