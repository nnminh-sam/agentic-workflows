# P4 · UI shell and read-only screens (M1)

Paths are relative to `D:/workspace/HAISE/agentic_workspace/agentic-workflows/` unless absolute. `DS` = `kaneki-harness-app/design-system/`.
P1 owns the final shapes of `TicketSummary`, `TicketDetail`, `NodeCard`, `DecisionEvent`, `AppError`; this plan only names fields it needs.

## 1. Goal and Done when

**Goal.** Turn `DS/app-mock.html` (static) into a data-driven UI: ticket rail, pipeline strip, node card, inspector, decision feed, top bar, sessions-dock placeholder. It runs first on fixture JSON in a plain browser/Edge, then on the live Host, with no UI code change between the two.

**Done when**
1. `index.html` opened with `?source=fixture` renders every fixture ticket (real `TODO-1`, `TODO-1-2` plus synthetic: gate pending, reviewer FAIL, loop-back g2, lite lane, bug kind, integrity error, empty workspace) with no console errors.
2. M1 parity (plan §7): for every ticket in `todo-nestjs-backend`, `parity.mjs` shows the same ticket ids, `settled/traceLength`, per-node status/verdict, `next` text, tokens total and event count as `sdlc web`'s `/api/tickets` + `/api/ticket` (`harness/web.py:94-97`). Zero diffs.
3. Light, dark and system themes work; all text/surfaces use tokens only (no hard-coded colours).
4. After a `decisions.jsonl` change, the affected ticket row and the decision feed update within 1 s, and unchanged rows are not re-rendered (checked by a DOM mutation counter in a test).
5. Whole UI is operable by keyboard; Ctrl+K palette works with read-only actions.

## 2. Scope

**In:** UI shell (layout, theme, tabs, palette, keyboard), `DataSource` interface with `FixtureSource` and `HostSource`, store, view-model mapping, the 7 screens in §4.3, empty/loading/error states, component gaps in §4.5, UI tests, parity script.
**Out:** gate dialog and any write (P5), session dock behaviour (P6), cost tab content (P7), settings/onboarding (P8). Their entry points are visible but disabled or placeholder (`Run next node`, `Run policy`, `New ticket`, Cost tab, Review-gate palette item).

## 3. Inputs and outputs

**Inputs from other parts (names per `specs/P1-data-contract.md`; P1 owns shapes)**

| From | Name | Used for |
|---|---|---|
| P1 | `TicketSummary` | rail rows, top-bar counts |
| P1 | `TicketDetail` (incl. `nodes: NodeCard[]`, loop-back notes) | pipeline, node card, inspector artifact list |
| P1 | `DecisionEvent` | feed, loop-back note |
| P1 | `AppError` | error states |
| P1 | JSON Schemas in `kaneki-harness-app/contracts/`, fixtures in `kaneki-harness-app/fixtures/` | validation in tests, `FixtureSource` data |
| P3 (Host) | `invoke("list_tickets")`, `invoke("get_ticket", {id})`, `invoke("read_artifact", {ticket,name})`, `invoke("open_in_editor", {path,line?})`, `invoke("get_workspace")` | `HostSource` |
| P3 (Host) | events `ticket-changed {id}`, `tickets-changed`, `decision-appended {ticket, event}`, `host-error` | live updates |
| P2 | Scaffold that serves `ui/` and exposes `invoke`/`on` (Wails `window.go.main.App.X` + `runtime.EventsOn`) | the one adapter file `host-bridge.js` |

Exact IPC names are P1's catalogue; the names above are what P4 assumes (ASSUMPTION) and `HostSource` is the only file to edit if they differ.

**Outputs provided**
- `kaneki-harness-app/ui/` (static files, no build step) that P2 packages.
- Public UI hooks for later parts: `registerPaletteAction({id,title,group,enabled,run})`, `registerTab({id,title,render})`, `store.selected` (ticket, node), a `<Dock/>` slot (P6), a `<GateBanner onReview>` callback prop (P5), a Cost tab slot (P7), `<Toast>` helper (P5/P6).
- `ui/tools/parity.mjs`, `ui/tools/snap.mjs` (screenshots), fixtures loader.
- New design-system components (§4.5) added to `DS/components.css` + `DS/gallery-data.js`.

