---
name: kaneki-ship
description: "Commit, push and open the PR/MR for a ticket that kaneki-flow brought to deliverable-ready: the only path that writes git history, for exactly the reviewed files, after the user approves the exact request."
when_to_use: "Invoke after kaneki-flow closes deliverable-ready (first delivery) or after a review round reaches deliverable-ready again (re-ship). Never during other nodes."
argument-hint: "<ticket-id> [--repo <repo-id>]... [--no-pr] [--backend native-git|my-git] [--profile <path>]"
user-invocable: true
---

# Kaneki Ship

The only way a kaneki ticket gets commits, pushes and a PR/MR. Nodes never commit (worktree git hooks, the flow
guard, and `sdlc git` refuse it). This skill runs once per repository, for one approved request. Two ship
backends are supported:

- **`native-git` (default, agent-agnostic):** `sdlc ship --approve <requestDigest>` stages the reviewed files,
  grants a short-lived approval in `.sdlc/ship-approvals.json`, commits as the profile identity (`git.user`),
  verifies the commit, pushes if configured, and opens the PR/MR (`gh`, `glab`, or prints manual instructions).
- **`my-git` (optional Claude Code backend, via `delivery.shipBackend: "my-git"` or `--backend my-git`):**
  stages the files and prints `/my-git:commit`, `/my-git:push`, and `/my-git:pr` `next` steps, followed by
  `sdlc ship-record`.

`SDLC="sh $CLAUDE_SKILL_DIR/scripts/sdlc"` (or `sdlc` if installed on `PATH`). Speak to the user in the profile's
`locale.chat` (English unless set).

<HARD-GATE>
Never commit, push or open a PR without the user approving the exact request shown, by its digest, in this
session. Never edit the request, the drafts or the code here: a change means going back to kaneki-flow
(`invalidate`), which reruns the review and re-issues receipts. Never force-push. Never stage, commit or push by
hand.
</HARD-GATE>

## Steps (per repository of the ticket)

1. `$SDLC status --ticket <id> --compact`: `deliverable-ready` must be done. If not, stop and say which node is next.
2. `$SDLC ship-request --ticket <id> --repo <repo>`. It refuses if receipts are missing, expired or no longer match
   the checkout, or if `drafts/commit-msg.md` fails Conventional Commits / no-AI-attribution rules. In that case
   `delivery-drafts` owns the fix. Show the user, verbatim: repository, branch → base, every file in `files`, the
   commit message, the identity (`name <email>`), whether it pushes and how the PR is opened (`pr`), `via`, and the
   `requestDigest`.
3. Ask the user: **Ship exactly this** (recommended) / **Change something** / **Stop**.
   - Change → say which node owns the change (commit message → `delivery-drafts`; code → `coding`) and stop;
     kaneki-flow reruns from there.
4. On approval: `$SDLC ship --ticket <id> --repo <repo> --approve <requestDigest> [--no-pr]`. It rebuilds the
   request and refuses if anything changed since the user saw it.
   - If `via` is `native-git` (default): `sdlc ship` commits, verifies, pushes, and opens the PR (when `prMode` is
     `gh` or `glab`) automatically, and records the result. If `next` lists manual PR instructions, tell the user
     to open the PR/MR from `prBodyFile` and run `sdlc ship-record --ticket <id> --repo <repo> --pr <url>`.
   - If `via` is `my-git`: run the `next` steps in order (`/my-git:commit`, `/my-git:push`, `/my-git:pr`), then run
     `$SDLC ship-record --ticket <id> --repo <repo> [--pr <url>]`.
5. Report: commit sha, branch, push result, PR/MR URL (or manual/compare instructions). If a push failed, the
   commit stays recorded: fix access, push, then run `$SDLC ship-record --ticket <id> --repo <repo> [--pr <url>]`.

## After the PR

When reviewers comment: `$SDLC review-fetch --ticket <id> --repo <repo>` (GitHub with `gh`), or export the review to
a file and use `handback` (browser mode, other hosts). kaneki-flow then runs `read-review → triage-review → …`. When
that round reaches `deliverable-ready` again, ship again with this skill. Pushing to the same branch updates the open
PR, so PR creation is skipped on a re-ship.
