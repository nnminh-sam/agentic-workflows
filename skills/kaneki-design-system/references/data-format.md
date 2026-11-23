# `.ds.json` and `components.css` format

`assets/starter.ds.json` and `assets/starter.components.css` are complete,
valid examples of everything below (`ds.py init` copies them). When in doubt,
copy their shape.

Text fields marked *(html)* may contain inline HTML: `<code>`, `<b>`, `<a>`. Write
`&lt;` for a literal `<`. Every other text field is plain text.

## Top level

```json
{ "system": {}, "principles": [], "tokens": {}, "contrast": [], "icons": {}, "components": [], "screens": [] }
```

Only `system.name` and at least one component are required. A foundation page
appears in the gallery only when its data exists.

## `system`

| Field | Meaning |
|---|---|
| `id` | slug; names the exported files and the stored theme choice (default: from name) |
| `name` | display name (sidebar, title, breadcrumb) |
| `version` | e.g. `"v2"` |
| `status` | badge on the overview: `Stable`, `Draft`, `Proposed`, `Beta`, `Deprecated` (the colour follows the word) |
| `tagline` | overview H1, e.g. "Every component, one at a time" |
| `description` *(html)* | overview lead: what the system is for, what it was built from, known gaps |
| `date` | `YYYY-MM-DD` the content reflects |
| `lang` | `en` or `vi`, the gallery UI language |
| `prefix` | class prefix, e.g. `k-`; used to check that example classes exist |
| `runtimeVars` | custom properties the app sets inline at runtime (`style={{'--weeks': 6}}`), e.g. `["weeks", "c"]`. Without the entry, CSS that reads them fails the build as an unknown token. Examples must set them inline as the app does. |
| `sources` *(html)* | list of where the content came from (`<code>src/theme.css</code> @ a1b2c3d`) |
| `stage` | `{ "background", "color", "font", "surface", "rootClass" }`: **token names** for the page ground, text, font and card surface that example stages use. Without them, stages are neutral grey. `rootClass` (optional) is a class put on every stage and thumbnail, standing in for the app's `<body>`/root element. Scope ported global resets to it, e.g. `.app-root button {…}`. |

## `principles`

`[{ "title": "…", "body": "…(html)" }]`. Shown as cards on the overview and on their own page. Keep it to 2–6.

## `tokens`

```json
"tokens": {
  "themes": [{ "id": "light", "name": "Light", "scheme": "light" }, { "id": "dark", "name": "Dark", "scheme": "dark" }],
  "notes":  { "colour": "lead text…", "type": "…", "spacing": "…", "radius": "…", "elevation": "…", "motion": "…", "icons": "…", "contrast": "…" },
  "color":   [ { "name": "ink", "group": "Text", "value": { "light": "#101828", "dark": "#eef1f5" }, "usage": "…" } ],
  "shadow":  [ { "name": "shadow-md", "value": { "light": "…", "dark": "…" }, "usage": "…" } ],
  "fonts":   [ { "name": "font-text", "value": "\"Inter\", system-ui, sans-serif", "usage": "…" } ],
  "type":    [ { "name": "body", "family": "{font-text}", "size": "14px", "lineHeight": "20px", "weight": 400,
                 "letterSpacing": "0", "transform": "uppercase", "sample": "…", "usage": "…", "className": "k-body" } ],
  "spacing": [ { "name": "space-2", "value": "8px", "usage": "…" } ],
  "radius":  [ { "name": "radius-md", "value": "8px", "usage": "…" } ],
  "motion":  [ { "name": "duration-fast", "value": "120ms", "usage": "…" }, { "name": "ease-out", "value": "cubic-bezier(.2,0,0,1)", "usage": "…" } ],
  "other":   [ { "title": "Sizes", "tokens": [ { "name": "control-h", "value": "32px", "usage": "…" } ] } ]
}
```

- **Themes**:
  - `id` becomes `[data-theme="<id>"]`, and the first theme is also `:root`.
  - `scheme` (`light`/`dark`) sets `color-scheme`, the gallery chrome colour and the stage fallback.
  - One theme is fine: the switch then has no "side by side".
  - More than two themes is fine too (brands, high contrast).
  - `className` (optional) is put on the stage for that theme. Use it when the product switches themes with a class (`.light-mode`), so CSS such as `.light-mode .auth-screen {…}` still applies.
- **Names**: letters, digits and dashes, with no leading `--`. Keep the source's case: `--cat-Food` is a different property from `--cat-food`. The CSS variable is `--<name>`. Names are unique across all families.
- **Themed families** (`color`, `shadow`): the value is either
  - `{ "<themeId>": value }`: a missing theme falls back to the first theme, with a warning; or
  - a single value for every theme.
- **Aliases**: `"{accent}"` becomes `var(--accent)`, resolved per theme. Refs inside a value work too: `"0 0 0 2px {accent}"`.
- **Colour values**: use hex or `rgb()/rgba()` so contrast can be measured. Other formats (oklch, color-mix) build, but give a warning on any contrast pair that uses them.
- `group` (colour only) splits the colour page into sections, in first-seen order. Typical groups: Surfaces, Text, Accent, Status, Data, Overlays.
- `usage` is required in spirit: a token with no usage gives a warning. See authoring-guide §3.
- **Numbers**: in `spacing`/`radius`/`other` they become `px`; in `motion` they become `ms`.
- **Type**: `family` is a font token alias or a literal stack. `className` is optional: give it when components.css defines a class for that style.
- **Extra families**: any other list under `tokens` (e.g. `"z": [...]`) is emitted to `:root` and shown on the "More tokens" page, the same as `other`.

