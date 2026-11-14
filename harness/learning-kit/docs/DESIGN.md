# learning-kit: system design

Status: v1.4 (2026-11-10, library web app + MCP server) · Owner: nnminh-sam · Keep this file current. Every behaviour change
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
| Goal-first scoping, prerequisites before dependents | Learning paths: intake → reviewed plan → one topic per node, in dependency order (§4a) |
| Don't re-teach what's known (expertise reversal) | Path placement quiz: high score *and* high confidence → the learner may test out |
| Integration and transfer | Capstone nodes and milestone checkpoints that mix earlier topics |
| Sleep consolidation | Max 3 gate rounds per topic per day |

Non-goals: a general LMS, multi-user support, mobile apps, flashcard decks for trivia.

## 2. Architecture

```
 you (editor / terminal / browser)
   │  /path  /learn  /quiz  /note  /recall   ← Claude Code skills (judgement + orchestration)
   ▼
 Claude Code main session ──spawns──► subagents (Agent tool; model/effort from config.agents)
   │                                    collect (sonnet·med) · aggregate / quiz_author / quiz_review /
   │                                    reteach / note_review / reword (opus·med) ·
   │                                    path_plan (opus·high) / path_review (opus·med)
   │  shell: lk <cmd>
   ▼
 lk (Python ≥ 3.9, stdlib only)         ← deterministic core: state machine, storage, validation,
   │  ├─ topics.py   state machine + concepts         scoring, gate, FSRS, sessions
   │  ├─ learnpath.py learning paths: plan contract, versions, derived status, next step
   │  ├─ sources.py  global source bank (fetch, dedupe, search)
   │  ├─ quiz.py     item schema, validation, coverage, presentation, auto-grading
   │  ├─ grader.py   free-recall grading via `claude -p --model opus --effort medium --json-schema`
   │  ├─ sessions.py one file per sitting: gate / practice / review / placement / checkpoint
   │  ├─ gate.py     the 80% rule
   │  ├─ fsrs.py     FSRS-4.5 scheduler
   │  ├─ srs.py      cards, due queue, interleaving
   │  ├─ mistakes.py cross-topic mistakes log
   │  ├─ reader.py   research.md → self-contained HTML page (lk read)
   │  ├─ lock.py     one writer at a time across processes (<home>/.lk.lock)
   │  └─ service.py  the one API every front door calls (§8)
   │          ▲                              ▲
   │   server.py (stdlib HTTP)        mcp_server.py (optional `mcp` SDK, stdio)
   │   lk web · lk serve              lk mcp ◄── Claude Code / any MCP client (§8b)
   ▼          ▲
 web/ (vanilla JS, no build)            ← library (paths, topics, docs, sessions) + one quiz app for every
   index.html · app.js (router, TYPES)     question type and mode; style.css (light/dark, phone-width)
 reader.py → <topic>/research.html      ← research.md rendered as a page: diagrams, steps, callouts (§8a)
   ▼
 DATA HOME (plain JSON / JSONL / Markdown, UTF-8 + LF, readable in any editor, diffable in git)
```

**The split rule:** if it needs judgement, it's a skill or subagent. If it must give the same answer
twice (state transitions, scores, schedules, file formats), it's Python. Skills never hand-edit
state files; they call `lk`.

**One service, several front doors.** `lk/service.py` is the only API the web app and the MCP server call
(the CLI is moving onto it). It validates network input (slugs, session ids, doc paths), never returns an
answer key before grading, summarises the quiz bank as counts (browsing must not spoil fresh gate items), and
takes the data-home lock around writes. Adapters stay a few lines per route or tool, so a rule fixed in the
service holds on every door.

### Locations