## 4. Design

### 4.1 UI technology: recommendation

**Preact + htm, vendored as two small files, no bundler.** Alternatives considered:

| Option | Size | New-to-desktop fit | Verdict |
|---|---|---|---|
| Plain ES modules + helpers | 0 KB | Fine for a tiny UI, but we have 7 screens with selection, filters, live partial updates; hand-written DOM diffing becomes the project | Reject (grows into a bad framework) |
| **Preact 10 + htm** | ~4 KB + ~1 KB gz | JSX-like templates in plain `.js`, no Node build, works from `file://` and in Wails | **Recommend** |
| Lit / Svelte / Solid | 5-15 KB; Svelte/Solid need a compiler | Compiler = a build step to learn | Reject for M1 |
| React | ~45 KB+, build step | Heavier start-up, no benefit | Reject |

Why: the DS is plain CSS (`DS/components.css`, `.k-*`), so components just emit the existing markup; Preact's keyed diff gives "re-render only changed rows" for free; no build means `ui/` is the same folder in dev, tests and release; Node 22 is on the machine only for tests. The mock's own script is plain inline JS (`DS/app-mock.html:130-178`), so the move from it is small. Use JSDoc `@typedef` copied from P1 schemas for editor hints; no TypeScript.
Vendoring: put `preact.module.js`, `hooks.module.js`, `htm.module.js` in `ui/vendor/` (downloaded once by a developer, version pinned in `ui/vendor/VERSIONS.txt`). ASSUMPTION: P2 does not impose a bundler; if it does (Vite), the same files work unchanged.

### 4.2 App structure

```
kaneki-harness-app/ui/
  index.html              links ../design-system/tokens.css + components.css (copied or symlinked by P2), icons.js, main.js
  main.js                 picks source (host if bridge present, else fixture), mounts <App/>
  host-bridge.js          the ONLY stack-specific file: invoke(name,args), on(name,cb)  [Wails | browser stub]
  sources/
    source.js             DataSource JSDoc interface
    fixture-source.js     reads fixtures/*.json via fetch; replays a scripted event sequence for demos/tests
    host-source.js        invoke()/on() wrapper
  store.js                tiny store: state + subscribe + actions; selectors memoised
  model/                  PURE functions contract -> view model (unit-tested)
    ticket-row.js  pipeline.js  node-card.js  feed.js  format.js
  components/             Preact components, one file per DS component group
  screens/                rail.js  topbar.js  pipeline-view.js  inspector.js  decisions-view.js  dock-placeholder.js
  palette.js  keys.js  theme.js
  tools/parity.mjs  tools/snap.mjs
  tests/*.test.mjs
```

**DataSource interface** (identical for both implementations)
```
listTickets(): Promise<{project, tickets: TicketSummary[]}>
getTicket(id): Promise<TicketDetail>
readArtifact(id, name): Promise<{text, truncated}>
openInEditor(path, line?): Promise<void>
subscribe(handlers: {onTicketChanged(id), onTicketsChanged(), onDecision(id, event), onHostError(e)}): unsubscribe
```
`FixtureSource.subscribe` accepts a script (`[{afterMs, kind, payload}]`) so tests and demos drive "live" behaviour deterministically.

**State store** (`store.js`, plain object + `subscribe`, one `useStore(selector)` hook):
`workspace`, `tickets: Map<id, TicketSummary>`, `order: id[]`, `details: Map<id, TicketDetail>` (loaded lazily for the selected ticket, plus any ticket the decision feed needs), `selected: {ticket, node}`, `railFilter {text, status}`, `feed: DecisionEvent[]` (workspace-wide, capped, see §4.6), `artifact: {key, text, loading, error}`, `theme: 'light'|'dark'|'system'`, `tab: 'pipeline'|'cost'|'decisions'`, `loading|error` flags per resource. Selected ticket/node/tab/filter/theme persist in `localStorage` (try/catch).

