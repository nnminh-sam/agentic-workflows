"""`sdlc install`: install Kaneki playbooks, host preambles, shims, and host bindings.

Supports `--host antigravity|claude|codex|gemini|cursor|opencode|generic` (default `antigravity`).
- Project installs write `.sdlc/bin/sdlc` (POSIX shim with LF line endings), `.sdlc/.harness-home`,
  and `.sdlc/host.local.json`.
- Host skill/rule files compose `hosts/<host>/preamble.md` + canonical `playbooks/{flow,onboarding,ship}.md`.
- `<hostRoot>/kaneki-harness.json` tracks version and file digests so re-installs preserve user edits
  (`keptYourEdits`) unless `--force` is passed.
- Optional Claude Code plugin registration (`--with-my-git`) is strictly opt-in for `--host claude`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import __version__
from . import hosts as H
from .errors import HarnessError
from .state import now, sha256_bytes, sha256_file

SKILLS: tuple[tuple[str, str], ...] = (
    ("kaneki-flow", "flow.md"),
    ("kaneki-onboarding", "onboarding.md"),
    ("kaneki-ship", "ship.md"),
)
MANIFEST = "kaneki-harness.json"


def _split_frontmatter(text: str) -> tuple[str, dict[str, str], str]:
    """Return `(raw_yaml_block_with_delimiters, parsed_simple_kv, body)`."""
    if not text.startswith("---\n"):
        return "", {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", {}, text
    raw_block = text[: end + 5]
    yaml_body = text[4:end]
    meta: dict[str, str] = {}
    for line in yaml_body.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.+)$", line)
        if m:
            val = m.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            meta[m.group(1)] = val
    return raw_block, meta, text[end + 5:].lstrip("\n")


def _render_skill_doc(home: Path, host: str, skill: str, playbook_file: str, *, mode: str) -> str:
    """Compose a host-native SKILL.md or Cursor .mdc rule from `hosts/<host>/preamble.md` + `playbooks/`."""
    orig = (home / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    raw_fm, meta, _ = _split_frontmatter(orig)
    name = meta.get("name", skill)
    desc = meta.get("description", "")
    preamble = H.load_preamble(home, host).strip()
    playbook = (home / "playbooks" / playbook_file).read_text(encoding="utf-8").strip()

    if mode == "claude":
        header = raw_fm.rstrip("\n")
    elif mode == "mdc":
        header = f"---\ndescription: {json.dumps(desc)}\nalwaysApply: false\n---"
    elif mode == "markdown":
        header = ""
    else:
        header = f"---\nname: {name}\ndescription: {json.dumps(desc)}\n---"

    parts = [p for p in (header, preamble, playbook) if p]
    return "\n\n".join(parts) + "\n"


def _copy_tree_entries(src_dir: Path, dest_dir: Path, key_prefix: str, *,
                       exclude_names: set[str] | None = None,
                       exclude_parts: set[str] | None = None) -> list[tuple[Path, str, bytes, bool]]:
    entries: list[tuple[Path, str, bytes, bool]] = []
    ex_names = exclude_names or set()
    ex_parts = exclude_parts or set()
    for src in sorted(src_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        if "__pycache__" in rel.parts or rel.name in ex_names or (ex_parts & set(rel.parts)):
            continue
        is_exec = bool(src.stat().st_mode & 0o111)
        entries.append((dest_dir / rel, f"{key_prefix}/{rel.as_posix()}", src.read_bytes(), is_exec))
    return entries


def _shim_script(home: Path) -> bytes:
    resolved = home.resolve().as_posix()
    script = (
        "#!/bin/sh\n"
        "set -eu\n"
        'SDLC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        'H=${SDLC_HOME:-}\n'
        'if [ -z "$H" ] && [ -f "$SDLC_DIR/.harness-home" ]; then\n'
        '  H=$(cat "$SDLC_DIR/.harness-home")\n'
        "fi\n"
        'if [ -z "$H" ]; then\n'
        f'  H="{resolved}"\n'
        "fi\n"
        'exec sh "$H/skills/kaneki-flow/scripts/sdlc" "$@"\n'
    )
    return script.encode("utf-8")


def _pointer_doc(host: str) -> bytes:
    if host == "codex":
        text = (
            "# Kaneki SDLC Workflow (Codex CLI)\n\n"
            "Kaneki workflows are installed under `.agents/skills/` (`kaneki-flow`, `kaneki-onboarding`, `kaneki-ship`).\n"
            "Use `sh .sdlc/bin/sdlc` for every driver call and `sh .sdlc/bin/sdlc dispatch` to run units.\n"
        )
    elif host == "gemini":
        text = (
            "# Kaneki SDLC Workflow (Gemini CLI)\n\n"
            "Kaneki skills live under `.gemini/skills/` (`kaneki-flow`, `kaneki-onboarding`, `kaneki-ship`).\n"
            "Run every driver command with `sh .sdlc/bin/sdlc` and dispatch units with `sh .sdlc/bin/sdlc dispatch`.\n"
        )
    else:
        text = (
            "# Kaneki SDLC Workflow\n\n"
            "Kaneki playbooks live under `.sdlc/playbooks/` (`flow.md`, `onboarding.md`, `ship.md`, `nodes/`, `roles/`).\n"
            "Run every driver command via `sh .sdlc/bin/sdlc` and unit subprocesses via `sh .sdlc/bin/sdlc dispatch`.\n"
        )
    return text.encode("utf-8")


def _collect_host_entries(home: Path, host: str, project_root: Path, host_root: Path) -> list[tuple[Path, str, bytes, bool]]:
    entries: list[tuple[Path, str, bytes, bool]] = []

    if host == "claude":
        for skill, pb in SKILLS:
            src_skill = home / "skills" / skill
            for dest, key, data, is_exec in _copy_tree_entries(
                src_skill, host_root / "skills" / skill, f"skills/{skill}",
                exclude_names={".harness-home"}, exclude_parts={"agents"},
            ):
                if key == f"skills/{skill}/SKILL.md":
                    data = _render_skill_doc(home, "claude", skill, pb, mode="claude").encode("utf-8")
                entries.append((dest, key, data, is_exec))
        entries.extend(
            _copy_tree_entries(home / "skills" / "kaneki-flow" / "agents", host_root / "agents", "agents")
        )
        return entries

    if host in ("antigravity", "codex", "gemini", "opencode"):
        for skill, pb in SKILLS:
            rendered = _render_skill_doc(home, host, skill, pb, mode="skill").encode("utf-8")
            entries.append((host_root / "skills" / skill / "SKILL.md", f"skills/{skill}/SKILL.md", rendered, False))
        entries.extend(_copy_tree_entries(
            home / "playbooks" / "nodes",
            host_root / "skills" / "kaneki-flow" / "references" / "nodes",
            "skills/kaneki-flow/references/nodes",
        ))
        entries.extend(_copy_tree_entries(
            home / "playbooks" / "roles",
            host_root / "skills" / "kaneki-flow" / "references" / "roles",
            "skills/kaneki-flow/references/roles",
        ))
        ir = home / "playbooks" / "interpret-repo.md"
        entries.append((
            host_root / "skills" / "kaneki-onboarding" / "references" / "interpret-repo.md",
            "skills/kaneki-onboarding/references/interpret-repo.md",
            ir.read_bytes(),
            False,
        ))
        if host == "codex":
            entries.append((project_root / "AGENTS.md", "project:AGENTS.md", _pointer_doc("codex"), False))
        elif host == "gemini":
            for name in ("guard-git", "guard_git.py"):
                p = home / "skills" / "kaneki-flow" / "scripts" / name
                entries.append((host_root / "scripts" / name, f"scripts/{name}", p.read_bytes(), bool(p.stat().st_mode & 0o111)))
            entries.append((project_root / "GEMINI.md", "project:GEMINI.md", _pointer_doc("gemini"), False))
        return entries

    if host == "cursor":
        for skill, pb in SKILLS:
            rendered = _render_skill_doc(home, "cursor", skill, pb, mode="mdc").encode("utf-8")
            entries.append((host_root / "rules" / f"{skill}.mdc", f"rules/{skill}.mdc", rendered, False))
        entries.extend(_copy_tree_entries(home / "playbooks" / "nodes", host_root / "kaneki" / "nodes", "kaneki/nodes"))
        entries.extend(_copy_tree_entries(home / "playbooks" / "roles", host_root / "kaneki" / "roles", "kaneki/roles"))
        ir = home / "playbooks" / "interpret-repo.md"
        entries.append((host_root / "kaneki" / "interpret-repo.md", "kaneki/interpret-repo.md", ir.read_bytes(), False))
        return entries

    # generic
    for skill, pb in SKILLS:
        rendered = _render_skill_doc(home, "generic", skill, pb, mode="markdown").encode("utf-8")
        entries.append((host_root / "playbooks" / pb, f"playbooks/{pb}", rendered, False))
    entries.extend(_copy_tree_entries(home / "playbooks" / "nodes", host_root / "playbooks" / "nodes", "playbooks/nodes"))
    entries.extend(_copy_tree_entries(home / "playbooks" / "roles", host_root / "playbooks" / "roles", "playbooks/roles"))
    ir = home / "playbooks" / "interpret-repo.md"
    entries.append((host_root / "playbooks" / "interpret-repo.md", "playbooks/interpret-repo.md", ir.read_bytes(), False))
    entries.append((project_root / "AGENTS.md", "project:AGENTS.md", _pointer_doc("generic"), False))
    return entries


def _host_dirs(target: Path, host: str) -> tuple[Path, Path, bool]:
    """Return `(project_root, host_root, is_claude_user_scope)`."""
    if host == "claude":
        claude = target / ".claude" if target.name != ".claude" else target
        user_scope = claude.resolve() == (Path.home() / ".claude").resolve()
        return claude.parent, claude, user_scope
    sub = {
        "antigravity": ".agents",
        "codex": ".agents",
        "gemini": ".gemini",
        "cursor": ".cursor",
        "opencode": ".opencode",
        "generic": ".sdlc",
    }[host]
    return target, target / sub, False


def _next_hint(host: str, with_my_git: bool) -> str:
    if host == "claude" and with_my_git:
        return ("Restart Claude Code in the project (accept the my-git marketplace trust prompt), "
                "then /kaneki-onboarding, then /kaneki-flow <ticket>.")
    if host == "claude":
        return "Start Claude Code in the project and run /kaneki-onboarding, then /kaneki-flow <ticket>."
    if host == "antigravity":
        return ("Run kaneki-onboarding then kaneki-flow <ticket> using .agents/skills/ "
                "(driver CLI via `sh .sdlc/bin/sdlc`).")
    return f"Installed for {host}; invoke the kaneki-onboarding workflow and run `sh .sdlc/bin/sdlc`."


def install(home: Path, target: Path, *, host: str = "antigravity", force: bool = False,
            dry_run: bool = False, with_my_git: bool = False) -> dict[str, Any]:
    from . import mygit

    if not target.is_dir():
        raise HarnessError(f"install target is not a folder: {target}")
    if host not in H.KNOWN_HOSTS:
        raise HarnessError(f"unknown host {host!r}; known: {', '.join(H.KNOWN_HOSTS)}")
    if with_my_git and host != "claude":
        raise HarnessError("--with-my-git is only supported with --host claude")

    mygit_root: Path | None = None
    if with_my_git:
        mygit_root = mygit.require_root(home)  # checked first: no half install without the git toolchain

    project_root, host_root, user_scope = _host_dirs(target, host)
    manifest_path = host_root / MANIFEST
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": {}}

    entries = _collect_host_entries(home, host, project_root, host_root)
    if not user_scope:
        entries.append((project_root / ".sdlc" / "bin" / "sdlc", "project:.sdlc/bin/sdlc", _shim_script(home), True))

    written, kept, unchanged = [], [], []
    record: dict[str, str] = {}
    for dest, key, data, is_exec in entries:
        new_digest = sha256_bytes(data)
        if dest.is_file():
            cur = sha256_file(dest)
            if cur == new_digest:
                unchanged.append(key)
                record[key] = new_digest
                continue
            if not force and old.get("files", {}).get(key) != cur:
                kept.append(key)  # edited by user since last install (or pre-existing hand-written file)
                record[key] = cur
                continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if is_exec or os.name != "nt" and dest.name == "sdlc":
                try:
                    os.chmod(dest, 0o755)
                except OSError:
                    pass
        written.append(key)
        record[key] = new_digest

    if host == "claude" and not dry_run:
        for skill, _ in SKILLS:
            marker = host_root / "skills" / skill / "scripts" / ".harness-home"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(home.resolve()) + "\n", encoding="utf-8")

    binding_path = H.local_binding_path(project_root)
    wrote_binding = False
    if not user_scope and not dry_run:
        hh_marker = project_root / ".sdlc" / ".harness-home"
        hh_marker.parent.mkdir(parents=True, exist_ok=True)
        hh_marker.write_text(str(home.resolve()) + "\n", encoding="utf-8")
        binding_path, wrote_binding = H.write_local_binding(home, project_root, host, overwrite=force)

    my_git: dict[str, Any] | None = None
    if with_my_git and mygit_root is not None:
        my_git = mygit.install(mygit_root, project_root, dry_run=dry_run, user_scope=user_scope)

    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "version": __version__,
        "host": host,
        "harnessHome": str(home.resolve()),
        "installedAt": now(),
        "files": record,
    }
    if my_git is not None:
        manifest["myGit"] = {"root": my_git["root"], "version": my_git["version"], "plugin": my_git["plugin"]}
    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    res: dict[str, Any] = {
        "host": host,
        "target": str(host_root),
        "projectRoot": str(project_root),
        "version": __version__,
        "written": written,
        "unchanged": len(unchanged),
        "keptYourEdits": kept,
        "dryRun": dry_run,
        "next": _next_hint(host, with_my_git),
    }
    if not user_scope:
        res["shim"] = str(project_root / ".sdlc" / "bin" / "sdlc")
        res["hostBinding"] = str(binding_path)
        res["hostBindingWritten"] = wrote_binding
    if my_git is not None:
        res["myGit"] = my_git
    return res
