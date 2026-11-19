---
name: graph
description: Open a browser page with a git map of the whole workspace - every repo (the profile's repositories, also ones outside the workspace folder), every checkout (base clone and worktrees) with the branch it has checked out, how far each local branch is ahead/behind its remote branch and its base branch (staging, production, or a nested parent feature branch) with the commits behind each count, and a drawn commit graph. Read-only. Use when the user runs /my-git:graph or asks which branch each worktree is on, what is unpushed/unpulled, what is merged, or for a git graph of the workspace.
argument-hint: "[workspace-path] [--all] [--fetch] [--text] [--json] [--max-commits N]"
allowed-tools: Bash(python3 *workspace_graph.py*), Bash(python *workspace_graph.py*)
---

Open the workspace git map in the browser.

Arguments: $ARGUMENTS

`<root>` below is `${CLAUDE_PLUGIN_ROOT}` (the my-git plugin folder). On Windows use `python` if `python3` is not on PATH.

## What it finds

- **Workspace**: the path argument (default: the current directory). If a parent folder holds `sdlc.profile.json`, that folder is the workspace, so it works from inside any worktree.
- **Repos**: `repositories[].path` from the profile (relative to the workspace or anywhere on disk), every git repo directly under the workspace, and the owner repo of every checkout under `paths.worktreeRoot` (default `worktrees`). With no profile it just scans the folder.
- **Checkouts**: `git worktree list` of each repo: base clone, worktree under the worktree root, or worktree elsewhere (external).
- **Base of a branch**: production is compared with remote staging (what is unreleased), staging with remote production, and a work branch with remote staging. With `nested_work_branches` on, a branch is compared with its parent instead when that parent is an unmerged remote work branch closer than staging. Names come from the my-git branching contract.

## Steps

1. Pick the mode from the arguments:
   - Default (no `--text` / `--json`): the HTML page. Run, passing the other arguments through unchanged:

     `python3 "<root>/scripts/workspace_graph.py" $ARGUMENTS --open`

     It writes a self-contained page (default `<temp>/my-git-graph/<workspace>-<hash>.html`, the same file on every run) and opens it in the default browser. The page has a sidebar with each base repo and its worktrees below it (clicking one selects its branch and highlights its commits in the graph), stat tiles, the drawn commit graph, a branch table with filters (active, needs action, merged, local only, all) and a detail panel listing the commits behind every ahead/behind count. It works offline.
   - `--text`: the terminal view. Remove `--text` and run `python3 "<root>/scripts/workspace_graph.py" $ARGUMENTS --color never`. Show the output verbatim in one fenced code block.
   - `--json`: the same data for scripts. Use it when you need to reason about the data rather than show it.

   Other flags: `--fetch` runs `git fetch --prune` per repo first (network, through each repo's own `core.sshCommand`). Without it the remote counts are as of the last fetch, which the page shows. Only add `--fetch` when the user asked for fresh data. `--all` draws every local branch, not only trunks and checked-out ones. `--max-commits N` caps the commits drawn per repo (default 400). `--html FILE` writes the page to FILE without opening it.
2. Reply with the file path the script printed (so the user can reopen it) and, in at most five short bullets, what needs action. To find it, run the same command with `--json` in place of `--open` and read each branch's `notes` and each checkout's `dirty` / `untracked` / `missing`:
   - `push`: local commits not on the remote. `pull (ff)`: the remote moved and the branch can fast-forward. `diverged`: both moved.
   - `N unreleased on <staging>`: a release PR (`/my-git:pr --release`) would carry them.
   - `merged`: the branch's commits are all in its base, so it may be finished. Never delete branches: tell the user and let them decide.
   - `base moved`: the base has commits the branch lacks. Mention it only for checked-out branches.
   - Dirty or untracked checkouts, detached HEADs, `missing (prunable)` worktrees, `upstream gone`.
   If nothing needs action, say so in one line. If the script says it could not open a browser, give the path and stop.

This skill is read-only. Don't run any fix it suggests (pull, push, merge, prune) unless the user asks. To refresh the page, run the skill again (the page's footer also shows the exact command).
