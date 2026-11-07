# respond-review (after PR)

**Goal:** one reply per comment, in the reviewer's language (profile `locale.review`).

- ACCEPT → what changed, with the commit or evidence pointer.
- REJECT → the evidence.
- CLARIFY → the question.

Output `review-reply.md`. Boundary **user**. The delivery adapter's `reship` section posts the replies and
re-pushes only on the user's explicit go.
