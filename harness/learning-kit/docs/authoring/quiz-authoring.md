# Quiz authoring and review guide

## Item schema (all types)

```json
{
  "type": "mcq | multi | order | cloze | predict | explain",
  "concept": "<concept id from concepts.json>",
  "difficulty": 1,
  "prompt": "Markdown. Fenced code allowed.",
  "code": "optional code shown under the prompt (required for predict unless the prompt has a fence)",
  "source_ids": ["S0003"],
  "explanation": "REQUIRED. Shown after grading: why the answer is right, and why the tempting wrong one is wrong."
}
```

Type-specific fields:

| type | fields | graded by |
|---|---|---|
| `mcq` | `options` (3–6), `answer` (index), optional `misconceptions` {"<index>": "why tempting"} | script |
| `multi` | `options` (3–8), `answer` (list of indexes) | script (partial credit: right − wrong picks) |
| `order` | `steps` (≥3, **in the correct order**; shuffled on display) | script (pairwise inversions) |
| `cloze` | prompt with `{{1}}`…`{{n}}`, `blanks`: list of accepted-answer lists, optional `case_sensitive`, `llm_fallback` | script (+LLM if `llm_fallback`) |
| `predict` | `code` or fenced code, `reference_answer`, `rubric` (≥2 points) | Claude, per rubric point |
| `explain` | `reference_answer`, `rubric` (≥2 points) | Claude, per rubric point |

Don't set `id`, `round`, `added_in`. Import assigns them. A file is `{"items": [...]}` or a bare list.

## Writing good items

- **Mix:** about 50% free-recall (explain/predict), the rest split across mcq/multi/order/cloze. Every
  **core** concept needs at least one explain or predict item. Every target concept needs at least 2 fresh
  items (1 for aware).
- **Test understanding, not recognition:** ask why, what breaks if, when not to, compare X vs Y, predict
  the output, spot the bug. Avoid "which of these is the definition of…".
- **Distractors are real misconceptions.** Take them from prior.md (what the learner wrongly believed),
  mistakes.jsonl and the "common misconceptions" section. Put the reason in `misconceptions`.
- **Every answer must be supported by the cited sources.** If the sources don't settle it, don't ask it.
- **One unambiguous correct answer** for mcq. No "all of the above" or "none of the above". Similar option
  lengths. No grammatical giveaways.
- **Rubric points** are atomic, checkable claims ("mentions that readers see a snapshot as of transaction
  start"), 2–5 per item, ordered by importance. The reference_answer is a model answer in 2–6 sentences.
- **Difficulty:** 1 = recall a fact, 2 = apply or explain, 3 = analyze a scenario or trade-off. Aim for roughly 30/50/20.
- Use concrete scenarios from the learner's world (backend services, their project) where it fits.
- **Retake rounds** (after reteach): new items on the missed concepts. Don't reuse wording from spent items.
  Target exactly what the reteach covered.

## Review checklist (fresh reviewer agent)

For each item, check and fix (don't just comment):
1. Is the marked answer correct according to the cited snapshots? (Open them: `LK source path <id>`.)
2. Is exactly one mcq option defensible? Could an expert argue for another one?
3. Do the wording, option length or grammar give the answer away?
4. Does the item test the stated concept, at the stated difficulty?
5. Can the rubric points actually be graded, and does the reference answer satisfy all of them?
6. Coverage: each target concept ≥2 items, each core concept ≥1 explain/predict, about half free-recall.
Write the corrected file plus a short report: items changed, items dropped and why. Validate with `LK quiz validate`.

## Grading (when the agent grades by hand)

Per rubric point: 1 = clearly and correctly present, 0.5 = partial or vague, 0 = missing or wrong. A confident
wrong claim about the core idea zeroes the related point. Judge meaning, not wording; English or Vietnamese
are both fine; ignore typos and speech-to-text errors. Score = mean of points. Free-recall counts as correct at ≥ 0.7.
