# kaneki harness

A generic, gated SDLC ticket harness for coding agents (Claude Code, Codex, Antigravity; also Gemini CLI,
Cursor, OpenCode), ported from `wms-flow` 1.22.4. It runs from a terminal or from Zed's Agent Panel.
One ticket (bug or feature) goes from intake through analysis, approved plan, coding, independent
review, test cases, real-surface evidence, final review with receipts, delivery drafts, and the after-PR
review-fix loop. Everything project-specific (tracker, stacks, platforms, rules, testcase format, evidence,
delivery) lives in a project profile and adapters.

Status: **draft v0.1**. The driver and onboarding run end to end (see Tests); adapters and rulepacks are starter sets.

## Layout

```
graph/lifecycle.json          the core graph (17 nodes, 4 layers, 2 extension slots; families add more)
harness/                      driver library (stdlib only, Python 3.9+; tested on Windows, Linux, 3.9–3.12)
skills/kaneki-flow/           the controller skill: SKILL.md, node cards, agents, packet template (rendered per host on install)
hosts/<host>/                 per-agent tool binding (preamble.md) and model tiers (tiers.json)
skills/kaneki-onboarding/     onboarding skill: detect → verify → ask → write the project profile
skills/kaneki-ship/           ship skill: the only path that commits, pushes and opens the PR/MR
contracts/                    JSON Schemas: profile, adapter, rulepack, extension, state, onboarding candidates, profile evidence
adapters/{tracker,stack,surface,delivery}/   bundled adapters
rulepacks/                    core rules + per-stack packs
templates/                    commit / PR / tracker reply / testcase templates
examples/generic-web/         TypeScript API + web, GitHub
examples/wms/                 the WMS/Kai setup re-expressed: Backlog, Laravel + Flutter, IF as a family
docs/design.md                architecture and extension points
docs/porting-from-wms-flow.md node-by-node map of what was kept, merged and dropped
docs/zed-workspace/plan.md    plan: Zed + ACP (Claude Code, Codex, Antigravity) and the web board (diagram: docs/zed-acp-any-agent.html)
tests/                        end-to-end driver tests
```

## Quick start

```bash
# 1. install for each agent you use (--host is required; run it once per agent, same workspace)
python skills/kaneki-flow/scripts/sdlc.py install --host claude --target /path/to/workspace --with-my-git
python skills/kaneki-flow/scripts/sdlc.py install --host codex  --target /path/to/workspace
#    (macOS/Linux: `sh skills/kaneki-flow/scripts/sdlc install ...` works too)

# 2. in the agent, inside the workspace (Claude Code shown; other agents: "use the kaneki-flow skill for ISSUE-123")
/kaneki-onboarding            # detect, verify, ask (git account, languages), write sdlc.profile.json
/kaneki-flow ISSUE-123        # analysis → plan → code → review → tests → evidence → final review → drafts
/kaneki-ship ISSUE-123        # the only step that commits, pushes and opens the PR (after you approve)
```

What each `--host` installs: `claude` → `.claude/` (skills, agents, guard hook; `--target ~/.claude` for every
project; `--with-my-git` registers the my-git plugin); `codex` and `antigravity` → one shared `.agents/skills/`
(one skill file with a section per agent) plus `AGENTS.md` for Codex. Every host also gets the driver launchers in
`.sdlc/bin/`: `sdlc` / `sdlc.cmd` and its own `sdlc-<host>` / `sdlc-<host>.cmd`, which tell the driver which agent
is working. The first install writes `.sdlc/host.local.json` (the default agent); later installs keep it.

## Lanes: small changes stay small

| Lane | Trace | Subagents | Worktree |
| --- | --- | --- | --- |
| `lite` | scope (you approve) → change → check → delivery-drafts → deliverable-ready | 1 (routine reviewer) | no: in place, on a ticket branch |
| `full` (default) | the whole graph (11 nodes for a bug) | ~12: coding on light (Haiku), reviews on routine, semantic review on deep | yes |

```bash
/kaneki-flow CSS-12 --lite        # or: sdlc init --ticket CSS-12 --kind feature --lane lite
sdlc lane --ticket CSS-12 --set full --reason "touches the API"   # escalate when it turns out bigger
```

`lite` is for CSS, copy, a config value, a contained fix with a known cause: about 5 files or fewer, no
data/schema/API/auth/shared-flow change. The controller writes the scope and makes the change itself; an
independent reviewer still checks the diff before anything ships, and `/kaneki-ship` works the same way.
It also works when the profile sits inside the repository (one repo open in the editor), which `full` refuses.

## Tracking: STATUS.md, the web board, Zed

- **`STATUS.md`** in every ticket folder is rewritten by the driver after each change: progress, next step,
  who does it (you / inline / subagent), verdicts, models, tokens, links to every artifact, recent decisions.
- **`sdlc web --open`** serves a live board of every ticket on `http://127.0.0.1:8765/`: progress strips, the
  node table, artifact preview, the decision log, and **Open in Zed** (uses the `zed` CLI, or `SDLC_EDITOR`).
  Read-only: approvals still happen in the agent thread, so the log records who approved.
