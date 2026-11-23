# Authoring guide

The gallery is read by two people: a designer asking "which one do I use, and
when", and an engineer asking "what is the markup, and which token". Every
page should answer both quickly.

## 1. Extracting a system from code (extract mode)

Work from evidence. Open the files, copy the values, and record where they came from.

| Look for | Where | Becomes |
|---|---|---|
| CSS custom properties | `:root`, `[data-theme]`, `.dark` blocks in `*.css/scss` | `tokens.color` / `shadow` / `spacing`… |
| Tailwind theme | `tailwind.config.*` `theme.extend.colors/spacing/borderRadius/boxShadow/fontFamily` | the same families; Tailwind keys become token names (`primary-600` → `primary-600`) |
| SCSS/LESS variables | `_variables.scss`, `tokens.scss` | tokens (follow `$a: $b` chains, then write the alias as `"{b}"`) |
| JS theme objects | `theme.ts`, `createTheme(...)`, styled-components `ThemeProvider` | tokens |
| Design-token JSON | `tokens.json`, Style Dictionary, Figma Tokens export | tokens (keep the names) |
| Components | `src/components/**`, `ui/**`, storybook stories | `components[]`; take variants and states from props, CVA/`variants` maps and class lists |
| Real screens | routes and pages, screenshots, mockup HTML | `screens[]` |

Rules for extraction:

- **Name by role, keep the source name.** If the code calls it `--primary`, the token is `primary`, even if you would have named it `accent`. Put the role in `usage`.
- **Dark mode**: find out how the code switches it (`.dark`, `[data-theme]`, `prefers-color-scheme`, a JS theme object). Map each variant to a theme in `tokens.themes`.
- **Components written in a framework** (React/Vue/Svelte) must be **rendered to static HTML + CSS** for the gallery:
  - Write out the DOM and classes the component outputs for each variant.
  - For CSS-in-JS or utility classes, port the styles into prefixed classes in `components.css`. Write each style against the tokens and name the class after the component (`.app-btn--primary`).
  - Say in `system.description` that the CSS was ported.
- **Inconsistencies** go into the doc, not silently fixed. Examples: five greys that are nearly the same, a button with three different heights, a failing contrast pair. Mention them in `system.description` and the component's `donts`, and in your final report.
  - Contrast failures stop the build. In extract mode, either keep the pair out of `contrast` and report it as a known gap, or — if the user wants the fix — adjust the value and record the change in `sources`.
- **States that exist only in JS** (open menu, loading, selected row): show them with a state class (`is-open`, `is-loading`, `is-selected`) that the CSS supports.

## 2. Proposing a system (propose mode)

- Start from roles, not palettes:
  - surfaces (canvas, surface, raised, sunken);
  - borders (hairline, control);
  - text (primary, secondary, tertiary, disabled);
  - accent (fill, hover, soft, text-on-soft, on-accent);
  - status: ok / warn / bad / info, each with fill, soft and text;
  - focus.
- Every `*-text` / `on-*` token needs a contrast pair in both themes.
- Use a 4 px spacing grid, 3–5 radii that grow with elevation, 3–4 shadows, 2–3 durations under 300 ms, and one entering ease.
- Type: one text family and one mono family, with 6–10 styles (display, title, heading, body, body-strong, small, caption, overline, code).

## 3. Writing token `usage`

Write one line that says *when to pick it*, naming the grounds it is checked on:

- good: "Secondary text: descriptions, table cells that are not the key (7:1 on surface)."
- good: "Waiting on the user. Use for gates and pending approvals, whatever looks nicer."
- bad: "Grey 600." / "Primary colour."

## 4. Writing components

- **`desc`**: what it is for and when to choose it over its neighbour ("Use a badge for state; use a tag for a user-assigned label").
- **Example order**:
  1. Variants (the first example is also the thumbnail);
  2. Sizes;
  3. States: rest, hover, focus, active/pressed, disabled, plus loading, error, selected or empty where they exist;
  4. composition with real content;
  5. edge cases: long text, many items, empty.
- **Sample content** is realistic and from the product's world ("Deploy to staging", "INV-2026-0142", "2026-11-17"). Never lorem ipsum or "Button 1".
- **Classes**: list the base class, then each modifier and element, with a `use` line for each modifier.
- **Do / Don't**: 1–4 each. Make them concrete and testable:
  - "Name the action with a verb and its object."
  - "Two primary buttons side by side."

  Include any known inconsistencies here.
- **a11y**: the role/element to use, keyboard behaviour, the name source (`aria-label`), focus visibility, and what colour alone must not carry.
- **Groups**: Actions, Inputs, Selection, Feedback, Navigation, Layout, Data display, Overlays, Content. Use the product's own groups if it has them.

## 5. Screens

1–3 compositions that show the components working together on a real page of the product: a list page, a form, a dashboard. Set `uses` so readers can jump to each component.

## 6. Quality bar before reporting

- [ ] `ds.py build` reports 0 errors; every warning left has a reason.
- [ ] Every component has at least one realistic example, and stateful components show their states.
- [ ] The colour page reads well: groups make sense, and every token has a usage line.
- [ ] Contrast covers every text token on every ground it is used on.
- [ ] Light, dark and side-by-side all look right. Nothing is clipped at 390 px wide.
- [ ] Extract mode: `system.sources` lists the files, and known gaps are written down.