**Update flow** (D4, no polling): on `ticket-changed {id}` → `getTicket(id)` (and a row-level refresh via `listTickets` only when `tickets-changed`) → replace that map entry → only components whose selector output changed re-render. Debounce 150 ms per ticket id to absorb bursts. On Host reconnect/start: full `listTickets()` once. The old board polls every 2.5 s and re-sends everything (`harness/web_page.html:194-212`); we do not.

**Routing / tabs.** No URL router. Selection lives in the store; `Pipeline | Cost | Decisions` tabs use `.k-tabs` (`DS/app-mock.html:~54`). Cost renders a "coming in a later release" empty state until P7 registers it.

**Keyboard model** (`keys.js`, one `keydown` listener): `Ctrl+K` palette; `Esc` close palette / clear filter; `J/K` or `Up/Down` move in rail when rail focused; `Enter` select ticket; `Left/Right` move node selection in the pipeline (roving tabindex); `Ctrl+1/2/3` tabs; `Ctrl+\` toggle theme cycle; `/` focus rail filter. Shortcuts are ignored while typing in inputs.

**Command palette (M1, read-only).** Reuses `.k-palette*` (`DS/app-mock.html:~120-140`). Actions: Go to ticket X (one per ticket, fuzzy match), Go to node X of selected ticket, Open STATUS.md in Zed, Open ticket folder, Open selected artifact in Zed, Switch tab, Theme light/dark/system, Reload data. Disabled placeholders with hint "available in M2/M3": Review gate, Run next node, Reopen a node, Stop session. Registry via `registerPaletteAction` so P5/P6 add theirs. Fuzzy match = simple subsequence score, ~30 lines, no library.

**Theme.** `theme.js` sets `document.documentElement.dataset.theme`. Tokens already define `[data-theme="dark"]` and a `prefers-color-scheme` fallback guarded by `:root:not([data-theme="light"])` (`DS/tokens.css:52`, `:102-103`). "System" = remove the attribute (so the media query applies); light/dark = set it. Use the existing `.k-segmented#theme` control (`DS/app-mock.html:~25`).

### 4.3 Screens for M1 and the DS components they use

Layout is the DS grid: `.k-app` with areas `top / rail main insp / dock` (`DS/components.css:369-370`; sizes `--rail-width:260px --inspector-width:360px --dock-height:188px`, `DS/tokens.css:177-179`).

