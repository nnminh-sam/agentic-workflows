"""Agent-agnostic git hooks (`pre-commit`, `pre-push`) scoped to linked ticket worktrees.

When `sdlc worktree` binds a linked worktree, Kaneki enables `extensions.worktreeConfig` and sets
`--worktree core.hooksPath` to `<git-dir>/kaneki-hooks` (inside that worktree's own git metadata dir).
Main checkouts (`git_dir == common_dir`) are never touched, so in-place `lite` lanes and normal repo work
stay unaffected. Any previously configured repo `core.hooksPath` (or `<common-dir>/hooks`) is chained
after Kaneki's approval check, preserving stdin (for `pre-push` ref lines).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

_CHECK_APPROVAL_PY = '''#!/usr/bin/env python3
"""Worktree git hook approval check (self-contained stdlib Python 3.9+, fail closed)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

APPROVAL_PATHS = (Path(".sdlc") / "ship-approvals.json", Path(".claude") / "kaneki-ship-approvals.json")


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("commit", "push"):
        print("Blocked by kaneki worktree hook: invalid hook action.", file=sys.stderr)
        return 1
    action = sys.argv[1]
    cfg_path = Path(__file__).resolve().parent / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        project = Path(cfg["project"])
        worktree = str(cfg["worktree"])
    except Exception as exc:  # noqa: BLE001 - fail closed on missing/corrupt hook config
        print(f"Blocked by kaneki worktree hook: unreadable config ({exc}).", file=sys.stderr)
        return 1
    now = time.time()
    for rel in APPROVAL_PATHS:
        app_path = project / rel
        if not app_path.is_file():
            continue
        try:
            data = json.loads(app_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("Blocked by kaneki worktree hook: unreadable ship approvals file.", file=sys.stderr)
            return 1
        for entry in (data.get("approvals", []) if isinstance(data, dict) else []):
            if not isinstance(entry, dict) or entry.get("expiresAt", 0) <= now:
                continue
            if action in entry.get("allow", []) and _same_path(str(entry.get("path", "")), worktree):
                return 0
    print(f"Blocked by kaneki worktree hook: nodes never {action}; only kaneki-ship does (after user approval).",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"Blocked by kaneki worktree hook: internal error ({type(exc).__name__}: {exc}).", file=sys.stderr)
        sys.exit(1)
'''


def _hook_script(action: str, chain_hook: Path | None) -> str:
    quoted_chain = shlex.quote(chain_hook.as_posix()) if chain_hook else "''"
    return (
        "#!/bin/sh\n"
        'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        "ok() { \"$@\" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }\n"
        "run_check() {\n"
        '  if [ -n "$SDLC_PYTHON" ] && ok $SDLC_PYTHON; then\n'
        f'    $SDLC_PYTHON "$here/check_approval.py" "{action}" </dev/null\n'
        "    return $?\n"
        "  fi\n"
        "  for py in python3 python; do\n"
        '    if command -v "$py" >/dev/null 2>&1 && ok "$py"; then\n'
        f'      "$py" "$here/check_approval.py" "{action}" </dev/null\n'
        "      return $?\n"
        "    fi\n"
        "  done\n"
        "  if command -v py >/dev/null 2>&1 && ok py -3; then\n"
        f'    py -3 "$here/check_approval.py" "{action}" </dev/null\n'
        "    return $?\n"
        "  fi\n"
        '  echo "Blocked by kaneki worktree hook: Python 3.9+ not found." >&2\n'
        "  return 1\n"
        "}\n"
        "run_check || exit $?\n"
        f"chain={quoted_chain}\n"
        'if [ -n "$chain" ] && [ -x "$chain" ]; then\n'
        '  exec "$chain" "$@"\n'
        "fi\n"
        "exit 0\n"
    )


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _resolve_git_path(base: Path, raw: str) -> Path:
    p = Path(raw.strip())
    return (p if p.is_absolute() else (base / p)).resolve()


def _write_lf(path: Path, text: str, executable: bool = False) -> None:
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    if executable:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


def install(worktree: Path, project: Path) -> dict[str, Any]:
    """Install `pre-commit` and `pre-push` approval hooks if `worktree` is a linked git worktree."""
    wt_abs = worktree.resolve()
    proj_abs = project.resolve()
    gd_res = _git(wt_abs, "rev-parse", "--git-dir")
    cd_res = _git(wt_abs, "rev-parse", "--git-common-dir")
    if gd_res.returncode != 0 or cd_res.returncode != 0:
        return {"installed": False, "reason": "not-a-git-worktree"}
    git_dir = _resolve_git_path(wt_abs, gd_res.stdout)
    common_dir = _resolve_git_path(wt_abs, cd_res.stdout)
    if git_dir == common_dir:
        return {"installed": False, "reason": "main-checkout"}

    hooks_dir = (git_dir / "kaneki-hooks").resolve()
    chain_dir: Path | None = None
    cur_res = _git(wt_abs, "config", "--get", "core.hooksPath")
    if cur_res.returncode == 0 and cur_res.stdout.strip():
        existing = _resolve_git_path(wt_abs, cur_res.stdout)
        if existing != hooks_dir:
            chain_dir = existing
        elif (hooks_dir / "config.json").is_file():
            try:
                old_cfg = json.loads((hooks_dir / "config.json").read_text(encoding="utf-8"))
                if old_cfg.get("chainDir"):
                    chain_dir = Path(old_cfg["chainDir"]).resolve()
            except (OSError, ValueError):
                chain_dir = None
    else:
        chain_dir = (common_dir / "hooks").resolve()

    ext_res = _git(wt_abs, "config", "extensions.worktreeConfig", "true")
    cfg_res = _git(wt_abs, "config", "--worktree", "core.hooksPath", hooks_dir.as_posix())
    if ext_res.returncode != 0 or cfg_res.returncode != 0:
        return {"installed": False, "warning": "git does not support worktreeConfig; worktree hooks skipped"}

    hooks_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"schemaVersion": 1, "worktree": str(wt_abs), "project": str(proj_abs),
           "chainDir": str(chain_dir) if chain_dir else None}
    _write_lf(hooks_dir / "config.json", json.dumps(cfg, indent=2) + "\n")
    _write_lf(hooks_dir / "check_approval.py", _CHECK_APPROVAL_PY, executable=True)
    _write_lf(hooks_dir / "pre-commit", _hook_script("commit", chain_dir / "pre-commit" if chain_dir else None), executable=True)
    _write_lf(hooks_dir / "pre-push", _hook_script("push", chain_dir / "pre-push" if chain_dir else None), executable=True)
    return {"installed": True, "hooksDir": str(hooks_dir), "chained": str(chain_dir) if chain_dir else None}
