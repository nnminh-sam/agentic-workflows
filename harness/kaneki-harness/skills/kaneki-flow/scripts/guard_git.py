#!/usr/bin/env python3
"""PreToolUse guard for the Bash and PowerShell tools while kaneki-flow is active: the node rules only.

my-git (the project's git plugin) owns identity, SSH-key, remote and commit-message rules. This guard adds what
my-git does not know about the flow (user decision: nodes never commit; only kaneki-ship does):

- HISTORY: commit, merge, rebase, push, … are refused, except `commit` / `push` in the checkout of a request the
  user approved in /kaneki-ship (`sdlc ship` writes `<project>/.sdlc/ship-approvals.json`, valid 30 min).
- DESTRUCTIVE: reset --hard, clean -f, branch -D, … are refused (never delete work).

Self-contained on purpose (stdlib, no harness import): a moved or missing harness can never block every git command.
Keep HISTORY and destructive() in sync with harness/gitpolicy.py (a test checks it).

Fail closed: Claude Code treats any exit code other than 2 as "allow", so an unexpected error is turned into exit 2
for commands that run git (Python 3.9 compatible: stock macOS).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

HISTORY = {"commit", "merge", "rebase", "cherry-pick", "revert", "am", "tag", "push", "pull", "filter-branch",
           "replace", "notes", "fast-import", "commit-tree"}
APPROVABLE = {"commit", "push"}
APPROVAL_PATHS = (Path(".sdlc") / "ship-approvals.json", Path(".claude") / "kaneki-ship-approvals.json")
APPROVALS = APPROVAL_PATHS[0]
# The driver's own `git` subcommand, through any launcher: sdlc, sdlc.py, sdlc.cmd, sdlc-<host>, sdlc-<host>.cmd.
WRAPPER_RE = re.compile(r"""sdlc(?:-[A-Za-z0-9_]+)?(?:\.py|\.cmd)?["']?(?:\s+--profile\s+(?:"[^"]*"|'[^']*'|\S+))?\s+git\b""")
GIT_RE = re.compile(r"""(?:^|[\s;|&(`]|\$\()(?:[^\s;|&()`'"]*[/\\])?git(?:\.exe)?(?=\s)""")


def sub_and_rest(args: list[str]) -> tuple[str, list[str], str | None]:
    """Skip git's global options; return (subcommand, rest, the `-C <path>` value if any)."""
    i, cdir = 0, None
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            if args[i] == "-C":
                cdir = args[i + 1] if cdir is None else os.path.join(cdir, args[i + 1])
            i += 2
        else:
            i += 1
    return (args[i] if i < len(args) else ""), args[i + 1:], cdir


def destructive(sub: str, rest: list[str]) -> bool:
    return (
        (sub == "reset" and "--hard" in rest)
        or (sub == "clean" and any(a.startswith("-") and "f" in a.lstrip("-") for a in rest if not a.startswith("--")))
        or (sub == "clean" and "--force" in rest)
        or (sub == "checkout" and ("--" in rest or "." in rest) and "-b" not in rest)
        or (sub == "restore" and "--staged" not in rest)
        or (sub == "branch" and any(a in ("-D", "-d", "--delete") for a in rest))
        or (sub == "stash" and rest[:1] in (["drop"], ["clear"]))
        or (sub == "worktree" and rest[:1] == ["remove"] and ("--force" in rest or "-f" in rest))
        or (sub == "update-ref" and "-d" in rest)
        or (sub == "gc" and any(a.startswith("--prune") for a in rest))
    )


def _same(a: str, b: str) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def approvals(project: str | None) -> list[dict]:
    """Unexpired ship approvals, from the project dir upwards (like the hook finds this script)."""
    d = Path(project) if project else None
    while d is not None:
        found_file = False
        items: list[dict] = []
        now = time.time()
        for rel in APPROVAL_PATHS:
            f = d / rel
            if not f.is_file():
                continue
            found_file = True
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
            if isinstance(data, dict):
                items.extend(a for a in data.get("approvals", []) if isinstance(a, dict) and a.get("expiresAt", 0) > now)
        if found_file:
            return items
        d = d.parent if d.parent != d else None
    return []


def approved(sub: str, cdir: str | None, cwd: str | None, project: str | None) -> bool:
    if sub not in APPROVABLE:
        return False
    target = cdir if cdir and os.path.isabs(cdir) else os.path.join(cwd or os.getcwd(), cdir or "")
    return any(sub in a.get("allow", []) and _same(a.get("path", ""), target) for a in approvals(project))


def refusals(command: str, cwd: str | None = None, project: str | None = None) -> list[str]:
    """Every plain `git …` in a shell command line that the node rules refuse. Calls of the wrapper
    (`… sdlc … git --ticket …`) are skipped; the wrapper checks its own arguments."""
    command = WRAPPER_RE.sub("sdlc SDLCWRAPPEDGIT", command)
    out = []
    for m in GIT_RE.finditer(command):
        segment = re.split(r"[;|&]|\$\(|`|\n", command[m.end():], maxsplit=1)[0]
        try:
            args = shlex.split(segment, posix=True)
        except ValueError:
            args = segment.split()
        sub, rest, cdir = sub_and_rest(args)
        if sub in HISTORY:
            if approved(sub, cdir, cwd, project):
                continue
            out.append(f"git {segment.strip()[:80]}: nodes never change history; only /kaneki-ship commits and pushes "
                       "(through my-git, after the user approves the request)")
        elif destructive(sub, rest):
            out.append(f"git {segment.strip()[:80]}: can delete work; nodes never delete")
    return out


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print("Blocked by kaneki-flow guard: unreadable hook input.", file=sys.stderr)
        return 2
    command = str((data.get("tool_input") or {}).get("command") or "")
    if "git" not in command:
        return 0
    refused = refusals(command, data.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd"))
    if refused:
        print("Blocked by kaneki-flow guard:\n- " + "\n- ".join(refused), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a crashing guard must block, never silently allow
        print(f"Blocked by kaneki-flow guard: internal error ({type(exc).__name__}: {exc}); git commands are refused "
              "until the guard works.", file=sys.stderr)
        sys.exit(2)