| Screen | DS components (class names verified in `DS/components.css`) | Data and rules |
|---|---|---|
| **Top bar** | `.k-topbar .k-brand .k-breadcrumb .k-cmdk .k-pill--attention .k-segmented` | Breadcrumb = workspace project name. Pill = count of tickets that are waiting on the user (see "waiting on you" gap below); click selects the first such ticket and its node. Cost placeholder text `Today –` with `title="Cost arrives in a later release"` (P7 fills). Hidden pill when count is 0. |
| **Ticket rail** | `.k-rail .k-rail__head .k-rail__list .k-ticket (.is-selected) .k-ticket__head/__count/__title/__who .k-dot--* .k-segments (i.is-ok / is-run) .k-input-wrap .k-segmented` | Row: dot by state (attention = waiting on you, run = in progress, ok = all settled, bad = integrity problem, idle = none started), `settled/traceLength`, title, 12-segment strip from `strip[]`, "who" line from `next`. Filter: text box + status chips (All / Waiting on you / Running / Done). Sort: waiting-on-you first, then `updatedAt` desc (same as `board.list_tickets`, `harness/board.py:101`). "New ticket" button disabled until P8. |
| **Pipeline strip** | `.k-pipeline .k-node (--done/--run/--attention/--fail/--stale, .is-selected) .k-node__dot .k-node__gen .k-edge(--done) .k-loopnote` | One `.k-node` per `NodeCard`. State map: `done`→done, `in_progress`→run (or attention when waiting on user), `stale`→stale, `pending`→default, verdict `FAIL`/`rejected`→fail. `k-node__gen` shows `gN` when `generation > 1` (board exposes `generation`, `harness/board.py:37`). Loop-back note: for the latest `gate` event with a `reopen`/`stale` list (`contracts/decision-event.schema.json` gate branch requires `node, stale`), render `.k-loopnote` "node gN rejected -> X reopened - time". Arrow-key roving focus. |
| **Node card** | `.k-card .k-card__head/__title/__body .k-badge--outline .k-banner--attention .k-table .k-tier(--sonnet/…) .k-verdict(--pass/fail/pending) .k-chips .k-chip` | Title, `generation N` badge, optional banner "Gate waiting for you since …" (when waiting on user), units table: unit, tier·model, verdict, tokens, rounds (`units`, `unitsDone`, `models`, `tokens`, `attempts`, `lastUnitVerdict` exist in `harness/board.py:36-42`; per-unit tier/verdict/rounds per unit need P1 to expose them, see §3 risks). Artifact chips from `artifacts[]`; missing files render disabled with strike-through (as the board does with `.chip.missing`, `harness/web_page.html:135`). Chip click = preview in inspector; `Ctrl+click` or inspector button = open in Zed. |
| **Inspector** | `.k-inspector .k-inspector__head/__body .k-code .k-btn .k-badge .k-empty .k-skeleton` | Header: file name, `gN` badge, "Open in Zed" button -> `openInEditor(path)`. Body: artifact text in `.k-code` (mono, `white-space:pre-wrap`), read through `readArtifact` (Host/board cap is 512 KB: `harness/web.py:26`; show "truncated, open in Zed" note). Light Markdown colouring only: `## ` lines -> `.t-h`, `OK/PASS` -> `.t-ok`, `NG/FAIL` -> `.t-bad`, dim path:line -> `.t-dim` (token classes used at `DS/app-mock.html:~100`); text is always escaped. No Markdown renderer in v1 (D9). Empty state "Pick a file". |
| **Decision feed** | Decisions tab: `.k-events .k-event(__time/__who)`; dock right column: `.k-log` with `.is-accent/.is-ok/.is-bad` | Dock log = live workspace-wide last 50 events, newest first, with the green "live" dot (`.k-dot--run`). Decisions tab = selected ticket's full list, grouped by day, with filter by event kind and actor (controller / user). Event text built like `board._event_text` (`harness/board.py:143-153`): `event`, node, unit, verdict, actor, stale, note/reason. Hash-chain integrity problems show a `.k-banner--bad`. |
| **Sessions dock (placeholder)** | `.k-dock__head .k-empty` | Left dock column: "Sessions" header + `.k-empty` "Session supervision arrives with M3". Exports `<Dock>` slot for P6. |
| **States** | `.k-skeleton`, `.k-empty`, `.k-banner--bad`, `.k-error`, `.k-spinner` | Loading: skeleton rows in rail, skeleton card. Empty workspace: rail `.k-empty` "No tickets yet. Start one with /kaneki-flow" (matches board wording, `harness/web_page.html:117`). Error per ticket (`TicketSummary` with `error`, as `summarize_ticket` returns `{ticket, error}` at `harness/board.py:83`): rail row shows a warn dot + message; detail shows `.k-banner--bad`. Host down / harness not found: full-width `.k-banner--bad` with "Retry". |

**"Waiting on you" gap (important).** Nothing in the board JSON says a ticket is waiting on the user; `next` is only a sentence, and the "your approval" wording is built inside `next_hint` (`harness/board.py:71`). The strings are not a contract. Required from P1: a structured field on `TicketSummary`, e.g. `waitingOn: "user" | "gate" | null` plus `waitingNode`, computed from the same condition (`planned units all ok`, or in_progress with no units, and node `boundary == "user"`, `lifecycle.json:169` shows a `user` boundary node). P4 uses that field; until P1 lands, a fixture-only fallback parses `next` (ASSUMPTION, marked TODO and removed before M1 done).
Also: the real data cannot show this state. `sdlc tickets` on the real workspace returns two tickets, both fully settled 12/12 with `nextPending: null` (TODO-1 shipped, TODO-1-2 "done: ship with /kaneki-ship"), so the attention/gate UI must be proven with synthetic fixtures.

### 4.4 View-model layer (the part worth unit-testing)

