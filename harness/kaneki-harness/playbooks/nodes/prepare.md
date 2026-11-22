# prepare

**Goal:** a ticket the rest of the graph can trust: the ticket text, the repositories it touches, a
worktree per repository, and an environment that passes the doctor.

1. Fetch the ticket through the **tracker adapter** (`fetch` section). Save it verbatim to `ticket.md`
   (title, body, comments, attachment list, source URL, fetched-at). Redact secrets/PII.
2. For each repository in the context: confirm the path exists and is a git checkout, note base branch and
   HEAD. Create (or reuse) a ticket worktree/branch per the stack adapter's `worktree` rule, where `{git}` is the
   git command from the packet's Git section (project identity) and `{worktreeRoot}`, `{repoId}`, `{ticket}`,
   `{branchPrefix}`, `{base}` take the values the packet's Repositories list gives for that repository. The
   worktree goes exactly at the path the packet names (`<worktreeRoot>/<repoId>-<ticket>`), never next to the
   repository; the controller binds it with `sdlc worktree --repo <id> --path <dir>`, which refuses any other
   folder. Record the project git identity the packet names in `environment.md`.
3. Run each stack adapter's `doctor` commands and the profile `environment.doctor` commands; record exit codes.
4. For every surface the ticket needs, record whether its runner is available (surface adapter
   `prerequisites`). A missing runner is not a failure here; `execute-and-evidence` decides.
5. Write `environment.md`: `## Repositories` (id, path, worktree, base, HEAD, git identity), `## Doctor` (command → result),
   `## Surfaces` (surface → runner → available?), `## Shared state` (DBs/queues this ticket may mutate).

**G7** if the ticket cannot be fetched, a repository path does not resolve, or a doctor command fails.
