# kaneki-harness app

UI assets for the Kaneki harness views. The desktop app that was planned here was dropped on 2026-11-23: Zed is the
working app and a local web board (`sdlc web`) gives the overview. See
[`../docs/zed-workspace/plan.md`](../docs/zed-workspace/plan.md).

| Folder | What |
|---|---|
| `design-system/` | Tokens (`tokens.json` is the source; `build_tokens.py` writes `tokens.css` / `tokens.js`), components, icons, gallery, and the web board mock (`board-zed-mock.html`) |
| `doc/design-system.html` | Rendered design-system document |

Check the generated tokens: `python design-system/build_tokens.py --check`.
