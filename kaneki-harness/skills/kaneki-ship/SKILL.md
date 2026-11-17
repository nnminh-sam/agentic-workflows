---
name: kaneki-ship
description: "Commit, push and open the PR/MR for a ticket that kaneki-flow brought to deliverable-ready: the only path that writes git history, as the project's configured account, for exactly the reviewed files, after the user approves the exact request."
when_to_use: "Invoke after kaneki-flow closes deliverable-ready (first delivery) or after a review round reaches deliverable-ready again (re-ship). Never during other nodes."
argument-hint: "<ticket-id> [--repo <repo-id>]... [--no-pr] [--profile <path>]"
user-invocable: true
---

# Kaneki Ship

The only way a kaneki ticket gets commits, pushes and a PR/MR. Nodes never commit (the kaneki-flow guard and
`sdlc git` refuse it); this skill does, once per repository, for one approved request.

`SDLC="sh $CLAUDE_SKILL_DIR/scripts/sdlc"`. Speak to the user in the profile's `locale.chat` (English unless set).

<HARD-GATE>
Never commit, push or open a PR without the user approving the exact request shown, by its digest, in this
session. Never edit the request, the drafts or the code here: a change means going back to kaneki-flow
(`invalidate`), which reruns the review and re-issues receipts. Never force-push. Never run plain `git` for any
of this; the driver does it with the project identity (`git.user`, `git.sshCommand`).
</HARD-GATE>

## Steps (per repository of the ticket)

1. `$SDLC status --ticket <id> --compact`: `deliverable-ready` must be done. If not, stop and say which node is next.
2. `$SDLC ship-request --ticket <id> --repo <repo>`. It refuses if receipts are missing, expired or no longer match
   the checkout. Show the user, verbatim: repository, branch → base, every file in `files`, the commit message,
   the identity (`name <email>`), whether it pushes and opens a PR/MR, and the `requestDigest`.
3. Ask with AskUserQuestion: **Ship exactly this** (recommended) / **Change something** / **Stop**.
   - Change → say which node owns the change (commit message → `delivery-drafts`; code → `coding`) and stop;
     kaneki-flow reruns from there.
4. On approval: `$SDLC ship --ticket <id> --repo <repo> --approve <requestDigest> [--no-pr]`. It rebuilds the
   request and refuses if anything changed since the user saw it, stages exactly the listed files, commits as the
   project identity, pushes the branch, and opens the PR/MR (delivery adapter: `gh` / `glab`). Before the first
   PR, check the CLI is logged in as the project's account (`gh auth status` / `glab auth status`) and ask if not.
5. Report: commit sha, branch, push result, PR/MR URL. A failed push keeps the commit; fix access and run steps
   2–4 again (same branch).

## After the PR

When reviewers comment: `$SDLC review-fetch --ticket <id> --repo <repo>` (GitHub; other hosts: export the review
to a file and use `handback`). kaneki-flow then runs `read-review → triage-review → …`; when that round reaches
`deliverable-ready` again, ship again with this skill. Pushing to the same branch updates the open PR.
