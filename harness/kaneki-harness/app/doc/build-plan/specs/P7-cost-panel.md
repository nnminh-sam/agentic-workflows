# P7 · Real cost panel (M4)

**Milestone:** M4. **Output:** `parts/P7-cost-panel.plan.md`

## Problem
The harness's `token-usage.md` counted ~1.4M tokens for TODO-1-2 while the Claude Code session logs showed ~20M+
processed (plan §2). D7: compute real cost from `~/.claude/projects/<cwd-slug>/<session>.jsonl` and
`<session>/subagents/*.jsonl` (`message.usage`), deduplicated by message id, priced per call, shown next to the
harness count, with waste flags. M4 done-when: "Reproduces the TODO-1 vs TODO-1-2 numbers".

## Plan must cover
1. **Log format**: inspect a few real session logs under `C:/Users/minhnn4/.claude/projects/` (read-only; look at
   field names only, do not copy conversation content into the plan): where `usage` lives (input, output,
   cache_creation, cache_read, model), message ids and why duplicates occur (streaming/retries), subagent files,
   how to tell which ticket/node a session belongs to (cwd slug, session name `-n`, the packet path in the first
   prompt, harness `results/`). Look for earlier comparison scripts (`rg --files -g '*todo1*'` under
   `D:/workspace/HAISE`, and the harness `token-usage.md`); if not found, say so.
2. **Attribution**: session → ticket → node; app-spawned sessions (P6) can be tagged at spawn time — define that
   tag so attribution is exact going forward; heuristics for older sessions.
3. **Pricing** (Q4): per-model table for input/output/cache write/cache read; where it lives (user setting with a
   shipped default), how to mark prices as unverified. Do not invent current prices — leave placeholders and say
   where to verify.
4. **Computation**: incremental parsing (byte offsets, large files), dedupe, aggregation by ticket / node / model /
   session / day; where it runs (Host, or a Python helper in the harness — recommend; consider that the harness is
   stdlib-only), caching of results.
5. **Waste flags** (definitions with thresholds): leftover driver context, oversized agent start context, loops
   (same node re-run), cache-miss storms.
6. **UI**: cost tab (real vs harness, by node/model, compare two runs), top bar "today's cost"; `CostSummary`
   shape for P1.
7. **Verification**: reproduce TODO-1 vs TODO-1-2; tolerances.

## Not in scope
Spawning (P6) beyond the attribution tag; billing/API usage outside local logs.
