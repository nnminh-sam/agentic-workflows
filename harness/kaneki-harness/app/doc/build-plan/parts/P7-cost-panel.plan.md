# P7 · Real cost panel (M4)

Status: plan, 2026-11-18. Research only; nothing was built. Paths are relative to
`D:/workspace/HAISE/wp-agentic-workflows/agentic-workflows/` unless absolute. Session-log fields were inspected by
name and structure only; no conversation text is quoted here.

## 1. Goal and Done when

**Goal.** Show what a ticket really cost, computed from the Claude Code session logs (D7), next to the harness's own
token count, broken down by node, model, session and day, with waste flags and a two-run comparison.

**Done when** (all testable):
1. A fixture test over the real logs of `todo-nestjs-backend` reproduces the TODO-1 and TODO-1-2 token totals within
   0.5 % and the call counts exactly (section 7 gives the measured reference numbers).
2. Cost tab shows, per ticket: real tokens, real cost (list price, flagged "estimate"), harness tokens, ratio.
3. Breakdown by node, model, session, day; each row says whether it was attributed exactly (tag) or by heuristic.
4. Four waste flags (section 5) fire on TODO-1-2 where the earlier comparison found the problem, and stay quiet on a
   clean single-node session.
5. "Compare two runs" renders TODO-1 vs TODO-1-2 per node (tokens and USD).
6. Top bar shows today's cost; cold full scan of the 45 MB TODO workspace logs finishes in under 3 s, a warm
   refresh in under 200 ms.
7. Prices are user-editable, shipped as `unverified` placeholders; no price is hard-coded in code.

## 2. Scope

**In:** log parsing, dedupe, attribution, pricing, aggregation, caching, waste flags, `CostSummary` proposal, the
Cost tab and top-bar widget, the spawn-time attribution tag (definition only), verification.

**Out:** spawning itself (P6; this part only defines the tag P6 must write), API/billing outside local logs,
team-wide or multi-machine totals, budget enforcement (alerts only, as a later idea), editing prices from the web.

## 3. Inputs from other parts and outputs provided

**Inputs**
| From | What |
|---|---|
| P1 (data contract) | Workspace registry (workspace path, profile, `.sdlc/tickets`), the IPC/event conventions, owner of the final `CostSummary` type (this plan proposes it). |
| P1 / harness | `sdlc tickets` and `board.summarize_ticket` for node order, `tokensTotal` (harness count) and node list (`harness/kaneki-harness/harness/board.py:23-54`). Per-node harness tokens come from `state.json` units (`harness/kaneki-harness/harness/usage.py:32-54`). |
| P1 / file watcher | Event `ticket-changed` (decisions.jsonl or state.json changed) used to invalidate node windows. |
| P6 (spawn) | Writes the attribution tag (section 4.2) and the `sessions.jsonl` spawn ledger entry at spawn time. |
| Settings part | Settings store holding `prices` and `costRoots`. |

**Outputs**
| Kind | Name | Notes |
|---|---|---|
| IPC | `cost_summary(workspace, ticket?, range?)` | returns `CostSummary` |
| IPC | `cost_compare(workspace, ticketA, ticketB)` | returns `CostCompare` (per node deltas) |
| IPC | `cost_today(workspaces[])` | returns `{usd, tokens, asOf}` for the top bar |
| IPC | `cost_prices_get()` / `cost_prices_set(table)` | price table incl. `verified` flag |
| IPC | `cost_rescan(workspace)` | force a cold rescan (drops the cache) |
| Event | `cost-updated {workspace, ticket?}` | emitted after an incremental scan changed totals |
| File | `<app-data>/cost-cache/<slug>.json` | derived cache, safe to delete |
| Harness CLI | `sdlc cost --workspace W [--ticket T] [--json]` (new, read-only) | the engine (section 4.5) |

## 4. Design

### 4.1 Log format (verified 2026-11-18)

Layout under `C:/Users/minhnn4/.claude/projects/<cwd-slug>/`:
- `<session-id>.jsonl` (one per session) and `<session-id>/subagents/agent-<agentId>.jsonl` plus
  `agent-<agentId>.meta.json` (listing of the TODO workspace slug confirmed: 45 MB, 15 sessions, subagent dirs).
