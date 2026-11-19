#!/usr/bin/env python3
"""PreToolUse guard for the Bash and PowerShell tools while kaneki-flow is active.

Reads the hook input (JSON on stdin), finds every plain `git …` in the command line and refuses what the project
git policy forbids (harness/gitpolicy.py): history writes and pushes (only kaneki-ship does those), deleting work,
identity overrides, and remote access outside `sdlc git`. Exit 2 blocks the tool call and shows the reason.

Fail closed: Claude Code treats any exit code other than 2 as "allow", so every unexpected error is turned into
exit 2 for commands that mention git (Python 3.9 compatible: stock macOS).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def harness_home() -> Path | None:
    env = os.environ.get("SDLC_HOME")
    if env and (Path(env) / "harness").is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    marker = here.parent / ".harness-home"
    if marker.is_file():
        p = Path(marker.read_text(encoding="utf-8").strip())
        if (p / "harness").is_dir():
            return p
    for parent in here.parents:
        if (parent / "graph" / "lifecycle.json").is_file() and (parent / "harness").is_dir():
            return parent
    return None


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print("Blocked by kaneki-flow guard: unreadable hook input.", file=sys.stderr)
        return 2
    command = str((data.get("tool_input") or {}).get("command") or "")
    if "git" not in command:
        return 0
    home = harness_home()
    if home is None:
        print("Blocked by kaneki-flow guard: harness not found (set SDLC_HOME or reinstall with `sdlc install`); "
              "git commands are refused until the policy can be checked.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(home))
    from harness.gitpolicy import shell_refusals  # noqa: E402
    refused = shell_refusals(command)
    if refused:
        print("Blocked by kaneki-flow guard:\n- " + "\n- ".join(refused) +
              "\nUse `sdlc git --ticket <id> --repo <repo> -- <args>` for allowed git commands; commits and pushes "
              "happen only through kaneki-ship.", file=sys.stderr)
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
