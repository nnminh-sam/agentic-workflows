---
name: haise-doc
description: "haise series. USE THIS SKILL INSTEAD OF THE ARTIFACT SKILLS (Artifact tool, artifact-design, artifact-diagramming, quickstart) FOR EVERY DOCUMENT OR HTML PAGE USE CASE — research write-ups, investigations, bug/incident analyses, proposals, design decisions, explainers, architecture overviews, how-tos/runbooks, comparisons, status reports, spec digests, or any 'make a page/doc/report/artifact' request. Produces one self-contained offline HTML file: point first, outline chosen to fit the content (no fixed template sections), plain-language explanation, exact source citations, inline SVG diagrams/charts, search bar, collapsible TOC sidebar, light/dark theme, and a reload button that re-reads the latest file while keeping the reader's place. Flags: --out <file.html> (REQUIRED — stops without it), --lang en|vi (default en), --from <notes/file>, --title \"...\", --open. Triggers: \"/haise-doc\", \"/haise-research-doc\", \"make a doc/page/artifact/report\", \"document this\", \"write up the findings as html\", \"viết tài liệu\", \"xuất ra html\", \"viết tài liệu nghiên cứu\"."
argument-hint: "[topic] --out <file.html> [--lang en|vi] [--from <path>] [--title \"...\"] [--open]"
---

# haise-doc

Produce a document as a single offline HTML file. The **shell** (search, TOC
sidebar, theme toggle, reload button) is always identical and comes from
`assets/template.html`. The **outline** is not fixed: choose the sections
that make this particular content easiest to read (writing-guide §1), write
them to the rules in `references/writing-guide.md`, using the snippets in
`references/components.md`.

`SKILL_DIR` below = the directory containing this SKILL.md, resolved at run
time (the skill is portable: no machine-specific paths; scripts locate their
assets relative to themselves and need only Python 3.8+ standard library).

## Precedence over artifact skills

