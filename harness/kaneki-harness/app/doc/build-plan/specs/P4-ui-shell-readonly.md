# P4 · UI shell and read-only screens (M1)

**Milestone:** M1. **Output:** `parts/P4-ui-shell-readonly.plan.md`

## Problem
Turn `design-system/app-mock.html` (static, assembled from the design system) into the real UI: data-driven from
the P1 contract, running first against fixture files and then against the live Host. M1 done-when (plan §7):
"Matches `sdlc web` data for every ticket in `todo-nestjs-backend`".

## Plan must cover
1. **UI technology**: plain ES modules + small helpers vs. a light framework (Preact, Lit, Svelte, Solid) vs.
   React. Criteria: memory/start-up (plan §6 targets), the user is new to desktop apps, design-system classes are
   plain CSS, bundle size. Recommend one.
2. **App structure**: state store (tickets, selected ticket/node, decisions feed, theme), data adapter with two
   implementations (`FixtureSource`, `HostSource`) behind one interface, routing/tabs, keyboard model, command
   palette (Ctrl+K, read-only actions for M1), theme (light/dark/system via `data-theme`).
3. **Screens for M1**, each mapped to design-system components (`.k-*` classes, see `gallery-data.js`): ticket rail
   (filter, status, "waiting on you"), pipeline strip (generation `g2`, loop-back line), node card, inspector
   (artifact preview as text, "Open in Zed" button), decision feed (live), top bar (gates-need-you count, cost
   placeholder), sessions dock (placeholder until P6), empty/loading/error states.
4. **Component gaps**: anything the screens need that the design system lacks; how new components get added
   (gallery-data entry + CSS) so the gallery stays the source of truth.
5. **Performance**: render only changed rows on events, virtualise long decision lists, no per-frame timers.
6. **Testing**: unit tests for view-model mapping (contract → view), fixture-driven screenshot or DOM tests (which
   runner works without installing a browser: e.g. Node test runner + happy-dom/jsdom, or headless Edge already on
   the machine), the M1 parity check vs. `sdlc web` output.
7. Accessibility basics (focus order, keyboard reachability, contrast already checked by `build_tokens.py`).

## Not in scope
Gate dialog and any write action (P5), session dock behaviour (P6), cost tab content (P7), settings/onboarding (P8).
