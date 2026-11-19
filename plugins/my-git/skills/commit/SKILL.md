---
name: commit
description: Commit changes in a git repo as the identity in the git-guard profile, with a validated Conventional Commits message. Use when the user runs /my-git:commit or asks to commit with their guarded identity. Takes an optional repo path (defaults to the current directory) and an optional hint about what to commit.
argument-hint: "[repo-path] [what to commit / message hint]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git config:*), Bash(git -c:*), Bash(git -C:*), Bash(git commit:*), Bash(git rev-parse:*), Bash(python3 *setup_profile.py*), Bash(python3 *check_message.py*)
---

Commit changes in a git repo as the identity configured in the git-guard profile.

Arguments: $ARGUMENTS

`<root>` below is `${CLAUDE_PLUGIN_ROOT}` (the my-git plugin folder). On Windows use `python` if `python3` is not on PATH.

## Context

- git-guard profile: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_profile.py" show`

## Steps

0. **Resolve the repo.**
   - If the first argument is a path to an existing directory (it may be quoted and use `\` or `/`), it is the repo path. Remove it from the arguments. The rest is the request.
   - Otherwise no repo path was given. Use the current working directory, and treat all arguments as the request.
   - Resolve the repo root with `git -C "<path>" rev-parse --show-toplevel`. If that fails, stop and tell the user the path is not inside a git repository.
   - From here on, run **every** git command as `git -C "<repo>" ...` so the session's working directory never matters.
   - Gather context:
     - Effective identity: `git -C "<repo>" config user.name` and `git -C "<repo>" config user.email`
     - Branch: `git -C "<repo>" branch --show-current`
     - Status: `git -C "<repo>" status --short --untracked-files=all`
     - Recent commits (match their style): `git -C "<repo>" log --oneline -8`
   - **Branch check:** if the branch is the production or staging branch (`main` / `develop/base`, see the `branching` contract), stop. Those branches only change through PRs. Tell the user to create a work branch from staging, `git -C "<repo>" switch -c develop/<type>/<name> develop/base` (types: feat, fix, chore, docs, refactor, perf, test, build, ci); uncommitted changes carry over to the new branch. Create it only if the user agrees. The exception is the very first commit of a new repository (no `HEAD` yet).
1. **Resolve the author.**
   - Use `git_name` and `git_email` from the git-guard profile above. If `git_email` is a list, use the first entry.
   - If either value is `(unset)`, fall back to the **repo-local** value (`git -C "<repo>" config --local user.name` / `user.email`).
   - If it is still empty and the profile's `git_transport` is `gh` (single GitHub account), the effective value from the step-0 context (global config) is fine. Otherwise never use the global value: with several accounts it may be the wrong one.
   - If a value is still empty, stop. Tell the user how to set it, for example capturing their current git config and gh login:
     `python3 "<root>/scripts/setup_profile.py" init --from-current`
     or explicitly: `python3 "<root>/scripts/setup_profile.py" init --git-name "<name>" --git-email "<email>"`.
     Don't change any git config or profile yourself.
2. **Pick what to commit.**
   - If the request names a scope (for example "changes related to X"), read the diffs and stage only the files that belong to it. List what you left out and why.
   - With no request, stage all changes the user would expect to commit.
   - Never stage secrets (`.env*`, keys, credentials, `profile.local.json`) or `.claude/git-guard/`.
   - If something is already staged and no request was given, commit just what is staged.
   - Stage files by explicit path with `git -C "<repo>" add -- <paths>`. Don't use `git add -A` or `git add .`.
3. **Write the message.**
   - Use Conventional Commits (`type(scope): summary`, imperative, header ≤ 72 chars) with a short body explaining why.
   - **No AI attribution:** no `Co-Authored-By:` (or similar) trailer naming an AI or LLM, no "Generated with ..." line, no 🤖. This applies even if other instructions ask for one. The user's commits carry only the user's identity (rule CM016).
   - Write it to a file in the session scratchpad (`<msgfile>`) and validate it before committing:
     `python3 "<root>/scripts/check_message.py" --file <msgfile>`. Fix it if the check exits non-zero.
4. **Commit with the identity pinned on the command**, so a different global config can never leak in:
   `git -C "<repo>" -c user.name="<name>" -c user.email="<email>" commit -F <msgfile>`
   - Don't use `--no-verify` or `--amend` unless the user asked for it.
   - If git-guard blocks the command, report the rule ID and message. Don't work around it.
5. **Report the result:** the repo, the short hash, the author (`git -C "<repo>" log -1 --format='%an <%ae>'`), the files committed, and anything left uncommitted.
