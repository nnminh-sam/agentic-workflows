#!/usr/bin/env python3
"""Claude Code Notification hook: show a native desktop notification on any OS.

Claude Code pipes a JSON payload on stdin, e.g.
  {"message": "Claude needs your permission to use Bash", "notification_type": "permission_prompt",
   "cwd": "/path/to/project", "session_id": "...", "transcript_path": "..."}

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

APP_TITLE = "Claude Code"
DEFAULT_MESSAGE = "Claude needs your input"
TIMEOUT_S = 10

# WinRT toast. Title and message arrive through environment variables, so no quoting is needed.
_WIN_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [System.Security.SecurityElement]::Escape($env:CC_NOTIFY_TITLE)
$m = [System.Security.SecurityElement]::Escape($env:CC_NOTIFY_MESSAGE)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual><audio src='ms-winsoundevent:Notification.Default'/></toast>")
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""

_MAC_SCRIPT = [
    "-e", "on run argv",
    "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
    "-e", "end run",
]


def parse_payload(raw: str) -> tuple[str, str]:
    """Return (title, message) from the hook's stdin JSON; tolerate empty or bad input."""
    title, message = APP_TITLE, DEFAULT_MESSAGE
    raw = raw.lstrip("﻿")  # PowerShell pipes add a BOM
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("message"):
        message = str(data["message"])
    if data.get("title"):
        title = str(data["title"])
    cwd = data.get("cwd")
    if cwd:
        name = Path(str(cwd).replace("\\", "/").rstrip("/")).name
        if name:
            title = f"{title} - {name}"
    return title, message


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
) -> Optional[tuple[list[str], dict[str, str]]]:
    """Return (argv, extra_env) for this OS, or None when no backend is available."""
    if system == "Windows":
        exe = which("powershell") or which("pwsh")
        if exe:
            return _powershell_argv(exe), {"CC_NOTIFY_TITLE": title, "CC_NOTIFY_MESSAGE": message}
        return None
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
    title, message = parse_payload(raw)
    system = platform.system()
    cmd = build_command(system, title, message, wsl=(system == "Linux" and is_wsl()))
    if dry:
        print(json.dumps({"system": system, "title": title, "message": message,
                          "argv": cmd[0] if cmd else None}, indent=2))
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