Pure functions in `ui/model/`, no DOM:
- `rowModel(summary) -> {id, state, dotClass, count, title, segments[], who, sortKey}`
- `pipelineModel(detail) -> {nodes:[{node, cls, genLabel, icon, selected}], loopNote|null}` (loop note from latest gate event with `stale`)
- `nodeCardModel(card, events) -> {title, genBadge, banner|null, units[], artifacts[]}`
- `feedModel(events) -> rows[]`, `eventText(e)` (port of `board._event_text`)
- `format.js`: UTC time `HH:MM`, relative age, token count (`1,112,295` -> `1.1M`), path shortening.
Each takes P1 types and returns plain objects; components only map them to markup. Contract changes then touch `model/` only.

### 4.5 Component gaps and how they get added

Gaps the screens need that `DS/components.css` / `gallery-data.js` do not have (checked by class list; `rg "k-" DS/components.css`):

| Gap | Need | Proposed class | Notes |
|---|---|---|---|
| Rail filter chips | status filter in rail | reuse `.k-segmented` (small) | no new class if it fits 260 px; else `.k-filter` |
| Waiting-on-you ticket variant | row highlight | `.k-ticket--attention` | left accent bar, uses `--warn` tokens |
| Missing artifact chip | disabled chip | `.k-chip.is-missing` | strike-through, `--ink-disabled` |
| Virtual list container | feed, long ticket lists | `.k-vlist` (+ `__spacer`) | pure layout helper, no visuals |
| Day separator in events | group by date | `.k-event-day` | overline style |
| Integrity/unknown-ticket row | error row in rail | `.k-ticket--error` | uses `--bad-soft` |
| Truncation notice | inspector "truncated" | reuse `.k-callout--info` | none |

Process for each: (1) add CSS to `DS/components.css` (tokens only, light+dark verified), (2) add a `gallery-data.js` entry with example HTML, classes list and do/don't (pattern at `DS/gallery-data.js:9-22`), (3) run `python DS/build_tokens.py` (contrast check) if any token is touched, (4) open `gallery.html` in both themes, (5) only then use it in `ui/`. The UI never ships a class that is not in the gallery. P4 does not edit `tokens.json` unless a new token is unavoidable.
Note: `.k-dot--run` has an infinite CSS animation (`DS/components.css:135`) and `.k-spinner` too (`:166`); with many tickets this is many compositor animations. Rule: at most the selected/visible rows animate (`animation-play-state: paused` on rows outside viewport is unnecessary; instead cap to run-state tickets only), and `prefers-reduced-motion` already disables all (`DS/components.css:14`).

### 4.6 Performance

- **Row-level updates.** Preact keyed lists (`key = ticket id` / node name / event `at+prev`); components wrapped with `memo` and fed primitive/frozen view-model objects; selectors return the same reference when unchanged, so an event for TODO-2 never re-renders TODO-1's row. Test: mutation counter (§6).
- **Decision lists.** Dock log capped at 50 in memory. Decisions tab can hold 100+ events per ticket (TODO-1-2 has 106 lines: `.sdlc/tickets/TODO-1-2/decisions.jsonl`); render via a small windowed list (fixed row height 36 px, render visible +/- 10, `.k-vlist`) above 200 rows; below that render all (simpler, fine). Append-only: new event prepended, scroll position preserved when the user is not at top.
- **No per-frame timers.** No `setInterval`/`requestAnimationFrame` loops. Relative "5 min ago" labels refresh on a single 60 s `setTimeout` chain only while the window is visible (`visibilitychange`). Paused when hidden.
- **Detail loading.** Only the selected ticket's `TicketDetail` is fetched; others come from `TicketSummary`. Artifact text fetched on click, cached by `(ticket,name,mtime)` for the session, max 5 entries.
- **Targets** (plan §6): UI adds < 20 MB to the WebView2 total; first paint < 300 ms of UI work after the Host is ready (measure with `performance.mark` in `main.js`, printed in a debug footer behind `?perf`).
- Do not parse `decisions.jsonl` in the UI; the Host/harness delivers parsed `DecisionEvent`s (P1/P3).

### 4.7 Accessibility basics

