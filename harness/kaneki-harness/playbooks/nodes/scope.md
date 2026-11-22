# scope (lite lane)

**Goal:** one short, approved statement of a small change. The controller does this node itself (no subagent).

1. Read the request: the ticket through the tracker adapter when there is one, otherwise the user's own words.
   Save it verbatim at the top of `scope.md` under `## Request`.
2. Locate the code via line-numbered search (`INSPECT_CODE`), then read only what the change touches. No investigation beyond that.
3. Write `scope.md`:
   - `## Change` — what changes, in one to five lines (selector, property, old value → new value for CSS).
   - `## Files` — every file you expect to edit, `path:line` where known. More than ~5 files is not lite.
   - `## Done when` — how anyone can tell it worked: the check commands to run, and what to look at
     (page, component, breakpoint, light/dark).
   - `## Why lite` — answer each with yes/no: touches only presentation/copy or a contained piece of logic;
     no data, schema, API, auth or shared-flow change; cause or intent already clear; fits in one review.
     Any **no** → escalate (below), do not continue.
4. Ask the user **Proceed / Edit / Escalate to full** via `ASK_USER`, then close with `--actor user`.

**Escalate:** `sdlc lane --ticket <id> --set full --reason '<which answer was no>'`. The full trace starts at
`prepare`; keep `scope.md` as input for it.

**G1** the intent is unclear; **G3** there are materially different ways to do it; **G7** a repository path does not resolve.
