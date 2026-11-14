"""`sdlc install`: copy the kaneki skills and agents into a project's (or the user's) `.claude/`.

- skills: kaneki-flow, kaneki-onboarding, kaneki-ship → <target>/.claude/skills/<name>/
- agents: kaneki-worker, kaneki-deep → <target>/.claude/agents/
- every installed scripts/ folder gets `.harness-home` (the harness path), so no SDLC_HOME is needed
- <target>/.claude/kaneki-harness.json records the version and the digest of every installed file; a re-install
  overwrites only files still identical to what was installed (a file you edited is kept and reported).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import __version__
from .errors import HarnessError
from .state import now, sha256_file

SKILLS = ("kaneki-flow", "kaneki-onboarding", "kaneki-ship")
MANIFEST = "kaneki-harness.json"


def _files(home: Path) -> list[tuple[Path, str]]:
    out = []
    for skill in SKILLS:
        src = home / "skills" / skill
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.name != ".harness-home" and "agents" not in f.relative_to(src).parts:
                out.append((f, f"skills/{skill}/{f.relative_to(src).as_posix()}"))
    for f in sorted((home / "skills" / "kaneki-flow" / "agents").glob("*.md")):
        out.append((f, f"agents/{f.name}"))
    return out


def install(home: Path, target: Path, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    if not target.is_dir():
        raise HarnessError(f"install target is not a folder: {target}")
    claude = target / ".claude" if target.name != ".claude" else target
    manifest_path = claude / MANIFEST
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": {}}
    written, kept, unchanged = [], [], []
    record: dict[str, str] = {}
    for src, rel in _files(home):
        dest = claude / rel
        new_digest = sha256_file(src)
        if dest.is_file():
            cur = sha256_file(dest)
            if cur == new_digest:
                unchanged.append(rel)
                record[rel] = new_digest
                continue
            if not force and old["files"].get(rel) != cur:
                kept.append(rel)  # edited by you since the last install (or not installed by us)
                record[rel] = cur
                continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        written.append(rel)
        record[rel] = new_digest
    for skill in SKILLS:
        marker = claude / "skills" / skill / "scripts" / ".harness-home"
        if not dry_run:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(home.resolve()) + "\n", encoding="utf-8")
    manifest = {"schemaVersion": 1, "version": __version__, "harnessHome": str(home.resolve()),
                "installedAt": now(), "files": record}
    if not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"target": str(claude), "version": __version__, "written": written, "unchanged": len(unchanged),
            "keptYourEdits": kept, "dryRun": dry_run,
            "next": "In Claude Code, inside the project: /kaneki-onboarding, then /kaneki-flow <ticket>."}