- The cwd slug is the cwd with `:`, `\`, `/` replaced by `-` (e.g. `D--workspace-HAISE-testing-todo-nestjs-backend`).
  Sessions started elsewhere live under their own slug: the TODO-1-2 harness driver session lives in slug
  `D--workspace-HAISE-agentic-workspace-agentic-workflows-kaneki-sdlc-harness`, not the workspace slug
  (`todo-1-vs-todo-1-2.html` source S3/S15, run command text). So the slug alone is not enough.
- No separate slug exists for the git worktrees (`worktrees/api-TODO-1-2`): a listing of
  `~/.claude/projects` shows none, because the node sessions ran with the workspace as cwd. ASSUMPTION: sessions
  started with a worktree as cwd would get their own slug; the scanner must therefore accept a list of slugs.

Record types seen in a session file (count shown for one 2.6 MB session): `assistant`, `user`, `attachment`,
`system`, `api-request`, `api-request-blob`, `queue-operation`, `last-prompt`, `mode`, `permission-mode`,
`session-owner`, `ai-title`, `tools-used`, `file-history-*`, and **`cost-state`**.

**Where usage lives.** On `type == "assistant"` rows: `message.usage` with `input_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `output_tokens_details.thinking_tokens`,
`cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`, `service_tier`, `speed`, `iterations[]`,
`server_tool_use.{web_search_requests, web_fetch_requests}`. `message.model` is the real model id
(`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` seen). `message.id` is the API message id (`msg_...`);
row keys also include `requestId`, `apiBlockIndex`, `timestamp`, `sessionId`, `cwd`, `gitBranch`, `isSidechain`,
`agentId` (subagent files only), `attributionSkill`.

**Why duplicates occur.** Each content block of one API response (thinking, text, each tool_use) is written as its
own `assistant` row; all share one `message.id` and one `requestId`, and carry identical `usage`. In the 2.6 MB
session: 201 assistant rows, 124 unique ids, 67 ids repeated, and 0 ids whose usage differed between rows. Across
the TODO-1 scan, 632 duplicate rows were dropped for 722 unique calls. Rule: **count one usage per `message.id`**
(first seen); if usage ever differs between rows of the same id (not observed), take the row with the larger
`output_tokens`. A resumed or forked session can repeat ids across files, so the dedupe set is global per scan.

**Claude Code's own cost record.** `type == "cost-state"` rows carry cumulative `totalCostUSD`,
`totalAPIDuration`, `modelUsage{<model>:{inputTokens, outputTokens, cacheReadInputTokens, cacheCreationInputTokens,
cacheCreationEphemeral5m/1hInputTokens, thinkingTokens, webSearchRequests, costUSD}}`, `hasUnknownModelCost`
(15 such rows across the workspace slug; 2 in the sampled session, last one `totalCostUSD` about 22.6 for that
session). Use as a **cross-check**, not the source: it is per session, cumulative, and ASSUMPTION it may or may not
include subagent files (not verified). The plan: show "Claude Code reported" beside "our estimate" in the session
drill-down when present, and flag a gap above 10 %.

**Which ticket and node a session belongs to** (fields that exist):
- `custom-title.customTitle` and `agent-name.agentName` rows: the `-n <name>` the session was started with. Node
  sessions of TODO-1-2 are named `todo12-<node>`; 12 such sessions found (verified).
