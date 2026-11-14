---
name: commit
description: Commit changes in a git repo as the identity in the git-guard profile, signed with the profile's signing key, with a validated Conventional Commits message. Use when the user runs /my-git:commit or asks to commit with their guarded identity. Takes an optional repo path (defaults to the current directory) and an optional hint about what to commit.
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
1b. **Resolve the signing key** from `signing_key` in the git-guard profile above:
   - `default`: sign with git's own signing setup (`user.signingkey`, `gpg.format`, `gpg.program` from git config). Check `git -C "<repo>" config user.signingkey`; if it's empty, git falls back to the author's GPG key, so mention that in the report.
   - any other value: it is the path of an SSH key file (private key or `.pub`) to sign with. Expand a leading `~` to the home directory. The key is pinned on the commit command (step 4), not written to git config.
   - `(unset)`: the commit is not signed. Before committing, tell the user once that signing isn't set up and how to set it, then continue unsigned:
     `python3 "<root>/scripts/setup_profile.py" init --signing-key default` (git's own signing config), or
     `python3 "<root>/scripts/setup_profile.py" init --signing-key "<path to SSH key>"`.
     Don't set it yourself: the key is the user's choice.
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
4. **Commit with the identity (and signing key) pinned on the command**, so a different global config can never leak in:
   - `signing_key` is `default`:
     `git -C "<repo>" -c user.name="<name>" -c user.email="<email>" commit -S -F <msgfile>`
   - `signing_key` is a key path:
     `git -C "<repo>" -c user.name="<name>" -c user.email="<email>" -c gpg.format=ssh -c user.signingkey="<key path>" commit -S -F <msgfile>`
   - `signing_key` is unset:
     `git -C "<repo>" -c user.name="<name>" -c user.email="<email>" commit -F <msgfile>`
   - If signing fails (no key, locked agent, wrong passphrase, `gpg`/`ssh-keygen` errors, git older than 2.34 for SSH signing), stop and show the error. Never retry without `-S` unless the user says so.
   - Don't use `--no-verify` or `--amend` unless the user asked for it.
   - If git-guard blocks the command, report the rule ID and message. Don't work around it.
5. **Report the result:** the repo, the short hash, the author (`git -C "<repo>" log -1 --format='%an <%ae>'`), whether it's signed and with what (`git -C "<repo>" log -1 --format='%G? %GK'`: `N` means unsigned; for SSH signatures `%G?` may show `U`/`E` until `gpg.ssh.allowedSignersFile` is configured, which is fine), the files committed, and anything left uncommitted.
