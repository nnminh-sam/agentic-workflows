---
name: pr
description: Open a GitHub pull request for the current branch under the my-git branching model. By default the PR targets the staging branch (develop/base), or the parent work branch the current branch was forked from (nested feature branches, --base to choose); with --release it opens the release PR from develop/base into the production branch (main). Runs the unit tests and lint locally first and writes the body with Summary, Detailed changes, Explanation and Test results. Use when the user runs /my-git:pr or asks to open, create or raise a PR / pull request / merge request.
argument-hint: "[repo-path] [--release] [--draft] [hint about the change]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git rev-parse:*), Bash(git branch:*), Bash(git remote:*), Bash(git ls-remote:*), Bash(git fetch:*), Bash(git -C:*), Bash(gh pr list:*), Bash(gh pr view:*), Bash(gh pr create:*), Bash(gh repo view:*), Bash(gh auth status:*), Bash(python3 *check_pr.py*), Bash(python3 *setup_profile.py*), AskUserQuestion
---

Open a pull request following the my-git branching model.

Arguments: $ARGUMENTS

`<root>` below is `${CLAUDE_PLUGIN_ROOT}` (the my-git plugin folder). On Windows use `python` if `python3` is not on PATH.

## PR mode (`pr_via`)

Read it first: `python3 "<root>/scripts/setup_profile.py" show` (row `pr_via`; unset means `gh`).

- `gh` (default): steps 2, 8 and 9 use `gh` as written.
- `browser`: **never run `gh`**, not even `gh pr list` or `gh auth status`; rule GH010 denies every gh command, and
  GH001 denies `curl`/`Invoke-RestMethod` to `api.github.com`. Skip the open-PR check in step 2 (the compare page
  shows an open PR itself). In step 7 add `--compare-url "<repo>"` so the check also prints the prefilled link.
  Steps 8 to 10 change as noted there: you hand the user the link, and the user submits the PR.

## Branching model

Branch names come from the `branching` contract (`settings.production_branch` / `settings.staging_branch`; defaults below). If a project override changes them, use the project's names.

| | Head (source) | Base (target) |
|---|---|---|
| default | the current work branch `develop/<type>/<name>` | `develop/base` (staging), or its parent work branch (see below) |
| `--base <branch>` | the current work branch | `<branch>`: `develop/base` or another work branch (a parent) |
| `--release` | `develop/base` | `main` (production) |

**Nested work branches** (`settings.nested_work_branches`, on by default): a feature can have its own parent branch,
for example `develop/feat/kaneki-harness`, with child branches such as `develop/feat/kh-P1-T07` that start from it and
open their PR into it; the parent then opens its PR into `develop/base`. Child names must be flat: git cannot hold
`develop/feat/kaneki-harness` and `develop/feat/kaneki-harness/x` at the same time.

`main` and `develop/base` never receive direct commits or pushes. They only change through merged PRs.

## Steps

0. **Resolve the repo.**
   - If the first argument is a path to an existing directory, it is the repo path. Remove it from the arguments. Otherwise use the current working directory.
   - Get the repo root with `git -C "<path>" rev-parse --show-toplevel`. If that fails, stop: the path is not inside a git repository.
   - Run **every** git command as `git -C "<repo>" ...`.
   - Parse the flags: `--release`, `--draft`, `--base <branch>`. Whatever is left is the user's hint about the change.
   - Gather context: the current branch (`git -C "<repo>" branch --show-current`), `git -C "<repo>" status --short`, and `git -C "<repo>" remote -v`.
1. **Pick base and head.**
   - Default: head is the current branch. The base:
     - `--base <branch>` given: use it. It must be `develop/base` or another work branch `develop/<type>/<name>`
       (not the head itself); `check_pr.py` in step 7 enforces this (BR005).
     - Otherwise, after the fetch in step 2, run `python3 "<root>/scripts/pr_base.py" "<repo>" <head>`. Use its `base`
       line. If it prints a `tie` line, ask the user which of those branches is the parent. Mention the chosen base and
       the runner-up (`candidate` lines) in the step-8 confirmation so the user can correct it.
     - If the current branch is `main` or `develop/base`, stop. Tell the user that work goes on a branch made with `git switch -c develop/feat/<name> develop/base`, and that `/my-git:pr --release` opens the release PR.
     - If the branch name doesn't match `develop/<type>/<name>` (types: feat, fix, chore, docs, refactor, perf, test, build, ci), stop. Suggest `git branch -m develop/<type>/<name>`. Don't rename it yourself.
   - `--release`: head is `develop/base` and base is `main`, whatever branch is checked out. Pass `--head develop/base` explicitly.
