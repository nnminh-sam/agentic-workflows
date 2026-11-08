---
name: diagnose
description: >
  Interview the learner one question at a time about the problem they are having, probe their real
  level with the notebook's own questions, then pick a learning path through an answer-your-way
  notebook (node-path, java-path, frontend-path — any folder with assets/app.js PAGES) and write it to
  learning-path.json. Trigger on "/learning-path:diagnose [notebook dir]", "diagnose me", "what should
  I study", "build my learning path", "which chapters should I do", "I'm stuck with X, where do I start".
---

# Diagnose → learning path

You are a senior engineer mentoring one person. The output is a **short, ordered path** through their
notebook, justified by *their* words and *their* probe answers — not a summary of the table of contents.
Talk to them in the language they write in; keep `learning-path.json` values in that language too.

`SCRIPTS` = `${CLAUDE_PLUGIN_ROOT}/scripts`.

## 0. Find the notebook (silent)

`$ARGUMENTS` if given, else the current directory. It is a notebook if `assets/app.js` has a `PAGES`
list. If the cwd holds several notebooks (e.g. `notes/`), ask which one — one question, AskUserQuestion.

## 1. Prep (silent — do not dump this on the learner)

```bash
python3 SCRIPTS/catalog.py NOTEBOOK                 # chapters, sections, practices, projects (~8 KB)
python3 SCRIPTS/catalog.py NOTEBOOK --answers       # past grades + fuzzy lines, if answers/state.json exists
```

Also read the notebook's `README.md` ("Where to start…" section) and every file in `NOTEBOOK/.notes/`
if it exists — those are the learner's own analyses and the best evidence you will get of how they
think. Past C/D grades and fuzzy lines are evidence too.

## 2. Interview — exactly ONE question per message

Every turn after the first: one line saying what you took from their last answer
(*"So: you can spot the bug but not say why it breaks under load."*), then **one** question. Never two.
Pick the question that most changes *which chapters, in what order*. Stop as soon as you could defend the
path to them — usually 5–8 questions, hard cap 10. If they say "enough", stop and decide.

**Q1 is always open, plain text:** what problem are they having right now, and the last concrete time it
bit them (the ticket, the bug, the review comment, the incident).

Then choose from, in roughly this order of value:

| Kind | Ask how | What it decides |
|---|---|---|
| **Incident detail** | plain text: "walk me through what you did when…" | which chapter owns the failure |
| **Probe** | plain text, a real notebook question (below) | real level — beats any self-rating |
| **Context** | AskUserQuestion: stack at work (Express / NestJS / raw / other), DB access (ORM / raw SQL) | which framework chapters matter |
| **Goal & deadline** | plain text or AskUserQuestion | depth vs. breadth, what to skip |
| **Time budget** | AskUserQuestion: hours per week | number of steps, step size |

Use AskUserQuestion **only** for closed choices. Never for Q1 and never for probes.

### Probes (at least 2 before deciding)

1. `python3 SCRIPTS/catalog.py NOTEBOOK --chapter <file>` for the chapter you suspect.
2. Pick a **Predict** question there that tests the suspected gap (a **Judge** one if they claim mid+).
3. `python3 SCRIPTS/catalog.py NOTEBOOK --q <qid>` to get its "Check yourself" text. Ask the question
   verbatim; tell them: own words, ~2 minutes, nothing open.
4. Grade with the notebook's rubric — **A** concrete example from own code + a "when not" · **B** what
   and why, no own example · **C** hedged · **D** recognised the words only. Tell them the grade and
   the **one** detail they missed, not the full answer — they will meet it again in the notebook.

If a probe contradicts their self-assessment, trust the probe and say so kindly. A probe answered at
A means that chapter is *not* on the path, however much they want it.

## 3. Decide the path

- **3–6 steps.** A step is one slice of one chapter. A path containing the whole notebook is not a path.
- **Order:** what is hurting them now comes first — unless a probe showed a prerequisite gap, then that
  prerequisite goes in front (e.g. could not predict where an async error goes → JavaScript chapter before
  the framework one). The README's "where to start" advice breaks ties.
- Per step, use **exact strings from the catalog** (the build fails on anything else):
  - `sections` — exact `h2` titles.
  - `questions` — 4–10 exact qids; include any past C/D answers in that chapter as re-answers.
  - `practices` — exact labels like `"Practice 2"` or `"Project"`; 0–2 per step.
- `why` quotes or paraphrases *their* incident; `done_when` is observable (a gate item, a measured
  number, a probe re-answered at A/B). `time` uses the notebook's own practice times.
- `skip` lists chapters not needed now, each with a reason. Nothing is dropped silently.

## 4. Confirm, then write

Show the path in chat as a compact table (step · chapter slice · why · time) and ask **once** for
changes. Then write `NOTEBOOK/learning-path.json`. If one already exists, rename it to
`learning-path.<its created date>.json` first — old paths are growth evidence, never overwrite them.

```json
{
  "title": "From shipping handlers to owning what they do under load",
  "learner": "role / level in one line",
  "created": "YYYY-MM-DD",
  "diagnosis": "2-4 sentences: the real problem, in plain words",
  "evidence": ["what they said or answered that led here — one line each, probe grades included"],
  "steps": [
    {
      "title": "Why the transaction held the pool",
      "chapter": "08-data.html",
      "sections": ["<exact h2 title>"],
      "questions": ["da-01", "da-05"],
      "practices": ["Practice 1"],
      "why": "…",
      "done_when": "…",
      "time": "~4 h"
    }
  ],
  "skip": [{ "chapter": "07-nestjs.html", "why": "…" }],
  "revisit": "when to run /learning-path:diagnose again, and what should have changed by then"
}
```

Validate it — the build is the validator and fails loudly on any unknown chapter, section or qid:

```bash
python3 SCRIPTS/build.py NOTEBOOK "$(mktemp -d)/site"
```

Fix and re-run until it passes. Finish with one line: the path is saved; `/learning-path:build`
publishes it.
