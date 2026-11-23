# Kaneki in Zed: plan

Status: **agreed design, not built** · 2026-11-23 · owner: nnminh-sam
Diagram: [`../zed-acp-any-agent.html`](../zed-acp-any-agent.html) (spec `../zed-acp-any-agent.flow.json`)
Board mock: [`../../app/design-system/board-zed-mock.html`](../../app/design-system/board-zed-mock.html)
Supersedes: the desktop app plan in [`../desktop-app/plan.md`](../desktop-app/plan.md) (shelved, see D1)

## 1. Goal

Work kaneki tickets from **Zed** with **Claude Code, Codex or Antigravity**, switch agents when one runs out of
usage, and see every ticket's state in a **local web board** without asking an agent.

Scope is limited to three agents:

| Agent | Interface | Unit dispatch | Unattended runs |
|---|---|---|---|
| **Claude Code** (default) | Zed Agent Panel over ACP (`claude-acp`), or the `claude` CLI in a Zed terminal | native subagents (`kaneki-light / worker / deep`) | allowed (`claude --bg`, `claude -p`) |
| **Codex** | Zed Agent Panel over ACP (`codex-acp`), or the `codex` CLI | `sdlc dispatch` → `codex exec` per unit | allowed (`codex exec`) |
| **Antigravity** | Zed Agent Panel with Google's official `antigravity-acp` only | native (`invoke_subagent`) | **never** from kaneki (policy, D6) |

Out of scope: Gemini CLI, pi, OpenCode, Cursor. Their presets may stay in `hosts/`, but they are not planned,
documented or tested here.

## 2. How it fits together

```
You ── Zed Agent Panel ──ACP──► Claude Code │ Codex │ Antigravity ─┐
   └─ Zed terminal (claude / codex CLI) ──────► Claude Code │ Codex ┤
   └─ Browser: kaneki board (127.0.0.1) ◄── reads ──┐              │ same kaneki-flow skill
          │ Open in Zed (file:line)                 │              ▼
          │ Continue in Zed (new thread + prompt)   │   host binding (SDLC_HOST → tools, tiers)
          ▼                                         │              ▼
        Zed                                         │   sdlc CLI (only writer) ── sdlc dispatch (codex units)
                                                    │              ▼
                         session logs (transcripts, cost) · ticket files (state.json, decisions.jsonl) · worktrees
```

- **ACP** (Agent Client Protocol) is the standard plug between an editor and a coding agent, as LSP is for
  language servers. Zed starts the agent program and talks JSON-RPC over stdin/stdout: prompts go in; messages,
  tool calls, diffs and permission requests come back. Any agent with an ACP server shows up in the same panel.
- **The harness is the contract**, not any agent: one playbook (`playbooks/flow.md`, five intents) plus a small
  per-agent binding (`hosts/<h>/preamble.md`, `tiers.json`), all driving `sdlc`. This part landed in PR #22.
- **State lives on disk**, so a ticket started with Claude continues with Codex or Antigravity at `nextPending`;
  every record keeps the host and model that produced it.
- **The board reads files only**: ticket state and the agents' own session logs. It never talks to an agent.

## 3. Decisions (from the 2026-11-23 discussion)

