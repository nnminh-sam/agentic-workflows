# Writing guide — haise doc

The reader is the user: a developer who wants the **point first**, then a
**detailed, plain-language explanation**, with **every factual claim traceable**
to an exact source and **pictures** wherever a picture beats a paragraph.

## 1. Choosing the outline (no fixed outline)

There is no mandatory section list. Before writing, decide what the reader
needs to *do* with this document, then pick the fewest sections that get them
there in reading order. Write the outline down (section ids + headings + the
one-sentence point of each) before filling it.

### Principles

1. **Lead with the point.** The first section answers "what do I need to know?"
   — the answer, the recommendation, the summary, or the "what you'll build".
   A reader who stops after it must still have the gist. Use `.verdict` for a
   one-to-three-sentence answer and `ol.findings` for the key points when that fits.
2. **One section = one question the reader has.** Name headings after what the
   reader gets ("Why the batch skips pallet orders"), not after document
   genre ("Analysis", "Details").
3. **Order by dependency, then by importance.** Introduce a concept before the
   section that relies on it; otherwise most important first.
4. **Only what earns its place.** Skip any part that would be empty or
   ceremonial. Merge sections that are under ~3 short paragraphs. Split a
   section that needs more than ~5 `h3`s.
5. **Scale with the material.** A short note may be 2–3 sections; a deep
   investigation 8–12. Long dumps (full call chains, raw tables, logs) go in
   `details.more` instead of new sections.
6. **Tail sections only when they have content:** open questions / limits,
   glossary (when keywords were translated or jargon is dense), sources (when
   anything is cited — always last).

### Starting points by situation (adapt, don't copy)

| Situation | A typical reading order |
|---|---|
| Research / investigation finding | Answer & key findings → evidence at a glance → background → one section per mechanism or sub-question → worked example → implications & what to do → open questions → glossary → sources |
| Bug / incident analysis | What broke & impact → root cause in one sentence → how it happens (flow with the broken step) → fix & verification → prevention → sources |
| Proposal / design decision | Recommendation → problem & constraints → options compared (✓/✗ matrix) → chosen design in detail → risks & rollout → open questions |
| Explainer / architecture overview | What it is in one paragraph → the big picture diagram → parts one by one → how a request/flow moves through → edge cases & gotchas → glossary |
| How-to / runbook | What you'll achieve & prerequisites → steps (`ol.steps`) → verify it worked → troubleshooting → reference |
| Comparison / evaluation | Verdict → criteria → side-by-side matrix → per-option notes → when to choose which |
| Status report / summary | Headline (stats tiles) → what changed → what's blocked → next steps |
| Spec / requirement digest | Scope in one paragraph → actors & entities → rules / flows → states → open questions → glossary → sources |

Mix freely: a research doc may need a how-to tail; a proposal may need a
worked example. If the situation fits none of these, design the order from
the principles above.

### Header

`h1` = the topic's answer or subject, not "Research on X" / "Document about X".
`.sub` = one sentence saying what the reader will get. `.eyebrow` = the doc
kind and optional ticket/ref (e.g. "Research · W3_KAI_DEV-206", "Proposal",
"Runbook"). Meta chips: the date chip is pre-filled; add only the chips that
help this doc (question asked, scope repo/branch/commit, audience, status,
overall confidence, owner).

### Numbering & TOC

Sections and subsections are **numbered automatically** by the shell — never
type numbers into `h2`/`h3`. The sidebar TOC is built from top-level
`section > h2` and their `h3`s; give every `h3` an `id` when you want to link to it.

## 2. Reading style (tailored to the user)

- **Point first, everywhere.** Doc starts with the point; each section starts
  with its own one-sentence conclusion, then the explanation.
