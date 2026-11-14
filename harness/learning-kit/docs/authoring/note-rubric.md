# Note review rubric

Score the learner's note (note.md) against the topic's concepts and research. Five criteria, 0–2 each:

| criterion | 2 | 1 | 0 |
|---|---|---|---|
| `correctness` | no factual errors | minor imprecision | a wrong claim about a core idea |
| `coverage` | every core concept present (non-core may be brief) | one core concept missing or thin | several missing |
| `own_words` | clearly their own phrasing and examples | some passages echo the research doc | mostly copied or paraphrased doc |
| `when_not` | says when to use it **and** when not to, with a reason | only one side, or no reason | absent |
| `connections` | links to something they already know or another topic, and explains the link | mentions a link without explaining it | none |

The bar is the same 80% as the quiz: **total ≥ 8/10, correctness = 2, no criterion at 0.** The script computes
this. Just score honestly.

Judge meaning, not polish. Dictated, informal and Vietnamese/English-mixed notes are fine. Don't reward
length. A short, correct, personal note beats a long one.

Return exactly:

```json
{
  "criteria": {
    "correctness": {"score": 2, "feedback": "..."},
    "coverage":    {"score": 1, "feedback": "Missing: <concept>"},
    "own_words":   {"score": 2, "feedback": "..."},
    "when_not":    {"score": 1, "feedback": "..."},
    "connections": {"score": 2, "feedback": "..."}
  },
  "missing_concepts": ["concept-id"],
  "summary": "Two sentences: the strongest part, and the single most valuable fix."
}
```

Feedback is addressed to the learner, specific, and never rewrites the note for them.
