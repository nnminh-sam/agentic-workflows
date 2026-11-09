"""`lk install`: create the data home, write the pointer + config, render skills into ~/.claude/skills."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import sys
from pathlib import Path

from . import paths
from .store import read_json, write_json, write_text

SKILLS = ("learn", "quiz", "note", "recall")

_NEEDS_QUOTES = re.compile(r"[\s\"'$`&|;<>()*?!#~\\]")


def quote(arg: str) -> str:
    """Quote one argument for the shell the skills run commands in.

    POSIX: shlex rules. Windows: double quotes, which Git Bash, cmd.exe and PowerShell all accept.
    Arguments are left bare when safe, so a command like `C:/Python312/python.exe D:/kit/lk.py ...`
    runs unchanged in bash, zsh, Git Bash, cmd and PowerShell.
    """
    if not _NEEDS_QUOTES.search(arg):
        return arg
    return f'"{arg}"' if os.name == "nt" else shlex.quote(arg)


def lk_command() -> str:
    # The absolute interpreter path: works without PATH changes or an activated venv.
    return f"{quote(Path(sys.executable).as_posix())} {quote((paths.KIT_DIR / 'lk.py').as_posix())}"


def render(text: str) -> str:
    return (text.replace("{{LK}}", lk_command())
                .replace("{{KIT}}", paths.KIT_DIR.as_posix())
                .replace("{{HOME}}", paths.home().as_posix()))


def _force_remove(func, path, _exc):
    # Windows refuses to delete read-only files (e.g. ones a sync tool or git marked read-only).
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(p: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(p, onexc=_force_remove)
    else:
        shutil.rmtree(p, onerror=_force_remove)


def init_home(home: Path) -> list[str]:
    made = []
    for sub in ("topics", "sources", "sessions"):
        p = home / sub
        if not p.exists():
            p.mkdir(parents=True)
            made.append(str(p))
    cfg = home / "config.json"
    if not cfg.exists():
        # Only overrides go in the user's config; defaults live in code (paths.DEFAULT_CONFIG).
        write_json(cfg, {"speech_lang": "en-US"})
        made.append(str(cfg))
    readme = home / "README.md"
    if not readme.exists():
        write_text(readme, (
            "# Learning data (learning-kit)\n\n"
            f"Tool: `{paths.KIT_DIR.as_posix()}` — design: `docs/DESIGN.md` there.\n\n"
            "- `topics/<slug>/` prior.md, research.md, concepts.json, quiz.json, attempts.jsonl, note.md, lab/\n"
            "- `sources/` global source bank (index.json + S####.md snapshots)\n"
            "- `sessions/` one JSON per quiz sitting\n"
            "- `srs.json`, `srs-log.jsonl` spaced-recall cards and review log\n"
            "- `mistakes.md` things I got wrong (reread monthly)\n"
        ))
        made.append(str(readme))
    return made


def install(home: str | None, skills_dir: str | None) -> dict:
    if not paths.WEB_DIR.is_dir() or not paths.SKILLS_SRC.is_dir():
        raise SystemExit(f"web/ or skills/ not found next to {paths.KIT_DIR}: run from a clone of the repo "
                         "(or `pip install -e .`), not from a plain wheel install")
    if home:
        ptr = read_json(paths.POINTER, {}) or {}
        ptr["home"] = Path(home).expanduser().resolve().as_posix()
        write_json(paths.POINTER, ptr)
    h = paths.home()
    made = init_home(h)
    dest_root = Path(skills_dir).expanduser() if skills_dir else Path.home() / ".claude" / "skills"
    installed = []
    for name in SKILLS:
        src = paths.SKILLS_SRC / name
        dst = dest_root / name
        if dst.exists():
            marker = dst / ".learning-kit"
            if not marker.exists():
                raise SystemExit(f"refusing to overwrite {dst}: it was not installed by learning-kit")
            _rmtree(dst)
        dst.mkdir(parents=True)
        for f in src.rglob("*"):
            if f.is_file():
                out = dst / f.relative_to(src)
                out.parent.mkdir(parents=True, exist_ok=True)
                if f.suffix == ".md":
                    write_text(out, render(f.read_text(encoding="utf-8")))
                else:
                    shutil.copy2(f, out)
        write_text(dst / ".learning-kit", f"installed from {paths.KIT_DIR.as_posix()}\n")
        installed.append(str(dst))
    return {"home": str(h), "created": made, "skills": installed, "lk": lk_command()}
