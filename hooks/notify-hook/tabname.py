#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: give the session's Zed terminal tab a unique name (Windows).

Zed labels every terminal tab "<project> — powershell.exe", so a toast click can't tell Claude tabs
apart. Right after you press Enter on a prompt, the focused Zed tab is the one this session runs in.
So this hook renames that tab once:
  - to the session name (/rename <name> or `claude -n <name>`), if there is one,
  - else to "<cwd folder> #<first 4 chars of the session id>".
It renames again only when that label changes (e.g. after /rename). focus.py does the keystrokes
in a detached process, so the prompt is never delayed, and records the label in tabs/<session>.txt;
a later toast click switches to that label.

It skips when: not Windows, not in a Zed terminal, Zed is not the foreground window, or there was
no keyboard/mouse input in the last 2 s (the prompt came from another session, not from you).
Disable with NOTIFY_HOOK_TAB_NAMES=0. Prints nothing (UserPromptSubmit output would reach Claude)
and always exits 0.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import focus  # noqa: E402
import notify  # noqa: E402

RECENT_INPUT_MS = 2000


def tab_label(data: dict, name: str) -> str:
    if name:
        return notify._short(name, 60)
    folder = Path(str(data.get("cwd") or "").replace("\\", "/").rstrip("/")).name or "claude"
    sid = "".join(c for c in str(data.get("session_id") or "") if c.isalnum())[:4]
    return f"{folder} #{sid}" if sid else folder


def ms_since_input() -> Optional[int]:
    import ctypes
    from ctypes import wintypes as wt

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


def should_try(env: dict, platform: str) -> bool:
    return platform == "win32" and env.get("NOTIFY_HOOK_TAB_NAMES") != "0" and notify.in_zed(env)


def spawn_rename(label: str, session: str) -> None:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([str(pyw if pyw.exists() else exe), str(HERE / "focus.py"),
                      "--rename-tab", label, "--session", session],
                     creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    try:
        if not should_try(dict(os.environ), sys.platform):
            return 0
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        data = notify.load_payload(raw)
        session = str(data.get("session_id") or "")
        if not session:
            return 0
        pending = notify.read_pending(data.get("transcript_path"))
        label = tab_label(data, pending.name if pending else "")
        if label == focus.read_tab_label(session):
            return 0
        idle = ms_since_input()
        if idle is None or idle > RECENT_INPUT_MS:
            focus.log(f"rename tab skipped: no recent input ({idle} ms); session={session}")
            return 0
        spawn_rename(label, session)
    except Exception as e:  # never block a prompt
        try:
            focus.log(f"tabname error: {e!r}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
