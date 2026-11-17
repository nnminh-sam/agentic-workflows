# change (lite lane)

**Goal:** the edit the approved scope describes, in place, with the fast checks run. The controller does this
node itself (no subagent).

1. Branch: if the repository is on its base branch, create the ticket branch first:
   `sdlc git --ticket <id> --repo <repo> -- switch -c <branchPrefix><ticket>`. Never commit (kaneki-ship does).
2. Edit only the files `scope.md` lists. Need another file? Add it to `scope.md` `## Files` with one line why;
   more than two unplanned files → stop and escalate to the full lane.
3. Run the stack adapter's fast commands that exist for the repository: format (check mode), lint, type-check
   or build. Do not run the full test suite unless `## Done when` asks for it.
4. Write `change.md`:
   - `## Changes` — per file: `path:line` and what changed.
   - `## Checks` — each command with its exit code and one line of result.
   - `## Rule self-check` (optional) — code-phase rules that touch these files.

**G6** a check needs a running service or shared state; **G7** a command is missing or fails for reasons outside
the change. A failing check caused by the change is fixed here, not handed to `check`.
