# learning-path — a Claude Code plugin

Two skills for the answer-your-way notebooks in `notes/` (`node-path`, `java-path`, `frontend-path`):

| Skill | Does |
|---|---|
| `/learning-path:diagnose [notebook]` | Interviews you **one question at a time**, probes your real level with the notebook's own questions, and writes `learning-path.json` into the notebook. |
| `/learning-path:build [notebook] [out]` | Builds the notebook + path into a static site (`dist/`): a "Your learning path" page, path badges on questions, deep links. Then publishes it (Claude artifact, GitHub Pages, or just the folder). |

## Install

```bash
claude plugin marketplace add /home/MINHNN4/workspace/mimosa/notes/learning-path-plugin
claude plugin install learning-path@learning-path
```

## Scripts (stdlib Python, usable by hand)

```bash
S=plugins/learning-path/scripts
python3 $S/catalog.py ../node-path                     # chapter/section/practice map
python3 $S/catalog.py ../node-path --chapter 08-data.html
python3 $S/catalog.py ../node-path --q da-01           # question + "Check yourself"
python3 $S/catalog.py ../node-path --answers           # past grades from answers/state.json
python3 $S/build.py   ../node-path                     # → ../node-path/dist
```

`build.py` never copies `answers/`, `.notes/`, `examples/`, zips or `serve.py`, and fails if
`learning-path.json` names a chapter, section or question the notebook doesn't have.
