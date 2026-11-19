#!/usr/bin/env python3
"""Claude Code Notification hook: show a native desktop notification on any OS.

Claude Code pipes a JSON payload on stdin, e.g.
  {"message": "Claude needs your permission to use Bash", "notification_type": "permission_prompt",
   "cwd": "/path/to/project", "session_id": "...", "transcript_path": "..."}

The message says what Claude is waiting on, read from the tail of the session transcript:
  "Approve Bash: Run tests: npm test", "Answer: Which auth method?", "Waiting for you: <last reply>".
If the transcript can't be read, Claude Code's own message is shown.

When Claude Code runs in a Zed terminal on Windows, the toast carries a click target
(claude-notify-focus:?cwd=...&tab=...). focus.py handles it: it brings that Zed window forward
and switches to the session's terminal tab. install.py registers the URL scheme.

Backends, picked by platform:
  Windows  PowerShell + WinRT toast (no extra install)
  WSL      the Windows toast through powershell.exe
  macOS    osascript "display notification"
  Linux    notify-send (libnotify)
If no backend is available the hook does nothing. It always exits 0 so it never blocks Claude Code.

  python notify.py --dry-run < payload.json   # print the chosen command instead of running it
"""
from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

APP_TITLE = "Claude Code"
DEFAULT_MESSAGE = "Claude needs your input"
TIMEOUT_S = 10
PROTOCOL = "claude-notify-focus"
TAIL_BYTES = 768 * 1024
DETAIL_MAX = 160

# WinRT toast. Text and the click URL arrive through environment variables, so no quoting is needed.
# CC_NOTIFY_MESSAGE may hold two lines; each becomes a toast text line.
_WIN_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [System.Security.SecurityElement]::Escape($env:CC_NOTIFY_TITLE)
$lines = ($env:CC_NOTIFY_MESSAGE -split "`n" | Where-Object { $_ } | Select-Object -First 2 | ForEach-Object { '<text>' + [System.Security.SecurityElement]::Escape($_) + '</text>' }) -join ''
$launch = ''
if ($env:CC_NOTIFY_LAUNCH) { $launch = " activationType='protocol' launch='" + [System.Security.SecurityElement]::Escape($env:CC_NOTIFY_LAUNCH) + "'" }
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast$launch><visual><binding template='ToastGeneric'><text>$t</text>$lines</binding></visual><audio src='ms-winsoundevent:Notification.Default'/></toast>")
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""

_MAC_SCRIPT = [
    "-e", "on run argv",
    "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
    "-e", "end run",
]


# ---------------------------------------------------------------- payload

def load_payload(raw: str) -> dict:
    raw = raw.lstrip("﻿")  # PowerShell pipes add a BOM
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def title_message(data: dict, pending: Optional["Pending"] = None) -> tuple[str, str]:
    title, message = APP_TITLE, DEFAULT_MESSAGE
    if data.get("message"):
        message = str(data["message"])
    if data.get("title"):
        title = str(data["title"])
    cwd = data.get("cwd")
    if cwd:
        name = Path(str(cwd).replace("\\", "/").rstrip("/")).name
        if name:
            title = f"{title} - {name}"
    if pending is not None:
        better = pending.describe(str(data.get("notification_type") or ""))
        if better:
            message = better
        if pending.title:
            message = f"{message}\n{_short(pending.title, 80)}"
    return title, message


def parse_payload(raw: str) -> tuple[str, str]:
    """Return (title, message) from the hook's stdin JSON alone; tolerate empty or bad input."""
    return title_message(load_payload(raw))


# ---------------------------------------------------------------- transcript

def _short(text: str, limit: int = DETAIL_MAX) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _path_hint(p: str) -> str:
    parts = [x for x in str(p).replace("\\", "/").split("/") if x]
    return "/".join(parts[-2:])


def _first_str(inp: dict) -> str:
    return next((v for v in inp.values() if isinstance(v, str)), "")


def describe_tool(name: str, inp: dict) -> tuple[str, str]:
    """Return (what you need to do, detail) for a tool call waiting on you."""
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Bash", "PowerShell"):
        cmd = _short(inp.get("command", ""))
        desc = inp.get("description")
        return f"Approve {name}", (f"{_short(desc, 70)}: {cmd}" if desc else cmd)
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read"):
        return f"Approve {name}", _path_hint(inp.get("file_path") or inp.get("notebook_path") or "")
    if name == "AskUserQuestion":
        qs = inp.get("questions") or []
        q = qs[0].get("question", "") if qs and isinstance(qs[0], dict) else ""
        more = f" (+{len(qs) - 1} more)" if len(qs) > 1 else ""
        return "Answer", _short(q) + more
    if name == "ExitPlanMode":
        return "Review the plan", _short(inp.get("plan", ""), 100)
    if name == "WebFetch":
        return "Approve WebFetch", _short(inp.get("url", ""))
    if name == "WebSearch":
        return "Approve WebSearch", _short(inp.get("query", ""))
    if name in ("Agent", "Task"):
        return "Approve Agent", _short(inp.get("description") or inp.get("prompt", ""))
    if name == "Skill":
        return "Approve Skill", _short(inp.get("skill", ""))
    if name.startswith("mcp__"):
        parts = name.split("__")
        label = f"{parts[1]} {parts[-1]}" if len(parts) >= 3 else name
        return f"Approve {label}", _short(_first_str(inp))
    return f"Approve {name}", _short(_first_str(inp))


