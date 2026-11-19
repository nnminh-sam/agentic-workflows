#!/usr/bin/env python3
"""Install my-git (the git-guard hooks plus the commit/push skills) into a Claude Code project
(or for your user account).

  python install.py D:/work/my-project                 # plugin mode (default)
  python install.py D:/work/my-project --mode vendor   # copy the hook into the project
  python install.py --scope user                       # plugin mode for every project
  python install.py D:/work/my-project --uninstall     # undo either mode
  python install.py D:/work/my-project --repo-setup    # also apply the branching-model repo template
                                                       # (PR template + .githooks; see scripts/setup_repo.py)

plugin  Registers this folder as the local marketplace "my-git-local" and enables
        "my-git@my-git-local" in <project>/.claude/settings.json (or ~/.claude/settings.json
        with --scope user). The code stays here, so updating this folder updates every project
        (run `/plugin marketplace update my-git-local` in Claude Code to pick it up). The old
        "git-guard@git-guard-local" entries are removed.
        Claude Code asks you to trust the marketplace the first time it opens the project.
vendor  Copies scripts/, contracts/, viewer/ to <project>/.claude/hooks/git-guard/, the skills to
        <project>/.claude/skills/{git-guard,my-git-commit,my-git-push,my-git-pr}/, and adds the hooks to
        <project>/.claude/settings.json.
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
MARKETPLACE = "my-git-local"
PLUGIN_ID = f"my-git@{MARKETPLACE}"
LEGACY_MARKETPLACE = "git-guard-local"
LEGACY_PLUGIN_ID = f"git-guard@{LEGACY_MARKETPLACE}"
VENDOR_REL = Path(".claude") / "hooks" / "git-guard"
SKILLS_REL = Path(".claude") / "skills"
# shipped skill dir -> vendored skill dir (commit/push get a prefix so they can't clash with a project's own skills)
VENDOR_SKILLS = {"git-guard": "git-guard", "commit": "my-git-commit", "push": "my-git-push", "pr": "my-git-pr"}
SKILL_REL = SKILLS_REL / "git-guard"
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

def drop_legacy_plugin(s: dict) -> bool:
    """Remove the pre-rename git-guard@git-guard-local entries so the hooks don't run twice."""
    changed = s.get("enabledPlugins", {}).pop(LEGACY_PLUGIN_ID, None) is not None
    changed |= s.get("extraKnownMarketplaces", {}).pop(LEGACY_MARKETPLACE, None) is not None
    return changed


def install_plugin(settings_path: Path, dry: bool) -> None:
    s = read_json(settings_path)
    if drop_legacy_plugin(s):
        print(f"removed the old {LEGACY_PLUGIN_ID} plugin (renamed to {PLUGIN_ID})")
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
    drop_legacy_plugin(s)
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


def vendor_skill(src: Path, dst: Path, name: str, dry: bool) -> None:
    """Copy a skill, pointing ${CLAUDE_PLUGIN_ROOT} at the vendored hook folder and renaming it."""
    text = (src / "SKILL.md").read_text(encoding="utf-8")
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", VENDOR_REL.as_posix())
    text = (text.replace("my-git:commit", "my-git-commit").replace("/my-git:push", "/my-git-push")
            .replace("/my-git:pr", "/my-git-pr"))
    text = text.replace("<root>/skills/commit/SKILL.md", f"{(SKILLS_REL / VENDOR_SKILLS['commit']).as_posix()}/SKILL.md")
    lines = text.split("\n")
    lines = [f"name: {name}" if ln.startswith("name: ") else ln for ln in lines]
    if dry:
        print(f"[dry-run] copy skill {src} -> {dst}")
        return
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def install_vendor(project: Path, force: bool, dry: bool) -> None:
    target = project / VENDOR_REL
    for sub in ("scripts", "viewer", "templates"):
        copy_tree(PKG / sub, target / sub, keep_existing=False, dry=dry)
    copy_tree(PKG / "contracts", target / "contracts", keep_existing=not force, dry=dry)
    copy_tree(PKG / "tests", target / "tests", keep_existing=False, dry=dry)
    for name in ("README.md", ".gitignore"):
        if not dry:
            shutil.copy2(PKG / name, target / name)
    for src_name, dst_name in VENDOR_SKILLS.items():
        vendor_skill(PKG / "skills" / src_name, project / SKILLS_REL / dst_name, dst_name, dry)
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
    for pid in (PLUGIN_ID, LEGACY_PLUGIN_ID):
        if s.get("enabledPlugins", {}).get(pid):
            s["enabledPlugins"][pid] = False
            print(f"disabled the {pid} plugin in this project (the vendored copy replaces it)")
    write_json(settings_path, s, dry)


def uninstall_vendor(project: Path, dry: bool) -> None:
    settings_path = project / ".claude" / "settings.json"
    s = read_json(settings_path)
    if strip_vendored_hooks(s):
        write_json(settings_path, s, dry)
    for d in (project / VENDOR_REL, *(project / SKILLS_REL / n for n in VENDOR_SKILLS.values())):
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
    ap.add_argument("--repo-setup", choices=("apply", "bootstrap", "all"), nargs="?", const="apply",
                    help="also run scripts/setup_repo.py on the project: apply (default) = PR template + .githooks; "
                         "bootstrap = also git init + develop/base; all = also configure GitHub "
                         "(push, default branch, ruleset)")
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

    if args.repo_setup and not args.uninstall:
        if args.scope == "user":
            ap.error("--repo-setup needs a project, not --scope user")
        import subprocess
        cmd = [sys.executable, str(PKG / "scripts" / "setup_repo.py"), args.repo_setup, str(project)]
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"\nrepo setup: {' '.join(cmd)}")
        subprocess.run(cmd, check=False)

    if not args.uninstall:
        setup = (f'python3 "{(PKG / "scripts" / "setup_profile.py").as_posix()}"' if args.mode == "plugin"
                 else "python3 .claude/hooks/git-guard/scripts/setup_profile.py")
        print("\nNext steps:")
        print("  1. Restart Claude Code in the project (accept the marketplace trust prompt in plugin mode).")
        print(f"  2. From the project root, set who you are:")
        print(f"       single GitHub account (HTTPS + gh):    {setup} init --from-current --transport gh")
        print(f"       SSH with a pinned key:                  {setup} init --from-current --ssh-key <key path>")
        print(f"  3. Check it:                                 {setup} show")
    return 0


if __name__ == "__main__":
    sys.exit(main())
