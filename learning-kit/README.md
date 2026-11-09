# learning-kit

A learning loop where retrieval comes first, built on Claude Code:
**what I know → research (source bank) → read → quiz (80% gate) → reteach → lab → own-words note → spaced recall.**

Runs on Linux, macOS, Windows and WSL. Python 3.9+ standard library only; no pip dependencies.
Design and architecture: [docs/DESIGN.md](docs/DESIGN.md).

## Setup

```sh
git clone git@github.com:nnminh-sam/agentic-workflows.git && cd agentic-workflows/learning-kit
python lk.py install --home ~/learning     # data home + user-level skills in ~/.claude/skills
python -m unittest discover -s tests       # 27 tests, no network
```

- Use `python3` instead of `python` if that's what your OS calls it (most Linux and macOS installs).
- `--home` can be any folder (default `~/learning`). `LK_HOME` overrides it per shell.
- Optional: `pip install -e .` puts an `lk` command on your PATH (`lk.exe` on Windows). Use the editable
  install only: the app's web/ and skills/ are read from the checkout.
- Grading free-recall answers needs the `claude` CLI on PATH. Native installer, npm and Homebrew all work.

## Use (in Claude Code)

| Command | What it does |
|---|---|
| `/learn <topic> [what I know…]` | start or resume a topic; runs whichever stage is next |
| `/learn <topic> --new` | brand-new topic: just 3 questions + 1 prediction to start |
| `/learn <topic> --extend` | grow a finished topic (new concepts become the next gate) |
| `/learn <topic> --depth aware\|use\|deep` | how far to go (deep adds a lab) |
| `/quiz <topic> [--practice]` | open the quiz app (gate round, or no-stakes practice) |
| `/note <topic>` | write or dictate your note; rubric review at 80% |
| `/recall [--stats]` | today's spaced-recall cards, reworded, mixed across topics |

## CLI (what the skills call)

```sh
python lk.py topic list | topic status <slug> | topic advance <slug> <stage>
python lk.py source fetch <url> --topic <slug> --kind docs --quality 3
python lk.py quiz validate <file> --topic <slug> | quiz import <slug> <file>
python lk.py serve --topic <slug> | --review | --session <id>
python lk.py srs due | srs stats | stats
python lk.py --help            # or: python -m lk --help, or: lk --help after pip install -e .
```

After editing anything under `skills/`, re-run `python lk.py install`.

## Platform notes

| | |
|---|---|
| Browser | Opens automatically on Windows, macOS and Linux desktops. On WSL it opens the Windows browser (`wslview` or `explorer.exe`). On SSH or headless Linux the URL is printed instead. Forward the port with `ssh -L 8765:127.0.0.1:8765 host`. |
| Dictation | In-browser (Chrome, Edge, Safari): the 🎙 button, English or Vietnamese. Otherwise the OS tool: Win+H on Windows, Fn Fn on macOS. Firefox and most Linux Chromium builds have no speech API. |
| Shell | The skills issue POSIX-shell commands (Bash tool; Git Bash on Windows). |
| macOS python.org builds | If `lk source fetch` fails with an SSL error, run *Install Certificates.command* once. The skills fall back to WebFetch anyway. |