class Pending:
    """What the session is waiting on, read from the tail of its transcript JSONL."""

    def __init__(self, tool: Optional[tuple[str, dict]] = None, last_text: str = "", title: str = "",
                 name: str = ""):
        self.tool = tool            # (tool name, input) of the newest tool call with no result yet
        self.last_text = last_text  # newest assistant text, when no tool call is pending
        self.name = name            # explicit session name: /rename or `claude -n`
        self.title = title or name  # what to show: the name, else the AI title

    def describe(self, notification_type: str) -> str:
        if self.tool and notification_type != "idle_prompt":
            verb, detail = describe_tool(*self.tool)
            return f"{verb}: {detail}" if detail else verb
        if self.last_text and notification_type in ("idle_prompt", ""):
            return f"Waiting for you: {_short(self.last_text, 140)}"
        return ""


def read_tail_lines(path: Path, nbytes: int = TAIL_BYTES) -> list[str]:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        data = f.read()
    lines = data.decode("utf-8", errors="ignore").splitlines()
    return lines[1:] if size > nbytes else lines  # the first line may be cut


def read_pending(transcript_path: Optional[str]) -> Optional[Pending]:
    """Walk the transcript backwards to the newest assistant block; None if unreadable."""
    if not transcript_path:
        return None
    try:
        lines = read_tail_lines(Path(transcript_path))
    except OSError:
        return None
    pending = Pending()
    ai_title = ""
    resolved: set[str] = set()
    turn_found = False
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        kind = d.get("type")
        if not pending.name and kind in ("custom-title", "agent-name"):
            pending.name = str(d.get("customTitle") or d.get("agentName") or "")
        elif not ai_title and kind == "ai-title":
            ai_title = str(d.get("aiTitle") or "")
        if turn_found:
            if pending.name:
                break
            continue
        if d.get("isSidechain"):
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if kind == "user":
            resolved.update(str(c.get("tool_use_id", "")) for c in content
                            if isinstance(c, dict) and c.get("type") == "tool_result")
        elif kind == "assistant":
            for c in reversed(content):
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    if c.get("id") not in resolved:
                        pending.tool = (str(c.get("name", "")), c.get("input") or {})
                        turn_found = True
                        break
                elif c.get("type") == "text" and str(c.get("text", "")).strip():
                    pending.last_text = str(c["text"]).strip()
                    turn_found = True
                    break
    pending.title = pending.name or ai_title
    return pending


# ---------------------------------------------------------------- click target

def in_zed(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return env.get("ZED_TERM") == "true" or env.get("TERM_PROGRAM") == "zed"


def focus_url(data: dict, pending: Optional[Pending]) -> str:
    """The claude-notify-focus: URL the toast opens on click (handled by focus.py).

    The tab search uses only an explicit session name: Zed labels terminal tabs by shell process
    ("project — powershell.exe"), so it can find the tab only if the user gave the tab the same name.
    The AI title would fuzzy-match unrelated tabs.
    """
    q = {"cwd": str(data.get("cwd") or ""), "session": str(data.get("session_id") or "")}
    if pending and pending.name:
        q["tab"] = pending.name
    return f"{PROTOCOL}:?{urlencode(q)}"


# ---------------------------------------------------------------- backends

def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def _powershell_argv(exe: str) -> list[str]:
    encoded = base64.b64encode(_WIN_TOAST_PS.encode("utf-16-le")).decode("ascii")
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def build_command(
    system: str,
    title: str,
    message: str,
    which: Callable[[str], Optional[str]] = shutil.which,
    wsl: bool = False,
    launch: str = "",
) -> Optional[tuple[list[str], dict[str, str]]]:
    """Return (argv, extra_env) for this OS, or None when no backend is available."""
    if system == "Windows":
        exe = which("powershell") or which("pwsh")
        if not exe:
            return None
        env = {"CC_NOTIFY_TITLE": title, "CC_NOTIFY_MESSAGE": message}
        if launch:
            env["CC_NOTIFY_LAUNCH"] = launch
        return _powershell_argv(exe), env
    if system == "Darwin":
        exe = which("osascript")
        return ([exe, *_MAC_SCRIPT, title, message], {}) if exe else None
    if system == "Linux":
        if wsl:
            exe = which("powershell.exe")
            if exe:
                # WSLENV forwards these variables to the Windows process.
                fwd = "CC_NOTIFY_TITLE/u:CC_NOTIFY_MESSAGE/u"
                prior = os.environ.get("WSLENV")
                return _powershell_argv(exe), {
                    "CC_NOTIFY_TITLE": title, "CC_NOTIFY_MESSAGE": message,
                    "WSLENV": f"{prior}:{fwd}" if prior else fwd,
                }
        exe = which("notify-send")
        return ([exe, "--app-name", APP_TITLE, title, message], {}) if exe else None
    return None


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in args
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except (OSError, ValueError):
        raw = ""
    data = load_payload(raw)
    try:
        pending = read_pending(data.get("transcript_path"))
    except Exception:  # never let a transcript surprise block the toast
        pending = None
    title, message = title_message(data, pending)
    system = platform.system()
    launch = focus_url(data, pending) if system == "Windows" and in_zed() else ""
    cmd = build_command(system, title, message, wsl=(system == "Linux" and is_wsl()), launch=launch)
    if dry:
        print(json.dumps({"system": system, "title": title, "message": message, "launch": launch,
                          "argv": cmd[0] if cmd else None}, indent=2, ensure_ascii=False))
        return 0
    if not cmd:
        return 0
    argv_, extra = cmd
    try:
        subprocess.run(argv_, env={**os.environ, **extra}, timeout=TIMEOUT_S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