- **Plain words before jargon.** Explain an entity as what it *is* ("one box
  shipped out the door") before naming its table/class. Then map plain name →
  technical name in a table.
- Short sentences. One idea per sentence. Active voice. No filler intros
  ("In this section we will…").
- **"X in one sentence" callout** for the core mechanism / bug / decision
  (`.callout.bad` for a defect, `.callout.ok` for a confirmed fix, `.callout.warn` for a risk).
- Use **absolute dates** (2026-10-12), never "yesterday" / "last week".
- Numbers carry units. Codes carry their meaning (`110 inspected`, not `110`).
- When something is surprising or easy to misread, say so explicitly
  ("Note: 実績 literally means *result*, but here it is created at allocation").
- Prefer a table when comparing ≥ 3 items on ≥ 2 attributes.
- Hedge only where evidence is actually weak; then tag it `INFERRED`/`SUY RA`.
  Do not hedge verified facts.

## 3. Confidence tags (on every non-trivial factual claim)

| Meaning | en | vi | HTML |
|---|---|---|---|
| Seen directly in the cited source (code read, spec text, query output, repro) | VERIFIED | CHẮC CHẮN | `<span class="tag sure">VERIFIED</span>` |
| Reasoned from evidence, not confirmed end-to-end | INFERRED | SUY RA | `<span class="tag infer">INFERRED</span>` |
| Unresolved; needs someone's answer | OPEN | OPEN | `<span class="tag open">OPEN</span>` |

Rule: a claim with a citation and no tag is read as VERIFIED. Every INFERRED
claim states in a short clause *why* it is inferred (what was not checked).

## 4. Citations (whenever a claim rests on a source)

Inline: put `<a class="cite" href="#src-N">SN</a>` right after the claim it
supports (after punctuation is fine). Several: `…<a class="cite" href="#src-2">S2</a><a class="cite" href="#src-5">S5</a>`.
The shell adds hover titles and "cited in ↩ §n" back-links automatically.

Every source entry in `#sources` has:

1. `id="src-N"` — numbered in order of first citation.
2. `.src-id` (`S1`), `.src-type` badge, `.src-title` (human name of the source).
3. `.src-loc` — the **exact locator**, enough to reopen it in 10 seconds:
   - Code: `repo/path/File.php:120-148` + branch and commit hash (short) + symbol name.
   - Spec / office doc: full file name incl. version, sheet name, row/cell or section number.
   - Ticket: key, comment number/author/date.
   - DB / command: the exact command or query, where it ran (container/env), when.
   - Web: URL, page title, publisher, accessed date (and published date if known).
   - Conversation decision: who decided, date, gate/step name.
4. `blockquote.src-quote` — a **verbatim excerpt** (≤ 8 lines) that supports
   the claim. If the excerpt is in another language, keep the original and add
   `<span class="tr">` with the translation underneath.
5. `.src-note` (optional) — reliability: version freshness, whether the quote is
   from old/ folders, local vs production, etc.

**Never cite from memory.** Reopen each source while writing and copy the
quote and line numbers from what is actually there. If a source can no longer
be opened, either drop the claim or tag it `INFERRED` and say why in `.src-note`.

## 5. Foreign-language keywords

Applies to any keyword whose original is not in the output language
(Japanese, Chinese, Korean; also Vietnamese terms in an English doc, and
vice-versa).

- **Pattern:** `<strong class="kw">translated term</strong> <span class="orig" lang="ja">(原語)</span>`
  → renders as **shipment header** (出荷ヘッダ).
- Use it at the **first mention in each `h2` section** and in the term column of
  tables. Later mentions in the same section: translated term only, not bold.
  (This keeps the doc readable; the search box finds both forms.)
- Translate by meaning in context, not literally. If the literal meaning is
  misleading, explain it once (see §2).
- Proper nouns: romanize/use the official name — **Kai** (貝印).
- Status values, enum labels, file names, sheet names, class/table/column names
  and quoted UI text stay **verbatim** — put them in `<code>` (no bold), and add
  the meaning after: `<code>引当済</code> (allocated)`.
- In SVG diagrams put the original in a `<tspan class="orig">(原語)</tspan>`
  after the translated label.
- Every translated keyword also gets a glossary row.
- vi output: common English engineering terms (deadlock, transaction, batch,
  commit, API) stay in English, not bold, no parentheses.

## 6. Visual evidence

Pick the visual by the question being answered:

| Question shape | Visual |
|---|---|
| What are the parts and how do they relate? | Box-and-arrow entity diagram |
| What happens in what order between actors? | Sequence diagram (lanes + arrows) |
| How does a status/record change? | State machine or before → after cards |
| Where does it break / what is missing? | Flow with the broken step in `--bad` colours |
| How much / how many / trend? | Bar chart (categories) or line chart (time) |
| Which option satisfies which condition? | ✓/✗ matrix table |
| When did things happen? | Timeline (`ol.steps` or SVG) |

Every visual: `<figure class="fig">` + `<figcaption>` that says what to notice
and cites the data's source. Charts and diagrams are inline SVG drawn with the
shell's CSS variables so they work in light and dark themes (see
`components.md`). Never use external images, CDNs or JS chart libraries — the
file must open offline from `file:///`.

## 7. Language of the shell

`--lang en` (default) or `--lang vi` sets `<html lang>`; the shell then
localises its own UI (search placeholder, TOC label, figure/table captions,
theme tooltips). All prose, headings, captions, tags and glossary meanings are
written in the output language. Source quotes stay in their original language
(+ translation).
