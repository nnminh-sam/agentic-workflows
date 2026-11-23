# notify-hook

A Claude Code `Notification` hook that shows a native desktop notification whenever Claude is waiting on you: a permission prompt, a question, or idle input. One script works on every OS, using only the Python standard library.

| Platform | Backend | Needs |
|---|---|---|
| Windows | WinRT toast via PowerShell | nothing extra |
| WSL | Windows toast via `powershell.exe` | nothing extra |
| macOS | `osascript` "display notification" | nothing extra |
| Linux | `notify-send` | `libnotify-bin` (or your distro's equivalent) |

If no backend is available, the hook does nothing. It always exits 0, so it never blocks Claude Code.

## What the notification says

The hook reads the tail of the session transcript (`transcript_path` in the payload) and says what Claude is waiting on, plus the session name or AI title on the last line:

| Waiting on | Toast text |
|---|---|
| a Bash / PowerShell permission | `Approve Bash: Run tests: npm test` |
| an Edit / Write / Read permission | `Approve Edit: src/main.py` |
| an AskUserQuestion | `Answer: Which DB should we use? (+1 more)` |
| plan approval | `Review the plan: ...` |
| idle prompt | `Waiting for you: <start of Claude's last reply>` |
| MCP / other tools | `Approve slack send_message: ...` |

If the transcript can't be read, Claude Code's own message is shown.

## Click to jump to the Zed tab (Windows)

When Claude Code runs in a Zed terminal (`ZED_TERM=true`), clicking the toast opens `claude-notify-focus:?cwd=...&tab=...`. `focus.py` handles it:

1. **Window:** brings forward the Zed window whose title names the deepest folder of the session's cwd (otherwise the topmost Zed window). This always works.
2. **Tab:** switches to the tab this session runs in, using a name the tab was given automatically:
   - Zed labels every terminal tab by its shell (`workspace — powershell.exe`), so tabs can't be told apart. `tabname.py`, a `UserPromptSubmit` hook, fixes that: right after you press Enter on a prompt, the focused Zed tab is this session's tab, so it renames that tab once, to the session name (`/rename <name>` or `claude -n <name>`) or else to `<folder> #<session id prefix>`. The label and the PID of the session's `claude.exe` are saved in `~/.claude/hooks/notify-hook/tabs/<session>.txt`.
   - It renames again only when the session name changes (`/rename`) or the session runs under a different `claude.exe` (resumed in a new tab, Zed restarted). A `cd` keeps the first label, so there are no extra keystroke renames.
   - Tabs opened by the `zed-spawn-session` skill are named by the spawn script itself, right after it opens the tab (Zed is in front then), with a pre-assigned `--session-id`. The hook only records the PID for those, so spawned sessions are clickable even if you never type in them.
   - It skips when Zed isn't the foreground window or there was no keyboard/mouse input in the last 2 s (a prompt that came from another session). Turn it off with `NOTIFY_HOOK_TAB_NAMES=0`.
   - On click, `focus.py` opens Zed's searchable all-tabs switcher, pastes the saved label and confirms. A session that hasn't had a prompt since install has no label yet, so the click only focuses the window.

Key bindings `install.py` adds to `%APPDATA%\Zed\keymap.json` (backup in `keymap.json.bak`):

| Key | Action | Context |
|---|---|---|
| `ctrl-alt-shift-3` | `tab_switcher::ToggleAll` | Workspace |
| `ctrl-alt-shift-4` / `-5` | paste / confirm | TabSwitcher only |
| `ctrl-alt-shift-6` | `terminal::RenameTerminal` | Terminal only |
| `ctrl-alt-shift-7` / `-8` | paste / confirm | single-line text box only (the rename box) |

Paste and confirm are scoped so that if the switcher or rename box fails to open they match nothing: no text and no Enter reach the terminal, where Enter could accept a permission prompt. Before each key it checks Zed is still the foreground window, and clipboard text is restored. Log: `~/.claude/hooks/notify-hook/focus.log`.

macOS and Linux: notification text is enriched the same way; click-to-focus is Windows-only.

## Install

```sh
python install.py                              # every project: ~/.claude/settings.json
python install.py --project path/to/repo       # one project: <repo>/.claude/settings.local.json
python install.py --matcher "permission_prompt|idle_prompt"   # only some notification types
python install.py --uninstall
python install.py --dry-run
```

On Windows the installer also registers the `claude-notify-focus:` URL scheme under `HKCU\Software\Classes` (runs `focus.py` with `pythonw`) and adds the Zed bindings above; `--no-click` skips both, `--uninstall` removes both.

The installer copies `notify.py`, `focus.py` and `tabname.py` to `<claude dir>/hooks/notify-hook/`, then adds one `Notification` entry (and, on Windows with click-to-focus, one `UserPromptSubmit` entry for `tabname.py`) that runs it with the Python interpreter you ran the installer with. It leaves other settings and hooks alone, and re-running it replaces its own entry instead of adding a second one. Re-run it after editing `notify.py` to upgrade.

Notification types you can match: `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `auth_success`.

## Try it

```sh
echo '{"message":"hello","cwd":"/work/my-app"}' | python notify.py             # shows a notification
echo '{"message":"hello"}' | python notify.py --dry-run                        # prints the command only
python focus.py --dry-run "claude-notify-focus:?cwd=D%3A%5Cwork&tab=api-review"  # shows the Zed window it would pick
python -m unittest discover -s tests
```

## Troubleshooting

- **Windows:** no toast? Turn off Focus Assist / Do Not Disturb and allow notifications for "Windows PowerShell" under Settings → System → Notifications.
- **macOS:** the first notification may ask to allow notifications for Script Editor / osascript.
- **Linux:** install `notify-send` and make sure a notification daemon is running.
- Still nothing? Open `/hooks` in Claude Code once (it reloads settings) or restart it.
