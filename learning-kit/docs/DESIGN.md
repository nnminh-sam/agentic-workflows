# learning-kit: system design

Status: v1.1 (2026-11-09, OS-agnostic) · Owner: nnminh-sam · Keep this file current. Every behaviour change
updates the relevant section and adds a row to the **Decision log**.

---

## 1. Problem and goal

The old loop was: agent researches → agent writes a doc → I read it → Q&A → I edit the doc → I never
come back. That gives a good doc and weak memory. Reading feels like learning but is mostly
recognition, and without retrieval and spacing most of it is gone within weeks.

**Goal:** a toolchain where every topic goes through the steps that actually build durable,
usable knowledge, and where the expensive judgement (research, question writing, grading) is
done by Claude while the bookkeeping (state, scores, schedules) is deterministic.

| Learning principle | Where it lives in the system |
|---|---|
| Pre-testing / generation: guess before you're taught | `prior` stage: what I know + 3 questions + 1 prediction, written before research |
| Retrieval practice beats rereading | Gate quiz; ~50% free-recall (explain / predict) |
| Calibration: being sure and wrong is the most valuable signal | Confidence (guess / unsure / sure) on every answer; ⚠ confident-wrong → mistakes log |
| Mastery learning | 80% gate on the current concept set; targeted reteach; fresh items each round |
| Spacing + interleaving | FSRS per item across all topics; review sessions mix topics |
| Variation (recall the concept, not the wording) | Recall items are reworded before each review |
| Elaboration / self-explanation | Own-words note, rubric-reviewed at the same 80% bar |
| Learn by building | `deep` topics have a lab: prediction → experiment → result |
| Sleep consolidation | Max 3 gate rounds per topic per day |

Non-goals: a general LMS, multi-user support, mobile apps, flashcard decks for trivia.

## 2. Architecture

```
 you (editor / terminal / browser)
   │  /learn  /quiz  /note  /recall          ← Claude Code skills (judgement + orchestration)
   ▼
 Claude Code main session ──spawns──► subagents (Agent tool; model/effort from config.agents)
   │                                    collect (sonnet·med) · aggregate / quiz_author / quiz_review /
   │                                    reteach / note_review / reword (opus·med)
   │  shell: lk <cmd>
   ▼
 lk (Python ≥ 3.9, stdlib only)         ← deterministic core: state machine, storage, validation,
   │  ├─ topics.py   state machine + concepts         scoring, gate, FSRS, sessions
   │  ├─ sources.py  global source bank (fetch, dedupe, search)
   │  ├─ quiz.py     item schema, validation, coverage, presentation, auto-grading
   │  ├─ grader.py   free-recall grading via `claude -p --model opus --effort medium --json-schema`
   │  ├─ sessions.py one file per sitting: gate / practice / review
   │  ├─ gate.py     the 80% rule
   │  ├─ fsrs.py     FSRS-4.5 scheduler
   │  ├─ srs.py      cards, due queue, interleaving
   │  ├─ mistakes.py cross-topic mistakes log
   │  └─ server.py   127.0.0.1 HTTP server + JSON API
   ▼
 web/ (vanilla JS, no build)            ← one quiz app for every question type and every mode
   index.html · app.js (TYPES registry) · style.css (light/dark, phone-width)
   ▼
 DATA HOME (plain JSON / JSONL / Markdown, UTF-8 + LF, readable in any editor, diffable in git)
```

**The split rule:** if it needs judgement, it's a skill or subagent. If it must give the same answer
twice (state transitions, scores, schedules, file formats), it's Python. Skills never hand-edit
state files; they call `lk`.

### Locations

