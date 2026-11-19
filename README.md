# agentic-workflows

Claude Code tooling: an SDLC ticket harness, a learning loop, a git-guard plugin, a notification hook and a few
skills. Each folder is self-contained, has its own README, and is installed on each machine with its own
installer (nothing machine-specific is committed).

| Folder | What it is | Install | Tests |
| --- | --- | --- | --- |
| [`harness/kaneki-harness`](harness/kaneki-harness/README.md) | Gated SDLC ticket harness (`/kaneki-flow`, `/kaneki-onboarding`, `/kaneki-ship`): one bug or feature from intake through plan, coding, review, evidence and delivery, driven by a project profile; git goes through `plugins/my-git`, which the install also registers | `sh harness/kaneki-harness/skills/kaneki-flow/scripts/sdlc install --target <workspace>` | `python -m unittest discover -s tests -v` |
| [`harness/learning-kit`](harness/learning-kit/README.md) | Retrieval-first learning loop (`/learn`, `/path`, `/quiz`, `/note`, `/recall`) with a local quiz app and spaced recall | `python lk.py install --home ~/learning` | `python -m unittest discover -s tests` |
| [`plugins/my-git`](plugins/my-git/README.md) | Plugin: git-guard hooks that check identity, Conventional Commits and `gh` usage, and the `main` / `develop/base` branching model, plus `/my-git:commit`, `/my-git:push`, `/my-git:pr` and `/my-git:graph` (workspace map of repos, worktrees and ahead/behind) | `python plugins/my-git/install.py <project>` or `--scope user` | `python -m unittest` (in `tests/`) |
| [`hooks/notify-hook`](hooks/notify-hook/README.md) | `Notification` hook: native desktop toast when Claude waits on you | `python install.py` | `python -m unittest discover -s tests` |
| [`skills/haise-doc`](skills/haise-doc/SKILL.md) | Skill: self-contained offline HTML documents | copy to `~/.claude/skills/` | |
| [`skills/haise-flow`](skills/haise-flow/SKILL.md) | Skill: flow / architecture diagram pages from a JSON spec | copy to `~/.claude/skills/` | |
| [`skills/zed-spawn-session`](skills/zed-spawn-session/SKILL.md) | Skill: spawn a Claude Code session in a new Zed terminal tab | copy to `~/.claude/skills/` | |
| [`harness/kaneki-harness/app`](harness/kaneki-harness/app/doc/build-plan/00-build-plan.md) | Kaneki desktop app: build plan and design system (not built yet) | | `python design-system/build_tokens.py --check` |
| `archives/` | Retired work kept for reference | | |

Run each test command from inside its folder.

## Kaneki workspace layout

Run the harness from a workspace folder that holds this repository, not from inside it:

```
<workspace>/
├─ .claude/              kaneki skills + agents (sdlc install --target .)
├─ sdlc.profile.json     repositories: [{id: "agentic-workflows", path: "agentic-workflows"}]
├─ .sdlc/                adapters + ticket folders
├─ tickets/              local tracker files
├─ worktrees/            one worktree per work branch
└─ agentic-workflows/    this repository, kept on main
```

## Branches

Work goes on `develop/<type>/<name>` branches (feat, fix, chore, docs, refactor, perf, test, build, ci) started from
`develop/base`, and reaches `develop/base` through a PR (`/my-git:pr`); `develop/base` reaches `main` through a release
PR (`/my-git:pr --release`). Neither `main` nor `develop/base` takes direct commits or pushes.

Test locally before pushing (`/my-git:pr` runs the tests and lint); CI runs the learning-kit tests only.