- **`sdlc tickets`** / **`sdlc status-of --path <file>`** give the same view as JSON.
- **`sdlc zed-setup --target .`** adds Zed tasks (`task: spawn` → `kaneki: board (web)`, `kaneki: tickets`,
  `kaneki: ticket status (from open file)`, `kaneki: validate profile`). Your own tasks are kept.

## Using kaneki in Zed

Work tickets from Zed's Agent Panel with Claude, Codex or Antigravity, and switch agents between nodes.
Background and decisions: [docs/zed-workspace/plan.md](docs/zed-workspace/plan.md); diagram:
[docs/zed-acp-any-agent.html](docs/zed-acp-any-agent.html).

1. **Install once per agent**, into the workspace folder you open in Zed:
   `sdlc install --host claude|codex|antigravity --target <workspace>` (run it again with another `--host` to add
   an agent; agents installed earlier keep their setup). Claude reads `.claude/`, Codex and Antigravity share `.agents/skills/`.
2. **Add the agents in Zed**: command palette → `zed: acp registry`, then add **Claude Agent**, **Codex** and
   **Google Antigravity**. Sign in to each one the first time you open a thread with it.
3. **Start a ticket**: open the Agent Panel (new thread → pick the agent) and ask
   `/kaneki-flow ISSUE-123` (Claude) or "use the kaneki-flow skill for ISSUE-123" (Codex, Antigravity).
   Run `/kaneki-onboarding` (or "use the kaneki-onboarding skill") once per workspace first.
4. **Switch agents between nodes** (for example when one runs out of usage): let the current node finish or
   stop it, open a new thread with the other agent and ask for the same ticket. State is on disk, so it resumes at
   `nextPending`. The driver logs a `host-switch` event, and `STATUS.md`, `sdlc tickets` and the board show which
   agent ran each node and unit. Never switch in the middle of a node.
5. **Windows**: every agent runs commands in PowerShell, where `sh` is not available. Each agent calls its own
   launcher, `.sdlc\bin\sdlc-<agent>.cmd` (`sh .sdlc/bin/sdlc-<agent>` on macOS/Linux), which also tells the
   driver which agent is working; packets name the right one. Python 3.9+ must be on PATH (or set `SDLC_PYTHON`).
6. **Antigravity is interactive only**: use it through Zed's official Google Antigravity agent, or run `agy`
   yourself. Its units run on its own subagents. Never start it from `sdlc dispatch` (the driver refuses), a script,
   `agy -p` or a community bridge: Antigravity's terms do not allow third-party software to drive it.
   Codex units run through `sdlc dispatch` (`codex exec -m <model> -c model_reasoning_effort=<effort> <prompt>`).

## Workspace layout

Run the harness from a workspace folder that holds the repositories, not from inside a repository. Every ticket
gets one worktree per repository, and all of them live together under `paths.worktreeRoot` (default `worktrees`,
relative to the profile), named `<repoId>-<ticket>`:

```
my-project-workspace/            ← open Zed (or your agent's CLI) here
├─ .claude/  .agents/  AGENTS.md  kaneki skills per agent (sdlc install --host <agent> --target .)
├─ .sdlc/bin/                    driver launchers: sdlc, sdlc.cmd, sdlc-<agent>, sdlc-<agent>.cmd
├─ sdlc.profile.json             repositories: [{id: "shop", path: "shop"}]
├─ .sdlc/adapters/  .sdlc/tickets/<ticket>/
├─ shop/                         main checkout (control; not edited during a ticket)
└─ worktrees/
   ├─ shop-ISSUE-12/             branch ticket/ISSUE-12
   └─ shop-ISSUE-15/
```

The driver resolves the location: the `prepare` packet names the exact worktree path, the stack adapter rule is
`{git} worktree add {worktreeRoot}/{repoId}-{ticket} -b {branchPrefix}{ticket} {base}`, and `sdlc worktree`
refuses to bind any other folder. `validate` and `init` refuse a `worktreeRoot` inside a repository. Onboarding
writes `worktrees` for a workspace folder; when the onboarding root is itself the repository it writes
`../<repo>-worktrees` instead (and suggests the workspace layout).

## Onboarding

`/kaneki-onboarding` replaces hand-filling `sdlc.profile.json`:

| Step | Command | What it does |
| --- | --- | --- |
| scan | `sdlc onboard-scan --root . --out cand.json` | repos, stacks, surfaces, CI/script commands, tracker, delivery, locale; each value with evidence and confidence; questions for what cannot be detected |
| verify | `sdlc onboard-verify --candidates cand.json [--allow-tests]` | runs the detected doctor/lint/format commands; never installs or mutates; tests only when allowed |
| write | `sdlc onboard-write --candidates cand.json --answers a.json [--refresh] [--dry-run]` | writes `sdlc.profile.json`, `sdlc.profile.evidence.json` (provenance) and `.sdlc/adapters/stack/<repo>.json` (detected commands, `extends` the bundled adapter), then validates |

