#!/usr/bin/env python3
"""Install the notify-hook Notification hook into Claude Code.

  python install.py                              # user scope: ~/.claude/settings.json
  python install.py --project D:/work/my-app     # one project: <project>/.claude/settings.local.json
  python install.py --matcher permission_prompt  # only notify for some notification types
  python install.py --uninstall                  # remove the hook entry and the copied script
  python install.py --dry-run                    # show what would change

The installer copies notify.py and focus.py to <claude dir>/hooks/notify-hook/, so the hook keeps
working if this folder moves; re-run it to upgrade. On Windows it also registers the
claude-notify-focus: URL scheme (HKCU, runs focus.py when a toast is clicked) and adds the key
bindings focus.py needs to Zed's keymap.json (backup: keymap.json.bak). Skip those with --no-click. The hook command uses the Python interpreter
that runs this installer. Other settings and hooks are left untouched; a previous notify-hook
entry (or the older notify-toast.ps1 entry) is replaced instead of duplicated.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from focus import PROTOCOL, ZED_BINDINGS  # noqa: E402

PKG = Path(__file__).resolve().parent
EVENT = "Notification"
TAB_EVENT = "UserPromptSubmit"  # tabname.py: names the Zed tab (Windows click-to-focus only)
EVENTS = (EVENT, TAB_EVENT)
INSTALL_REL = Path("hooks") / "notify-hook"
OUR_MARKERS = ("/hooks/notify-hook/notify.py", "/hooks/notify-hook/tabname.py", "/hooks/notify-toast.ps1")
SCRIPTS = ("notify.py", "focus.py", "tabname.py")


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
        shown = {e: data.get("hooks", {}).get(e) for e in EVENTS}
        print(f"[dry-run] would write {path}:\n{json.dumps(shown, indent=2)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"updated {path}")


def is_ours(entry: dict) -> bool:
    return any(any(m in h.get("command", "").replace("\\", "/") for m in OUR_MARKERS)
               for h in entry.get("hooks", []))


def strip_ours(settings: dict) -> bool:
    hooks = settings.get("hooks", {})
    changed = False
    for event in EVENTS:
        entries = hooks.get(event, [])
        kept = [e for e in entries if not is_ours(e)]
        if len(kept) == len(entries):
            continue
        changed = True
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if "hooks" in settings and not settings["hooks"]:
        del settings["hooks"]
    return changed


def hook_command(script: Path) -> str:
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'


# ---------------------------------------------------------------- click-to-focus (Windows)

def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def protocol_command(focus_script: Path) -> str:
    return f'"{pythonw()}" "{focus_script}" "%1"'


def register_protocol(focus_script: Path, dry: bool, reg=None) -> None:
    cmd = protocol_command(focus_script)
    if dry:
        print(f"[dry-run] register {PROTOCOL}: -> {cmd}")
        return
    import winreg
    reg = reg or winreg
    base = rf"Software\Classes\{PROTOCOL}"
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, "URL:Claude notify focus")
        reg.SetValueEx(k, "URL Protocol", 0, reg.REG_SZ, "")
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, cmd)
    print(f"registered {PROTOCOL}: -> {cmd}")


def unregister_protocol(dry: bool) -> None:
    if dry:
        print(f"[dry-run] unregister {PROTOCOL}:")
        return
    import winreg
    base = rf"Software\Classes\{PROTOCOL}"
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + sub)
        except OSError:
            pass
    print(f"unregistered {PROTOCOL}:")


def zed_keymap_path() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Zed" / "keymap.json"


def _binding_owned(block: dict) -> bool:
    ours = {(b["context"], k) for b in ZED_BINDINGS for k in b["bindings"]}
    return any((block.get("context"), k) in ours for k in (block.get("bindings") or {}))


def update_keymap(path: Path, add: bool, dry: bool) -> None:
    """Add (or remove) focus.py's bindings in Zed's keymap.json, keeping everything else."""
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        try:
            data = json.loads(text) if text else []
        except ValueError:
            print(f"{path} has comments or invalid JSON; add these bindings by hand:\n"
                  f"{json.dumps(ZED_BINDINGS, indent=2)}")
            return
    else:
        data = []
    if not isinstance(data, list):
        print(f"{path}: expected a JSON array; skipped")
        return
    ours = {(b["context"], k) for b in ZED_BINDINGS for k in b["bindings"]}
    kept = []
    for block in data:
        if isinstance(block, dict) and _binding_owned(block):
            rest = {k: v for k, v in block["bindings"].items() if (block.get("context"), k) not in ours}
            if rest:
                kept.append({**block, "bindings": rest})
        else:
            kept.append(block)
    new = kept + ([dict(b) for b in ZED_BINDINGS] if add else [])
    if new == data:
        print(f"Zed keymap already up to date: {path}")
        return
    if dry:
        print(f"[dry-run] would write {path}")
        return
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{'added' if add else 'removed'} notify-hook bindings in {path}")


# ---------------------------------------------------------------- hook entry

def install(claude_dir: Path, settings_path: Path, matcher: Optional[str], dry: bool,
            click: bool = True, keymap: Optional[Path] = None) -> None:
    target = claude_dir / INSTALL_REL
    script = target / "notify.py"
    for name in SCRIPTS:
        if dry:
            print(f"[dry-run] copy {PKG / name} -> {target / name}")
        else:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PKG / name, target / name)
            print(f"copied {name} -> {target / name}")
    if click and sys.platform == "win32":
        register_protocol(target / "focus.py", dry)
        update_keymap(keymap or zed_keymap_path(), True, dry)

    s = read_json(settings_path)
    if strip_ours(s):
        print("replaced the previous notify hook entry")
    entry: dict = {"hooks": [{"type": "command", "command": hook_command(script), "timeout": 15}]}
    if matcher:
        entry = {"matcher": matcher, **entry}
    s.setdefault("hooks", {}).setdefault(EVENT, []).append(entry)
    if click and sys.platform == "win32":
        tab_entry = {"hooks": [{"type": "command", "command": hook_command(target / "tabname.py"), "timeout": 10}]}
        s["hooks"].setdefault(TAB_EVENT, []).append(tab_entry)
    write_json(settings_path, s, dry)


def uninstall(claude_dir: Path, settings_path: Path, dry: bool,
              click: bool = True, keymap: Optional[Path] = None) -> None:
    if click and sys.platform == "win32":
        unregister_protocol(dry)
        update_keymap(keymap or zed_keymap_path(), False, dry)
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
    ap.add_argument("--no-click", action="store_true",
                    help="skip the Windows click-to-focus setup (URL scheme + Zed keymap)")
    ap.add_argument("--claude-dir", help=argparse.SUPPRESS)  # override ~/.claude (tests)
    ap.add_argument("--zed-keymap", help=argparse.SUPPRESS)  # override Zed keymap path (tests)
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

    click = not args.no_click
    keymap = Path(args.zed_keymap) if args.zed_keymap else None
    if args.uninstall:
        uninstall(claude_dir, settings_path, args.dry_run, click, keymap)
    else:
        install(claude_dir, settings_path, args.matcher, args.dry_run, click, keymap)
        if not args.dry_run:
            print("\nTest it:  echo '{\"message\":\"hello\"}' | " + hook_command(claude_dir / INSTALL_REL / "notify.py"))
            print("Claude Code reloads settings on its own; if nothing fires, open /hooks once or restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
