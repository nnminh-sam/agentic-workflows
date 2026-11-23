#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: give the session's Zed terminal tab a unique name (Windows).

Zed labels every terminal tab "<project> — powershell.exe", so a toast click can't tell Claude tabs
apart. Right after you press Enter on a prompt, the focused Zed tab is the one this session runs in.
So this hook renames that tab once:
  - to the session name (/rename <name> or `claude -n <name>`), if there is one,
  - else to "<cwd folder> #<first 4 chars of the session id>".
It renames again only when the session name changes (e.g. after /rename) or the session now runs
under a different claude.exe (resumed in a new tab, or Zed restarted); a cd keeps the first label.
focus.py does the keystrokes in a detached process, so the prompt is never delayed, and records the
label and the claude.exe PID in tabs/<session>.txt; a later toast click switches to that label.
Tabs opened by zed-spawn are named by zed-spawn itself before claude starts (owner "spawn"); this
hook only records the PID for those, with no keystrokes.

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


def claude_pid() -> Optional[int]:
    """PID of the claude.exe that runs this hook (an ancestor process); None if not found."""
    import ctypes
    from ctypes import wintypes as wt

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wt.HANDLE(-1).value:
        return None
    procs: dict[int, tuple[int, str]] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            procs[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    pid = os.getpid()
    for _ in range(12):  # python <- bash/cmd ... <- claude.exe
        if pid not in procs:
            return None
        parent, exe = procs[pid]
        if exe == "claude.exe":
            return pid
        pid = parent
    return None


def plan(name_label: str, fallback: str, stored: tuple[str, str], pid: Optional[int]) -> tuple[str, str]:
    """Decide what to do with the tab: ('skip' | 'adopt' | 'rename', label).

    stored is (label, owner) from tabs/<session>.txt. The stored label belongs to *this* tab when its
    owner is this claude.exe, or zed-spawn named the tab before claude started (adopt: record the
    PID, no keystrokes). A resumed session runs in a new tab under a new claude.exe, so it renames.
    Without an explicit session name the first label sticks: a cd no longer renames the tab.
    """
    label, owner = stored
    me = str(pid) if pid else ""
    same_tab = bool(label) and (not me or owner in (me, focus.SPAWN_OWNER))
    want = name_label or (label if same_tab else fallback)
    if same_tab and want == label:
        return ("adopt" if me and owner != me else "skip"), want
    return "rename", want


def should_try(env: dict, platform: str) -> bool:
    return platform == "win32" and env.get("NOTIFY_HOOK_TAB_NAMES") != "0" and notify.in_zed(env)


def spawn_rename(label: str, session: str, owner: str) -> None:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([str(pyw if pyw.exists() else exe), str(HERE / "focus.py"),
                      "--rename-tab", label, "--session", session, "--owner", owner],
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
        name = pending.name if pending else ""
        pid = claude_pid()
        action, label = plan(tab_label(data, name) if name else "", tab_label(data, ""),
                             focus.read_tab_state(session), pid)
        if action == "skip":
            return 0
        if action == "adopt":
            focus.write_tab_state(session, label, str(pid))
            return 0
        idle = ms_since_input()
        if idle is None or idle > RECENT_INPUT_MS:
            focus.log(f"rename tab skipped: no recent input ({idle} ms); session={session}")
            return 0
        spawn_rename(label, session, str(pid or ""))
    except Exception as e:  # never block a prompt
        try:
            focus.log(f"tabname error: {e!r}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