- Subagent `*.meta.json`: `agentType` (e.g. `kaneki-worker`), `description` (human text that names node, role and
  ticket), `toolUseId` (links to the parent's tool_use row), `model`, `finished`.
- `attributionSkill` on rows (value `kaneki-flow` seen 976 times in the workspace) marks harness-driven turns.
- `cwd`, `gitBranch` on every row (branch `ticket/<id>` when in a worktree, ASSUMPTION from profile
  `branchPrefix`, `sdlc.profile.json` paths block).
- Harness side: `decisions.jsonl` `record`/`gate` events carry `node`, `unit`, `at`, and `state.json` units carry
  `model`, `generation`, `at` (`harness/kaneki-harness/harness/cli.py:531-540`).
- The first user prompt contains the packet path (`.sdlc/tickets/<id>/packets/...`): usable as a match key
  (regex on the path), but never stored or displayed; only the extracted `<id>/<node>` is kept.

**Earlier comparison scripts.** The throwaway `scratchpad/cost.py` and `scratchpad/final.py` are not on disk (the
report says they are temporary,
`D:/workspace/HAISE/testing/todo-nestjs-backend/docs/todo-1-vs-todo-1-2.html`, section "How the numbers were
measured", S3/S15). Nothing named `*todo1*` exists under `D:/workspace/HAISE` other than that HTML report. Method
and reference numbers are taken from that report; no script to port. A rough re-derivation was done (section 7).

**How the harness counts.** `record --tokens N` takes a number the controller copies from the subagent completion
notice (`harness/kaneki-harness/harness/cli.py:535,1057`; instruction at `harness/kaneki-harness/skills/kaneki-flow/SKILL.md:138-139`).
`usage.py` sums those per agent and generation (`harness/kaneki-harness/harness/usage.py:32-54`); rounds without `--tokens`
make it a lower bound (`usage.py:78,141`). Controller work is "inline" and unrecorded (`usage.py:70`). So the harness
number is the agent's own reported total, excluding the driver session, retries and per-call cache re-reads.

### 4.2 Attribution

Output unit: `Attribution {ticket, node, role, unit?, generation?, source: "tag"|"title"|"meta"|"window"|"unknown"}`.

**A. Exact, going forward (spawn tag).** P6 passes the session name as
`claude -n kaneki:<ticketId>:<node>:<runId>` (ticket id and node name contain only `[A-Za-z0-9._-]`; `:` is the
separator; `runId` = short id of the app's run). The scanner reads it from `custom-title.customTitle`/`agentName`.
P6 also appends one line to `<ticketDir>/app/sessions.jsonl`:
`{sessionId?, name, ticket, node, runId, role: "driver", spawnedAt, pid}` (session id is filled in by the scanner
when it sees the title; the app never needs the session id to spawn). Subagents inherit the parent's attribution:
a file under `<session>/subagents/` belongs to the parent session's ticket/node, and its role comes from
`meta.json.agentType` (`kaneki-worker` -> worker, `kaneki-deep` -> reviewer/deep; `harness/` agent names to verify
against `harness/kaneki-harness/agents/` ASSUMPTION). Within a driver session that runs several nodes (one-session mode,
D6), node comes from time windows (below) instead of the title.

**B. Heuristics for older sessions, in order, stopping at the first hit:**
1. `customTitle` matches `todo<digits>-<node>` or `<ticketId lowercased, dots removed>-<node>` style (the
   `todo12-review` convention): ticket by prefix mapping (`todo12` -> `TODO-1-2`), node by suffix with a small alias
   map (`review` -> `code-review`/`final-review` ambiguity, `drafts` -> `delivery-drafts`): `source: "title"`, and
   mark `confidence: "low"` when the alias is ambiguous.
2. Packet-path match: first user prompt (or the first subagent prompt) contains `.sdlc/tickets/<id>/packets/<node>-...`.
3. Subagent `meta.json.description` contains the node name and ticket id: `source: "meta"`.
4. Time window: for a session with unknown node, assign each call to the node whose `[nodeStart, gateAt]` window
   (from `decisions.jsonl` `record` and `gate` timestamps, newest generation wins) contains the call timestamp and
   whose ticket `cwd`/branch matches. `source: "window"`, always `confidence: "low"`.
5. Otherwise `ticket: null` -> bucket "Unattributed" (shown, counted in workspace total, excluded from ticket totals).

Driver sessions that span a ticket's start (TODO-1-2's first harness session also did earlier work) are cut by
time: ticket window = first `init` event to `deliverable-ready` gate (or now). Calls before the window are
"presession" and excluded from ticket totals (section 5, flag F1).

### 4.3 Pricing (Q4)

`PriceTable {version, asOf, source, verified: bool, models: {<model-id-prefix>: ModelPrice}}` with
`ModelPrice {inputPerMTok, outputPerMTok, cacheWrite5mMult, cacheWrite1hMult, cacheReadMult, longContext?:
{thresholdTokens, inputPerMTok, outputPerMTok}}`. Rules:
- **Shipped default** `prices.default.json` with every number `null` and `verified: false`; the UI then shows tokens
  only and a banner "prices not set". If the user chooses to seed it, the seed is written by the user (or by a
  future "import from claude-api reference" action), never invented in code. The earlier comparison report used a
  table (Opus/Sonnet/Haiku, cache writes 1.25x/2x, cache reads 0.1x, a higher Haiku tier above 100K prompt tokens);
  its section S5 cites the `claude-api` skill reference as the source. Treat those as candidate seed values to be
  re-verified, not as truth. **Where to verify:** Anthropic pricing page and the `claude-api` skill's
  "Current Models" and prompt-caching economics pages (names as cited in the report, S5).
- **User setting** in app settings (Settings part), edited as a table; overrides persist per user, not per
  workspace. Each row has `verified` (checkbox plus date). Any total computed with an unverified row is badged
  "estimate" in the UI, and exports carry `priceTableVersion`.
- **Per-call pricing**, not per-total, because Haiku's long-context tier depends on each call's prompt size:
  `prompt = input + cache_creation + cache_read`. Cache write multiplier chosen from the call's
  `cache_creation.ephemeral_1h` vs `_5m` split (both present in the sample: all 1h in the sampled session).
- **Unknown model** -> tokens counted, cost `null`, `hasUnknownModelCost: true` in the summary.
- **Fast/priority speed** (`usage.speed`, `service_tier`) is recorded in the aggregate keys but not priced
  differently until a price table says so. ASSUMPTION.
- Dollars are "list price equivalent", not a bill (the report states subscription plans have no per-token charge,
  report section "How the numbers were measured"); the UI says so in a tooltip.

### 4.4 Computation, caching, where it runs

**Where: a Python module in the harness, `harness/kaneki-harness/harness/cost.py`, exposed as the read-only
`sdlc cost --json`. Recommendation reasoned:**
- The harness is stdlib-only Python and already owns ticket semantics (`board.py`, `usage.py`, state/graph); the
  attribution and the node windows need those. Keeping it there honours "keep logic in the harness, Host thin" and
  lets the board, CI and the app agree.
- Parsing JSONL with `json` + `os` is fast enough (measured scans below), so no Go parser is needed. The Host only spawns `sdlc cost`, relays JSON, and watches the logs directory.
- Cost: a Python process start (~0.2-0.5 s) per call; mitigated by the warm cache and by the Host calling it only
  on change, with an optional long-lived `sdlc cost --serve-stdio` later (not v1). Alternative, a native parser in
  the Host, is rejected for v1: duplicates attribution logic across languages.
- It is read-only on the logs (opens with read share mode; Windows lets Claude keep appending) and writes only its
  own cache under the app data dir, never inside `.sdlc/` (D1: the harness writes tickets; the cache is not a ticket
  file).

**Incremental parsing.** Per file keep `{path, size, mtime, offset, tailHash}`. On rescan: if `size >= offset` and
the 64-byte window before `offset` still hashes to `tailHash`, read from `offset`, else reparse the file. Only
complete lines (up to the last `\n`) are consumed; the partial line stays unread. Logs are append-only, so this
catches nearly every change. Skip rows quickly: test `'"usage"' in line` or `'"cost-state"'`/`'"custom-title"'`
before `json.loads`. Large rows (`api-request-blob`, `file-history-*`) are skipped by that prefilter.

**Dedupe.** `seen: set[msg_id]` persisted in the cache per slug (about 30 bytes per id; 722+617+... calls is tiny).
Per call keep a compact record `{id, ts, model, in, cw5, cw1, cr, out, think, file, sub?, agent?}`, not the text.

**Aggregation.** One pass builds the call table; summaries are group-bys over it: ticket, node, role, model, session,
day (local timezone, user setting; the report used UTC cut points, so store UTC and convert in the UI). Cache file
`<app-data>/cost-cache/<slug>.json` holds file cursors, `seen`, calls, session meta (title, agentName, first/last ts),
and `schemaVersion`; a version mismatch triggers a full rescan. Windows of node/ticket come from `decisions.jsonl`,
read fresh each time (it is small), so changing a gate re-attributes without rescanning logs.

**Triggers.** Host watches `~/.claude/projects/<slug>/` (and `subagents/`) with a 2 s debounce, only for slugs
registered for the workspace (cwd slug of the workspace and of each worktree, plus slugs added in P6's ledger and a
user list in settings; the TODO-1-2 harness slug case shows why a manual list is required). It fires
`cost-updated`. Also recompute on `ticket-changed`. D4 says do not poll; the debounce is event-driven.

**Slug discovery.** Registered slugs = `slug(workspace)` + `slug(worktree)` for each worktree dir +
`costRoots` from settings + any slug whose scanned `cwd` is under the workspace (a cheap first-row probe of each
unregistered session file's `cwd`, cached; only for slugs whose name starts with the workspace slug).

### 4.5 `CostSummary` (proposal for P1)

```ts
type CostSummary = {
  schema: 1; workspace: string; ticket: string | null; asOf: string;      // ISO UTC
  priceTable: { version: string; verified: boolean; asOf: string | null };
  total: Totals;                      // real, deduped, all attributed calls in the ticket window
  harness: { tokens: number | null; unreportedRounds: number };   // from usage.py
  ratio: number | null;               // real.tokens / harness.tokens
  net: Totals;                        // total minus presession (F1), what we show as headline
  byNode: Array<{ node: string; generation: number | null; real: Totals; harnessTokens: number | null;
                  roles: Record<string, Totals>; confidence: "exact"|"low"; flags: string[] }>;
  byModel: Record<string, Totals>;
  bySession: Array<{ sessionId: string; name: string | null; role: "driver"|"worker"|"deep"|"other";
                     ticket: string | null; node: string | null; source: string; firstTs: string; lastTs: string;
                     calls: number; real: Totals; claudeCodeCostUSD: number | null }>;
  byDay: Array<{ day: string; real: Totals }>;
  flags: WasteFlag[];
  unattributed: Totals; unknownModels: string[];
};
type Totals = { calls: number; input: number; cacheWrite: number; cacheRead: number; output: number;
                tokens: number; usd: number | null; usdUnverified: boolean };
type WasteFlag = { id: "F1"|"F2"|"F3"|"F4"; severity: "info"|"warn"; node?: string; session?: string;
                   message: string; evidence: { metric: string; value: number; threshold: number };
                   estWasteUsd: number | null };
type CostCompare = { a: string; b: string; rows: Array<{ node: string; a: Totals|null; b: Totals|null;
                     deltaTokens: number; deltaUsd: number|null }>; totals: { a: Totals; b: Totals } };
```
`tokens = input + cacheWrite + cacheRead + output` (the definition used by the earlier report). Nodes use the
graph's names (`harness/kaneki-harness/graph/lifecycle.json`). P1 owns the final shape; keep `schema` and additive fields.

### 4.6 Waste flags (definitions; thresholds are settings with these defaults)

All flags carry their measured value and an estimated wasted USD (when prices are set). Defaults are starting
points to tune against TODO-1/TODO-1-2.

- **F1 Leftover driver context.** In a driver session, first-call context (`input + cache_creation + cache_read`
  of the first assistant call inside the ticket window) minus a fresh-session baseline, multiplied by the number
  of driver calls in the window. Baseline = median first-call context of sessions with `source: "tag"` and
  no prior work, else a settings value (the report measured 59,882 for a fresh driver, 183,539 at TODO-1-2 start,
  s S3). Fire when the excess > 60,000 tokens **and** > 10 calls. Waste = excess x calls x cacheRead price (these
  calls are reads). Presession tokens are also what "net" removes.
- **F2 Oversized agent start context.** For each worker/deep subagent, first-call context (same formula). Fire when
  > 25 % of the model's window, or > 80,000 tokens for light-tier models (setting). The harness already estimates
  the handed packet and warns at a quarter of the window (`harness/kaneki-harness/harness/cli.py:444-453`); F2 is the
  measured twin and links to that packet's `handedTokens` when present. Waste = (context - median of that role) x
  calls.
- **F3 Loops (node re-run).** Same node with `generation >= 2` or the same node name in more than one session /
  more than 2 worker rounds. Fire at generation >= 2 (info) and >= 3 (warn). Cost = tokens of generations
  before the final (`usage.py:53` `earlierGenerations`; for real tokens use calls whose timestamp is before the
  last `invalidate`/reopen of that node). Example in the data: `execute-and-evidence` generation 3, 655,001 harness
  tokens over all generations vs 167,750 current
  (`.sdlc/tickets/TODO-1-2/token-usage.md` per-node table).
- **F4 Cache-miss storm.** Per session, per call: `miss = cache_creation / (cache_creation + cache_read)`. Fire
  when >= 5 consecutive calls (or >= 20 % of calls) have `miss > 0.5` **and** `cache_creation > 20,000` each,
  excluding the first call of a session. Typical causes: prompt prefix change, idle gap beyond cache TTL (use the
  timestamp gap > 5 min vs 1 h), or compaction. Waste = (cacheWrite price - cacheRead price) x repeated
  creation tokens. Show the timestamp gap that likely caused it.
- Not flagged in v1 (considered): long-context tier crossings, thinking-token share, web search use. All are
  available in `Totals` extensions later.

### 4.7 UI (HTML/CSS/JS on the design system; `.k-*` classes)

- **Cost tab** (the existing mock tab, `harness/kaneki-harness/app/design-system/app-mock.html`, plan §4 row "Cost"):
  1. Header strip: real tokens (net), real USD (badge `estimate` if unverified), harness tokens, `x` ratio, price
     table state.
  2. Table "By node": columns node, gen, real tokens, USD, harness tokens, share bar, flags; click row opens
     sessions for that node. Rows with `confidence: low` show a dotted marker and tooltip with the heuristic.
  3. "By model" stacked bar; "By day" sparkline; "By session" list with `Claude Code reported` when present.
  4. Waste panel: flag cards (F1-F4) with metric, threshold, estimated waste USD, and "Show sessions".
  5. Compare: pick two tickets; table of per-node A vs B with delta; this is the TODO-1 vs TODO-1-2 view.
  6. Empty/edge states: no logs found, prices not set (tokens only), scanning (progress), unattributed bucket,
     unknown model.
- **Top bar "today's cost"**: sum across registered workspaces for local-day; badge; click opens Cost tab filtered
  to today. Updates on `cost-updated`.
- **Interactions**: "Rescan" button (`cost_rescan`), "Edit prices" opens Settings table, export CSV/JSON of
  `CostSummary` (writes through Host save dialog; not into `.sdlc/`).
- Stack-specific bits: only the IPC wrapper (`invoke`/`on`); Wails binds Go methods and `EventsOn`. The Host's
  only work is spawn `sdlc cost`, relay JSON, debounce the watcher.

## 5. Tasks

| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P7-T01 | `cost.py`: file discovery, JSONL scan with prefilter, dedupe by `message.id`, call table | M | none | Reproduces TODO-1 call count 722 and tokens 50,857,722 (+/- 0 on fixture) |
| P7-T02 | Fixture: curated, anonymized mini-logs (structure kept, text stripped) plus expected numbers; checked into the app repo | M | T01 | Fixture test runs offline and fails on a deliberate double-count |
| P7-T03 | Incremental cursor + cache file with version, tail hash, fallback to reparse | M | T01 | Appending to a fixture file only reads new bytes; truncated file reparsed |
| P7-T04 | Attribution A+B (tag, title, packet path, meta, window) with `source`/`confidence` | M | T01, P1 ticket reader | Each of the 12 `todo12-*` sessions gets a node; unmatched go to Unattributed |
| P7-T05 | Pricing module, `PriceTable`, per-call pricing, long-context tier, unknown model path; default table all-null | S | T01 | Unit tests with synthetic prices; no number appears in source |
| P7-T06 | Aggregation + `CostSummary`, `CostCompare`, harness-count join (`usage.py` / `board.py`), `sdlc cost` CLI subcommand | M | T04, T05 | `sdlc cost --json --ticket TODO-1-2` validates against a JSON schema file |
| P7-T07 | Waste flags F1-F4 with settings thresholds | M | T06 | Flags fire on TODO-1-2 (F1, F3) and not on a one-node tagged fixture |
| P7-T08 | Presession / ticket-window cut (net totals) | S | T04 | Net total within 0.5 % of the report's net for TODO-1-2 |
| P7-T09 | Cross-check with `cost-state` per session; gap flag | S | T01 | Shown in session drill-down; gap >10 % flagged |
| P7-T10 | Host: spawn `sdlc cost`, debounced watcher on registered slugs, `cost-updated` event, today's cost | M | T06, P1 Host skeleton | Appending to a log updates top bar within 3 s |
| P7-T11 | Slug discovery and settings list (`costRoots`), worktree slugs, harness-session slug case | S | T10, Settings part | TODO-1-2 harness session found without manual path |
| P7-T12 | UI: Cost tab, waste panel, compare, top-bar widget, price banner, empty/loading states | L | T06, T10, design system | Matches mock sections; light and dark |
| P7-T13 | Spawn tag spec handed to P6 plus `app/sessions.jsonl` ledger line, scanner reads both | S | T04, P6 | A P6-spawned session is attributed `source: "tag"` end to end |
| P7-T14 | Real-workspace verification script (section 7) and recorded results in the plan appendix | S | T06, T08 | Table in section 7 filled with measured values and tolerances |

## 6. Tests / verification

- **Unit:** dedupe (same id x3 -> 1; differing usage keeps larger output), cursor logic (append, truncate, partial
  last line), pricing (tier boundary at the threshold, 1h vs 5m creation split, unknown model), attribution
  precedence and alias ambiguity, each waste flag at threshold -1/threshold/threshold +1.
- **Fixture:** anonymized logs preserving key structure; golden `CostSummary` JSON compared with a tolerance of
  0 for counts and 0.01 % for USD.
- **Manual on the real workspace:** `sdlc cost --workspace D:/workspace/HAISE/testing/todo-nestjs-backend
  --ticket TODO-1` and `TODO-1-2` against section 7; the Cost tab and the HTML report must agree on tokens, calls
  and USD (with the same price table). Read-only.
- **Performance:** cold scan of the 45 MB workspace slug, warm refresh, memory of the helper (< 100 MB).
- **Live:** run one P6-spawned session; verify the tag, that totals move within the debounce, and that nothing is
  double-counted when the same session continues after a restart.
- **Privacy:** assert that the cache and CLI JSON contain no prompt/response text (only ids, names, numbers).

### Measured reference numbers (this research, read-only, 2026-11-18)

Tokens = `input + cache_creation + cache_read + output`, deduped by `message.id`, usage from `message.usage`
(subagent files included). Reproduced from the logs with a throwaway script, nothing saved.

| Item | Result |
|---|---|
| TODO-1: sessions `68a31649`, `97e301fa`, `af1bcc31` + subagents, cut at 2026-11-17T01:28:46Z | 722 calls (632 duplicate rows dropped), **50,857,722 tokens** (opus 39.60M, sonnet 11.26M; cache read 48.15M, cache write 2.11M, output 0.60M, fresh input 1,502) |
| TODO-1-2: 12 `todo12-*` node sessions + subagents, cut at 2026-11-17T10:19:50Z | 617 calls, 43,217,930 tokens (opus 21.91M, sonnet 16.90M, haiku 4.41M) |
| TODO-1-2: harness session `7a18b932` (separate slug), 06:28:54-07:48:30Z | 161 calls, 20,313,042 tokens (haiku 10.73M, opus 7.38M, sonnet 2.20M), gross of the presession |
| TODO-1-2 gross sum | **63,530,972 tokens, 778 calls** |
| Earlier report (net of presession 123,657 x 33 calls = 4.08M): TODO-1 / TODO-1-2 | 50.86M, 722 calls, $27.59 / 59.63M, 780 calls, $24.23 (`docs/todo-1-vs-todo-1-2.html`, S3/S15 text) |
| Check | TODO-1 matches the report exactly (50.86M, 722). TODO-1-2: gross minus presession is about 59.45M vs 59.63M (0.3 % off, 778 vs 780 calls; cut-point boundaries) |
| Harness count | TODO-1 1,763,370 (`.sdlc/tickets/TODO-1/token-usage.md:5`); TODO-1-2 2,066,547 (`.sdlc/tickets/TODO-1-2/token-usage.md:5`) |
| Real / harness | TODO-1 about 28.8x; TODO-1-2 about 28.9x (net) |

The design plan quotes "~1.4M vs ~20M+" for TODO-1-2 (`harness/kaneki-harness/docs/desktop-app/plan.md:28`), an earlier
snapshot (harness count then, and only the first-session logs). The final numbers above supersede it.

**Tolerances for the M4 test:** token totals within 0.5 %; call counts +/- 3 (boundary calls); USD within 1 % when
the same price table is used; ratio real/harness 25-35x (a drift outside that range is a signal worth a look, not
a failure). The exact USD per run cannot be re-derived without a verified price table; use the report's USD only as
a comparison once prices are entered.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Claude Code changes the log schema (field names, new record types) | Defensive parse (`.get`), `schemaVersion` on the cache, a canary test on the user's real logs at app start, `unknown record` counter shown in Settings. |
| Same API message counted twice (duplicate rows, resumed sessions) | Global dedupe by `message.id` per scan; fixture test with a deliberate double-count. |
| Wrong attribution for older sessions | Show `source`/`confidence`; low-confidence rows visually marked; unattributed bucket; allow manual remap (writes the app's own override file, not `.sdlc/`) in a later iteration. |
| Sessions in unexpected slugs (as the harness driver was) | Registered-slug list plus cwd probe plus manual `costRoots`. |
| Prices wrong or stale | All unverified by default; badge "estimate"; no number baked into code; table versioned and dated. |
| Large logs / slow first scan, Windows file locks while Claude appends | Prefilter, offsets, tail-hash, read-only share mode, retry once on sharing violation. |
| Privacy: logs contain conversation text | Parse only numeric/id/name fields; never cache or display text; the `sdlc cost` JSON is schema-checked in tests. |
| `cost-state` double counting if subagents are included both in it and in files | Used only as a cross-check; labeled; not added to totals. |
| Python helper startup cost on each refresh | Warm cache + change-driven calls + optional stdio server later. |
| Harness count ratio misread as harness "wrong" | Tooltip: the harness number is the agents' reported totals; real cost adds driver sessions, retries and cache re-reads. |

## 8. Decisions needed from the user

1. **Engine location.** Python `sdlc cost` in the harness (recommended) vs a native parser in the Host.
2. **Session naming for spawned sessions.** Adopt `kaneki:<ticket>:<node>:<runId>` as the `-n` name (recommended)
   so attribution is exact; old `todoNN-<node>` sessions stay heuristic.
3. **Price source.** Ship an all-null table and have you enter prices once (recommended), or let me seed values
   from the `claude-api` skill reference (marked unverified). I will not seed unless you say so.
4. **Net vs gross headline.** Show net of presession leftover (as in the report; recommended) with gross one click
   away, or gross only.
5. **Dollar framing.** Headline "list-price equivalent" with the subscription caveat (recommended) vs tokens only.
6. **Day boundary.** Local time (recommended; the report used UTC cut points, kept in storage).
7. **Cache location.** `%APPDATA%/<app>/cost-cache` (recommended) vs inside the workspace under a git-ignored dir.
8. **`sdlc cost` subcommand.** OK to add a new read-only harness subcommand (touches `cli.py` and a new
   `cost.py`; belongs to harness work, not this plan) or keep it as an app-bundled helper script?

## 9. Evidence

- `harness/kaneki-harness/app/doc/build-plan/specs/_context.md` (rules, format); `.../specs/P7-cost-panel.md` (spec).
- `harness/kaneki-harness/docs/desktop-app/plan.md:28,41,74,109,117` (problem, D7, session log path, M4, Q4).
- `harness/kaneki-harness/harness/board.py:23-54,78,87` (tokensTotal, summarize_ticket, list_tickets).
- `harness/kaneki-harness/harness/usage.py:1-21,32-54,70,78,127-141` (how harness counts tokens, lower-bound note).
- `harness/kaneki-harness/harness/cli.py:311-322,444-453,531-542,1057` (`--tokens`, packet size estimate, record).
- `harness/kaneki-harness/skills/kaneki-flow/SKILL.md:138-139` (driver passes subagent total tokens).
- `D:/workspace/HAISE/testing/todo-nestjs-backend/.sdlc/tickets/TODO-1/token-usage.md:5`,
  `.../TODO-1-2/token-usage.md:5,8-23` (harness totals, per-node table with generations).
- `D:/workspace/HAISE/testing/todo-nestjs-backend/sdlc.profile.json` (paths: ticketRoot, branchPrefix `ticket/`,
  worktreeRoot `worktrees`; models light/routine/deep).
- `D:/workspace/HAISE/testing/todo-nestjs-backend/docs/todo-1-vs-todo-1-2.html` (method, prices used, S3/S5/S15
  text; the "kaneki-todo1-vs-todo12" scripts are not on disk).
- `C:/Users/minhnn4/.claude/projects/D--workspace-HAISE-testing-todo-nestjs-backend/` (session files,
  `subagents/agent-*.jsonl`, `agent-*.meta.json`, record types and field names; fields only);
  `.../D--workspace-HAISE-agentic-workspace-agentic-workflows-kaneki-sdlc-harness/7a18b932-....jsonl`.
- Could not verify: current model prices; whether `cost-state` includes subagent spend; exact worktree-cwd slug
  behavior (no worktree slug exists on this machine); alias map from short node titles to node ids; exact session
  start and end times beyond the report's cut points; `harness/kaneki-harness/agents/` agent names for role mapping.