| What | Where (`~` = the user's home on every OS) |
|---|---|
| Tool | `learning-kit/` in the `agentic-workflows` monorepo (version-controlled there) |
| Data home | `$LK_HOME`, else the `home` in `~/.learning-kit.json` (written by `lk install --home`), else `~/learning`. `~` and `$VARS` are expanded. |
| Skills (user-level, rendered by `lk install`) | `~/.claude/skills/{learn,path,quiz,note,recall}` (each has a `.learning-kit` marker) |
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
    research.md               aggregated, every claim cites [S####]; may hold lk-steps/lk-sequence/lk-arch blocks
    research.html             derived by `lk read` (never edited; rebuilt from research.md any time)
    concepts.json             {concepts:[{id, name, summary, core, source_ids, related, added_in, status, mastered_at}]}
    qa.md                     questions I asked while reading
    quiz.json                 {items:[...]} the topic's question bank (ids q0001…)
    attempts.jsonl            one line per answered item (mode, score, confidence, time)
    reteach/round-N.md        targeted reteach for round N
    lab/README.md, RESULTS.md (deep only)
    note.md, note.vN.md, note-review.json, note-review.md
  paths/<slug>/
    path.json                 plan {title, goal, learner, nodes[], milestones[]} + record (see §4a)
    intake.md                 the learner's goal, purpose, level, time, prior knowledge (their words)
    plan.md                   the planner's rationale, cited [S####]; read by every later session
    placement.json            {items:[...]} placement bank, concept = node id (ids p001…)
  sessions/<date>-<mode>-<topic|path|mixed>-<tok>.json
  srs.json                    {cards:{"<slug>/<qid>": {s, d, due, last, reps, lapses, topic, item_id, concept}}}
  srs-log.jsonl               every review: grade, score, confidence, s, d, due, r_at_review
  mistakes.md / mistakes.jsonl
  .lk.lock                    the cross-process write lock (empty; lock.py)
  server.json                 {url, pid, home, started} while `lk web` runs; `lk serve` reuses that server
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

Guards (`topics._guard`): research needs prior.md; read needs research.md + target concepts + no broken
visual blocks (`lk read --check`);
quiz needs coverage (≥2 fresh items per target concept, 1 for aware; ≥1 fresh free-recall per core
concept); quiz_gen-after-reteach needs `reteach/round-N.md`; note-after-lab needs `lab/RESULTS.md`;
scheduled-from-note needs a passed note review. Transitions out of `quiz` are **gate-only**.
Entering `reteach` increments `round`. Entering `scheduled` enrolls cards.

Depth controls scope (config `depth.*`): `aware` (3–5 sources, 1–2 items/concept, no note/lab),
`use` (6–10, 2–3, note), `deep` (10–15, 3–4, lab + note).

## 4a. Learning paths (goals too broad for one topic)

A topic is sized for one quiz: 4–12 concepts, 2–5 research facets. "System design" isn't. A **path** is an
ordered DAG of nodes; each node points at one ordinary topic that `/learn` runs unchanged. Code:
`lk/learnpath.py`; skill: `/path`; planner and reviewer rules: `docs/authoring/path-planning.md`.

```
 /path <vague goal>
   intake (AskUserQuestion + one open question) ─► intake.md
   curriculum research (collectors → source bank) ─► planner (opus·high) ─► plan.md + draft
   fresh reviewer ─► learner approves ─► lk path import  (v1)
   placement quiz (optional) ─► learner decides: tested_out / fast-track (depth aware) / learn
   loop:  lk path next ─► learn | resume | review | relearn | checkpoint | expand | complete
```

**Plan vs record.** `path.json` has two kinds of fields. *Plan* fields (`title, goal, learner, nodes,
milestones`) are authored by the planner and only enter through `lk path import <slug> <draft>`, which
validates, bumps `version`, and appends `{added, removed, changed, reason}` to `history`. *Record* fields
(`overrides, placement, checkpoints, reviewed, history, version`) are written by lk only. A draft that
carries them has them ignored (reported as `ignored_fields`).

**Node contract.** `id` (stable across versions, `n01`…), `topic` (slug, unique in the path; an existing
topic is reused and its progress counts), `title`, `why`, `kind` core|elective|capstone, `depth`
aware|use|deep, `prereqs` (node ids), `detail` stub|full, and for `full` nodes `focus` (1–5 research facets)
and `outcomes` (observable). Optional `est_hours`, `source_ids`. Unknown fields are rejected.

**Validation** (`validate_plan`): every prereq points to an *earlier* node, so list order is a learning
order and cycles are impossible; ≤ `path.max_nodes` (15); a capstone has ≥2 prereqs; the first node is
`full`; milestones group ≥2 nodes, each node in at most one. Warnings: more than `full_window`+2 full
nodes (rolling wave: detail only what's next), >6 nodes without milestones.

**Import guards:** `intake.md` and `plan.md` must exist (goal before decomposition; rationale persisted for
later sessions); a re-plan needs `--reason`; removing or re-pointing a node whose topic has started needs
`--force` (topic data is never deleted).

**Node status is derived, never stored**: an override (`tested_out`, `skipped`) wins; else no topic →
`not_started`; topic at `scheduled` → `done`; otherwise `in_progress` (with its stage). Satisfied =
done / tested_out / skipped. A node is *ready* when all its prereqs are satisfied.

**`lk path next`** returns one action, decided the same way every time, so any session resumes correctly:
1. `plan` (no nodes) → 2. `placement` (not taken or skipped) → 3. `review` (a node is done but the plan
wasn't reviewed after it) → 4. `relearn` (a done node has lapsed concepts) → 5. `checkpoint` (every node of a
milestone is satisfied, ≥2 of them learned, no checkpoint yet) → 6. `resume` (first in-progress node) →
7. `expand` / `learn` (first ready not-started node: stub or full) → 8. `complete`.

**Placement** (`lk serve --placement <path>`): items in `placement.json`, `concept` = node id, ≤3 per node,
≤24 in total. Grading is the normal pipeline. The outcome is stored in `path.placement` only: no attempts, no
mistakes log, no topics created. Per node: `test_out` when mean ≥ `gate.pass_score` *and* mean confidence ≥
`path.test_out_confidence` (2.5), `fast_track` when mean ≥ `path.fast_track_score` (0.5), else `learn`. These are
recommendations. `lk path override <path> <node> tested_out` refuses without placement evidence at the bar
(`--force` if the learner insists).

**Checkpoint** (`lk serve --checkpoint <path> --milestone <id>`): up to `path.checkpoint_items_per_node` items
from each learned node's bank, free recall first, interleaved across topics. Answers are logged as attempts
(mode `checkpoint`, so they never spend gate items) and update existing FSRS cards. An Again lapses the concept,
so `next` sends the topic to relearn. The result goes into `path.checkpoints[<id>]`.

**Review after each node.** When a node reaches `done`, `next` asks for a plan review: note review, gate
weak spots, qa.md and mistakes are checked against the remaining nodes. Stubs get expanded to keep
`full_window` nodes ahead. Changes go in as a re-plan with `--reason`, then `lk path reviewed`.

**Topics stay shared.** `lk path for-topic <topic>` gives `/learn` the node context (goal, learner, focus,
outcomes, kind, prereq and unlocked topics). Research facets start from `focus`; capstones research trade-offs
between their prereqs. `/learn` also refuses to silently start a topic that is really a field: it offers a
path, a narrower slice, or an `aware` overview.

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

## 8. Web app (library + quiz)

One app, one JSON API (`lk/server.py`, stdlib `ThreadingHTTPServer`), every route a thin call into `service.py`.

- **`lk web`** runs it long-lived on `127.0.0.1:8765` (next free port if taken), writes `<home>/server.json`,
  prints `APP_URL=`. A second `lk web` for the same data home just prints the running URL.
- **`lk serve`** (what the skills call) keeps its old contract: prints `QUIZ_URL=` and `SESSION=`, blocks
  until the learner presses *Close session* (or `server.idle_timeout_min`, 120, of no activity), then prints
  `SESSION_RESULT=<json>`. If a live `lk web` serves the same home (checked via `/api/health`), it opens the
  session there and waits on the session file; otherwise it starts its own server that exits on close.
- **Views** (hash router, no build): `#/` library (recall due, unfinished sessions, paths, topics, recent
  sessions), `#/path/<slug>` (milestones, node status, the `next` step), `#/topic/<slug>` (stage and next
  step, docs, concepts, quiz coverage, sessions), `#/session/<id>[/<n>]` (the quiz; `n` deep-links a
  question), `#/mistakes`. Docs open as reader pages at `/read/<slug>?file=…` (§8a), rendered on request.
- **What the web app can start:** practice, gate (with a confirm), recall (unreworded; `/recall` rewords
  first) and a due milestone checkpoint. Anything needing judgement (research, quiz authoring, note review,
  planning) shows the Claude Code command instead of a button.
- API: `GET /api/health | overview | paths[/<slug>] | topics[/<slug>] | topics/<slug>/doc?file= |
  sessions?topic=&mode=&limit= | sessions/<id> | sessions/<id>/result | recall | mistakes | sources?q=`;
  `POST /api/sessions {kind, topic|path, milestone, n, limit}`, `POST /api/sessions/<id>/answer | finish |
  done`. Grading runs in a background thread per session; the UI polls `result`.
- Answers are saved on every Next, so a reload or crash resumes where you left off.
- Dictation uses the browser Web Speech API (en-US / vi-VN; Chrome, Edge, Safari). When it's missing or
  errors, the UI names the OS tool: Win+H on Windows, macOS Dictation, or the desktop's own tool.
- Keys: 1–9 pick options, Enter / Ctrl+Enter (⌘+Enter on macOS) = next.

## 8b. MCP server (agents)

`lk mcp` serves the same service layer over MCP on stdio, so Claude Code (or any MCP client) can browse
the learner's data and run a quiz in chat. It uses the official `mcp` SDK (v2, `MCPServer`) as an optional
extra: `pip install -e ".[mcp]"` (Python ≥ 3.10); the core stays stdlib and 3.9. Register it with
`claude mcp add learning-kit -- python <kit>/lk.py mcp`.

| Kind | Name | Notes |
|---|---|---|
| Tools, read-only | `overview`, `get_path`, `get_topic`, `read_doc`, `list_sessions`, `session_results`, `recall_due`, `mistakes`, `search_sources`, `read_source` | `read_only_hint`; source tools label their output untrusted |
| Tools, chat quiz | `start_quiz` (practice / recall), `next_question`, `answer_question`, `finish_quiz` | questions as presented (no keys); grading and side effects as in the browser |
| Resources | `lk://overview`, `lk://paths/{path}`, `lk://topics/{topic}`, `lk://topics/{topic}/docs/{+file}` | doc paths pass the SDK's traversal checks and the service's `.md`-inside-topic rule |

- **Gate, placement and checkpoint stay in the browser.** They change stages and plans, and they need the
  learner's own typed or dictated free recall. `start_quiz` refuses them and names the skill.
- The server instructions tell the agent to pass the learner's words verbatim with their own confidence,
  never answer or hint, and keep correctness hidden until `finish_quiz`.
- Authoring (sources, concepts, quiz import, plan import) is not exposed: it stays on the validated CLI path
  the skills use.
- Remote use (claude.ai, another machine) would mean Streamable HTTP plus auth. Not built; see §12.

## 8a. The reader (research as a page)

Long Markdown paragraphs were tiring to read: a 7-hop request trace written as one 200-word paragraph is
something you skim, not something you can picture. So the learner reads research through `lk read <slug>`,
which renders `research.md` (or `--file reteach/round-N.md`) into one self-contained HTML file next to it and
opens it (§9a: `open_file`, which goes through `wslpath -w` on WSL).

- **The Markdown stays the source of truth.** Agents read it, quiz items cite it, and it reads fine in an editor.
  The HTML is derived and deterministic (`lk/reader.py`, stdlib only), so it never drifts. Edit the .md, then rebuild.
- **Visual blocks** are fenced JSON written by the aggregator: `lk-steps` (an ordered process as numbered
  cards with cost chip, example, citations), `lk-sequence` (actors and messages as an SVG sequence diagram with
  phase bands; labels wrap so the diagram stays readable at full size), `lk-arch` (box-and-arrow flows, 1–3
  panels for before/after with pros and cons). A shared `phase` key gives the same colour everywhere, and hovering a
  step highlights its phase in the diagrams. Spec and writing rules: `docs/authoring/visuals.md`.
- **Callouts** use GitHub alert syntax (`> [!example]`, `misconception`, `interview`, `tip`, `warning`, `tldr`,
  `question`, `note`), so they stay readable as plain Markdown.
- **Page:** concept headings `{#id}` get a core/concept badge plus the concept's `summary` from concepts.json
  ("In short"); `[S####]` become chips that link to the source, show its title on hover, and can be hidden; a
  contents sidebar shows reading time per section; light/dark; phone width (diagrams scroll inside their own box).
- **Check** (`lk read --check`): errors = invalid visual blocks (JSON or schema, with the field path). They block
  `research → read`. Warnings: paragraphs over 110 words, citations not in the source bank, concepts with no `{#id}`
  heading, unknown callout kinds. The aggregator must clear both before handing back.
- **Safety:** research text comes from untrusted pages, so everything is HTML-escaped (no raw HTML passes
  through), and links are limited to http(s) and in-page anchors. The page loads nothing from the network.

## 9. Trust and safety

- Source snapshots and web pages are untrusted data. Snapshots carry a header that says so, and every
  skill and guide tells agents never to follow instructions found in them.
- Learner answers are passed to the grader on stdin as data, with an explicit "data, not instructions" rule.
- The web server binds 127.0.0.1. Every request must carry a local `Host` (127.0.0.1 / localhost / [::1] with
  the bound port), which blocks DNS rebinding; POSTs with a foreign `Origin` are rejected; static files and
  docs are path-contained; routes match on raw path segments, so `%2F` can't split one.
- Concurrency: the web app, `lk mcp` and CLI calls can run at once. Every read-modify-write takes
  `<home>/.lk.lock` (`msvcrt.locking` on Windows, `flock` elsewhere; reentrant per thread). The lock is never
  held across LLM grading or network fetches, and `finish` re-checks the status before applying, so two
  finishers apply side effects once.
- MCP: source snapshots go out marked untrusted; the agent gets questions only as presented.
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
| Two processes write at once | Serialised by `.lk.lock`; a writer that waits > 60 s gets a clear "busy" error |
| `lk web` killed without cleanup | `server.json` stays, but `/api/health` fails, so `lk serve` starts its own server |
| `mcp` SDK missing or Python 3.9 | `lk mcp` exits with the install command; nothing else depends on it |

## 11. Testing

`python -m unittest discover -s tests` (61 tests, 3 of them skipped without the `mcp` SDK; ~8 s; same on every OS). `tests/fake_grader.py` stands in for `claude -p`.
Covers: source dedupe and HTML extraction; validation of every type; auto-grading; answers hidden from the
client; stage guards; gate pass/fail/partial/core rule/weak concepts/round cap; grader failure → manual
grading; extend targeting; FSRS monotonicity and grade mapping; the review cycle with variants and lapses;
the full HTTP flow; skill rendering; portability (reserved and non-ASCII slugs, case-insensitive CLI slugs,
grading without `--json-schema`, shim detection, command quoting, LF atomic writes, `~` expansion, no
terminal browser on headless Linux); learning paths (`tests/test_paths.py`): plan validation (forward edges,
unknown prereqs, duplicate topics, capstones, milestones, size), versioned import with diff and guards, record
fields kept across re-plans, derived status, the full `next` walk, relearn priority, placement flow and the
test-out guard, checkpoint card updates and lapses, CLI round trip, skill rendering; the reader (`tests/test_reader.py`):
Markdown blocks and inline, HTML escaping and link filtering, citation chips and unknown ids, long-paragraph
warnings, each visual kind and its validation errors, shared phase colours, concept badges and anchors, the read-stage
guard, path containment for `--file`, CLI; the web app (`tests/test_kit.py` TestServer): the quiz flow on
per-session routes and the server stopping on close, library routes, doc path containment, Host and Origin
guards, the lock (reentrant, exclusive across threads and processes); MCP (`tests/test_mcp.py`, needs the SDK):
tools and resources over an in-memory client, errors as tool errors, gate refused in chat, a practice quiz
answered and graded in chat, and `lk mcp` launched as a stdio subprocess. Manually verified (2026-11-10):
library, path, topic, session and results views at desktop width and below the 520 px breakpoint; `lk serve`
handing a session to a running `lk web`. Manually verified (2026-11-09): real fetch of a postgresql.org
page, real Opus grading, UI screenshots at desktop and 360 px, light and dark.

## 12. Known gaps and roadmap

- [ ] Idempotent `_apply` (attempt lines can duplicate if the process dies between graded and applied).
- [ ] FSRS weight optimisation from `srs-log.jsonl` once there are ~1000 reviews.
- [ ] `lk stats` HTML dashboard (retention curve, calibration chart: confidence vs accuracy).
- [ ] Concept graph across topics (`related`) used for interleaving and for the note's connections criterion.
- [ ] More types: `spot-the-bug` (code + free text), `diagram` (label parts), `compare` (two-column).
- [ ] Optional git commit of the data home after each session.
- [ ] Source refresh: re-fetch snapshots older than N months and flag diffs that touch cited claims.
- [ ] Tested-out path nodes have no recall cards. Option: enroll the placement items of a tested-out node.
- [ ] Reader: more visual kinds (`lk-compare` side-by-side code, `lk-timeline`), and a "quiz me on this section"
  link from the page to a practice round on that concept.
- [x] Path page (2026-11-10): `#/path/<slug>` lists nodes by milestone with status, prereqs and the next step.
- [ ] Path page as a drawn DAG (edges, not just "after …").
- [ ] MCP over Streamable HTTP mounted at `/mcp` with auth, for claude.ai or another machine.
- [ ] Move the remaining CLI handlers onto `service.py`.
- [ ] Web app: placement quiz button (needs to know a placement bank exists), recall with reworded variants.
- [ ] Concept-level prereqs across topics (a later node's concepts linked to the earlier concepts they build on).

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
| 2026-11-10 | `service.py` as the single API for web + MCP (+ CLI over time) | One place for validation, answer hiding and locking; adapters stay thin |
| 2026-11-10 | Long-lived library app; `lk serve` keeps its contract and reuses a running `lk web` | Browse and quiz in one place without changing the skills |
| 2026-11-10 | Cross-process file lock instead of a single-writer daemon | Web, MCP and CLI write the same files; atomic replace stops torn files but not lost updates. A daemon is overkill for one user |
| 2026-11-10 | MCP via the official SDK as an optional extra, stdio first | Protocol churn is the SDK's job; the core stays stdlib 3.9; stdio needs no running server |
| 2026-11-10 | Agents may run practice and recall in chat, not gate/placement/checkpoint | Those decide stages and plans and need the learner's own free recall in a real UI |
| 2026-11-10 | Browsing shows quiz-bank counts, never prompts | Seeing fresh gate items before the gate would turn recall into recognition |
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
| 2026-11-09 | Learning paths as a layer above topics; each node is an unchanged topic | A broad goal can't pass one quiz; reusing the topic loop keeps one learning method and shares progress across paths |
| 2026-11-09 | Node status derived from topic state, never stored in path.json | Two copies of progress drift; any session can trust the path because it can't disagree with the topics |
| 2026-11-09 | Prereqs must point to earlier nodes | Makes list order a valid learning order and cycles impossible, with an error message a planner can act on |
| 2026-11-09 | Plan fields enter only through a validated, versioned import; record fields only through lk | Agents can re-plan freely without erasing the learner's decisions; every change has a reason in history |
| 2026-11-09 | Intake and plan.md required before import | Vague goals hide the real one; the rationale must outlive the session that wrote it |
| 2026-11-09 | Rolling wave: only ~3 nodes detailed ahead | Early nodes change what later ones should cover; detailing everything up front is wasted and stale |
| 2026-11-09 | Placement recommends, the learner decides; test-out needs evidence at the gate bar and high confidence | Confident-and-right is the only signal worth skipping weeks for; a lucky guess shouldn't skip a node |
| 2026-11-09 | Placement writes no attempts or mistakes | Not knowing what you haven't studied isn't a mistake; it would pollute the mistakes log and recall extras |
| 2026-11-09 | Module named `learnpath.py`, data in `<home>/paths/` | `lk/paths.py` already means file locations |
| 2026-11-09 | Research is read as an HTML page rendered from research.md (`lk read`), with JSON visual blocks | Dense Markdown was tiring to read and processes were narrated, not shown; keeping Markdown as the source keeps agents, citations and editors working, and deterministic rendering keeps styling consistent and testable (the agent chooses what to draw, Python draws it) |
| 2026-11-09 | Broken visual blocks block `research → read`; long paragraphs only warn | A broken diagram is a defect the learner would see; paragraph length is a style call the aggregator should fix but shouldn't stall the loop |
