# Kaneki Ship

The only path by which a Kaneki ticket gets commits, pushes and a PR/MR. Nodes never commit (worktree hooks,
host guards, and `sdlc git` refuse it). This workflow runs once per repository, for one approved request. Two ship
backends are supported:

- **`native-git` (default, host-agnostic):** `sdlc ship --approve <requestDigest>` stages the reviewed files,
  grants a short-lived approval in `.sdlc/ship-approvals.json`, commits as the profile identity (`git.user`),
  verifies the commit, pushes if configured, and opens the PR/MR (`gh`, `glab`, or manual instructions).
- **`my-git` (optional Claude Code backend, via `delivery.shipBackend: "my-git"` or `--backend my-git`):**
  stages the files and prints `/my-git:commit`, `/my-git:push`, and `/my-git:pr` next steps, followed by
  `sdlc ship-record`.

`$SDLC` = the driver command your host binding names (`.sdlc\bin\sdlc-<host>.cmd` on Windows,
`sh .sdlc/bin/sdlc-<host>` on macOS/Linux); run every driver command via `DRIVER_CALL`. Speak to the user in the profile's
`locale.chat` (English unless set).

<HARD-GATE>
Never commit, push or open a PR without the user approving the exact request shown, by its digest, in this
session. Never edit the request, the drafts or the code here: a change means going back to `kaneki-flow`
(`invalidate`), which reruns review and re-issues receipts. Never force-push. Never stage, commit or push by hand.
</HARD-GATE>

## Steps (per repository of the ticket)

1. `DRIVER_CALL`: `$SDLC status --ticket <id> --compact` — `deliverable-ready` must be `done`. If not, stop and name `nextPending`.
2. `DRIVER_CALL`: `$SDLC ship-request --ticket <id> --repo <repo>` — refuses if receipts are missing, expired or stale,
   or if `drafts/commit-msg.md` fails Conventional Commits / no-AI-attribution rules (`delivery-drafts` owns fixes).
   Show the user verbatim: repository, branch → base, every file in `files`, commit message, identity (`name <email>`),
   push/PR target, `via` backend, and `requestDigest`.
3. `ASK_USER`: **Ship exactly this** (recommended) / **Change something** / **Stop**.
   - Change → state which node owns the change (`delivery-drafts` or `coding`) and stop.
4. On approval, `DRIVER_CALL`: `$SDLC ship --ticket <id> --repo <repo> --approve <requestDigest> [--no-pr]`.
   It rebuilds the request and refuses if anything changed since approval.
   - If `via == "native-git"`: commit, push, and PR creation (for `gh`/`glab`) execute and record automatically. If `next`
     prints manual PR instructions, tell the user to open the PR/MR from `prBodyFile` and run
     `$SDLC ship-record --ticket <id> --repo <repo> --pr <url>`.
   - If `via == "my-git"`: execute the `next` steps in order, then run `$SDLC ship-record --ticket <id> --repo <repo> [--pr <url>]`.
5. Report commit SHA, branch, push result, and PR/MR URL (or compare instructions). If a push failed, fix access, push,
   then run `$SDLC ship-record --ticket <id> --repo <repo> [--pr <url>]`.

## After the PR

When reviewers comment: `DRIVER_CALL` `$SDLC review-fetch --ticket <id> --repo <repo>` (GitHub with `gh`), or export
the review to a file and run `handback`. `kaneki-flow` then runs `read-review → triage-review → …`. Pushing to the
same branch updates the open PR, so PR creation is skipped on re-ship.
