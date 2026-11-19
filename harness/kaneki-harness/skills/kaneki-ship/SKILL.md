---
name: kaneki-ship
description: "Commit, push and open the PR/MR for a ticket that kaneki-flow brought to deliverable-ready, through the my-git plugin: the only path that writes git history, for exactly the reviewed files, after the user approves the exact request."
when_to_use: "Invoke after kaneki-flow closes deliverable-ready (first delivery) or after a review round reaches deliverable-ready again (re-ship). Never during other nodes."
argument-hint: "<ticket-id> [--repo <repo-id>]... [--no-pr] [--profile <path>]"
user-invocable: true
---

# Kaneki Ship

The only way a kaneki ticket gets commits, pushes and a PR/MR. Nodes never commit (the kaneki-flow guard and
`sdlc git` refuse it). This skill does it once per repository, for one approved request. **Git itself is done by the
my-git plugin** (`/my-git:commit`, `/my-git:push`, `/my-git:pr`), which `sdlc install` registers from the same
checkout as the harness. kaneki decides *what* ships, and my-git decides *how*: identity, signing, SSH key, branch
rules, commit-message rules and PR mode (`gh`, or a browser compare link when `pr_via` is `browser`).

`SDLC="sh $CLAUDE_SKILL_DIR/scripts/sdlc"`. Speak to the user in the profile's `locale.chat` (English unless set).

<HARD-GATE>
Never commit, push or open a PR without the user approving the exact request shown, by its digest, in this
session. Never edit the request, the drafts or the code here: a change means going back to kaneki-flow
(`invalidate`), which reruns the review and re-issues receipts. Never force-push. Never stage, commit or push by
hand. `sdlc ship` stages, and the my-git skills commit and push. If the my-git guard blocks a step, report its rule ID
and stop. Never work around it.
</HARD-GATE>

## Steps (per repository of the ticket)

1. `$SDLC status --ticket <id> --compact`: `deliverable-ready` must be done. If not, stop and say which node is next.
2. `$SDLC ship-request --ticket <id> --repo <repo>`. It refuses if receipts are missing, expired or no longer match
   the checkout, or if `drafts/commit-msg.md` fails my-git's commit-message rules (Conventional Commits, no AI
   co-author). In that case `delivery-drafts` owns the fix. Show the user, verbatim: repository, branch → base,
   every file in `files`, the commit message, the identity (`name <email>`), whether it pushes and how the PR is
   opened (`pr`: `my-git`, `manual` or none), and the `requestDigest`.
3. Ask with AskUserQuestion: **Ship exactly this** (recommended) / **Change something** / **Stop**.
   - Change → say which node owns the change (commit message → `delivery-drafts`; code → `coding`) and stop;
     kaneki-flow reruns from there.
4. On approval: `$SDLC ship --ticket <id> --repo <repo> --approve <requestDigest> [--no-pr]`. It rebuilds the
   request and refuses if anything changed since the user saw it. It then stages exactly the listed files, writes the
   approved message and PR body under `ship/`, and lets the kaneki-flow guard pass `commit` and `push` in that
   checkout for 30 minutes. It commits nothing. Its `next` lists the my-git steps.
5. Run the `next` steps in order, each with the checkout path it names:
   - `/my-git:commit "<checkout>" ...`: commit **exactly what is staged**, with the message file `ship/<repo>-commit-msg.txt`
     unchanged. Stage nothing else.
   - `/my-git:push "<checkout>" --upstream` (when the request pushes).
   - `/my-git:pr "<checkout>" ...` (when `pr` is `my-git`): title = the commit header, body = `ship/<repo>-pr-body.md`.
     my-git confirms with the user and opens it (or hands over the compare link in browser mode). When `pr` is
     `manual`, tell the user to open the PR/MR from that body file.
6. `$SDLC ship-record --ticket <id> --repo <repo> [--pr <url>]`. It checks the new commit is the approved one: one
   commit on the approved head, the staged files, the approved message, the project identity. Then it records the
   commit as shipped (so the flow's commit gates accept it), whether the branch is on the remote, and the PR URL, and
   it withdraws the guard approval. If it refuses, the commit is not the approved request: undo it with
   `$SDLC git --ticket <id> --repo <repo> -- reset --soft <approved head>` and redo step 5.
7. Report: commit sha, branch, push result, PR/MR URL (or the compare link the user submits). If a push failed, the
   commit stays: fix access, `/my-git:push` again, then `ship-record` again.

## After the PR

When reviewers comment: `$SDLC review-fetch --ticket <id> --repo <repo>` (GitHub with `gh`), or export the review to
a file and use `handback` (browser mode, other hosts). kaneki-flow then runs `read-review → triage-review → …`. When
that round reaches `deliverable-ready` again, ship again with this skill. Pushing to the same branch updates the open
PR, so `/my-git:pr` is skipped on a re-ship.
