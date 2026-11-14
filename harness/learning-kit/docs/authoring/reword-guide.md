# Reword guide (spaced recall variants)

Input: due items from `LK srs due --json` (each has `uid`, `topic`, `item` with answers).
Output: `{"items": [ ...variants... ]}`. Each variant is a full item (same schema as quiz-authoring.md) plus
`"origin": "<uid>"`. Omit `id`.

Goal: the learner should recall the **concept**, not remember the question's wording or the position of the answer.

- Keep the same `concept` and `source_ids`. The correct answer must still be supported by the sources.
- Change the surface: a new scenario or example, different phrasing, different code (for predict), new
  distractors drawn from real misconceptions, a different option order.
- You may change the type when that tests the concept better (e.g. mcq → explain), but keep roughly
  half free-recall overall.
- Keep the difficulty the same or one step harder for cards with high stability (`card.s` > 30 days).
- Cards with lapses > 0: aim directly at the misconception recorded in `DATA/mistakes.jsonl` (match `origin`).
- If you can't make a faithful variant, skip it. The original is used.
Validate with `LK quiz validate <file> --variant`.
