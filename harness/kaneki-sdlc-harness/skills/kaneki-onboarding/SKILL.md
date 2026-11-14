---
name: kaneki-onboarding
description: "Onboard a project to the Kaneki SDLC harness: detect repositories, stacks, surfaces, CI commands, tracker and delivery from evidence, verify the commands by running them, ask only what cannot be detected, and write sdlc.profile.json with a provenance sidecar and project stack adapters."
when_to_use: "Invoke in a project directory before the first /kaneki-flow ticket, or with --refresh after the project changed (new repo, new CI commands). Replaces hand-filling the profile."
argument-hint: "[project-dir] [--repo <path>]... [--refresh] [--allow-tests] [--dry-run]"
user-invocable: true
---

# Kaneki onboarding

Produce a project profile the `kaneki-flow` driver trusts, with **every value backed by evidence**, **every
command verified by running it**, and **only what cannot be detected asked**. Never guess to make the profile
look complete: an unknown stays a default, is listed as open, and is reported.

`SDLC="sh $CLAUDE_SKILL_DIR/scripts/sdlc"` (the launcher finds Python 3.9+ on macOS, Linux and Windows Git Bash; override with `SDLC_PYTHON`) (set `SDLC_HOME` if the harness is installed elsewhere).
`ROOT` = the argument, else the current directory. `CAND` = `<scratchpad>/onboarding-candidates.json`
(a working file; never inside the project).

<HARD-GATE>
Never read `.env` or any secret file; only `.env.example`-style files are scanned, and only key names and URL
values are kept. Never run install or mutating commands (the driver refuses them); run tests only after the
user says yes. Write only `sdlc.profile.json`, `sdlc.profile.evidence.json`, `.sdlc/adapters/**`, and
`.gitignore` when the user agrees. Never overwrite an existing profile without `--refresh`; a refresh keeps
every value the user set. Never commit. Never change any git config (repo-local or global): the chosen
identity lives only in the profile and is applied per command by `sdlc git`. Never read or copy an SSH private
key; only its path goes into `git.sshCommand`.
</HARD-GATE>

## Steps

1. **Scan.** `$SDLC onboard-scan --root ROOT [--repo <path>]... --out CAND`. Monorepos and folders holding several
   repos are found automatically. Repositories outside ROOT (for example a backend checked out elsewhere) need
   `--repo`; if the README or the user mentions one that was not found, ask for its path.
2. **Show what was found.** Print one table: field · value · confidence · evidence (from `CAND.proposal` and
   `CAND.evidence`), repositories first, and the `gaps` list. Keep it short; group defaults on one line.
3. **Interpret, only where detection is weak.** For each repository whose `stack` is `unknown`, has
   `stackAlternatives`, or whose surfaces have confidence `low`, dispatch one `kaneki-worker` subagent with:
   *"Follow `$CLAUDE_SKILL_DIR/references/interpret-repo.md` for repository `<id>` at `<path>`; candidates at `CAND`;
   write `<scratchpad>/interpret-<id>.json`."* Units with different repositories run in parallel. Their suggestions
   only become the **recommended option** of a question; they never go into the profile unconfirmed.
4. **Verify.** If any `test` command was detected, ask once: *"Run the detected test commands to verify them?
   They may need a database or services."* (recommended: No). Then
   `$SDLC onboard-verify --candidates CAND [--allow-tests]`. Show failed commands with their last lines; a failing
   command is left out of the project adapter, never kept silently.
5. **Ask.** Turn `CAND.questions` into AskUserQuestion calls (≤ 4 per call, recommended option first, the `why`
   as the option description, one doubt per question). Add a question for any interpretation result. Ask in
   English unless the profile being refreshed already sets `locale.chat`.
   Two groups are **always** asked, even when detection looks certain, because they decide what other people see:
   - **Git account** (`q-git-identity`, and `q-git-ssh` when a remote uses SSH): the name and email every commit of
     every node is made as, and the SSH key for pushes. Options are the detected identities (repo-local config,
     global config, frequent recent authors); the user may type another with "Other" — then ask for the missing
     part (name or email) in a follow-up question. Never pick one silently. If a repository must use a different
     account, record it as `repositories.<id>.git.user`.
   - **Output language** (`q-lang`): one question, *keep everything in English?*, recommended **Yes**. Everything
     defaults to English, commit messages and PR text included; a language detected in the repository is only a
     hint in the question and is never written by itself. Only on **No** ask the `followUp` questions (chat,
     artifacts, delivery, review, tracker; ≤ 4 per call, `en` first) and write only the keys the user set to
     something. Keys are independent; there is no fallback between them.
   Write the answers to `<scratchpad>/onboarding-answers.json` as `{ "<field>": <value> }`: multi-select → list;
   `_gitignore` and `_device` → true/false; `_languages` → `"default"` (English: removes every `locale` key, also
   on refresh) or `"custom"` plus `locale.<key>` answers; `git.user` → `{"name": "...", "email": "..."}`;
   `git.sshCommand` → the command string, or `null` for the default ssh. Free-text fields the user gave (Jira site, Backlog space)
   go under `tracker.config`.
6. **Write.**
   - First run: `$SDLC onboard-write --candidates CAND --answers <answers>`.
   - Existing profile: run with `--refresh --dry-run`, show the `diff` (added / updated / kept-yours /
     no-longer-detected), ask Proceed / Stop, then run with `--refresh`.
   - The command validates the result; `validation.ok: false` is a failure to fix, not to report and leave.
7. **Report.** Profile path, evidence file, project adapters written, validation result and warnings, the
   `open` fields (defaults and low confidence) and the `gaps`, each with what the user can do about it. End with
   the next step: `/kaneki-flow <ticket-id> --profile <path>`.

## What is detected, from where

| Field | Source |
| --- | --- |
| repositories (path, remote, default branch) | `.git` at ROOT or one/two levels below, `git remote`, `origin/HEAD` |
| stack | manifests: `package.json`+`tsconfig`, `pyproject.toml`, `composer.json`+`artisan`, `pubspec.yaml`, `go.mod`, … |
| surfaces | dependencies and folders (express → api, react → web, pg/prisma → db, routes/web.php → web, Flutter → mobile) |
| lint / test / build / format commands | CI (`.github/workflows`, `.gitlab-ci.yml`) first, then package scripts → `.sdlc/adapters/stack/<repo>.json` (`extends` the bundled adapter) |
| tracker | ticket keys in commits/branches, tracker hosts in docs, issue templates |
| delivery | remote host (github.com → `github-pr`, else `manual`) |
| evidence runners, base URL | bundled runner per surface; `*URL*` values in `.env.example` |
| shared state | DB/queue services in compose files, DB-like env key names (asked) |
| locale | not detected: English unless the user explicitly picks another language (the repository language is shown as a hint only) |
| git identity (`git.user`, `git.sshCommand`) | repo-local and global `user.name` / `user.email`, frequent commit authors, `core.sshCommand` (always confirmed by the user) |
| testcase format | test-spec workbooks in the repo (asked) |

A stack with no bundled adapter gets a **draft** project adapter; `validate` warns until it is reviewed.
