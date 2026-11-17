# notify-hook

A Claude Code `Notification` hook that shows a native desktop notification whenever Claude is waiting on you: a permission prompt, a question, or idle input. One script works on every OS, using only the Python standard library.

| Platform | Backend | Needs |
|---|---|---|
| Windows | WinRT toast via PowerShell | nothing extra |
| WSL | Windows toast via `powershell.exe` | nothing extra |
| macOS | `osascript` "display notification" | nothing extra |
| Linux | `notify-send` | `libnotify-bin` (or your distro's equivalent) |

If no backend is available, the hook does nothing. It always exits 0, so it never blocks Claude Code.

## Install

```sh
python install.py                              # every project: ~/.claude/settings.json
python install.py --project path/to/repo       # one project: <repo>/.claude/settings.local.json
python install.py --matcher "permission_prompt|idle_prompt"   # only some notification types
python install.py --uninstall
python install.py --dry-run
```

The installer copies `notify.py` to `<claude dir>/hooks/notify-hook/notify.py`, then adds one `Notification` entry that runs it with the Python interpreter you ran the installer with. It leaves other settings and hooks alone, and re-running it replaces its own entry instead of adding a second one. Re-run it after editing `notify.py` to upgrade.

Notification types you can match: `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `auth_success`.

## Try it

```sh
echo '{"message":"hello","cwd":"/work/my-app"}' | python notify.py             # shows a notification
echo '{"message":"hello"}' | python notify.py --dry-run                        # prints the command only
python -m unittest discover -s tests
```

## Troubleshooting

- **Windows:** no toast? Turn off Focus Assist / Do Not Disturb and allow notifications for "Windows PowerShell" under Settings → System → Notifications.
- **macOS:** the first notification may ask to allow notifications for Script Editor / osascript.
- **Linux:** install `notify-send` and make sure a notification daemon is running.
- Still nothing? Open `/hooks` in Claude Code once (it reloads settings) or restart it.
