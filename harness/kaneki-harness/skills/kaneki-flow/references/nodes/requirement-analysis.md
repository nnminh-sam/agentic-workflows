# requirement-analysis (feature)

**Goal:** one consistent statement of what to build, with every conflict resolved by the user.

1. Inventory every requirement source (ticket, specs, designs, existing behaviour) with its authority:
   primary / supporting / historical.
2. Extract requirements as `R-n | statement | source:location | acceptance criterion`.
3. List conflicts and gaps: `C-n | sources in conflict | options | recommendation`. Each one the sources cannot settle goes into the ticket's one batch with `sdlc ask --ticket <id> --node requirement-analysis --class business --severity HIGH|LOW --question '...' --recommend '...'` (`questions.md` is generated; do not edit it). Ask everything the plan needs now: questions after the plan is approved are flagged as late.
4. State the non-goals explicitly.

Output `requirements.md`. **G1** if a primary source is unavailable or a conflict is unresolved.
A deep reviewer checks every R-n against its cited source.
