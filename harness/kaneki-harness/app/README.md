# kaneki-harness app

Desktop app for the Kaneki SDLC harness (Go + Wails host, Preact + htm UI). Planning stage: no app code yet.
It lives inside the harness (`harness/kaneki-harness/app/`) because it reads everything through the harness CLI
(`sdlc contract`, `tickets`, `ticket-detail`, `gate-request`); the harness stays the only writer of ticket folders.

| Folder | What |
|---|---|
| `doc/build-plan/` | Aggregate build plan (`00-build-plan.md`), part specs (`specs/`) and part plans (`parts/`), P1–P8 |
| `doc/*.html` | Rendered views of the build plan and the design system |
| `design-system/` | Tokens (`tokens.json` is the source; `build_tokens.py` writes `tokens.css` / `tokens.js`), components, icons, gallery, app mock |

Check the generated tokens: `python design-system/build_tokens.py --check`.

## Paths in the docs

Repo paths are relative to the repo root (`agentic-workflows/`): the harness is `harness/kaneki-harness/...`, the
app `harness/kaneki-harness/app/...`. Line numbers in citations were taken when the plan was written
(2026-11-18) and may have drifted. The app's runtime folders keep the product name: `%APPDATA%\kaneki-harness-app\`,
`%LOCALAPPDATA%\kaneki-harness-app\`.
