---
name: note
description: "Capture and review my own-words note for a learning-kit topic (write it in your editor or dictate it in chat), score it against the 5-criterion rubric with an 80% bar, and on pass schedule the topic for spaced recall. Triggers: /note <topic>, 'review my note on X'."
argument-hint: "<topic> [note text...]"
---

# /note: your own words, reviewed

```
LK = {{LK}}
KIT = {{KIT}}
DATA = <"home" from `LK config path`>   # normally {{HOME}}; LK_HOME overrides it (e.g. a test sandbox)
```

Commands are for a POSIX shell (Bash tool; Git Bash on Windows). In PowerShell, prefix `& ` if `LK` starts with a quote.

1. Resolve the slug and run `LK topic status <slug>`. Normally the stage is `note`. You can also use this
   at `scheduled` to revise a note. In that case, record the review but leave the stage alone.
2. **Get the note.** It can come from:
   - the args or the chat (typed or dictated), or
   - `DATA/topics/<slug>/note.md` if they wrote it in their editor. Ask which, if unclear.
   Save it to `note.md` **in their words**. Only fix dictation artifacts (filler words, obvious
   mis-transcriptions). Never improve, expand or restructure it. If a previous note exists, keep the old one
   as `note.v<N>.md`.
   Remind them, if useful: write it without the research doc open. The prompts are: core idea in 3 sentences;
   what I got wrong at first (see prior.md and mistakes); when I'd use it and when I wouldn't; how it
   connects to things I already know.
3. **Review** with a fresh Agent using `agents.note_review` from `LK config show`. Give it note.md,
   concepts.json (`LK concepts list <slug> --json`), research.md and prior.md, the gate's `weak_concepts`
   (`LK gate <slug>` → `last.weak_concepts`: concepts that scraped through, so check them hardest), plus
   `KIT/docs/authoring/note-rubric.md`. It returns the rubric JSON. Save it to the scratchpad.
4. `LK note record <slug> <review.json>`. The script applies the bar (≥ 8/10, correctness 2/2, nothing at 0),
   writes note-review.md, and on a pass moves the topic to `scheduled` and enrolls the cards.
5. Report: total score, pass/revise, the one or two most useful fixes. On a **pass**, if the topic is a
   node of a learning path (`LK path for-topic <slug>` is not empty), end with: next is `/path <path>`.
   For a path node, the reviewer also checks the note against the node's `outcomes`. On **revise**:
   - correctness < 2 or a concept misunderstood → offer a relearn of those concepts:
     `LK concepts set <slug> <id> --status learning` for each, then `LK topic advance <slug> reteach` and
     `/learn <slug>`.
   - otherwise → ask them to revise the note (in their own words again) and rerun `/note <slug>`.
