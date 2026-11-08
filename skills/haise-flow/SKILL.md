---
name: haise-flow
description: "haise series. Builds a standalone FLOW / WORKFLOW / PIPELINE / ARCHITECTURE diagram page in the archify blueprint style: swimlanes, coloured semantic node boxes with icons, gate / condition labels on orthogonal arrows, dashed loops and optional steps, legend, info cards below, plus the interactive shell (light/dark, visual-style switch, present mode, export PNG/SVG, node passport, search, route probe). One offline HTML generated from a JSON spec (<name>.flow.json kept next to it), so updates edit only the spec. USE THIS INSTEAD OF THE ARTIFACT SKILLS / mermaid pages whenever the deliverable is a diagram page of steps, gates, services, layers or data flow; use haise-doc for prose documents. Flags: --out <file.html> (REQUIRED — stops without it), --lang en|vi (default en), --from <notes/file/code path>, --title \"...\", --open. Triggers: \"/haise-flow\", \"vẽ flow\", \"vẽ sơ đồ kiến trúc\", \"workflow diagram\", \"architecture diagram\", \"flow như wms-ticket-workflow\", \"archify style\", \"sơ đồ step và gate\"."
argument-hint: "[subject] --out <file.html> [--lang en|vi] [--from <path>] [--title \"...\"] [--open]"
---

# haise-flow

Produce a flow or architecture page as a single offline HTML file, in the style of
`wms-ticket-workflow-1.22.4.html`. The **shell** (archify 2.17 CSS + viewer runtime:
toolbar, theme, style presets, present, export, passport, search, route probe) is
fixed and comes from `assets/shell.html`. The **content** — lanes, nodes, edges,
legend, cards — is a JSON spec that `scripts/build_flow.py` lays out and renders.
Never hand-write or hand-edit the SVG; change the spec and rebuild.

`SKILL_DIR` below = the directory containing this SKILL.md, resolved at run time.
Scripts need only Python 3.8+ standard library; `render_check.py` also needs a
local Chrome or Edge.

## Precedence

For any request whose deliverable is a diagram page (workflow, pipeline, steps and
gates, request flow, layered / service architecture, data flow), use this skill
instead of the Artifact tool, `artifact-design`, `artifact-diagramming`,
quickstart, or a mermaid HTML page. A prose document that merely contains a
diagram is `haise-doc`'s job. Publish online only if the user explicitly asks,
after the HTML exists.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--out <path>` | **required** | Output `.html`. The spec is written beside it as `<stem>.flow.json`. |
| `--lang en\|vi` | `en` | Language of every string you write (title, lanes, labels, cards) and of the default legend. Anything else → stop and say only `en`, `vi` are supported. |
| `--from <path>` | — | Source to diagram: notes, a doc, a skill / code directory, an existing diagram (mermaid, archify JSON, image description). Without it, diagram what this conversation established. |
| `--title "<t>"` | derived | Page title: a name ("Order API — Request Architecture"), not a sentence. |
| `--open` | off | Open the page when done (Windows `start "" "<path>"`, macOS `open`, Linux `xdg-open`). |

### Output path gate (blocking)

Before reading any source: `--out` missing/empty or not ending in `.html` → stop,
reply only with a one-line note that the output path is required plus the usage
line `/haise-flow [subject] --out <file.html> [--lang en|vi] [--from <path>]`, and
end the turn. Relative paths resolve against the cwd; report the absolute path. An
existing file not created by this skill in this conversation → ask before
overwriting (the build refuses without `--force`).

## Workflow

0. **Gate** — as above.

1. **Collect the facts.** From `--from` and / or the conversation: the steps or
   components, their real names, who / what performs each, what each arrow
   carries (gate, condition, payload, protocol), loops, optional / conditional
   parts, and hand-offs. This skill diagrams; it does not widen the work — gaps
   become open questions in the report, not invented boxes.

2. **Verify against the source of truth.** Reopen the code / config / doc for every
   name, gate, model, count or condition you will draw (e.g. the trace function,
   the routing table, the controller). Prefer code over prose docs when they
   disagree, and mention the disagreement in the report. Facts you could not
   reopen are left out or marked "(chưa xác minh)" / "(unverified)" in the
   node `detail`.

3. **Design the layout** with `references/spec-format.md` §6: pick lanes (phases or
   layers, 3–5), put each step / component in a column so the main path reads
   left→right then lane to lane, give parallel variants their own row, mark
   conditional steps `optional`, loops `dashed`, and the one main path
   `emphasis`. Box text stays short (label ≤ 16, sublabel ≤ 24 chars); exact
   names and details go to `detail` and the cards. Plan 2–4 cards, one per lane.

4. **Write the spec** to `<out-stem>.flow.json` (format: `references/spec-format.md`;
   starting points: `examples/wms-ticket-workflow.flow.json` for a workflow,
   `examples/order-api-architecture.flow.json` for an architecture).

5. **Lint, then build:**
   ```
   python "SKILL_DIR/scripts/build_flow.py" --spec "<stem>.flow.json" --check
   python "SKILL_DIR/scripts/build_flow.py" --spec "<stem>.flow.json" --out "<out>" [--force]
   ```
   Fix every error. Fix every warning by changing the spec (shorter text, another
   column/row, `route`, `label_seg`) — not by ignoring it; a warning you keep needs
   a stated reason in the report.

6. **Look at it:**
   ```
   python "SKILL_DIR/scripts/render_check.py" "<out>" [--height <px>]
   ```
   It writes `<stem>.visual-check.<W>x<H>.light.png` / `.dark.png` and reports
   console errors. Open both PNGs (raise `--height` until the cards are in view)
   and check: no label covering a line or box, no two lines reading as one, legend
   not clipped, dark mode readable, every lane's story readable without the
   tooltips. Iterate steps 4–6 until clean.

7. **Report** in the output language: the absolute HTML path and spec path, one
   line on what the diagram shows, counts (lanes / nodes / edges / cards), what was
   grouped or left out, facts left unverified, and source disagreements found.
   With `--open`, open the page.

## Updating

Edit the `.flow.json`, rebuild with `--force`, re-run `render_check.py`, and tell
the user to reload the page. Keep node ids stable so deep links (`#node-<id>`)
keep working. To refresh a diagram for a new version of the subject, re-verify
every node against the new source (step 2) rather than editing labels in place,
and write it to a new versioned file when the old one should stay as history.

## Rules

- One shell for every page: shell changes happen only in `assets/shell.html`
  (bump `SHELL_VERSION` in `build_flow.py`); pages are never patched by hand.
- Use only the node kinds, edge variants and card dots listed in the spec format;
  colours come from the shell's tokens, which keeps light/dark/presets working.
- Keep identifiers verbatim (node names, endpoints, table names, gate letters);
  translate the meaning beside them.
- No external resources; the page must work from `file://` offline.
- The shell bundles the archify 2.17.0-dev.1 viewer runtime; keep its
  `<meta name="generator">` line and do not strip its licence comments.