- Landmarks: `header`, `nav` (rail), `main`, `aside` (inspector), `section` (dock) already match the mock's elements.
- Focus order: top bar -> rail -> tabs -> pipeline -> node card -> inspector -> dock. Pipeline and rail use roving tabindex + arrow keys; visible `:focus-visible` ring already in DS (the gallery shows `is-focus` states, `gallery-data.js:16`).
- Every icon-only button has `aria-label`; the mock already does (`DS/app-mock.html:~25`, `Filter`). Status is never colour-only: dots paired with text ("Waiting on you", "Running"), nodes carry icon + label.
- Live regions: the "gates need you" pill and toasts use `aria-live="polite"`; the decision log is `role="log"` with `aria-live="off"` (too chatty).
- Palette: `role="dialog" aria-modal`, focus trapped, Esc closes, focus returns to the trigger.
- Contrast: tokens are already checked by `DS/build_tokens.py`; P4 adds a test that no component CSS uses a literal colour (`rg "#[0-9a-f]{3,6}" ui/ DS/components.css` must be empty except `tokens.*`).
- Respect `prefers-reduced-motion` (already global in `components.css:14`). Min click target 28 px for icon buttons (`.k-btn--sm`).
- Vietnamese text: all text nodes escaped and rendered with the DS font stack; no string slicing by bytes (note: board slices with `[:160]`, `harness/board.py:150`, fine for chars).

## 5. Tasks

| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P4-T01 | Create `ui/` skeleton: `index.html` linking DS css/icons, vendored Preact+htm, `main.js` mounting empty `<App/>` with the `.k-app` grid | S | P2 scaffold (or none: runs from `file://`) | Opens in Edge, shows empty grid in light and dark |
| P4-T02 | `host-bridge.js` + `DataSource` interface JSDoc + `?source=` switch | S | P2 (bridge call names) | Browser stub and one stack bridge both resolve `invoke` and `on` |
| P4-T03 | `FixtureSource` (fetch fixtures, scripted event replay) | M | P1 fixtures + schemas | Loads all fixtures, replay emits events on schedule |
| P4-T04 | `store.js`, selectors, localStorage persistence, debounce | M | T02 | Unit tests for actions and selector identity pass |
| P4-T05 | `model/` pure functions (`rowModel`, `pipelineModel`, `nodeCardModel`, `eventText`, `format`) + tests | M | P1 types | Tests pass on real + synthetic fixtures, `eventText` matches `board._event_text` on all events of TODO-1-2 |
| P4-T06 | Top bar + theme control + Ctrl+K opener | S | T01, T04 | Theme light/dark/system works, persists, no flash on load |
| P4-T07 | Ticket rail (rows, filter, sort, states) | M | T04, T05, gap CSS | All fixture states render; filter works; keyboard nav works |
| P4-T08 | Pipeline strip + loop-back note + node roving focus | M | T05, T07 | g2 badges and loop note appear for the loop-back fixture; arrows move selection |
| P4-T09 | Node card (units table, banner, artifact chips) | M | T05, T08 | Matches the board's node table values for TODO-1-2 |
| P4-T10 | Inspector (artifact preview, light colouring, truncation, Open in Zed) | M | T02, T09 | Previews `run-report.md` fixture; Open in Zed calls `openInEditor` |
| P4-T11 | Decision feed: dock log + Decisions tab, windowed list, filters | M | T05 | 500-event fixture scrolls smoothly; new event prepends without losing scroll |
| P4-T12 | Palette (registry, fuzzy match, read-only actions, disabled placeholders) | M | T04, T07 | All M1 actions work; placeholders show hint |
| P4-T13 | Component gaps: CSS + gallery entries (`.k-ticket--attention`, `.is-missing`, `.k-vlist`, `.k-event-day`, `.k-ticket--error`) | M | none | Gallery shows each in light and dark; `build_tokens.py` still passes |
| P4-T14 | Empty/loading/error states and Host-down banner | S | T07-T11 | Each synthetic fixture (empty workspace, integrity error, host down) renders its state |
| P4-T15 | Live update wiring on `HostSource` (events -> refresh, debounce, reconnect full reload) | M | P3 events | Touching a `decisions.jsonl` updates rail + feed < 1 s |
| P4-T16 | `tools/parity.mjs`: compare Host/fixture view with `sdlc web` JSON for every ticket | M | P1, P3 | Zero diffs on `todo-nestjs-backend` |
| P4-T17 | Tests: DOM render tests, mutation-count test, a11y checks, screenshot script `snap.mjs` (headless Edge) | M | T07-T12 | `node --test` green; screenshots saved for 6 key states x 2 themes |
| P4-T18 | A11y pass: focus order, aria labels, live regions, palette focus trap | S | T12 | Manual keyboard-only walkthrough passes (checklist in tests/README) |
| P4-T19 | `?perf` marks, memory check in WebView2 via P2 harness | S | T15 | Numbers recorded against plan §6 targets |

