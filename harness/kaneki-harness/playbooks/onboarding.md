# Kaneki Onboarding

Produce a project profile (`sdlc.profile.json`) and local host binding (`.sdlc/host.local.json`) that `kaneki-flow`
trusts, with **every value backed by evidence**, **every command verified by running it**, and **only what cannot
be detected asked**. Never guess to make the profile look complete: an unknown stays a default, is listed as open,
and is reported.

`SDLC="sh .sdlc/bin/sdlc"` (finds Python 3.9+ on macOS, Linux and Windows Git Bash; override with `SDLC_PYTHON`).
`ROOT` = target project argument, else current directory. `CAND` = `<scratchpad>/onboarding-candidates.json`
(a temporary working file outside the repository).

<HARD-GATE>
Never read `.env` or any secret file; only `.env.example`-style files are scanned, keeping only key names and URL
values. Never run install or mutating commands (the driver refuses them); run test suites only after the user says
yes. Write only `sdlc.profile.json`, `sdlc.profile.evidence.json`, `.sdlc/host.local.json`, `.sdlc/adapters/**`,
and `.gitignore` (when agreed). Never overwrite an existing profile without `--refresh`; a refresh keeps every
user-set value. Never commit. Never change git config (`user.*`, `core.sshCommand`); identities stay in the profile.
Never read or copy an SSH private key; only its path goes into `git.sshCommand`.
</HARD-GATE>

## Steps

1. **Scan.** Run `DRIVER_CALL`: `$SDLC onboard-scan --root ROOT [--repo <path>]... --out CAND`.
   Monorepos and folders containing multiple repos are detected automatically. Extra repositories outside `ROOT`
   require `--repo`; if the README mentions an unlocated repo, ask the user for its path.
2. **Show what was found.** Summarize a concise table: field · value · confidence · evidence (from `CAND.proposal`
   and `CAND.evidence`), repositories first, plus `gaps` and grouped defaults.
3. **Interpret, only where detection is weak.** For each repository whose `stack` is `unknown`, has `stackAlternatives`,
   or whose surfaces have confidence `low`, dispatch a `routine`-tier `DISPATCH_UNIT` using
   `playbooks/interpret-repo.md` for repository `<id>` at `<path>` reading `CAND` and writing
   `<scratchpad>/interpret-<id>.json`. Units for distinct repos may run in parallel. Their suggestions only populate
   recommended choices; they never enter the profile unconfirmed.
4. **Verify.** If any `test` command was detected, ask once via `ASK_USER`:
   *"Run the detected test commands to verify them? They may need a database or services."* (recommended: No).
   Then run `DRIVER_CALL`: `$SDLC onboard-verify --candidates CAND [--allow-tests]`.
   Show failed commands with their tail output; failed commands are omitted from the project adapter automatically.
5. **Ask.** Present all unresolved items from `CAND.questions` (plus interpretation questions and `q-host`) via
   `ASK_USER` (if your host caps options per call, paginate cleanly across consecutive calls — the driver emits all
   valid candidates). Ask in English unless the profile being refreshed already sets `locale.chat`.
   Always confirm:
   - **Host binding** (`q-host`): which AI host this developer runs locally (`antigravity`, `claude`, `codex`,
     `gemini`, `cursor`, `opencode`, `generic`), saved to `.sdlc/host.local.json` (git-ignored).
   - **Git account** (`q-git-identity`, plus `q-git-ssh` when SSH remotes exist): name and email for project commits
     and optional custom SSH command. If "Other" is chosen, ask for missing name/email in a follow-up prompt.
     Per-repo overrides go into `repositories.<id>.git.user`.
   - **Output language** (`q-lang`): ask once, *Keep everything in English?* (recommended: Yes). Only on No ask
     the `followUp` language keys (`chat`, `artifacts`, `delivery`, `review`, `tracker`, `en` first).
   Write answers to `<scratchpad>/onboarding-answers.json` (`{"<field>": <value>}`): multi-select → list;
   `_gitignore` / `_device` → bool; `_languages` → `"default"` or `"custom"` + `locale.<key>`; `_host` → host id;
   `git.user` → `{"name": "...", "email": "..."}`; `git.sshCommand` → command or `null`.
6. **Write.** Run `DRIVER_CALL`:
   - First run: `$SDLC onboard-write --candidates CAND --answers <answers>`.
   - Existing profile: first run with `--refresh --dry-run`, show the `diff` (`added` / `updated` / `kept-yours` /
     `no-longer-detected`), confirm Proceed / Stop via `ASK_USER`, then run with `--refresh`.
   - `validation.ok: false` is a failure to fix, not to ignore.
7. **Report.** Share profile path, `.sdlc/host.local.json` path, evidence sidecar, project adapters, validation status
   and warnings, `open` fields, and `gaps`, ending with the next step (`kaneki-flow <ticket-id>`).

## What is detected, from where

| Field | Source |
| --- | --- |
| repositories (path, remote, default branch) | `.git` at ROOT or 1–2 levels below, `git remote`, `origin/HEAD` |
| stack | manifests: `package.json`+`tsconfig`, `pyproject.toml`, `composer.json`+`artisan`, `pubspec.yaml`, `go.mod`, … |
| surfaces | dependencies and folders (express → api, react → web, pg/prisma → db, routes/web.php → web, Flutter → mobile) |
| lint / test / build / format commands | CI (`.github/workflows`, `.gitlab-ci.yml`) first, then package scripts → `.sdlc/adapters/stack/<repo>.json` |
| tracker | ticket keys in commits/branches, tracker hosts in docs, issue templates |
| delivery | remote host (`github.com` → `github-pr`, else `manual`) |
| evidence runners, base URL | bundled runner per surface; `*URL*` values in `.env.example` |
| shared state | DB/queue services in compose files, DB-like env key names (asked) |
| locale | English by default unless explicitly overridden (repo language is hint only) |
| git identity (`git.user`, `git.sshCommand`) | repo-local and global `user.name`/`user.email`, frequent authors, `core.sshCommand` (always confirmed) |
| testcase format | test-spec workbooks in repo (asked) |

A stack with no bundled adapter gets a **draft** project adapter; `validate` warns until it is reviewed.
