# P8 · Setup, settings, worktrees, packaging and quality (M5 + cross-cutting)

**Milestone:** M5, plus the test/CI strategy every milestone uses. **Output:** `parts/P8-setup-packaging-quality.plan.md`

## Problem
Not in the mock yet: first-run setup, settings, new ticket / onboarding, worktree list and cleanup, packaging.
Also nobody owns the overall quality story (tests, CI, release) across parts.

## Plan must cover
1. **First run & settings**: pick workspace (`sdlc.profile.json`), harness home, Python, editor (Zed path), prices
   (link to P7), theme, notifications; config file schema and location; multi-workspace (plan Q6) recommendation.
2. **New ticket**: what `sdlc init` needs (read `cli.py` `init` parser and the tracker adapters in
   `kaneki-harness/adapters/tracker/`); form design using design-system components; preview/confirm (D2).
3. **Onboarding a repo**: the `kaneki-onboarding` skill flow (`onboard-scan` → verify → `onboard-write`): which steps
   the app can drive vs. which stay in a Claude session; recommend v1 scope.
4. **Worktrees**: list ticket worktrees (`sdlc worktree`, `git worktree list`), cleanup **only with
   `git worktree remove`**; the app must **never delete a branch** (user rule) — design the UI so there is no
   such button, and show branches as "kept".
5. **Packaging**: installer options for Wails (NSIS), WebView2 bootstrap,
   code signing (needed or not for personal use), auto-update (needed in v1?), how the app finds the harness on
   another machine.
6. **Quality strategy across parts**: test pyramid (harness Python tests, Host unit tests, UI unit/DOM tests,
   fixture contract tests, a few end-to-end tests against a temp copy of the workspace), how to run them locally,
   CI (GitHub Actions on Windows runner? the repo is on GitHub as nnminh-sam; SSH rule in CLAUDE.md), lint/format,
   versioning and changelog.
7. **Docs**: what user/dev docs the app needs in `kaneki-harness-app/doc/`.

## Not in scope
Stack choice (P2), contract (P1); reference them.