| What | Where (`~` = the user's home on every OS) |
|---|---|
| Tool | `learning-kit/` in the `agentic-workflows` monorepo (version-controlled there) |
| Data home | `$LK_HOME`, else the `home` in `~/.learning-kit.json` (written by `lk install --home`), else `~/learning`. `~` and `$VARS` are expanded. |
| Skills (user-level, rendered by `lk install`) | `~/.claude/skills/{learn,quiz,note,recall}` (each has a `.learning-kit` marker) |
| Authoring guides used by subagents | `docs/authoring/*.md` |

`lk install` renders `{{LK}}`, `{{KIT}}` and `{{HOME}}` placeholders in the skill sources into absolute,
forward-slash paths for the machine it runs on. `LK` is the absolute interpreter path plus `lk.py`, so it
doesn't depend on PATH or an activated venv. `DATA` is **not** baked in: skills read it from `lk config path` at
run time, so launching Claude Code with `LK_HOME=<dir>` sandboxes the whole loop (CLI and skills) into that dir. **After editing anything in `skills/`, or moving the repo or
the Python install, re-run `python lk.py install`.** It refuses to overwrite a skill directory it didn't create.

## 3. Data model

```
<home>/
  config.json                 overrides only (defaults: lk/paths.py DEFAULT_CONFIG)
  sources/index.json          [{id S0001, url, norm_url, title, kind, quality 1-3, topics[], fetched, chars}]
  sources/S0001.md            snapshot (header marks it untrusted)
  topics/<slug>/
    state.json                {stage, depth, version, round, relearn, history[], rounds_log[]}
    prior.md                  my words, before research
    research.md               aggregated, every claim cites [S####]
    concepts.json             {concepts:[{id, name, summary, core, source_ids, related, added_in, status, mastered_at}]}
    qa.md                     questions I asked while reading
    quiz.json                 {items:[...]} the topic's question bank (ids q0001…)
    attempts.jsonl            one line per answered item (mode, score, confidence, time)
    reteach/round-N.md        targeted reteach for round N
    lab/README.md, RESULTS.md (deep only)
    note.md, note.vN.md, note-review.json, note-review.md
  sessions/<date>-<mode>-<topic|mixed>-<tok>.json
  srs.json                    {cards:{"<slug>/<qid>": {s, d, due, last, reps, lapses, topic, item_id, concept}}}
  srs-log.jsonl               every review: grade, score, confidence, s, d, due, r_at_review
  mistakes.md / mistakes.jsonl
```

### Question item (shared by every type)

Common fields: `type, concept, prompt (markdown), difficulty 1-3, source_ids[], explanation`, optional `code`.
Type fields: mcq `options, answer`; multi `options, answer[]`; order `steps` (correct order); cloze
`{{n}}` placeholders + `blanks[[accepted…]]` (+`llm_fallback`); predict/explain `reference_answer, rubric[]`.
Import assigns `id, round, added_in, created, reviewed`. Full authoring rules: `docs/authoring/quiz-authoring.md`.

The browser never receives `answer`, `rubric`, `reference_answer`, `blanks` or `explanation`
(`quiz.present`). Option order is shuffled per session with a stable seed.

**Adding a question type:** `quiz.py` (TYPES, validate_item, present, grade_auto or LLM_TYPES) →
`web/app.js` TYPES entry `render(item, saved) → {el, collect, focus?, key?}` → authoring guide → a test.

## 4. Topic state machine

```
 prior ─► research ─► read ─► quiz_gen ─► quiz ──gate pass──► lab (deep) ─► note ─► scheduled
                                  ▲         │                  note (use)     │        │  │
                                  │         │                  scheduled (aware, relearn)│
                                  │         └─gate fail─► reteach ◄───── note: correctness fail
                                  └──────────────────────────┘ ◄────────── scheduled: lapsed concepts
                                                         scheduled ──extend (version+1)──► research
```

Guards (`topics._guard`): research needs prior.md; read needs research.md + target concepts;
quiz needs coverage (≥2 fresh items per target concept, 1 for aware; ≥1 fresh free-recall per core
concept); quiz_gen-after-reteach needs `reteach/round-N.md`; note-after-lab needs `lab/RESULTS.md`;
scheduled-from-note needs a passed note review. Transitions out of `quiz` are **gate-only**.
Entering `reteach` increments `round`. Entering `scheduled` enrolls cards.

Depth controls scope (config `depth.*`): `aware` (3–5 sources, 1–2 items/concept, no note/lab),
`use` (6–10, 2–3, note), `deep` (10–15, 3–4, lab + note).

## 5. The gate (80% of the topic as it stands now)

Measured per gate session, over the **target concepts** (status new / learning / lapsed):

1. overall mean score ≥ `gate.pass_score` (0.8), **and**
2. every **core** target concept has ≥1 free-recall answer scoring ≥ `gate.free_recall_pass` (0.7).

A concept passes on its own when its mean ≥ 0.8 and, if core, rule 2 holds.

- **Pass** → all session concepts `mastered`. Concepts below 0.8 on their own are reported as
  `weak_concepts`. Their low-scoring items enter SRS with grade Again (due tomorrow), and the note
  reviewer is told to check them hardest.
- **Fail** → individually passed concepts are still `mastered`; the rest go to `learning`; stage moves
  to `reteach`, round += 1. The next round only targets what is still missing, with **fresh** items
  (spent items never come back in a gate).
- **Growth**: `extend` bumps `version`. New concepts arrive as `new` with `added_in = version`, so the bar
  follows the topic as it grows. Mastered concepts stay in SRS untouched.
- **Cap**: max `gate.max_rounds_per_day` (3) rounds per topic per day.

Note review uses the same bar: 5 criteria × 0–2, pass at total ≥ 8, correctness = 2, nothing at 0
(the score is computed in `cli.cmd_note`, not by the model).

## 6. Grading

| Type | Grader | Score |
|---|---|---|
| mcq | script | 1 / 0 |
| multi | script | max(0, (right − wrong picks) / #correct) |
| order | script | 1 − inversions / max inversions |
| cloze | script (LLM if `llm_fallback` and not exact) | blanks matched / blanks (NFKC, case-folded, punctuation-trimmed) |
| explain, predict | `claude -p` (opus, medium effort, no tools, JSON schema) | mean of per-rubric-point credit {0, 0.5, 1}; correct at ≥ 0.7 |

The model only judges each rubric point; Python does the arithmetic. Free-recall answers are graded in
one batch at the end of the session (8 per call). Measured: ~4–7 s per batch. "I don't know" and empty
answers score 0 without a model call.

**Invocation (portable):** the instructions, the output schema (as text) and the items all go on
**stdin**. argv holds only short flags, plus `--json-schema <compact JSON>` when it can be passed
safely. Under `grader.json_schema: "auto"` the schema argument is skipped when the resolved command is a
Windows `.cmd`/`.bat` shim (e.g. an npm install), because cmd.exe mangles quoted JSON. The reply is
then parsed from text (`extract_json` tolerates prose and ``` fences). Both paths were verified against the real CLI.

**Failure handling:** grader error → session `grade_failed`, script grades kept, the UI offers retry.
In the terminal: `lk grade <id>` retries, or `lk grade <id> --pending` → the agent grades by hand →
`lk grade <id> --apply file.json`.

## 7. Spaced recall (FSRS-4.5)

- One card per gate-attempted item of a mastered concept, keyed `<slug>/<qid>`. It's seeded at
  enrollment from that item's last gate attempt.
- Grade mapping (`fsrs.grade_from`): score < 0.5 → Again; < 0.8 → Hard; ≥ 0.8 → Hard / Good / Easy by
  confidence (guess / unsure / sure). Again → due next day, lapses += 1, concept → `lapsed`.
- Default weights, desired retention 0.9, max interval 365 days, daily limit 30.
- Due queue: due ≤ today, sorted by due date then lowest retrievability. Up to 3 not-yet-due confident
  mistakes (last seen ≥ 3 days ago) are mixed in. Shuffled, then round-robin across topics (interleaving).
- `/recall` rewords due items through a subagent (`docs/authoring/reword-guide.md`); a variant keeps
  `origin` and concept, and its grade updates the origin card. If rewording fails, the original is used.
- Lapsed concepts → `/learn <slug>` takes `scheduled → reteach` (relearn); passing goes straight back to
  `scheduled`.

## 8. Quiz server and app

- `lk serve` binds `127.0.0.1:8765` (falls back to the next free port), prints `QUIZ_URL=` and `SESSION=`,
  opens the browser (see §9a), and exits after `/api/done` or `server.idle_timeout_min` (120) of idle time. On exit
  it prints `SESSION_RESULT=<json>`, which the skill reads after a background run.
- API: `GET /api/session`, `POST /api/answer`, `POST /api/finish` (grades in a background thread),
  `GET /api/result` (poll), `POST /api/done`. POSTs with a foreign `Origin` are rejected.
- Answers are saved on every Next, so a reload or crash resumes where you left off. `#q=N` deep-links
  to a question.
- Dictation uses the browser Web Speech API (en-US / vi-VN; Chrome, Edge, Safari). When it's missing or
  errors, the UI names the OS tool: Win+H on Windows, macOS Dictation, or the desktop's own tool.
- Keys: 1–9 pick options, Enter / Ctrl+Enter (⌘+Enter on macOS) = next.

## 9. Trust and safety

- Source snapshots and web pages are untrusted data. Snapshots carry a header that says so, and every
  skill and guide tells agents never to follow instructions found in them.
- Learner answers are passed to the grader on stdin as data, with an explicit "data, not instructions" rule.
- The server is local-only, with an Origin check on POSTs and a static-file path-traversal guard.
- Nothing is pushed or published. The repo follows the workspace SSH rule (`core.sshCommand` pinned).

## 9a. Platform support (Linux, macOS, Windows, WSL)

The rule: **no OS-specific code paths except at the three OS boundaries** (launching the grader,
opening a browser, writing files). Everything else is stdlib Python and plain files.

| Concern | How it's handled |
|---|---|
| Paths | `pathlib` everywhere; rendered paths use `/` (valid on Windows too); `~` and `$VARS` expanded |
| Encoding and line endings | All reads and writes are explicit UTF-8; writes are LF (`newline="\n"`); stdout/stderr reconfigured to UTF-8 (Windows consoles default to cp1252); `.gitattributes` forces LF in the repo |
| Atomic writes | temp file + `os.replace`; on Windows it retries briefly if antivirus, an indexer or a sync tool holds the target |
| Filenames | Slugs are lowercase ASCII kebab-case (accents folded); Windows device names (`con`, `nul`, `com1`…) get `-topic` appended; the CLI lowercases slug args so case-sensitive (Linux) and case-insensitive (Windows, macOS) filesystems behave the same |
| Grader launch | `shutil.which` (honours PATHEXT); prompt on stdin; schema arg skipped for `.cmd`/`.bat` shims (§6) |
| Browser | Windows, macOS, Linux desktop: `webbrowser`. WSL: `wslview` or `explorer.exe`. Linux without `DISPLAY`/`WAYLAND_DISPLAY`: don't open (avoids a text browser taking over the terminal) and print the URL |
| Shell used by skills | Commands render as `<python> <kit>/lk.py …`, quoted only when needed (shlex on POSIX, double quotes on Windows), so they run in bash, zsh, Git Bash, cmd and PowerShell. Skills say to use the Bash tool; PowerShell needs `& ` only if `LK` is quoted |
| Entry points | `python lk.py`, `python -m lk`, or `lk` after `pip install -e .` |
| Dictation and shortcuts | Platform-aware hints in the UI (§8) |

Verified 2026-11-09: Windows 11 (Python 3.12, Git Bash and PowerShell) and Ubuntu 24.04 on WSL2
(Python 3.12) pass the full suite and the CLI smoke steps. Python 3.9 grammar was checked statically.
CI (`.github/workflows/learning-kit.yml` at the monorepo root, `working-directory: learning-kit`, path-filtered) runs
Ubuntu, macOS and Windows × Python 3.9, 3.12 and 3.13 on push.

## 10. Failure modes

| Failure | Behaviour |
|---|---|
| `claude` CLI missing / timeout / bad JSON | `grade_failed`, retry or manual grading; nothing lost |
| Page can't be fetched (JS, 403) | `lk source fetch` errors with the fallback: WebFetch → `lk source add --file` |
| Crash mid-write | All JSON writes are atomic (temp file + replace) |
| Crash between grading and applying | Re-running `finish` regrades only missing results. Side effects may duplicate attempt lines (known; see §12) |
| Too few fresh items | `advance quiz` / `session gate` refuse, listing the coverage issues |

## 11. Testing

`python -m unittest discover -s tests` (27 tests; ~3 s; same on every OS). `tests/fake_grader.py` stands in for `claude -p`.
Covers: source dedupe and HTML extraction; validation of every type; auto-grading; answers hidden from the
client; stage guards; gate pass/fail/partial/core rule/weak concepts/round cap; grader failure → manual
grading; extend targeting; FSRS monotonicity and grade mapping; the review cycle with variants and lapses;
the full HTTP flow; skill rendering; portability (reserved and non-ASCII slugs, case-insensitive CLI slugs,
grading without `--json-schema`, shim detection, command quoting, LF atomic writes, `~` expansion, no
terminal browser on headless Linux). Manually verified (2026-11-09): real fetch of a postgresql.org
page, real Opus grading, UI screenshots at desktop and 360 px, light and dark.

## 12. Known gaps and roadmap

- [ ] Idempotent `_apply` (attempt lines can duplicate if the process dies between graded and applied).
- [ ] FSRS weight optimisation from `srs-log.jsonl` once there are ~1000 reviews.
- [ ] `lk stats` HTML dashboard (retention curve, calibration chart: confidence vs accuracy).
- [ ] Concept graph across topics (`related`) used for interleaving and for the note's connections criterion.
- [ ] More types: `spot-the-bug` (code + free text), `diagram` (label parts), `compare` (two-column).
- [ ] Optional git commit of the data home after each session.
- [ ] Source refresh: re-fetch snapshots older than N months and flag diffs that touch cited claims.

## 13. Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-11-09 | Python stdlib only, no build for the web app | Zero install friction; the whole kit is readable in an afternoon |
| 2026-11-09 | Browser quiz app instead of AskUserQuestion | AskUserQuestion is multiple choice only (≤4 options, ≤4 questions per call); free recall, ordering, cloze, code and dictation need a real UI |
| 2026-11-09 | Grade free recall via `claude -p --json-schema`, points not scores | Deterministic arithmetic; the model judges each rubric point only; works headless from the server |
| 2026-11-09 | 80% gate on the *current* concept set, per-concept carry-over | The topic grows over time; retakes target only what is missing |
| 2026-11-09 | Core concepts need a correct free-recall answer | Stops passing on recognition alone |
| 2026-11-09 | Weak concepts on a pass are named, not blocked | Respects the 80% rule; SRS seeds them as Again so they return tomorrow |
| 2026-11-09 | FSRS-4.5 with default weights, per item | Better than SM-2 at the same complexity; per-item so a weak sub-skill isn't hidden by its topic |
| 2026-11-09 | Global source bank, snapshots as Markdown | Shared across topics; citations survive page changes |
| 2026-11-09 | Collectors on Sonnet, everything else on Opus at medium effort | Source hunting is many cheap searches; synthesis, question writing, review and grading need the stronger model. Set in `config.agents` |
| 2026-11-09 | Skill named `/recall`, not `/review` | Avoid clashing with Claude Code's built-in `/review` |
| 2026-11-09 | `explanation` required on every item | A bare "wrong" teaches nothing; the why is the learning moment |
| 2026-11-09 | Data home separate from the tool repo | The tool can be shared; the learning data is personal |
| 2026-11-09 | OS-agnostic: OS-specific code only at the grader, browser and file-write boundaries (§9a) | One codebase for Linux, macOS, Windows and WSL; CI matrix keeps it that way |
| 2026-11-09 | Grader prompt on stdin, not argv | argv quoting differs per OS and shell (PowerShell 5.1 and cmd.exe mangle JSON); stdin is the same everywhere |
| 2026-11-09 | Skills call `<abs python> <kit>/lk.py`, not a PATH command | Works without PATH or venv activation on any OS; `pip install -e .` is an optional extra for humans |
| 2026-11-09 | Moved into the `agentic-workflows` monorepo as `learning-kit/`; CI at the repo root | One repo for the user's agent workflows; GitHub only runs root-level workflows |
| 2026-11-09 | Skills resolve DATA via `lk config path`, not an install-time constant | `LK_HOME` sandboxes (e.g. a testing folder) work end to end, not just for the CLI |
| 2026-11-09 | Python ≥ 3.9 | Oldest still-common system Python (e.g. older macOS CLT and distro installs); nothing newer is needed |