## `contrast`

```json
[ { "fg": "ink-2", "bg": "surface", "min": 4.5, "themes": ["light", "dark"], "note": "Secondary text on cards." } ]
```

- Each pair is checked in every theme, or only in `themes` if given.
- A pair below `min` is a **build error**, unless the pair has `"knownGap": true`. In extract mode, use that for a failure the product really has: the gallery shows it as failing, and the build gives a warning instead of stopping.
- Use `min` 4.5 for body text, 3 for large text (≥ 24 px, or ≥ 18.66 px bold), and 3 for UI boundaries and icons (WCAG 1.4.11).
- A foreground with alpha is composited over the background. A background with alpha cannot be checked.

## `icons`

```json
"icons": { "className": "k-icon", "viewBox": "0 0 24 24",
           "attrs": { "fill": "none", "stroke": "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round" },
           "set": { "play": "<path d=\"M8 5.5v13l10.5-6.5z\"/>" } }
```

- `set` maps a name to the inner SVG markup. The build makes a `<symbol id="i-<name>">` sprite.
- `className` defaults to `<prefix>icon`. Style it in components.css (size, `flex:none`). It may contain `{name}`, which is replaced by the icon name: `"lucide lucide-{name}"` reproduces lucide-react's classes.
- In examples, write `{{icon:play}}`, or `{{icon:play|k-icon--sm}}` to add classes. This expands to `<svg class="k-icon k-icon--sm" aria-hidden="true"><use href="#i-play"/></svg>`.
- In extract mode, copy the paths from the product's real icon set. Do not draw new ones unless proposing.

## `components`

```json
{
  "id": "button", "group": "Actions", "name": "Button", "status": "Stable",
  "desc": "(html) What it is for and when to use it, 1–2 sentences.",
  "classes": [ "k-btn", { "name": "k-btn--primary", "use": "(html) The one next step in a view." } ],
  "examples": [ { "title": "Variants", "note": "(html, optional)", "wide": false, "html": "<div class=\"dsg-row\">…</div>" } ],
  "dos": ["(html)…"], "donts": ["(html)…"], "a11y": ["(html)…"]
}
```

- `id`: lowercase with dashes, unique across components and screens. The route is `#c/<id>`.
- `group` decides the sidebar section and the overview section, in first-seen order.
- `classes`:
  - plain strings show as chips;
  - if any entry has a `use`, the list shows as a Class/Use table instead;
  - each class should exist in components.css (warning if not).
- **Tokens used** is computed from the CSS. You do not write it.
- **Examples**:
  - The **first** example is the overview thumbnail (shown at 72% of a 520 px box), so make it the most typical one.
  - `wide: true` stacks the themes vertically instead of side by side. Use it for wide things: tables, toolbars, layouts. An example wider than half the page is stacked automatically when it renders.
  - `name="…"` attributes are made unique per stage, so radio groups do not interfere with each other.
  - No `<script>`, no inline `on*=` handlers, no remote `src`/`href`. Show states with state classes.
- `status` is optional and shows as a badge.

### Helper classes for example markup

These come from the gallery and are safe to use in any example. Do not use other `dsg-*` classes.

| Class | Does |
|---|---|
| `dsg-row` | flex row, wraps, 12 px gap; consecutive rows get 14 px between them |
| `dsg-stack` | vertical grid, 12 px gap |
| `dsg-grid` | auto-fit grid, 220 px min column, 16 px gap |
| `dsg-label` | small uppercase caption above a sub-example ("Hover", "Error") |

You may also use inline `style` attributes that reference tokens (`style="max-width:360px"`, `gap:var(--space-4)`).

## `screens`

```json
{ "id": "projects", "name": "Projects page", "summary": "One line for the tile.", "desc": "(html)", "status": "Draft",
  "uses": ["button", "card"], "html": "…full composition…" }
```

- Each screen shows full width, with the themes stacked.
- The thumbnail is scaled to 30% of 1100 px.
- `uses` links back to the component pages.

## `components.css` rules

1. Every selector is built on prefixed classes.
   - Bare element or universal selectors (`body`, `*`, `button`…) leak into the gallery chrome and give a warning. Scope them: `.k-prose p {…}`.
   - `:root {}` blocks that only declare custom properties are fine.
2. Colours, shadows, radii, spacing and durations come from `var(--token)`.
   - A hard-coded colour in a rule gives a warning.
   - A `var(--x)` with no token behind it is an **error**, unless the CSS declares `--x:` itself (component-local variables are allowed).
3. Theme differences live in the tokens, not in `[data-theme=dark] .x {}` rules. Use such rules only for things tokens cannot express.
4. Pair every `:hover`, `:focus-visible` or `:active` with a state class (`.k-btn:hover, .k-btn.is-hover`) so the gallery can show the state statically.
5. No `@import`, no web fonts, no remote `url()`. Data-URI images are allowed.
6. No `</style` anywhere.
