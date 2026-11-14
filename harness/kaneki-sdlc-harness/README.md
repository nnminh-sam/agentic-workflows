# kaneki-sdlc-harness

A generic, gated SDLC ticket harness for Claude Code, ported from `wms-flow` 1.22.4.
One ticket (bug or feature) goes from intake through analysis, approved plan, coding, independent
review, test cases, real-surface evidence, final review with receipts, delivery drafts, and the after-PR
review-fix loop. Everything project-specific (tracker, stacks, platforms, rules, testcase format, evidence,
delivery) lives in a project profile and adapters.

Status: **draft v0.1**. The driver and onboarding run end to end (52 tests); adapters and rulepacks are starter sets.

## Layout

```
graph/lifecycle.json          the core graph (17 nodes, 4 layers, 2 extension slots; families add more)
harness/                      driver library (stdlib only, Python 3.9+; tested on Windows, Linux, 3.9–3.12)
skills/kaneki-flow/           the Claude Code skill: SKILL.md (controller), node cards, agents, packet template
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
tests/                        end-to-end driver tests
```

## Quick start

```bash
# 1. install the skills, agents and git guard into a project (or into ~/.claude for every project)
sh skills/kaneki-flow/scripts/sdlc install --target /path/to/project

# 2. in Claude Code, inside the project
/kaneki-onboarding            # detect, verify, ask (git account, languages), write sdlc.profile.json
/kaneki-flow ISSUE-123        # analysis → plan → code → review → tests → evidence → final review → drafts
/kaneki-ship ISSUE-123        # the only step that commits, pushes and opens the PR (after you approve)
```

## Workspace layout

Run the harness from a workspace folder that holds the repositories, not from inside a repository. Every ticket
gets one worktree per repository, and all of them live together under `paths.worktreeRoot` (default `worktrees`,
relative to the profile), named `<repoId>-<ticket>`:

```
my-project-workspace/            ← open Claude Code here
├─ .claude/                      kaneki skills + agents (sdlc install --target .)
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

Every git command runs as the project's account (`git.user`, optional `git.sshCommand`, per-repo override), and
**nodes never commit**: only `/kaneki-ship` commits, pushes and opens the PR/MR.

- During the flow, git runs through `sdlc git --ticket <id> --repo <repo> -- <args>` (identity and SSH key per call,
  logged). It refuses commit/merge/rebase/push/pull/tag, anything that deletes work, and identity changes.
- A guard hook on the Bash and PowerShell tools refuses the same as plain `git`, plus plain remote access. It fails
  closed: if it cannot check, git is refused.
- `sdlc ship-request` prints the exact request (files, message, branch, identity) and its digest; `sdlc ship
  --approve <digest>` refuses any drift since, stages exactly those files, commits as the project identity, pushes,
  and opens the PR (`gh`/`glab`). `sdlc review-fetch` pulls the review back (GitHub).
- Gates reject any commit since the ticket started that kaneki-ship did not make.

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

- **Entry point:** `sh skills/<skill>/scripts/sdlc …` picks a Python 3.9+ (`SDLC_PYTHON`, `python3`, `python`,
  `py -3`), so it runs where only `python3` exists (macOS, Ubuntu) and skips the Windows Store stub. On Windows
  it needs Git Bash, which Claude Code already uses for its Bash tool.
- **Commands verified by onboarding** run in bash on every OS (Git Bash on Windows, never the WSL launcher),
  because CI commands are written for bash. Override with `SDLC_BASH`.
- **Paths:** profiles and ticket state store POSIX paths relative to the profile, so a project (with its
  tickets) can move between machines and OSes; absolute paths in `state.json` are a per-machine cache.
- **Text:** all files and git output are read as UTF-8, whatever the system locale.
- **Line endings:** `.gitattributes` forces LF so the launchers keep working after a Windows checkout.

Tested: Windows 11 (Python 3.12), Ubuntu 24.04 via WSL (Python 3.12), Alpine Linux in Docker (Python 3.9, 3.10).
Not yet run on a real Mac.
