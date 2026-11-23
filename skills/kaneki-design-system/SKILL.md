---
name: kaneki-design-system
description: "Build the design-system document of a product or system as one interactive, offline HTML gallery in the style of the Kaneki design-system gallery: sidebar with filter, foundations pages (colour, typography, spacing, radius, elevation, motion, icons, WCAG contrast, principles), one page per component with live examples rendered in every theme or side by side, show/copy markup, classes, tokens used, do/don't and accessibility rules, assembled screens, and an export page (tokens.css, components.css, tokens.json). Extracts the system from an existing codebase/CSS/mockups, or proposes one from a brief. Takes precedence over haise-doc and the artifact skills for design-system / UI component gallery / style guide / UI kit requests. Flags: --out <file.html> (REQUIRED), --from <path...>, --name \"...\", --prefix <cls->, --lang en|vi, --propose, --open. Triggers: \"/kaneki-design-system\", \"design system document\", \"component gallery\", \"style guide\", \"UI kit page\", \"document the design system of X\", \"tài liệu design system\", \"xây dựng design system\"."
argument-hint: "[system / scope] --out <file.html> [--from <path...>] [--name \"...\"] [--prefix ds-] [--lang en|vi] [--propose] [--open]"
---

# kaneki-design-system

Produce a system's design-system document as **one self-contained HTML file**
that works offline from `file://`. It has the same interaction model as the
Kaneki gallery (`harness/kaneki-harness/app/design-system/gallery.html`):

- a sidebar with a filter box (`/` focuses it). It lists Overview, Foundations, Screens and the component groups.
- a theme switch: each theme, or every theme side by side.
- component pages: live examples, a Show/Copy markup bar, a class table, the tokens used, and do/don't/a11y rules.
- an overview made of scaled live previews.
- an export page with Copy and Download buttons.

The HTML is **generated**: you never write it by hand. You write two source
files beside it, and `scripts/ds.py` checks them and builds the page:

| File | You write | Holds |
|---|---|---|
| `<stem>.ds.json` | yes | system meta, principles, tokens (themes, colour, shadow, fonts, type, spacing, radius, motion, other), contrast pairs, icons, components with example markup, screens |
| `<stem>.components.css` | yes | the system's component CSS, built only from `var(--token)` |
| `<stem>.html` | generated | the gallery |
| `<stem>.tokens.css` | generated | tokens as CSS custom properties, one block per theme |

`SKILL_DIR` = the directory holding this SKILL.md, worked out at run time.
The script needs only Python 3.8+ and its standard library.

