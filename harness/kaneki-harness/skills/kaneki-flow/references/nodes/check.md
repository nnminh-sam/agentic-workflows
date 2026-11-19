# check (lite lane)

**Goal:** one independent read of the real diff before anything ships. One routine reviewer unit (`quick-review`).

1. Read `scope.md` and `change.md` (the packet inputs), then the diff:
   `sdlc git --ticket <id> --repo <repo> -- diff` (in place: the working tree against HEAD) plus new files.
2. `## Diff vs scope` — does the diff do what `## Change` says, only that, in the files listed? Flag any hunk
   outside the scope. For CSS: selector specificity, other components sharing the class, breakpoints and
   dark mode the scope names, hard-coded values where the project uses variables/tokens.
3. `## Checks` — rerun the commands `change.md` lists; record exit codes. A check you cannot run is UNKNOWN,
   not PASS.
4. Code-phase rules in the rules file that apply to the touched paths: PASS / N/A / FAIL with `path:line`.
5. End the file with one line: `Verdict: APPROVED` or `Verdict: NEEDS CHANGES`.
   Critical/High finding or a failing check → NEEDS CHANGES. An empty diff → NEEDS CHANGES ("no change").

The controller closes with `--status needs-changes` (reopens `change`; G4 at the loop cap) or `approved`.
Closing `approved` records the subject digest and issues the receipts kaneki-ship checks.
