#!/usr/bin/env python3
"""Install the notify-hook Notification hook into Claude Code.

  python install.py                              # user scope: ~/.claude/settings.json
  python install.py --project D:/work/my-app     # one project: <project>/.claude/settings.local.json
  python install.py --matcher permission_prompt  # only notify for some notification types
  python install.py --uninstall                  # remove the hook entry and the copied script
  python install.py --dry-run                    # show what would change

The installer copies notify.py to <claude dir>/hooks/notify-hook/notify.py, so the hook keeps
working if this folder moves; re-run it to upgrade. The hook command uses the Python interpreter
that runs this installer. Other settings and hooks are left untouched; a previous notify-hook
entry (or the older notify-toast.ps1 entry) is replaced instead of duplicated.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

PKG = Path(__file__).resolve().parent
EVENT = "Notification"
INSTALL_REL = Path("hooks") / "notify-hook"
OUR_MARKERS = ("/hooks/notify-hook/notify.py", "/hooks/notify-toast.ps1")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    data = json.loads(text) if text else {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def write_json(path: Path, data: dict, dry: bool) -> None:
    if dry:
        print(f"[dry-run] would write {path}:\n{json.dumps(data.get('hooks', {}).get(EVENT), indent=2)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"updated {path}")


def is_ours(entry: dict) -> bool:
    return any(any(m in h.get("command", "").replace("\\", "/") for m in OUR_MARKERS)
               for h in entry.get("hooks", []))


def strip_ours(settings: dict) -> bool:
    hooks = settings.get("hooks", {})
    entries = hooks.get(EVENT, [])
    kept = [e for e in entries if not is_ours(e)]
    if len(kept) == len(entries):
        return False
    if kept:
        hooks[EVENT] = kept
    else:
        hooks.pop(EVENT, None)
    if "hooks" in settings and not settings["hooks"]:
        del settings["hooks"]
    return True


def hook_command(script: Path) -> str:
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'


def install(claude_dir: Path, settings_path: Path, matcher: Optional[str], dry: bool) -> None:
    script = claude_dir / INSTALL_REL / "notify.py"
    if dry:
        print(f"[dry-run] copy {PKG / 'notify.py'} -> {script}")
    else:
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PKG / "notify.py", script)
        print(f"copied notify.py -> {script}")

    s = read_json(settings_path)
    if strip_ours(s):
        print("replaced the previous notify hook entry")
    entry: dict = {"hooks": [{"type": "command", "command": hook_command(script), "timeout": 15}]}
    if matcher:
        entry = {"matcher": matcher, **entry}
    s.setdefault("hooks", {}).setdefault(EVENT, []).append(entry)
    write_json(settings_path, s, dry)


def uninstall(claude_dir: Path, settings_path: Path, dry: bool) -> None:
    s = read_json(settings_path)
    if strip_ours(s):
        write_json(settings_path, s, dry)
    else:
        print(f"no notify hook entry in {settings_path}")
    target = claude_dir / INSTALL_REL
    if target.exists():
        print(f"{'[dry-run] would remove' if dry else 'removing'} {target}")
        if not dry:
            shutil.rmtree(target)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="install for one project (settings.local.json) instead of the user")
    ap.add_argument("--matcher", help="notification types to match, e.g. 'permission_prompt|idle_prompt'")
    ap.add_argument("--claude-dir", help=argparse.SUPPRESS)  # override ~/.claude (tests)
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.project:
        project = Path(args.project).expanduser().resolve()
        if not project.is_dir():
            ap.error(f"not a directory: {project}")
        claude_dir = project / ".claude"
        settings_path = claude_dir / "settings.local.json"
    else:
        claude_dir = Path(args.claude_dir).expanduser() if args.claude_dir else Path.home() / ".claude"
        settings_path = claude_dir / "settings.json"

    if args.uninstall:
        uninstall(claude_dir, settings_path, args.dry_run)
    else:
        install(claude_dir, settings_path, args.matcher, args.dry_run)
        if not args.dry_run:
            print("\nTest it:  echo '{\"message\":\"hello\"}' | " + hook_command(claude_dir / INSTALL_REL / "notify.py"))
            print("Claude Code reloads settings on its own; if nothing fires, open /hooks once or restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
