#!/usr/bin/env python3
"""Claude Code PreToolUse hook (Bash | PowerShell): enforce the git-guard contracts.

stdin : hook JSON ({"tool_name", "tool_input": {"command"}, "cwd", ...})
stdout: a PreToolUse decision JSON when a rule fires; nothing when the command is clean.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import contracts as contracts_mod  # noqa: E402
from gitguard.engine import dialect_for, evaluate  # noqa: E402


def decide(payload: dict, contracts=None, **kw) -> dict | None:
    """Return the hook output object, or None to let the command through untouched."""
    tool = payload.get("tool_name", "")
    if tool not in ("Bash", "PowerShell"):
        return None
    command = (payload.get("tool_input") or {}).get("command", "")
    cwd = payload.get("cwd") or os.getcwd()
    if contracts is None:
        contracts = contracts_mod.load()
    verdict = evaluate(command, dialect_for(tool), cwd, contracts, **kw)
    decision = verdict.decision
    if decision == "allow":
        return None
    if decision == "warn":
        text = "git-guard warnings: " + "; ".join(f.render() for f in verdict.findings)
        return {
            "systemMessage": text,
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text},
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": verdict.reason(),
        }
    }


def _fail_safe(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"git-guard could not check this command: {reason}",
        }
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps(_fail_safe(f"bad hook input ({e})")))
        return 0
    try:
        out = decide(payload)
    except contracts_mod.ContractError as e:
        cmd = (payload.get("tool_input") or {}).get("command", "")
        out = _fail_safe(str(e)) if any(k in cmd for k in ("git", "gh", "github")) else None
    except Exception as e:  # never crash the agent loop; ask the user instead
        out = _fail_safe(f"{type(e).__name__}: {e}")
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
