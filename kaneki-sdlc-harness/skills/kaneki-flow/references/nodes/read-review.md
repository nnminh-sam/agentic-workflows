# read-review (after PR)

**Goal:** the complete, current review thread.

Using the delivery adapter's `review` section (or the handback file), list every comment:
`RC-n | author | file:line | text | commit it was made on | resolved?`. Mark comments made on an older head
as `outdated`; keep them, but they need a currency check. Output `review-thread.md`.
