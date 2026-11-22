"""The my-git plugin: kaneki's git toolchain (user decision 2026-11-19).

kaneki no longer commits, pushes or opens PRs itself. /kaneki-ship stages the approved files, then hands over to
/my-git:commit, /my-git:push and /my-git:pr, and records the result with `sdlc ship-record`. The my-git guard owns
identity, SSH-key and remote rules; kaneki-flow's own guard keeps only the node rules (see guard_git.py).

my-git is found next to the harness in the same checkout (`<repo>/plugins/my-git`), so `sdlc install` registers the
same version as the harness it installs. `SDLC_MYGIT` overrides the location.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import approvals
from .errors import HarnessError

MARKETPLACE = "my-git-local"
PLUGIN_ID = f"my-git@{MARKETPLACE}"
APPROVAL_TTL_SECONDS = approvals.APPROVAL_TTL_SECONDS


def find_root(home: Path) -> Path | None:
    env = os.environ.get("SDLC_MYGIT")
    if env:
        p = Path(env).expanduser().resolve()
        return p if (p / ".claude-plugin" / "plugin.json").is_file() else None
    for parent in [home.resolve(), *home.resolve().parents]:
        p = parent / "plugins" / "my-git"
        if (p / ".claude-plugin" / "plugin.json").is_file():
            return p
    return None


def require_root(home: Path) -> Path:
    root = find_root(home)
    if root is None:
        raise HarnessError("G7: the my-git plugin was not found (expected <checkout>/plugins/my-git next to the harness, "
                           "or set SDLC_MYGIT); kaneki commits, pushes and opens PRs only through my-git")
    return root


def version(root: Path) -> str:
    return str(json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "?"))


def check_message(root: Path, message: str) -> list[str]:
    """my-git's Conventional Commits / no-AI-attribution check (CM rules) on a commit message; [] when it passes."""
    with tempfile.TemporaryDirectory(prefix="kaneki-msg-") as d:
        f = Path(d) / "msg.txt"
        f.write_text(message.rstrip() + "\n", encoding="utf-8")
        res = subprocess.run([sys.executable, str(root / "scripts" / "check_message.py"), "--file", str(f)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if res.returncode == 0:
        return []
    text = (res.stdout + res.stderr).strip()
    return [ln.strip() for ln in text.splitlines() if ln.strip()] or [f"check_message.py exited {res.returncode}"]


# --- install ---------------------------------------------------------------------------------------------------

def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    data = json.loads(text) if text else {}
    return data if isinstance(data, dict) else {}


def _registered_path(settings: dict[str, Any]) -> str | None:
    src = ((settings.get("extraKnownMarketplaces") or {}).get(MARKETPLACE) or {}).get("source") or {}
    return src.get("path")


def _same(a: str | None, b: Path) -> bool:
    if not a:
        return False
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def install(root: Path, project: Path, dry_run: bool = False, user_scope: bool = False) -> dict[str, Any]:
    """Register this my-git as the plugin (my-git's own install.py, plugin mode; project scope, or user scope when
    kaneki is installed into ~/.claude), and repoint a `my-git-local` entry in the project's
    `.claude/settings.local.json`, which would otherwise win over settings.json."""
    cmd = [sys.executable, str(root / "install.py")] + (["--scope", "user"] if user_scope else [str(project)]) \
        + (["--dry-run"] if dry_run else [])
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if res.returncode != 0:
        raise HarnessError(f"G7: my-git install failed: {(res.stderr or res.stdout).strip()[:300]}")
    settings = (Path.home() / ".claude" if user_scope else project / ".claude") / "settings.json"
    out: dict[str, Any] = {"root": str(root), "version": version(root), "plugin": PLUGIN_ID,
                           "settings": str(settings), "repointed": [], "notes": []}
    if user_scope:
        out["notes"].append("Restart Claude Code (or run `/plugin marketplace update my-git-local`) to load this my-git")
        return out
    local = project / ".claude" / "settings.local.json"
    s = _read(local)
    old = _registered_path(s)
    if old is not None and not _same(old, root):
        s["extraKnownMarketplaces"][MARKETPLACE] = {"source": {"source": "directory", "path": root.as_posix()}}
        s.setdefault("enabledPlugins", {})[PLUGIN_ID] = True
        if not dry_run:
            local.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
        out["repointed"].append({"file": str(local), "from": old, "to": root.as_posix()})
    user = Path.home() / ".claude" / "settings.json"
    upath = _registered_path(_read(user))
    if upath is not None and not _same(upath, root):
        out["notes"].append(f"{user} also registers {MARKETPLACE} at {upath}; the project setting wins in this project, "
                            "other projects still use that copy")
    out["notes"].append("Restart Claude Code in the project (or run `/plugin marketplace update my-git-local`) so it "
                        "loads this my-git; set your identity once with my-git's setup_profile.py if not done")
    return out


# --- ship approvals (delegated to harness/approvals.py) --------------------------------------------------------

def approvals_file(project: Path) -> Path:
    return approvals.approvals_file(project)


def grant(project: Path, ticket: str, repo: str, path: str, branch: str, digest: str) -> dict[str, Any]:
    """Let the guard and worktree hooks pass commit/push for this approved request."""
    return approvals.grant(project, ticket, repo, path, branch, digest)


def revoke(project: Path, ticket: str, repo: str) -> None:
    approvals.revoke(project, ticket, repo)