Critical path: T05 -> T07 -> T08 -> T09 -> T15 -> T16.

## 6. Tests and verification

- **Unit (Node 22 built-in `node:test`, no browser):** `model/*` and `store.js` against fixtures. Includes: row state for each fixture; pipeline classes incl. `gN` and loop note; `eventText` equals `board._event_text`; format helpers (Vietnamese text in notes preserved).
- **DOM tests:** `node --test` with a lightweight DOM (`linkedom` or `happy-dom`, dev-only, installed into `ui/` by a human; both are pure JS). Render components with Preact, assert class names, ARIA, text. Mutation test: emit an event for ticket B, assert ticket A row's DOM node is untouched (identity + a MutationObserver count of 0).
- **Screenshots without installing a browser:** headless Edge is already installed (`C:/Program Files (x86)/Microsoft/Edge/Application/157.x/`, listed). `snap.mjs` runs `msedge --headless --screenshot=... --window-size=1440,900 "file:///.../index.html?source=fixture&theme=dark&state=gate-pending"`; `?theme=` and `?state=` query params pick theme and fixture (the mock already uses `?theme=` and `?open=`, `DS/app-mock.html:~133`). Screenshots are reviewed by eye and attached to the milestone; pixel diffing is out of M1 (ASSUMPTION: avoids flaky font rendering).
- **Parity (M1 gate):** `parity.mjs` starts `sdlc web` on a free port (read-only; it only serves GET, `harness/web.py:86-105`, and `POST /api/open` is never called), fetches `/api/tickets` and `/api/ticket?id=` for each ticket, runs the same data through P4's `model/` and through the Host source, and diffs the normalised view models: ids, order, `settled/traceLength`, status and verdict per node, `next`, `tokensTotal`, number and text of events. Prints a table; non-zero exit on any diff. Ticket order caveat: board sorts by `updatedAt` desc (`harness/board.py:101`).
- **Manual against the real workspace** (`D:/workspace/HAISE/testing/todo-nestjs-backend`, read-only): open app, select TODO-1 and TODO-1-2, compare with `sdlc web` side by side; open `run-report.md`; "Open in Zed" on `plan.md`; add nothing. Then, with the user's agreement and after P5 exists, watch a live event on a scratch ticket (not the real one).
- **A11y:** keyboard-only walkthrough; a script that checks every `button` without text has `aria-label`; no literal colours.
- **Not verifiable by me yet:** behaviour in WebView2 (P2), real Host events (P3).

## 7. Risks

| Risk | Mitigation |
|---|---|
| P1 does not expose `waitingOn`, per-unit tier/verdict/rounds, or the loop-back history, so the key screens cannot be built honestly | Raise as a P1 requirement now (see §4.3 and §8 Q2); until then fixture-only fallbacks, flagged TODO, removed before M1 done |
| Real data has no attention/g2/loop-back state (both tickets settled) | Synthetic fixtures from P1 cover them; add one live scratch ticket for the end-to-end check after P5 |
| Vendoring Preact by hand drifts | Pin versions in `ui/vendor/VERSIONS.txt`; one developer-run download script; no npm at runtime |
| DOM-test library needs `npm install` (not allowed in the planning phase) | Install happens in the implementation phase, dev-only, outside the packaged `ui/`; alternative is headless-Edge-only tests |
| Preact+htm diverges if P2 picks a bundler | Files are standard ES modules; works the same under Vite |
| Infinite CSS animations (`.k-dot--run`) cost idle CPU with many running tickets | Only for state=run, `prefers-reduced-motion` honoured; measure idle CPU in T19 |
| Big `decisions.jsonl` (106 lines now, will grow) | Host sends parsed events; windowed list; Host/P1 can send "last N" with a "load older" call (asked of P1) |
| Design drift between `ui/` and gallery | Rule: no class outside the gallery; a test greps `ui/` class names against `components.css` |
| Wails event/binding API changes (v2 vs v3) | All confined to `host-bridge.js`; UI never imports Wails APIs directly, it sees `invoke`/`on` only |
| Telex (Vietnamese IME) input in filter/note fields | Plain `<input>`; verify in the P2 Wails spike (P2-T07) per plan §6; no key handlers on printable keys while an input is focused |

