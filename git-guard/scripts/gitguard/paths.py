"""Where git-guard's code, default contracts and per-project state live.

git-guard runs in one of two layouts:

* plugin   - code in the Claude Code plugin cache (${CLAUDE_PLUGIN_ROOT}), shared by every
             project. Per-project state lives in <project>/.claude/git-guard/.
* vendored - code copied into <project>/.claude/hooks/git-guard/ (install.py --mode vendor).
             Per-project state lives next to the code, as in git-guard 1.x.

Per-project state:
  profile.local.json        the project profile (git-ignored)
  contracts/<name>.json     optional override of one shipped contract (plugin layout)
  rules.html                the generated rules viewer (git-ignored)
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # the folder holding scripts/, contracts/, viewer/
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
DEFAULT_CONTRACTS_DIR = PACKAGE_ROOT / "contracts"
VIEWER_SHELL = PACKAGE_ROOT / "viewer" / "shell.html"
USER_DIR = Path(os.path.expanduser("~")) / ".claude" / "git-guard"

LOCAL_GITIGNORE = "# git-guard: personal profile values and the viewer that embeds them\nprofile.local.json\nrules.html\n"


def is_vendored(root: Path = PACKAGE_ROOT) -> bool:
    return root.parent.name == "hooks" and root.parent.parent.name == ".claude"


def project_dir(env: dict | None = None, root: Path = PACKAGE_ROOT) -> Path:
    env = os.environ if env is None else env
    for key in ("GIT_GUARD_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        if env.get(key, "").strip():
            return Path(env[key]).expanduser().resolve()
    if is_vendored(root):
        return root.parents[2]
    return Path.cwd().resolve()


def local_dir(env: dict | None = None, root: Path = PACKAGE_ROOT) -> Path:
    """The folder holding this project's profile, contract overrides and rules.html."""
    if is_vendored(root):
        return root
    return project_dir(env, root) / ".claude" / "git-guard"


def ensure_local_dir(path: Path | None = None) -> Path:
    """Create the per-project folder (plugin layout) with a .gitignore for the personal files."""
    path = LOCAL_DIR if path is None else path
    path.mkdir(parents=True, exist_ok=True)
    gi = path / ".gitignore"
    if path != PACKAGE_ROOT and not gi.exists():
        gi.write_text(LOCAL_GITIGNORE, encoding="utf-8")
    return path


def contract_path(fname: str, local: Path | None = None) -> Path:
    """A project override in <local>/contracts/ wins over the shipped default."""
    local = LOCAL_DIR if local is None else local
    override = local / "contracts" / fname
    if local != PACKAGE_ROOT and override.exists():
        return override
    return DEFAULT_CONTRACTS_DIR / fname


LOCAL_DIR = local_dir()
PROJECT_PROFILE = LOCAL_DIR / "profile.local.json"
USER_PROFILE = USER_DIR / "profile.json"
RULES_HTML = LOCAL_DIR / "rules.html"


def display(path: Path) -> str:
    """Path relative to the project when it is inside it, else absolute."""
    try:
        return path.relative_to(project_dir()).as_posix()
    except ValueError:
        return str(path)
