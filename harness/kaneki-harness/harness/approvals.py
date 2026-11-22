"""Short-lived ship approvals in `<project>/.sdlc/ship-approvals.json`.

Both the worktree git hooks (`worktree_hooks.py`) and the tool-level guard (`guard_git.py`) read this file to
allow `commit` and `push` only in a checkout whose exact request the user approved in `sdlc ship`. A legacy
`.claude/kaneki-ship-approvals.json` file is still accepted on read as a fallback and cleaned up on revoke.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

APPROVALS_REL = Path(".sdlc") / "ship-approvals.json"
LEGACY_APPROVALS_REL = Path(".claude") / "kaneki-ship-approvals.json"
APPROVAL_TTL_SECONDS = 30 * 60


def approvals_file(project: Path) -> Path:
    return project / APPROVALS_REL


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    now = time.time()
    return [a for a in data.get("approvals", []) if isinstance(a, dict) and a.get("expiresAt", 0) > now]


def _write_entries(path: Path, items: list[dict[str, Any]]) -> None:
    if not items:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schemaVersion": 1, "approvals": items}, indent=2) + "\n", encoding="utf-8")


def load(project: Path) -> list[dict[str, Any]]:
    return _read_entries(approvals_file(project))


def grant(project: Path, ticket: str, repo: str, path: str, branch: str, digest: str) -> dict[str, Any]:
    """Let git commit/push pass in `path` for this approved request, for APPROVAL_TTL_SECONDS."""
    items = [a for a in load(project) if not (a.get("ticket") == ticket and a.get("repo") == repo)]
    entry = {"ticket": ticket, "repo": repo, "path": str(Path(path).resolve()), "branch": branch,
             "requestDigest": digest, "allow": ["commit", "push"], "expiresAt": int(time.time()) + APPROVAL_TTL_SECONDS}
    _write_entries(approvals_file(project), items + [entry])
    return entry


def revoke(project: Path, ticket: str, repo: str) -> None:
    items = [a for a in load(project) if not (a.get("ticket") == ticket and a.get("repo") == repo)]
    _write_entries(approvals_file(project), items)
    legacy = project / LEGACY_APPROVALS_REL
    if legacy.is_file():
        leg_items = [a for a in _read_entries(legacy) if not (a.get("ticket") == ticket and a.get("repo") == repo)]
        _write_entries(legacy, leg_items)


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def is_approved(project: Path, action: str, target_path: str) -> bool:
    for rel in (APPROVALS_REL, LEGACY_APPROVALS_REL):
        for entry in _read_entries(project / rel):
            if action in entry.get("allow", []) and _same_path(str(entry.get("path", "")), target_path):
                return True
    return False
