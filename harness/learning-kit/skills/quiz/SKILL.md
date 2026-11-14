---
name: quiz
description: "Open the learning-kit quiz app in the browser for a topic: the gate quiz when the topic is at its quiz stage, or a no-stakes practice round with --practice. Triggers: /quiz <topic>, 'quiz me on X', 'practice X'."
argument-hint: "<topic> [--practice] [--n 10]"
---

# /quiz

```
LK = {{LK}}
```

Commands are for a POSIX shell (Bash tool; Git Bash on Windows). In PowerShell, prefix `& ` if `LK` starts with a quote.
If the server prints `Open <url> in your browser.` (headless Linux, SSH, WSL without a browser bridge), give them that URL.

1. Resolve the topic slug (`LK topic list --json`, fuzzy match, and ask if it's ambiguous).
2. `LK topic status <slug>`:
   - `--practice`, or the stage is not `quiz`: practice round. `LK serve --topic <slug> --practice --n <n>`.
     Practice never changes the stage or the schedule, but mistakes are still logged.
   - stage `quiz`: gate round. `LK serve --topic <slug>`.
   - stage `quiz_gen` / `reteach`: there are no fresh items yet. Tell them to run `/learn <slug>`.
3. Run it with Bash `run_in_background: true`. Read `QUIZ_URL=` from the output and give it to them.
4. When it exits, read `SESSION_RESULT=` and report in ≤6 lines: score, the 80% bar (gate only), missed
   concepts, confident-wrong count, next step (`/learn <slug>` to continue the loop).
5. If the result shows `grade_failed`, follow "Grading fallback" in the /learn skill
   (`LK grade <id> --pending` → grade → `LK grade <id> --apply <file>`), or retry with `LK grade <id>`.
