#!/usr/bin/env python3
"""Install git-guard into a Claude Code project (or for your user account).

  python install.py D:/work/my-project                 # plugin mode (default)
  python install.py D:/work/my-project --mode vendor   # copy the hook into the project
  python install.py --scope user                       # plugin mode for every project
  python install.py D:/work/my-project --uninstall     # undo either mode

plugin  Registers this folder as the local marketplace "git-guard-local" and enables
        "git-guard@git-guard-local" in <project>/.claude/settings.json (or ~/.claude/settings.json
        with --scope user). The code stays here, so updating this folder updates every project
        (run `/plugin marketplace update git-guard-local` in Claude Code to pick it up).
        Claude Code asks you to trust the marketplace the first time it opens the project.
vendor  Copies scripts/, contracts/, viewer/ to <project>/.claude/hooks/git-guard/, the skill to
        <project>/.claude/skills/git-guard/, and adds the hooks to <project>/.claude/settings.json.
        The project is then self-contained (commit it); re-run to upgrade. Existing contracts and
        profile.local.json are kept unless --force.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
MARKETPLACE = "git-guard-local"
PLUGIN_ID = f"git-guard@{MARKETPLACE}"
VENDOR_REL = Path(".claude") / "hooks" / "git-guard"
SKILL_REL = Path(".claude") / "skills" / "git-guard"
HOOK_MARK = "/.claude/hooks/git-guard/scripts/"
VENDOR_HOOKS = {
    "PreToolUse": {"matcher": "Bash|PowerShell", "script": "pre_tool_use.py"},
    "SessionStart": {"matcher": "startup|resume|clear", "script": "session_start.py"},
}


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
        print(f"[dry-run] would write {path}:\n{json.dumps(data, indent=2)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"updated {path}")


def is_vendored_hook(entry: dict) -> bool:
    return any(HOOK_MARK in h.get("command", "").replace("\\", "/") for h in entry.get("hooks", []))


def strip_vendored_hooks(settings: dict) -> bool:
    hooks = settings.get("hooks", {})
    changed = False
    for event in list(hooks):
        kept = [e for e in hooks[event] if not is_vendored_hook(e)]
        if len(kept) != len(hooks[event]):
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not hooks and "hooks" in settings:
        del settings["hooks"]
    return changed


# --- plugin mode ---------------------------------------------------------------------------

def install_plugin(settings_path: Path, dry: bool) -> None:
    s = read_json(settings_path)
    s.setdefault("extraKnownMarketplaces", {})[MARKETPLACE] = {
        "source": {"source": "directory", "path": PKG.as_posix()}}
    s.setdefault("enabledPlugins", {})[PLUGIN_ID] = True
    if strip_vendored_hooks(s):
        print("removed vendored git-guard hooks from settings (the plugin replaces them)")
    write_json(settings_path, s, dry)


def uninstall_plugin(settings_path: Path, dry: bool) -> None:
    s = read_json(settings_path)
    s.get("enabledPlugins", {}).pop(PLUGIN_ID, None)
    s.get("extraKnownMarketplaces", {}).pop(MARKETPLACE, None)
    for key in ("enabledPlugins", "extraKnownMarketplaces"):
        if key in s and not s[key]:
            del s[key]
    write_json(settings_path, s, dry)


# --- vendor mode ---------------------------------------------------------------------------

def copy_tree(src: Path, dst: Path, keep_existing: bool, dry: bool) -> None:
    for f in src.rglob("*"):
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        out = dst / f.relative_to(src)
        if keep_existing and out.exists():
            print(f"kept {out}")
            continue
        if dry:
            print(f"[dry-run] copy {f} -> {out}")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)


def install_vendor(project: Path, force: bool, dry: bool) -> None:
    target = project / VENDOR_REL
    for sub in ("scripts", "viewer"):
        copy_tree(PKG / sub, target / sub, keep_existing=False, dry=dry)
    copy_tree(PKG / "contracts", target / "contracts", keep_existing=not force, dry=dry)
    copy_tree(PKG / "tests", target / "tests", keep_existing=False, dry=dry)
    for name in ("README.md", ".gitignore"):
        if not dry:
            shutil.copy2(PKG / name, target / name)
    copy_tree(PKG / "skills" / "git-guard", project / SKILL_REL, keep_existing=False, dry=dry)
    print(f"{'[dry-run] ' if dry else ''}copied git-guard to {target}")

    settings_path = project / ".claude" / "settings.json"
    s = read_json(settings_path)
    strip_vendored_hooks(s)
    hooks = s.setdefault("hooks", {})
    for event, spec in VENDOR_HOOKS.items():
        hooks.setdefault(event, []).append({
            "matcher": spec["matcher"],
            "hooks": [{"type": "command", "timeout": 30,
                       "command": f'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/git-guard/scripts/{spec["script"]}"'}],
        })
    if s.get("enabledPlugins", {}).get(PLUGIN_ID):
        s["enabledPlugins"][PLUGIN_ID] = False
        print("disabled the git-guard plugin in this project (the vendored copy replaces it)")
    write_json(settings_path, s, dry)


def uninstall_vendor(project: Path, dry: bool) -> None:
    settings_path = project / ".claude" / "settings.json"
    s = read_json(settings_path)
    if strip_vendored_hooks(s):
        write_json(settings_path, s, dry)
    for d in (project / VENDOR_REL, project / SKILL_REL):
        if d.exists():
            print(f"{'[dry-run] would remove' if dry else 'removing'} {d}")
            if not dry:
                shutil.rmtree(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", help="project root (default: current directory)")
    ap.add_argument("--mode", choices=("plugin", "vendor"), default="plugin")
    ap.add_argument("--scope", choices=("project", "user"), default="project",
                    help="plugin mode only: user = ~/.claude/settings.json")
    ap.add_argument("--force", action="store_true", help="vendor mode: overwrite the project's contracts")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    project = Path(args.project or ".").expanduser().resolve()
    if args.mode == "vendor" and args.scope == "user":
        ap.error("--scope user only applies to --mode plugin")
    if not project.is_dir():
        ap.error(f"not a directory: {project}")
    if project == PKG or PKG in project.parents:
        ap.error("pick a project outside the package folder")

    if args.mode == "plugin":
        settings_path = (Path.home() / ".claude" / "settings.json" if args.scope == "user"
                         else project / ".claude" / "settings.json")
        (uninstall_plugin if args.uninstall else install_plugin)(settings_path, args.dry_run)
    elif args.uninstall:
        uninstall_vendor(project, args.dry_run)
    else:
        install_vendor(project, args.force, args.dry_run)

    if not args.uninstall:
        setup = (f'python3 "{(PKG / "scripts" / "setup_profile.py").as_posix()}"' if args.mode == "plugin"
                 else "python3 .claude/hooks/git-guard/scripts/setup_profile.py")
        print("\nNext steps:")
        print("  1. Restart Claude Code in the project (accept the marketplace trust prompt in plugin mode).")
        print(f"  2. From the project root, set who you are:  {setup} init --github-user <login> --ssh-key <key path>")
        print(f"  3. Check it:                                 {setup} show")
    return 0


if __name__ == "__main__":
    sys.exit(main())
