#!/usr/bin/env python3
"""Click handler for notify-hook toasts (Windows): jump to the Claude Code session's Zed tab.

The toast opens  claude-notify-focus:?cwd=<project dir>&session=<id>&tab=<session title>
and Windows runs  pythonw focus.py "<that URL>"  (install.py registers the URL scheme).

1. Find the Zed window for cwd: among visible Zed.exe windows, the one whose title names the deepest
   folder of cwd; otherwise the topmost Zed window. Restore it and bring it to the front.
2. Switch to the tab: Zed has no command API, so it sends key bindings that install.py adds to Zed's
   keymap (see ZED_BINDINGS): open the searchable all-tabs switcher, paste the tab's label as the
   query, confirm. The label is the one tabname.py gave the tab (tabs/<session>.txt), else the
   session name from the URL. Paste and confirm are bound only inside the TabSwitcher context, so if the switcher
   failed to open they do nothing; no Enter or text ever reaches the terminal (where Enter could
   accept a permission prompt). Before every key it checks Zed is still the foreground window.
   The clipboard text is restored afterwards.

  focus.py --rename-tab <label> --session <id>   (run detached by tabname.py) renames the focused
  Zed terminal tab through the rename box and saves the label.

Logs to focus.log next to this file. Always exits 0.

  python focus.py --dry-run "claude-notify-focus:?cwd=D%3A%5Cwork%5Capp&tab=My+session"
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

PROTOCOL = "claude-notify-focus"
LOG = Path(__file__).resolve().with_name("focus.log")

# Bindings install.py writes to Zed's keymap.json; keys are (Zed syntax, Windows virtual-key codes).
OPEN_KEY = "ctrl-alt-shift-3"
PASTE_KEY = "ctrl-alt-shift-4"
CONFIRM_KEY = "ctrl-alt-shift-5"
# Naming a tab (tabname.py): rename works only in a focused terminal; paste/confirm only in a
# single-line text box (the rename box). If the rename box didn't open, focus is still the terminal
# and paste/confirm match nothing, so no text or Enter reaches Claude's prompt.
RENAME_KEY = "ctrl-alt-shift-6"
RENAME_PASTE_KEY = "ctrl-alt-shift-7"
RENAME_CONFIRM_KEY = "ctrl-alt-shift-8"
ZED_BINDINGS = [
    {"context": "Workspace", "bindings": {OPEN_KEY: "tab_switcher::ToggleAll"}},
    {"context": "TabSwitcher", "bindings": {PASTE_KEY: "editor::Paste", CONFIRM_KEY: "menu::Confirm"}},
    {"context": "Terminal", "bindings": {RENAME_KEY: "terminal::RenameTerminal"}},
    {"context": "Editor && mode == single_line",
     "bindings": {RENAME_PASTE_KEY: "editor::Paste", RENAME_CONFIRM_KEY: "menu::Confirm"}},
]
ALL_KEYS = (OPEN_KEY, PASTE_KEY, CONFIRM_KEY, RENAME_KEY, RENAME_PASTE_KEY, RENAME_CONFIRM_KEY)
TABS_DIR = Path(__file__).resolve().with_name("tabs")  # <session_id>.txt holds the label we gave the tab


def tab_state(session: str) -> Path:
    safe = "".join(c for c in session if c.isalnum() or c in "-_")[:80] or "unknown"
    return TABS_DIR / f"{safe}.txt"


def read_tab_label(session: str) -> str:
    try:
        return tab_state(session).read_text(encoding="utf-8").strip() if session else ""
    except OSError:
        return ""

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def log(msg: str) -> None:
    try:
        old = LOG.read_text(encoding="utf-8").splitlines()[-200:] if LOG.exists() else []
        old.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")
        LOG.write_text("\n".join(old) + "\n", encoding="utf-8")
    except OSError:
        pass


def parse_url(url: str) -> dict:
    """Return {'cwd','session','tab'} from a claude-notify-focus: URL (missing keys -> '')."""
    parts = urlsplit(url.strip().strip('"'))
    query = parts.query or (parts.path.split("?", 1)[1] if "?" in parts.path else "")
    q = parse_qs(query)
    return {k: (q.get(k) or [""])[0] for k in ("cwd", "session", "tab")}


def title_tokens(title: str) -> list[str]:
    """Split a Zed window title like 'my-app — src/main.rs' into lowercase pieces."""
    for sep in ("—", "–", " - ", "|"):
        title = title.replace(sep, "\0")
    return [t.strip().lower() for t in title.split("\0") if t.strip()]


def pick_window(windows: list[tuple[int, str]], cwd: str) -> Optional[int]:
    """windows: [(hwnd, title)] in z-order (topmost first). Prefer the deepest cwd folder named."""
    if not windows:
        return None
    folders = [p.lower() for p in cwd.replace("\\", "/").split("/") if p and not p.endswith(":")]
    best, best_depth = windows[0][0], -1
    for hwnd, title in windows:
        toks = title_tokens(title)
        for depth, name in enumerate(folders):
            if name in toks and depth > best_depth:
                best, best_depth = hwnd, depth
    return best


# ---------------------------------------------------------------- Win32

class Win:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ct, self.wt = ctypes, wt
        self.u32 = ctypes.WinDLL("user32", use_last_error=True)
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k = self.u32, self.k32
        u.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
        u.GetWindowTextLengthW.argtypes = [wt.HWND]
        u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.GetWindow.argtypes = [wt.HWND, wt.UINT]
        u.GetWindow.restype = wt.HWND
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetForegroundWindow.restype = wt.HWND
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.IsIconic.argtypes = [wt.HWND]
        u.BringWindowToTop.argtypes = [wt.HWND]
        u.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
        u.OpenClipboard.argtypes = [wt.HWND]
        u.GetClipboardData.argtypes = [wt.UINT]
        u.GetClipboardData.restype = wt.HANDLE
        u.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
        u.SetClipboardData.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.OpenProcess.restype = wt.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k.CloseHandle.argtypes = [wt.HANDLE]
        k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wt.HGLOBAL
        k.GlobalLock.argtypes = [wt.HGLOBAL]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [wt.HGLOBAL]

    def _pid(self, hwnd) -> int:
        pid = self.wt.DWORD()
        self.u32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        return pid.value

    def _exe(self, pid: int) -> str:
        h = self.k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = self.ct.create_unicode_buffer(1024)
            size = self.wt.DWORD(1024)
            ok = self.k32.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(size))
            return buf.value if ok else ""
        finally:
            self.k32.CloseHandle(h)

    def zed_windows(self) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        exe_cache: dict[int, bool] = {}

        @self.ct.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)
        def cb(hwnd, _):
            if not self.u32.IsWindowVisible(hwnd) or self.u32.GetWindow(hwnd, 4):  # GW_OWNER
                return True
            n = self.u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            pid = self._pid(hwnd)
            if pid not in exe_cache:
                exe_cache[pid] = Path(self._exe(pid)).name.lower() == "zed.exe"
            if exe_cache[pid]:
                buf = self.ct.create_unicode_buffer(n + 1)
                self.u32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
            return True

        self.u32.EnumWindows(cb, 0)
        return found

    def is_foreground(self, hwnd) -> bool:
        fg = self.u32.GetForegroundWindow()
        return bool(fg) and (fg == hwnd or self._pid(fg) == self._pid(hwnd))

    def activate(self, hwnd, timeout: float = 2.0) -> bool:
        if self.u32.IsIconic(hwnd):
            self.u32.ShowWindow(hwnd, SW_RESTORE)
        deadline = time.time() + timeout
        while time.time() < deadline:
            # A tapped Alt lifts Windows' foreground lock for this process.
            self.u32.keybd_event(VK_MENU, 0, 0, None)
            self.u32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
            self.u32.BringWindowToTop(hwnd)
            self.u32.SetForegroundWindow(hwnd)
            time.sleep(0.12)
            if self.is_foreground(hwnd):
                return True
        return False

    def chord(self, zed_key: str) -> None:
        """Press a Zed-syntax chord such as 'ctrl-alt-shift-3'."""
        *mods, key = zed_key.split("-")
        vk_mods = [{"ctrl": VK_CONTROL, "alt": VK_MENU, "shift": VK_SHIFT}[m] for m in mods]
        vk = ord(key.upper())
        for m in vk_mods:
            self.u32.keybd_event(m, 0, 0, None)
        self.u32.keybd_event(vk, 0, 0, None)
        self.u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)
        for m in reversed(vk_mods):
            self.u32.keybd_event(m, 0, KEYEVENTF_KEYUP, None)

    def _open_clipboard(self) -> bool:
        for _ in range(20):
            if self.u32.OpenClipboard(None):
                return True
            time.sleep(0.03)
        return False

    def get_clipboard(self) -> Optional[str]:
        if not self._open_clipboard():
            return None
        try:
            h = self.u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = self.k32.GlobalLock(h)
            try:
                return self.ct.wstring_at(p) if p else None
            finally:
                self.k32.GlobalUnlock(h)
        finally:
            self.u32.CloseClipboard()

    def set_clipboard(self, text: str) -> bool:
        if not self._open_clipboard():
            return False
        try:
            self.u32.EmptyClipboard()
            data = (text + "\0").encode("utf-16-le")
            h = self.k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = self.k32.GlobalLock(h)
            self.ct.memmove(p, data, len(data))
            self.k32.GlobalUnlock(h)
            return bool(self.u32.SetClipboardData(CF_UNICODETEXT, h))
        finally:
            self.u32.CloseClipboard()


def keymap_ready(path: Optional[Path] = None) -> bool:
    path = path or Path(os.environ.get("APPDATA", "")) / "Zed" / "keymap.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return all(k in text for k in ALL_KEYS)


def run_keys(win: Win, hwnd, text: str, open_key: str, paste_key: str, confirm_key: str) -> str:
    """open -> paste text -> confirm, checking Zed is foreground before each key."""
    saved = win.get_clipboard()
    try:
        steps = [("open", lambda: win.chord(open_key), 0.35),
                 ("paste", lambda: win.set_clipboard(text) and win.chord(paste_key), 0.30),
                 ("confirm", lambda: win.chord(confirm_key), 0.10)]
        for name, act, wait in steps:
            if not win.is_foreground(hwnd):
                return f"stopped before {name}: Zed lost focus"
            act()
            time.sleep(wait)
        return "ok"
    finally:
        time.sleep(0.15)
        if saved is not None:
            win.set_clipboard(saved)


def rename_tab(label: str, session: str, delay: float = 0.4) -> str:
    """Rename the focused Zed terminal tab (the one the user just typed in) and remember the label."""
    time.sleep(delay)  # let the Enter that submitted the prompt finish
    win = Win()
    windows = win.zed_windows()
    hwnd = next((h for h, _ in windows if win.is_foreground(h)), None)
    if not hwnd:
        return "skipped: Zed is not the foreground window"
    if not keymap_ready():
        return "skipped: Zed keymap lacks notify-hook bindings"
    result = run_keys(win, hwnd, label, RENAME_KEY, RENAME_PASTE_KEY, RENAME_CONFIRM_KEY)
    if result == "ok" and session:
        TABS_DIR.mkdir(parents=True, exist_ok=True)
        tab_state(session).write_text(label + "\n", encoding="utf-8")
    return result


def _arg(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else ""


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in args
    if sys.platform != "win32":
        print("focus.py only supports Windows")
        return 0
    if "--rename-tab" in args:
        label, session = _arg(args, "--rename-tab"), _arg(args, "--session")
        try:
            log(f"rename tab -> {label!r}: {rename_tab(label, session)}; session={session}")
        except Exception as e:
            log(f"rename error: {e!r}")
        return 0
    urls = [a for a in args if a.lower().startswith(PROTOCOL)]
    info = parse_url(urls[0]) if urls else {"cwd": "", "session": "", "tab": ""}
    tab = read_tab_label(info["session"]) or info["tab"]
    try:
        win = Win()
        windows = win.zed_windows()
        hwnd = pick_window(windows, info["cwd"])
        if dry:
            print({"info": info, "tab": tab, "zed_windows": windows, "picked": hwnd,
                   "keymap_ready": keymap_ready()})
            return 0
        if not hwnd:
            log(f"no Zed window; {info}")
            return 0
        if not win.activate(hwnd):
            log(f"could not bring Zed to front; {info}")
            return 0
        if not tab:
            log(f"focused window only (tab not named yet); {info}")
        elif not keymap_ready():
            log("focused window; Zed keymap lacks notify-hook bindings (re-run install.py)")
        else:
            log(f"switch to tab {tab!r}: {run_keys(win, hwnd, tab, OPEN_KEY, PASTE_KEY, CONFIRM_KEY)}; {info}")
    except Exception as e:  # a click handler must never crash loudly
        log(f"error: {e!r}; {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
