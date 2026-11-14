---
name: recall
description: "Spaced-recall session across all learning-kit topics: pull the cards due today (FSRS), reword them so I recall the concept and not the wording, mix topics, run them in the quiz app, and reschedule. Triggers: /recall, 'what's due', 'spaced review', 'ôn tập'. (Named recall to avoid clashing with the built-in /review.)"
argument-hint: "[--limit 20] [--no-reword] [--stats]"
---

# /recall: spaced review

```
LK = {{LK}}
KIT = {{KIT}}
```

Commands are for a POSIX shell (Bash tool; Git Bash on Windows). In PowerShell, prefix `& ` if `LK` starts with a quote.

1. `--stats` → `LK stats` and `LK topic list`, summarize in ≤8 lines (due today / 7 days, estimated retention,
   lapses, topics by stage), then stop.
2. `LK srs due --json --limit <limit>`. If it's empty: say when the next card is due (`LK srs stats`) and stop.
3. **Reword** (skip with `--no-reword`): one Agent with `agents.reword` from `LK config show`, following
   `KIT/docs/authoring/reword-guide.md`. Give it the due-items JSON file (save the output of step 2 to the scratchpad).
   It writes `{"items":[...]}` variants, each with `origin` = the card's uid. Then
   `LK quiz validate <variants> --variant`. If some fail, drop them (the original is used instead).
4. `LK serve --review [--variants <file>] [--limit <limit>]` with Bash `run_in_background: true`. Give them `QUIZ_URL=`.
5. When it exits, read `SESSION_RESULT=` and report: n recalled / n, the next due dates (summarize the `srs` list),
   and any **lapsed** topics. For each lapsed topic, suggest `/learn <topic>` (it will reteach the lapsed concepts).
6. Once a month, or when they ask, suggest rereading `mistakes.md` in the data home (`LK config path` → home).
