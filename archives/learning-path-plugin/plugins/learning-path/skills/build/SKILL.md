---
name: build
description: >
  Build an answer-your-way notebook (node-path, java-path, frontend-path) plus its learning-path.json
  into a static website for learning in the browser, and publish it on the web. Adds a "Your learning
  path" page, path badges on questions and deep links. Trigger on "/learning-path:build [notebook dir]
  [out dir]", "build the notebook site", "publish my learning path", "make the notebook public",
  "deploy the notebook".
---

# Build → public site

`SCRIPTS` = `${CLAUDE_PLUGIN_ROOT}/scripts`. Talk to the user in the language they write in.

## 1. Build

Notebook = first word of `$ARGUMENTS`, else the current directory (it must have `assets/app.js` with
`PAGES`). Output = second word, else `NOTEBOOK/dist`.

```bash
python3 SCRIPTS/build.py NOTEBOOK [OUT]
```

- No `learning-path.json` → it still builds the plain notebook. Say so in one line and offer
  `/learning-path:diagnose` first.
- A validation failure (unknown chapter / section / qid, or the notebook's own `check.py` failing) →
  fix `learning-path.json` against `python3 SCRIPTS/catalog.py NOTEBOOK --chapter <file>` and rebuild.
  Never edit the chapters to make it pass.

What ends up in OUT: the chapters, `assets/` (minus `check.py`), and — with a path — `path.html` plus
`assets/path.js`. **Never published:** `answers/`, `.notes/`, `examples/`, zips, `serve.py`. Those are
the learner's private work; if they want any of it public, they must say so explicitly.

## 2. Preview

`python3 -m http.server -d OUT 8780` → `http://localhost:8780/path.html`. On a static host there is no
`serve.py`, so the badge reads `local only` and each visitor's answers live in *their* browser — that is
expected, not a bug.

## 3. Publish — ask where (AskUserQuestion, one question)

`path.html` contains the diagnosis and interview evidence. Before anything leaves the machine, say so in
one line and let them choose:

1. **Claude artifact** (recommended: private link, shareable when they choose). Use the Artifact tool,
   following its own instructions: `file_path` = `OUT/index.html`, `files` = every other file in OUT,
   keyed by its path relative to OUT (`path.html`, `08-data.html`, `assets/style.css`, …), `icon` =
   `book`. Republish to the same URL on later builds.
2. **GitHub Pages / Netlify / any static host.** OUT is plain static files with no build step. Give the
   commands (e.g. push OUT to a `gh-pages` branch) and run them only after the user confirms — pushing
   is publishing.
3. **Just the folder.** Done; tell them the path.

Finish with the link (or folder path) in one line.