| Id | Decision | Why |
|---|---|---|
| D1 | **Shelve the desktop app.** Zed is the working app; a browser board is the overview. | A new app would rebuild Zed (terminal, editor, previews, palette). The three new parts (gate decision, supervision, cost) need no window of their own. |
| D2 | **No Zed fork, no Zed extension UI.** | Zed extensions can add languages, debuggers, themes, icon themes, snippets, MCP servers and agent servers only; there is no panel, status-bar or webview API. A fork would be a permanent rebase burden. |
| D3 | **Status view = the web board** (`sdlc web`), not a terminal dashboard or Markdown preview. | The browser is always open; the board already exists (127.0.0.1, per-run token). |
| D4 | **Agents run through ACP in the Agent Panel by default; CLI in a terminal tab is the alternative.** | One UI for every agent; switching is "new thread, other agent". |
| D5 | **The harness CLI stays the only writer of ticket state.** The board, Zed tasks and a future runner all go through `sdlc`. | One audit trail (hash-chained `decisions.jsonl`). |
| D6 | **Antigravity is interactive-only, via Google's own ACP server.** Never `sdlc dispatch`, the runner, `agy -p` or a community bridge. | Antigravity Terms §6 and FAQ: third-party software wrapping `agy` is not supported on consumer plans; suspensions are reported. Google's docs list `agy acp` in Zed as the supported editor path. |
| D7 | **Gates are never crossed by automation.** User-boundary gates close only with `--actor user`; `autoSkipPlanApproval` stays the only opt-in exception. | Keeps the HARD-GATE meaning of the harness. |
| D8 | **No link to an existing Zed thread; transcripts are shown in the board.** "Continue in Zed" starts a new thread with the right prompt. | Zed has no thread deep link or `zed --agent` (PR #62060 closed unmerged, 2026-08-08; `zed:///agent/thread/<id>` reported broken). Units are subagents or subprocesses anyway, not Zed threads. |
| D9 | **Gate approval also through the kernel** (`sdlc decide`, from a Zed task). | Works whichever agent ran the node, and when no agent session is open. |
| D10 | **Learning output (reflect node) needs user approval before it changes anything.** | The harness never rewrites its own graph, gates or driver. |

## 4. Facts checked on this machine (2026-11-23)

- Zed **1.28.1** installed; latest stable **1.29.3** (2026-11-20). ACP external agents work on Windows.
- Zed's ACP registry (`%LOCALAPPDATA%\Zed\external_agents\registry\registry.json`) lists, with Windows support:
  `claude-acp` 0.108.0 (binary; installed here as 0.107.0, bundles its own `claude.exe`), `codex-acp` 2.7.0
  (npx), `antigravity-acp` 1.5.0 (binary from `dl.google.com`, authors "Google LLC").
- `settings.json` has `"agent_servers": {"claude-acp": {"type": "registry"}}`.
- On PATH: `claude` yes; `codex` and `agy` not installed; Node/npx present.
- Zed CLI 1.28.1 has no agent or thread options. Zed docs: external-agent threads can be imported into history;
  Zed Skills do not apply to external agents; steering is Zed-Agent only; agents "run as separate processes"
  (behaviour on Zed close not documented).
- Antigravity's Zed page still says "macOS or Linux", but the registry ships an official Windows build.

## 5. Work plan

### P0 · Spike (half a day, no harness code)

1. In a Claude Agent thread: does it see `kaneki-flow` (skills from `.claude/`), run `sh .sdlc/bin/sdlc status`,
   and is a planted `git commit` in a ticket worktree refused (worktree hook, and the skill guard if hooks load)?
2. Same for a Codex thread (`codex-acp`) and an Antigravity thread (`antigravity-acp`): skill loading, `sdlc` calls,
   Codex unit dispatch through `sdlc dispatch`.
3. `start "zed://agent?prompt=hello"`: does the Agent Panel open with the prompt filled in, and in which agent?
   This picks the default action of "Continue in Zed".
4. Where Antigravity keeps its session logs (for transcripts), if anywhere readable.

Done when each item is recorded as verified / not working in this file.

### P1 · Host fixes in the harness

1. **Host in the rendered skill**: `install` writes `SDLC="SDLC_HOST=<h> sh .sdlc/bin/sdlc"` into each host's
   SKILL.md, so every agent identifies itself without Zed `env` settings (undocumented for registry agents).
2. **`.agents/` conflict**: `codex` and `antigravity` both install into `.agents/skills/…` with different
   preambles. Install one shared SKILL.md with a binding section per host, chosen by `SDLC_HOST`.
3. **`host-switch` decision event** when a ticket's host changes; shown on the board and in STATUS.md.
4. **Antigravity guard**: `sdlc dispatch` and the runner refuse `SDLC_HOST=antigravity` (D6); note it in
   `hosts/antigravity/preamble.md`.
5. **`sdlc install --host` required** (the default is `antigravity` today), or default back to `claude`.
6. Model aliases in `hosts/codex/tiers.json` checked against the installed Codex.

Tests in `tests/` for each (install output per host, refusal, event shape).

### P2 · Board (upgrade `sdlc web` to the mock)

1. Ticket rail with the agent per ticket and "needs you" count; pipeline with the agent per node.
2. Node inspector: units (agent, tier/model, verdict, tokens), artifacts with **Open in Zed** (`zed -e <file>:<line>`,
   already supported by `/api/open`), node events.
3. **Transcripts**: a session-log reader, Claude Code first (`~/.claude/projects/<workspace>/*.jsonl` + `subagents/`),
   then Codex (`~/.codex/sessions/…`) and `sdlc dispatch` logs. Matching: unit ↔ log by the packet path in the first
   message (exact); controller thread ↔ log by its `sdlc … --ticket <id>` calls for that node. Read-only, tool calls
   folded, live tail for running sessions.
4. **In flight**: units whose packet was rendered with no result recorded yet; tickets waiting on the user.
5. **Continue in Zed**: `zed://agent?prompt=…` if P0 confirms it, otherwise copy the prompt and focus Zed.
   "Open this thread" stays disabled until Zed ships thread links.
6. Keep the board local-only (127.0.0.1, per-run token); transcripts never leave the machine.

Fixtures: sample session logs (Claude, Codex) and ticket folders; tests for matching and the API.

### P3 · Gate decisions through the kernel

`sdlc decide --ticket <id>`: reads `gate-request`, asks Proceed / Re-run / Stop in the terminal, writes
`sdlc gate --actor user`. Zed task `kaneki: decide gate` from `sdlc zed-setup`. The board's gate banner points to it.

### P4 · Token monitoring

1. `harness/cost.py` + `sdlc cost`: real usage from the same session logs as P2 (dedupe by message id), next to the
   self-reported `record --tokens`. Shown on the board's Cost tab and in STATUS.md.
2. Budgets in the profile (ticket / node / daily); `packet` refuses an over-budget dispatch (new gate G8).

### P5 · Background runner (Claude Code and Codex only)

`sdlc run / tick / recover`, started by Task Scheduler at logon and every N minutes: runs controller-boundary
nodes, parks at user gates and notifies, recovers after a shutdown by restarting the node at `nextPending`.
Claude via `claude --bg` or `claude -p`; Codex via `codex exec`. Refuses Antigravity (D6). Usage-limit back-off.

### P6 · Reflect node (later)

A trailing `reflect` node per ticket and a scheduled cross-ticket retro write evidence-cited proposals (project
lessons, rules, profile tuning, harness feedback). Only `sdlc learn apply --actor user` makes them active (D10).

Order: P0 → P1 → P2 → P3; P4 and P5 after P2 (they share the log reader); P6 last.

## 6. Switching agents when Claude usage ends

1. Let the current node finish, or stop it; never switch in the middle of a node.
2. In Zed: new thread → Codex or Antigravity → "use the kaneki-flow skill for `<ticket>`". It resumes at `nextPending`.
3. The board shows the switch (P1 event) and which agent ran each node; Codex units run through `sdlc dispatch`.
4. Unattended work continues only with Claude Code or Codex (P5).

## 7. Open questions and risks

- Does Claude Agent over ACP load project skills and the kaneki guard hook? (P0.1) If not: CLI in a terminal tab.
- Do ACP agent processes stop when Zed closes? Undocumented; assume yes, so long unattended runs belong to P5.
- Antigravity policy may change; forum threads asking about ACP integrations were still open on 2026-11-23.
- `zed://agent?prompt=` is undocumented; which agent it opens is unknown (P0.3).
- Antigravity transcript location unknown (P0.4); without it the board shows Antigravity nodes without transcripts.
- Session-log formats are each agent's internal detail and can change between versions; keep one reader per agent,
  covered by fixtures.

## 8. Sources

- Zed: [external agents](https://zed.dev/docs/ai/external-agents), [agent panel](https://zed.dev/docs/ai/agent-panel),
  [extensions](https://zed.dev/docs/extensions/developing-extensions), [tasks](https://zed.dev/docs/tasks),
  [stable releases](https://zed.dev/releases/stable), [PR #62060](https://github.com/zed-industries/zed/pull/62060),
  [discussion #48083](https://github.com/zed-industries/zed/discussions/48083)
- ACP: [agentclientprotocol.com](https://agentclientprotocol.com/), [zed.dev/acp](https://zed.dev/acp)
- Antigravity: [terms](https://antigravity.google/terms/), [FAQ](https://antigravity.google/docs/faq/),
  [CLI ACP](https://antigravity.google/docs/cli/acp/), [Zed setup](https://antigravity.google/docs/ide/extensions/zed)
- Harness: `docs/agent-agnostic-decoupling-analysis.html`, `harness/hosts.py`, `harness/dispatch.py`,
  `harness/install.py`, `harness/web.py`, `harness/board.py`