Read before writing content:
- `references/data-format.md`: every field of `.ds.json`, and the rules for the CSS.
- `references/authoring-guide.md`: how to take a system from code, how to name roles, and how to write examples, usage notes and rules.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--out <path>` | **required** | Output `.html`. The sources go beside it under the same stem. |
| `--from <path...>` | — | Where the system lives: a repo or folder, CSS/SCSS/Tailwind/theme files, mockup HTML, screenshots, a spec. Several are allowed. |
| `--name "<n>"` | derived | The system's display name. |
| `--prefix <p->` | `ds-` | Class prefix for the CSS. With `--from`, use the prefix the code already uses. |
| `--lang en\|vi` | `en` | Language for the gallery UI and all prose. |
| `--propose` | off | Design a new system from a brief instead of documenting an existing one. |
| `--open` | off | Open the result in the browser when done. |

### Output path gate (blocking)

- `--out` missing → **stop**. Reply only with "the output path is required"
  and the usage line `/kaneki-design-system [scope] --out <file.html> …`.
  Do not guess a path.
- `--out` not ending in `.html` → stop and ask for an `.html` path.
- The sources (`<stem>.ds.json` / `.components.css`) already exist:
  - if this conversation made them, this is an update (see "Updating");
  - otherwise ask before overwriting them. `ds.py init` refuses to overwrite without `--force`.

## Two modes

- **Extract** (default, needs `--from` or a system discussed in the conversation).
  Document the system **as it is**:
  - Every token value, class name and state comes from the source. List the files in `system.sources`.
  - Inconsistencies you find go in `system.description` or a component's `donts`, framed as "known gaps". Do not quietly fix them.
  - No source at all → ask what to document. Do not invent a system.
- **Propose** (`--propose`, or the user asks you to design a new system). Design
  the tokens and components from the brief. Set `system.status` to `"Proposed"`
  and say so in the description. Contrast must still pass.

## Subagents: Sonnet, medium effort, always

You may delegate the component and screen transcription for a large system
(reading TSX/templates and writing `components`/`screens` JSON fragments for
one area each). Every subagent this skill starts **must** be launched with:

```
Agent({ ..., model: "sonnet", effort: "medium" })
```

- Never another model (no Opus, Fable or Haiku) or another effort level, and never omit the two fields: the default inherits the main session's model.
- Never use `subagent_type: "fork"`. A fork always inherits the main model and ignores `model`.
- If a fragment fails `ds.py check`, fix it yourself in the main session, or re-run the same Sonnet/medium agent with the errors attached. Do not escalate the model.
- Keep 3–4 areas at most; split by stylesheet or feature so no two agents document the same component.
- These parts stay in the main session, not in subagents:
  - inventory;
  - token extraction and usage notes;
  - porting the CSS, and its cascade order;
  - deciding which contrast failures are known gaps;
  - merging and de-duplicating fragments;
  - spot-checking subagent findings against the source;
  - the build and the visual checks.
- Give each subagent:
  - the path to `references/data-format.md`;
  - its files;
  - the rendering rules: a desktop-width stage, static states, the icon shorthand and realistic sample data;
  - one output path for a JSON fragment `{components, screens, icons, notes}`.

  Have it reply with only the path, the ids and the notes count.

## Workflow

0. **Gate.** Apply the output path gate. With no valid `--out`, stop here.

1. **Inventory** (extract mode). Find where the design lives with `rg`, not grep or find. For example:
   - `rg -n -- "--[a-z0-9-]+\s*:" -g "*.{css,scss,less,vue,svelte}"` for custom properties;
   - Tailwind `theme`/`extend` in `tailwind.config.*`;
   - theme objects (`createTheme`, `tokens.*`, `theme.ts`);
   - `rg --files -g "**/components/**"` for the component inventory;
   - the CSS for each component's variants, sizes and states.

   Write the inventory down: token families, components grouped, states per component, themes.
   Small gaps are fine to fill by opening one more file. Do not start new design work in extract mode.

2. **Scaffold.**
   ```
   python "SKILL_DIR/scripts/ds.py" init --out "<out>" --name "<name>" --prefix <p-> --lang <en|vi>
   ```
   This writes starter sources. They show the format: replace **all** of the starter content.

3. **Write the tokens** in `.ds.json`:
   - themes;
   - colour roles with `usage`, grouped;
   - aliases as `"{other}"`;
   - shadows, fonts, type scale, spacing, radius, motion, and other families;
   - a `contrast` list covering every text-on-ground pair the system promises (4.5 for text, 3 for large text and UI borders);
   - `system.stage`: the tokens for page background, text colour, font and surface;
   - `notes` for the lead text of each foundation page.

4. **Write `components.css`**:
   - only prefixed classes;
   - only `var(--token)` for colour, shadow, radius and spacing;
   - every pointer or focus state also as a state class (`is-hover`, `is-focus`, `is-active`, `is-open`…) so states can sit side by side.

   In extract mode, port the real CSS into this form. Keep the class names as they are.

5. **Write the components**: one entry per component, grouped (Actions, Inputs, Feedback, Navigation, Layout, Data display, Overlays…). Each entry has:
   - `desc` (what it is for);
   - `classes`, with `use` for each modifier;
   - examples covering variants, sizes and states, with realistic sample content. Use the `dsg-row` / `dsg-stack` / `dsg-grid` helpers for layout and `{{icon:name}}` for icons;
   - `dos`/`donts`, and `a11y` where relevant.

   Then add 1–3 **screens** that put the pieces together, and the principles.
   For a large system you may split this step across subagents by area, following
   "Subagents: Sonnet, medium effort, always" above.

6. **Build and fix** until there are 0 errors. Resolve the warnings, or state why one stays:
   ```
   python "SKILL_DIR/scripts/ds.py" build "<out>"
   ```
   It checks:
   - unknown or alias-broken tokens, and `var(--x)` with no token behind it;
   - hard-coded colours, and bare element selectors that leak into the gallery;
   - classes that are listed or used but not defined;
   - unknown icons, scripts, remote resources and leftover `{{…}}` in examples;
   - duplicate ids;
   - **WCAG contrast for every pair in every theme** (a failing pair is an error).

7. **Visual check.** Use a browser tool if one is available (claude-in-chrome or Playwright). If not, use headless Chrome or Edge:
   `chrome --headless=new --screenshot=<png> --window-size=1400,1000 "file:///<out>#c/<id>"`.
   - Look at the overview, two or three component pages, and the colour and contrast pages.
   - Try light, dark and side by side (`?mode=dark`, `?mode=split`).
   - Check a narrow width. Headless Chrome stops at 500 px, so load the page in a 390 px iframe.

   Fix what is clipped or overflowing, and anything unreadable in dark mode.

8. **Report** in the output language:
   - the absolute path of the HTML and of both sources;
   - counts of components, screens, tokens × themes, icons and contrast checks (`build` prints them);
   - the mode (extract or propose), and the sources used;
   - known gaps or inconsistencies you found in the system;
   - any warnings left on purpose.

   With `--open`, open the file (Windows `start "" "<path>"`, macOS `open`, Linux `xdg-open`).

## Updating

Edit `<stem>.ds.json` / `<stem>.components.css`, then run `ds.py build` again.
The reader reloads the page; the chosen theme mode is remembered. Never edit
the generated HTML or tokens.css: the next build overwrites them.

## Rules

- The gallery chrome lives in `assets/shell.html` (version in `SHELL_VERSION` in `ds.py`).
  - Change the chrome only there, for every system. Never patch one system's output.
  - Chrome classes are `dsg-*` and chrome colours are `--dsg-*`. The system must not use those names.
  - The shell may use only the helper classes listed in data-format.md.
- The page stays self-contained: no CDN, no web fonts, no remote images. Font stacks fall back to system fonts.
- Sample content in examples is realistic and specific (real labels, dates, IDs from the product when extracting). Never lorem ipsum.
- Keep identifiers verbatim: class names, token names, file paths. Dates are absolute (`2026-11-23`).
- Do not publish the file anywhere (Artifact, Docs, wiki) unless the user asks. It may contain product details.
