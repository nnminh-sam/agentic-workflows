# Reteach guide

Input: the missed concepts, the learner's actual wrong answers and grader feedback (`LK session show <id> --full`),
the snapshots, research.md, and prior.md.
Output: `topics/<slug>/reteach/round-<N>.md`, short (≈300–900 words). Only the missed concepts.

For each missed concept:

```markdown
## <Concept name>

**What you said:** "<their answer, quoted briefly>"
**The gap:** one sentence naming the exact misconception or missing piece.

**The idea, again, from a different angle:** a fresh explanation that differs from research.md: a new
analogy, a worked example, or a step-by-step trace. Cite sources [S0004].

**Contrast:** "It's tempting to think X because Y. Actually Z, because ..."

**Check yourself (don't answer here, just think):** one question to try before the next round.
```

Don't repeat research.md word for word, because rereading the same text doesn't help. Don't include the
next round's questions.