A refresh never overwrites a value you set: answered values, hand edits (the profile differs from the value
the sidecar recorded) and edited project adapters are kept and reported as `kept-yours`. `.env` is never read.

## Tests

```bash
python3 -m unittest discover -s tests -v      # macOS / Linux  (Windows: python or py -3)
```

## Languages

Skills, node cards and packets are English, and **so is every output by default**: commit messages, PR text,
ticket artifacts and replies. A language changes only when set explicitly, key by key:

1. a per-ticket override you ask for in the prompt (recorded with `sdlc lang --ticket <id> --set delivery=ja`),
2. the profile's `locale`, e.g. `"locale": {"tracker": "ja"}` (Japanese replies to the customer, everything else English),
3. English.

Keys: `chat` (the controller talking to you), `artifacts` (ticket files), `delivery` (commit messages, PR text),
`review` (PR replies), `tracker` (ticket replies). They are independent, with no fallback between them, and
nothing is inferred from the repository or from the language you write in. Onboarding asks one question,
*keep everything in English?*, and writes no `locale` at all when you keep it.

## Git identity and the commit rule

Git goes through the **my-git** plugin (`<checkout>/plugins/my-git`). `sdlc install --host claude --with-my-git`
registers it in the target project from the same checkout as the harness, so both are always the same version.
Re-install after updating the checkout. That install refuses if my-git is missing; `SDLC_MYGIT` points it at another copy. my-git's guard checks
identity, the pinned SSH key, remotes, branch names and commit messages on every git/gh command. **Nodes never
commit**: only `/kaneki-ship` does, through my-git.

- The kaneki-flow guard hook adds the node rules only. It refuses commit/merge/rebase/push/pull/tag and anything that
  deletes work. It needs no harness path (self-contained) and fails closed if it crashes.
- `sdlc git --ticket <id> --repo <repo> -- <args>` runs git in the ticket worktree as the profile identity, logged.
  It refuses the same commands, plus identity changes.
- `sdlc ship-request` validates the commit message with my-git and prints the exact request (files, message,
  branch, identity) and its digest. `sdlc ship --approve <digest>` refuses any drift since, stages exactly those
  files, and lets the guard pass commit/push in that checkout for 30 minutes. Then `/my-git:commit`, `/my-git:push`
  and `/my-git:pr` do the git work. `sdlc ship-record` checks the commit is the approved one (parent, tree, message,
  identity) and records it. `sdlc review-fetch` pulls the review back (GitHub with `gh`).
- Gates reject any commit since the ticket started that kaneki-ship did not record.

## Integrity

- `state.json` is checked against its schema and against the digest of the last driver write on every load;
  `decisions.jsonl` is hash-chained. An outside edit stops the ticket until `status --accept-current-state --reason`.
- One writer per ticket (a lock; stale locks older than 10 minutes are broken and logged).
- A reopened node's artifacts get a `## SUPERSEDED:` line (as in wms-flow); only the text below it counts.
- Gates check declared sections and parsers (`## Condition → negative case`, rule verdicts for every rule id,
  `Verdict:`, `Confidence:`, `Reopen:`), citations at receipt nodes, and the language of drafts.
- Receipts hash HEAD, the tracked diff and the content of every new file (harness folders excluded).
- Every contract file (graph, profile, adapters, rulepacks, extensions, facts, state, log events) is validated
  against its JSON Schema on load (bundled validator; `jsonschema` is used when installed).

## Platforms

Works the same on macOS, Linux and Windows:

- **Entry point:** in a project, `.sdlc/bin/sdlc-<agent>` (`sh`, macOS/Linux) or `.sdlc\bin\sdlc-<agent>.cmd`
  (PowerShell or cmd, Windows; no Git Bash needed). Both pick a Python 3.9+ (`SDLC_PYTHON`, then `python3`/`python`,
  then `py -3`) and skip the Windows Store stub. Packets name the launcher for the OS they are rendered on. Without
  a project launcher (user-scope `~/.claude` install) use `sh skills/kaneki-flow/scripts/sdlc …`, or
  `python …/scripts/sdlc.py …` on Windows.
- **Commands verified by onboarding** run in bash on every OS (Git Bash on Windows, never the WSL launcher),
  because CI commands are written for bash. Override with `SDLC_BASH`.
- **Paths:** profiles and ticket state store POSIX paths relative to the profile, so a project (with its
  tickets) can move between machines and OSes; absolute paths in `state.json` are a per-machine cache.
- **Text:** all files and git output are read as UTF-8, whatever the system locale.
- **Line endings:** `.gitattributes` forces LF so the launchers keep working after a Windows checkout.

Tested: Windows 11 (Python 3.12), Ubuntu 24.04 via WSL (Python 3.12), Alpine Linux in Docker (Python 3.9, 3.10).
Not yet run on a real Mac.
