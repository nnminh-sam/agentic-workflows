# P7 · Real cost panel (M4)

**Milestone:** M4. **Output:** `parts/P7-cost-panel.plan.md`

## Problem
The harness's `token-usage.md` counted ~1.4M tokens for TODO-1-2 while the backend session logs showed ~20M+
processed (plan §2). D7: compute real cost via a pluggable `UsageCollector` interface — reading Claude Code
session logs (`~/.claude/projects/<cwd-slug>/<session>.jsonl`), `sdlc dispatch` logs (`logs/*.dispatch.log`),
and harness `token-usage.md` — deduplicated by message id, priced per tier/model, shown next to the harness count,
with waste flags. M4 done-when: "Reproduces the TODO-1 vs TODO-1-2 baseline numbers and handles non-Claude hosts".

## Plan must cover
1. **Collector interface & log formats**: define one Go/Python usage-event model supporting:
   - Claude Code JSONL (`usage`: input, output, cache_creation, cache_read, model, `message.id` dedupe)
   - `sdlc dispatch` JSON/JSONL stdout in `logs/*.dispatch.log` (Codex, Gemini, OpenCode, Claude `-p`)
   - Graceful fallback to harness `state.json` / `token-usage.md` when host logs are opaque (e.g. IDE hosts).
2. **Attribution**: session → ticket → node → tier; app-spawned sessions (P6) and `sdlc dispatch` tag runs at spawn time.
3. **Pricing** (Q4): per-model table keyed by resolved model name (and mapped to capability tier `light`/`routine`/`deep`
   in charts); user setting with a shipped default, marking prices as unverified until confirmed.
4. **Computation**: incremental parsing (byte offsets, large files), dedupe, aggregation by ticket / node / tier / model /
   session / day; caching of results.
5. **Waste flags** (definitions with thresholds): leftover driver context, oversized agent start context, loops
   (same node re-run), cache-miss storms, unverified inherited model runs (`modelMismatch: true`).
6. **UI**: cost tab (real vs harness, by node/tier/model using semantic `--tier-*` tokens, compare two runs), top bar
   "today's cost"; `CostSummary` shape for P1.
7. **Verification**: reproduce TODO-1 vs TODO-1-2 (tolerances) plus multi-host fixtures (`codex`, `antigravity`, `gemini`).

## Not in scope
Spawning (P6) beyond the attribution tag; billing/API usage outside local logs.
