---
name: zed-spawn-session
description: Spawn an independent Claude Code session in a new Zed terminal tab in the current Zed window (no split), pre-wired to check in and report back to this session over cross-session messaging. Works on Windows, macOS and Linux. Use when the user works in Zed and wants a parallel agent they can watch and type into, e.g. "spawn a claude session in zed", "open claude in a new zed tab", "delegate X to a new terminal". Not for in-process subagents.
---

# zed-spawn-session

Opens a real, separate `claude` process in a new Zed terminal tab (the view is not split), named and told who its parent is. The child checks in as soon as it starts, so you know the link works, and sends a final report when done.

Scripts, in this skill's `scripts/` folder (use the skill folder's absolute path):

| OS | Script | Run it with |
|---|---|---|
| Windows | `zed-spawn.ps1` | the PowerShell tool, as a separate `powershell.exe` process |
| macOS, Linux | `zed-spawn.sh` | the Bash tool |

## How it works

Zed has no command API, so the script drives it with keystrokes:
1. A key bound to `workspace::NewCenterTerminal` opens a new terminal tab in the center pane. The view stays as it is: no split.
2. It pastes a command into the new terminal that runs a generated launcher, which starts `claude -n <child> --append-system-prompt-file <protocol>` with the task. It pastes rather than types because input methods like Vietnamese Telex rewrite typed letters; the user's clipboard text is restored afterwards.

| | Windows | macOS | Linux |
|---|---|---|---|
| New terminal key | Ctrl+Alt+Shift+1 | Ctrl+Option+Shift+Y | Ctrl+Alt+Shift+Y |
| Keymap file | `%APPDATA%\Zed\keymap.json` | `~/.config/zed/keymap.json` | `~/.config/zed/keymap.json` |
| Keystrokes | WScript.Shell SendKeys | `osascript` (System Events) | `xdotool` (X11) or `wtype` (wlroots Wayland) |
| Clipboard | Get/Set-Clipboard | `pbcopy`/`pbpaste` | `xclip`/`xsel` or `wl-copy`/`wl-paste` |
| Paste key | Ctrl+Shift+V | Cmd+V | Ctrl+Shift+V |

On macOS and Linux the binding can be changed with `ZED_SPAWN_KEY_NEW` (Zed syntax, e.g. `ctrl-alt-shift-y`); set the same value for `--install-keymap` and for spawning.

## Steps

1. **One-time setup.** If the script says the keymap is missing, run its install option: `-InstallKeymap` (Windows) or `--install-keymap` (macOS/Linux). It appends the binding to Zed's keymap and backs up the old file to `keymap.json.bak`. Extra one-time needs:
   - **macOS:** the app running Claude Code (Zed, Terminal, iTerm…) needs Accessibility permission (System Settings → Privacy & Security → Accessibility), or `osascript` keystrokes fail.
   - **Linux X11:** `xdotool` and `xclip` or `xsel`.
   - **Linux Wayland:** `wtype` and `wl-clipboard`; works on wlroots compositors (Sway, Hyprland). GNOME and KDE Wayland block synthetic keystrokes; there, run Zed under XWayland or tell the user it isn't supported.
2. **Know this session's name (the parent).** Call `ListAgents`: its first line says "This session is <name>". If this session has no name, ask the user to `/rename <name>`. Don't guess.
3. **Pick a child name.** Short kebab-case, unique among running sessions, e.g. `api-review`.
4. **Write a self-contained task brief.** The child starts with no context from this conversation: include the goal, relevant paths, constraints, what's ruled out, and what "done" means. Newlines get flattened (and double quotes on Windows).
5. **Decide the options** (Windows / macOS-Linux spelling):
   - `-Worktree` / `--worktree` when the child will edit files in a git repo that another session is also editing.
   - `-PermissionMode` / `--permission-mode acceptEdits|plan|auto` only if the user asked.
   - `-Dir` / `--dir <path>` if the work belongs elsewhere than the current directory.
   - `-DelayMs 2500` / `--delay 2.5` if a previous spawn pasted the command before the shell was ready.
6. **Warn the user once, then run.** For about 2 seconds the script sends keys to Zed, so the user should not click or type meanwhile. Before every keystroke it checks that Zed really is the foreground window and stops, sending nothing more, if it is not (Windows can refuse to switch focus; keys sent anyway would land in another app, e.g. Microsoft Teams treats Ctrl+Alt+Shift+1 as "download diagnostic logs" and Enter would post the pasted command into a chat). If it stops with a focus error, ask the user to click into Zed and run it again.
   - Windows: always a separate `powershell.exe` process (run in-process from the PowerShell tool, its keystrokes never reach Zed):
     ```powershell
     powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\zed-spawn.ps1" -Name <child> -Parent <this-session> -Task "<brief>" [-Worktree]
     ```
   - macOS / Linux:
     ```bash
     bash "<skill-dir>/scripts/zed-spawn.sh" --name <child> --parent <this-session> --task "<brief>" [--worktree]
     ```
7. **Confirm the link.** The child's first action is a check-in message ("<child> online, starting: ..."). It arrives here as a message from another session. Don't poll. If none arrives within a minute or two, call `ListAgents` to see whether the child is running, and tell the user. To follow up, `SendMessage` to the child's name.

## Notes

- Needs Zed running and Claude Code with cross-session messaging (v2.1.234+). Parent and child must be on the same machine (a WSL session can't reach a native Windows one).
- A peer message can't approve permission prompts; the child's prompts are answered in its tab.
- If the child never receives messages, check the `crossSessionInbound` setting (`accept` / `hold` / `refuse`).
- `-DryRun` / `--dry-run` prints what would be pasted and the launcher without touching Zed.
- Each spawn adds another terminal tab to the active pane; close finished tabs by hand. Any `ctrl-alt-shift-2` → `pane::SplitAndMoveRight` binding left in the keymap from older versions is no longer used and can be removed.
- The macOS/Linux script has been syntax-checked and dry-run tested, but not yet run against a live Zed on those systems. If a key binding doesn't fire, check the action names in Zed's command palette and adjust the bindings via the env vars.
