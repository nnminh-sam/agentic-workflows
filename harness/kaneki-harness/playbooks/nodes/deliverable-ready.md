# deliverable-ready (no dispatch)

Controller only:

1. `sdlc receipts --ticket <id>`: every receipt is present, has at least `delivery.minHoursLeft` hours left, and
   matches the checkout. Expired → `--reissue`. Changed → invalidate the node that owns the change.
2. Print each draft verbatim and ask the user: approve all / edit (name it) / stop.
   Edit → `sdlc invalidate --node delivery-drafts --reason "<user wording>"`.
3. On approval, close with `--actor user`, then hand over to `/kaneki-ship`: it is the only path that commits,
   pushes and opens the PR/MR, as the project git identity, after the user approves the exact request.