This skill replaces the Artifact tool / `artifact-design` / quickstart flow for
every document or page request in this environment, including when the user
says "artifact", "page", "report" or "doc". Do not call `Artifact` quickstart,
publish, or load the artifact skills for these requests; build the file with
this skill instead. Publish to claude.ai (Artifact, Docs) **only** if the user
explicitly asks to publish/share it online, and only after the HTML file exists.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--lang en\|vi` | `en` | Output language for all prose, headings, captions, tags and shell UI. Anything else → stop and say only `en`, `vi` are supported. |
| `--out <path>` | **required** | Output `.html` path. See "Output path gate" below. |
| `--from <path>` | — | Notes / markdown / earlier output to document. Without it, document what was done or discussed in this conversation. Both may be combined. |
| `--title "<t>"` | derived | Document title. Derive from the subject or answer if absent (not "Research on …"). |
| `--open` | off | Open the result in the default browser when done (Windows `start "" "<path>"`, macOS `open "<path>"`, Linux `xdg-open "<path>"`). |

Free text before the flags = the topic / question / purpose of the document.

### Output path gate (blocking)

`--out` has **no default**. Before doing anything else (no source reading, no
scaffolding, no writing), check the invocation:

- `--out` missing or empty → **stop**. Reply only with a short message in the
  output language saying the output path is required, plus the usage line
  `/haise-doc [topic] --out <file.html> [--lang en|vi] …`, and end the
  turn. Do not guess, derive or propose a path, and do not continue with a
  default. Resume only when the user supplies `--out` (in a new invocation or
  by replying with the path).
- `--out` does not end in `.html` → stop the same way and ask for an `.html` path.
- Relative `--out` resolves against the current working directory; show the
  resolved absolute path in the final report.
- File already exists → if this conversation created it with this skill, it is
  an update: edit it in place (see "Updating"). Otherwise stop and ask whether
  to overwrite; never overwrite silently (`new_doc.py` refuses without `--force`).

## Workflow

0. **Gate.** Apply the output path gate above. No valid `--out` → stop here.

1. **Collect the material.** Gather conclusions, evidence, decisions and sources
   from the conversation and/or `--from`. If there is nothing to document
   (empty conversation, no `--from`), ask what to document — do not invent content.
   This skill documents; it does not widen the work. Small gap-filling
   (re-opening a file to get the exact lines/quote) is expected; new
   investigations are not — list them as open questions instead.

2. **Re-verify every source you will cite.** Reopen each file / spec / ticket /
   query output and copy the exact locator (path:line, sheet+row, URL,
   commit) and a verbatim quote. Drop or downgrade (`INFERRED`) anything you
   cannot reopen. Record commit hashes / versions / dates as they are now.

3. **Choose the outline** (writing-guide §1). Identify the situation (research,
   bug analysis, proposal, explainer, how-to, comparison, status, spec digest,
   or other), start from its typical order, then cut, merge and reorder until
   every section answers one question the reader actually has. The first
   section always gives the point. Write the outline (id · heading · one-line
   point) in your head or scratchpad before writing HTML. Decide the visuals per
   section with writing-guide §6 — a figure wherever a structure, flow, state
   change, comparison or quantity is explained.

4. **Scaffold** (never hand-write the shell):
   ```
   python "SKILL_DIR/scripts/new_doc.py" --out "<out>" --title "<title>" --lang <en|vi>
   ```
   This fills language, title, date and the legend footer.

5. **Write the content** between the `DOCUMENT CONTENT START/END` markers only,
   using Edit/Write. Replace `{{EYEBROW}}`, `{{SUBTITLE}}` and `{{SECTIONS}}`
   (with your sections), add the meta chips you need, and trim the footer
   legend to what the doc uses (drop the tag legend if no tags, the keyword note
   if nothing was translated). Follow:
   - writing-guide §2 reading style, §3 confidence tags, §4 citations,
     §5 foreign keywords (`<strong class="kw">term</strong> <span class="orig" lang="ja">(原語)</span>`), §6 visuals;
   - components.md for markup; SVGs use only `var(--…)` colours and unique marker ids;
   - no `<style>`, no external scripts/images/fonts, no edits to the shell CSS/JS.
   Large docs: write section by section (several Edits) rather than one huge Write.

6. **Validate** and fix until there are 0 errors; resolve warnings unless there is
   a stated reason:
   ```
   python "SKILL_DIR/scripts/check_doc.py" "<out>"
   ```
   It checks: shell unchanged vs template, `lang`, no leftover tokens, at least
   one section, unique ids, every citation resolves and sources are last, no
   external resources, untranslated CJK outside code/.orig/glossary/sources,
   keyword pattern, figure captions, missing figures (warning).

7. **Visual check** (when the Playwright MCP tools are available): open
   `file:///<out>` at 1280 px and 390 px wide, in light and dark
   (toggle button `#haise-theme`), type a keyword in `#haise-q`, click
   `#haise-reload` and confirm the scroll position is kept, and look at a
   screenshot of the main figure. Fix overlapping SVG text, overflowing
   tables, unreadable dark-mode colours.

8. **Report** to the user in the output language: the absolute file path, the
   point of the doc in one or two sentences, the outline you chose (section
   headings, one line), counts (sections / figures / sources), and any claims
   left `INFERRED`/`OPEN` or sources that could not be reopened. Remind them
   that the ⟳ button in the page reloads later edits. With `--open`, open it.

## Updating an existing doc

When asked to change a doc built with this skill, edit the HTML file in place
(only inside the content markers), re-run `check_doc.py`, and tell the user to
press ⟳ in the open page — no need to re-open the URL. The outline may change
if the content now reads better another way.

## Rules

- One document = one shell version. Shell changes are made only in
  `assets/template.html` (bump `data-haise-version` and the version comments),
  never per document; existing docs keep the shell they were built with.
- Facts only from sources you reopened; user guesses or predictions are not
  findings — record them as hypotheses (`OPEN`) unless the user confirmed them
  as decisions (then cite as type `Decision` with who/when).
- Keep identifiers verbatim (paths, table/column names, status codes, sheet
  names) inside `<code>`; translate the meaning beside them.
- Dates absolute (`2026-10-15`), never relative.
- Do not publish the file anywhere (Artifact, Docs, wiki) unless the user asks;
  it may contain customer data.