## 8. Decisions needed from the user

1. **UI technology: Preact + htm, no build step (recommended)**, vs plain JS (smaller, but we would write our own update logic) vs a compiled framework (Svelte). Recommendation: Preact + htm.
2. **"Waiting on you" must become a real field from the harness (`waitingOn` on `TicketSummary`) instead of parsing the sentence in `next`.** Recommendation: yes, ask P1 to add it in `board.summarize` (small, stdlib, with a test in `kaneki-harness/tests/test_lanes_and_board.py`). It touches the harness the board also uses.
3. **Fixtures as the M1 proof for attention/loop-back states**, since real tickets are all done. Recommendation: accept synthetic fixtures; do one live check on a scratch ticket after P5.
4. **Screenshot/DOM test runner:** headless Edge screenshots (already installed) plus `linkedom` for DOM tests (one dev-only npm install). Recommendation: yes. Alternative: Edge only.
5. **Decision list size:** show all events per ticket (virtualised) or only the last 200 with "load older". Recommendation: last 200 + "load older".
6. **Dock log scope:** workspace-wide last 50 events (mock shows one ticket's). Recommendation: workspace-wide, filterable by ticket.
7. **Markdown preview:** keep text-only with light colouring in M1 (D9). Recommendation: yes; rendered Markdown only if you ask later.
8. **DS folder copy vs link:** the app needs `tokens.css`, `components.css`, `icons.js` at runtime. Recommendation: P2's build copies them from `design-system/` into the packaged `ui/` (one source of truth); no hand copy.

## 9. Evidence

Read (path:line):
- `kaneki-harness-app/doc/build-plan/specs/_context.md:1-70`, `specs/P4-ui-shell-readonly.md:1-30`, `specs/P1-data-contract.md:1-40`.
- `kaneki-harness/docs/desktop-app/plan.md:31-125` (D1-D11, §5 interfaces, §6 targets, §7 M1 done-when, Q5).
- `kaneki-harness-app/design-system/app-mock.html:1-180` (layout, all M1 screens, theme and palette script `:130-178`).
- `kaneki-harness-app/design-system/components.css:14,135,166,251-258,369-393` (reduced motion, animations, events/log, app grid).
- `kaneki-harness-app/design-system/gallery-data.js:9-22,137-215` (registry pattern; component ids, groups); class list gathered with `rg` over `components.css`.
- `kaneki-harness-app/design-system/tokens.css:3,52,102-103,177-179`; `icons.js:1-12`.
- `kaneki-harness/harness/board.py:1-16,17-62,63-76,78-102,143-153` (summary fields, `next_hint`, list sorting, event text).
- `kaneki-harness/harness/web.py:1-12,26,86-105` (endpoints, 512 KB cap, token only on POST) and `harness/web_page.html:96-212` (board render and 2.5 s polling).
- `kaneki-harness/contracts/decision-event.schema.json:1-60` (event kinds, gate requires node/stale).
- `kaneki-harness/graph/lifecycle.json:169` (a `user`-boundary node).
- Real data: `sdlc tickets` run read-only in `D:/workspace/HAISE/testing/todo-nestjs-backend` (2 tickets, both 12/12, `nextPending: null`); `.sdlc/tickets/TODO-1-2/decisions.jsonl` is 106 lines; node 22.23.2 present; Edge 157 present under `C:/Program Files (x86)/Microsoft/Edge/Application/`.

Not verified: per-unit tier/verdict/rounds are not in `summarize` output (only unit ids, models list, tokens, attempts); the exact shape of gate events' `reopen`/`stale` fields beyond the schema's required list; Preact/htm versions and sizes (from memory, check at vendoring); WebView2 behaviour; P1/P3 final IPC names.