2. **Check the head is ready.**
   - Run `git -C "<repo>" fetch origin` (read-only).
   - The head must exist on the remote: `git -C "<repo>" ls-remote --exit-code --heads origin <head>`.
     - If it doesn't exist (work branch only), stop. Tell the user to publish it with `/my-git:push --upstream`.
   - Local commits that aren't on the remote (`git -C "<repo>" log --oneline origin/<head>..<head>`) won't be in the PR. List them and suggest `/my-git:push` first. Don't push yourself.
   - Uncommitted changes aren't in the PR either. Mention them.
   - The base must exist on the remote too (`git -C "<repo>" ls-remote --exit-code --heads origin <base>`); a parent work
     branch has to be published before its children can open PRs into it.
   - There must be something to merge: `git -C "<repo>" log --oneline origin/<base>..origin/<head>`. If it's empty, stop.
   - If a PR already exists (`gh pr list --head <head> --base <base> --state open`), show its URL and stop.
     Browser mode: skip this check.
3. **Gather the changes:**
   - commits: `git -C "<repo>" log --format='%h %s%n%b' origin/<base>..origin/<head>`
   - file summary: `git -C "<repo>" diff --stat origin/<base>...origin/<head>`
   - read the diff (`git -C "<repo>" diff origin/<base>...origin/<head>`) as far as needed to explain it.
4. **Run the local checks (unit tests + lint).** These are required: the body must report both.
   - Find the project's commands, in this order:
     1. the project's `CLAUDE.md` / `README` / `CONTRIBUTING`
     2. `package.json` scripts (`test`, `test:unit`, `lint`), using the lockfile's manager (`pnpm`/`yarn`/`bun`/`npm`)
     3. `Makefile` targets (`test`, `lint`)
     4. `pyproject.toml` / `tox.ini` (`pytest`, `ruff check`, `flake8`)
     5. `go.mod` (`go test ./...`, `go vet ./...` / `golangci-lint run`)
     6. `Cargo.toml` (`cargo test`, `cargo clippy`)
     7. `build.gradle` / `pom.xml`
   - Run them in the repo, on the head branch's content. If another branch is checked out (for example with `--release`), say so, and ask before switching branches.
   - Record each command and its outcome: pass/fail counts, number of lint issues, and the key failure lines.
   - **If tests or lint fail**, show the failures and ask the user with AskUserQuestion: "Fix first" (stop) or "Open PR anyway" (the Test results section then states the failures plainly).
   - **If no test or lint command exists**, ask the user for the command. If there really is none, write `Unit tests: none configured` / `Lint: none configured` in the body. Never invent results.
5. **Write the title.**
   - Use the Conventional Commits header format `type(scope): subject`, imperative, at most 72 characters.
   - For a single-purpose branch, take the type from the branch (`develop/feat/...` gives `feat`).
   - A parent work branch going into `develop/base` gets a title for the whole feature; its Detailed changes list the
     child PRs merged into it (`git log --merges --oneline origin/develop/base..origin/<head>`).
   - For `--release`, use `chore(release): <version or date>`. Use the version from the changelog or tags if there is one, otherwise today's date.
6. **Write the body** to a file in the session scratchpad (`<bodyfile>`), with exactly these sections. Get the skeleton from `python3 "<root>/scripts/check_pr.py" --print-template`, or from `.github/pull_request_template.md` if the repo has one.
   ```markdown
   ## Summary
   <1-3 sentences: what this PR does and the outcome for users/the system>

   ## Detailed changes
   - <area/file>: <change>
   - ...

   ## Explanation
   <why: the problem, the approach chosen and why, trade-offs, risks, follow-ups>

   ## Test results
   - Unit tests: `<command>` → <N passed, M failed, duration>
   - Lint: `<command>` → <clean / N issues>
   ```
   - For `--release`, the Detailed changes section lists the PRs and commits since the last release (`git log --oneline origin/main..origin/develop/base`).
   - **No AI attribution, anywhere:**
     - no `Co-Authored-By:` (or similar) trailer naming an AI or LLM
     - no "Generated with Claude Code" or similar line
     - no 🤖
     The guard blocks these (CM016 / GH009).
7. **Validate before creating:**
   `python3 "<root>/scripts/check_pr.py" --title "<title>" --body-file <bodyfile> --base <base> --head <head>`
   Fix the title or body until it prints `ok`.
   Browser mode: append `--compare-url "<repo>"`. It prints `compare-url: <link>` after `ok`. If it also says the body
   is too long for the link, the link carries only the title and the user pastes the body from `<bodyfile>`.
8. **Confirm with AskUserQuestion.** Show:
   - head → base
   - the title
   - the body (or its first lines plus the Test results section)
   - whether it is a draft
   - the exact command
   Offer "Create PR" and "Cancel". Create it only on an explicit "Create PR".
   Browser mode: show the link instead of the command, and offer "Open link" and "Cancel".
9. **Create the PR**, non-interactively:
   `gh pr create --base <base> --head <head> --title "<title>" --body-file <bodyfile> [--draft]`
   - Never use `--fill`, `--web` or `--template`. The guard can't check those bodies.
   - If git-guard blocks the command, report the rule ID and message. Fix the PR content, never the guard.
   - Browser mode: don't create anything. On "Open link", open it with `python3 -m webbrowser "<link>"` and print the
     link too. The user checks the prefilled form and presses "Create pull request".
10. **Report:** the PR URL (browser mode: the compare link, and that the user submits it), head → base, and the local test and lint outcome. Don't merge it: merging goes through GitHub, where review and the branch rules apply.
