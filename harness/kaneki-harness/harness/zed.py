"""`sdlc zed-setup`: add Kaneki tasks to a workspace's `.zed/tasks.json` (Zed: `task: spawn`).

Tasks run the driver with this Python and this harness, from the Zed project root (where sdlc.profile.json lives).
An existing tasks.json is merged by label: tasks you wrote are kept, ours are added or refreshed. A tasks.json with
comments (JSONC) is not rewritten; the tasks are printed for you to paste instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .errors import HarnessError

PREFIX = "kaneki: "


def tasks(home: Path) -> list[dict[str, Any]]:
    entry = str((home / "skills" / "kaneki-flow" / "scripts" / "sdlc.py").resolve())
    py = sys.executable

    def task(label: str, args: list[str], **extra: Any) -> dict[str, Any]:
        return {"label": PREFIX + label, "command": py, "args": [entry, *args], "cwd": "$ZED_WORKTREE_ROOT",
                "use_new_terminal": False, "allow_concurrent_runs": False, "reveal": "always", **extra}

    return [
        task("board (web)", ["web", "--open"], reveal="no_focus"),
        task("tickets", ["tickets"]),
        task("ticket status (from open file)", ["status-of", "--path", "$ZED_FILE"]),
        task("validate profile", ["validate"]),
        # interactive: its own terminal tab; the ticket comes from the open file, else a picker (no open file -> ".")
        task("decide gate", ["decide", "--path", "${ZED_FILE:.}"], use_new_terminal=True),
        # interactive too: walks the reflect proposals waiting for you (apply / reject / skip)
        task("review learning", ["learn", "review"], use_new_terminal=True),
    ]


def setup(home: Path, target: Path, dry_run: bool = False) -> dict[str, Any]:
    if not target.is_dir():
        raise HarnessError(f"not a folder: {target}")
    path = target / ".zed" / "tasks.json"
    ours = tasks(home)
    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            return {"ok": False, "tasksJson": str(path), "reason": "tasks.json has comments or is not plain JSON; "
                    "paste these tasks into it yourself", "tasks": ours}
        if not isinstance(existing, list):
            raise HarnessError(f"{path} is not a JSON list of tasks")
    labels = {t["label"] for t in ours}
    kept = [t for t in existing if t.get("label") not in labels]
    merged = kept + ours
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return {"ok": True, "tasksJson": str(path), "added": sorted(labels), "keptYours": len(kept), "dryRun": dry_run,
            "next": "In Zed: `task: spawn` (alt-shift-t) → 'kaneki: board (web)'."}
