#!/usr/bin/env bash
# Spawn an independent Claude Code session in a new Zed terminal, placed in a
# right-hand split pane, pre-wired to talk to a parent session over
# cross-session messaging (ListAgents / SendMessage). macOS and Linux.
#
# Zed has no command API, so this drives it with keystrokes:
#   1. $KEY_NEW   -> workspace::NewCenterTerminal (new terminal tab)
#   2. $KEY_SPLIT -> pane::SplitAndMoveRight      (move it to a right split)
#   3. pastes a command that runs a generated launcher, which starts `claude`
# The two bindings must exist in ~/.config/zed/keymap.json (use --install-keymap).
#
# Keystroke backends:
#   macOS        osascript + System Events (grant Accessibility to the app running this script)
#   Linux X11    xdotool + xclip|xsel
#   Linux Wayland wtype + wl-clipboard (wlroots compositors; not GNOME/KDE)
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  zed-spawn.sh --name <child> --parent <session> --task "<brief>" [--dir <path>]
               [--worktree] [--permission-mode <mode>] [--delay <seconds>] [--dry-run]
  zed-spawn.sh --install-keymap

Env overrides:
  ZED_SPAWN_KEY_NEW    binding for workspace::NewCenterTerminal (default ctrl-alt-shift-y)
  ZED_SPAWN_KEY_SPLIT  binding for pane::SplitAndMoveRight       (default ctrl-alt-shift-u)
  ZED_APP_NAME         macOS app name to activate (default Zed)
EOF
}
die() { echo "zed-spawn: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

NAME="" PARENT="" TASK="" DIR="$PWD" WORKTREE=0 PERM="" DELAY=1.5 INSTALL=0 DRY=0
KEY_NEW="${ZED_SPAWN_KEY_NEW:-ctrl-alt-shift-y}"
KEY_SPLIT="${ZED_SPAWN_KEY_SPLIT:-ctrl-alt-shift-u}"
ZED_APP="${ZED_APP_NAME:-Zed}"

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME=${2-}; shift 2 ;;
    --parent) PARENT=${2-}; shift 2 ;;
    --task) TASK=${2-}; shift 2 ;;
    --dir) DIR=${2-}; shift 2 ;;
    --worktree) WORKTREE=1; shift ;;
    --permission-mode) PERM=${2-}; shift 2 ;;
    --delay) DELAY=${2-}; shift 2 ;;
    --install-keymap) INSTALL=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

OS=$(uname -s)
KEYMAP="${XDG_CONFIG_HOME:-$HOME/.config}/zed/keymap.json"   # Zed uses ~/.config/zed on macOS too

keymap_ok() {
  [ -f "$KEYMAP" ] &&
    grep -Eq "\"$KEY_NEW\"[[:space:]]*:[[:space:]]*\"workspace::NewCenterTerminal\"" "$KEYMAP" &&
    grep -Eq "\"$KEY_SPLIT\"[[:space:]]*:[[:space:]]*\"pane::SplitAndMoveRight\"" "$KEYMAP"
}

if [ "$INSTALL" = 1 ]; then
  if keymap_ok; then echo "Zed keymap already has the bindings: $KEYMAP"; exit 0; fi
  have python3 || die "python3 needed to edit $KEYMAP; add under context \"Workspace\": \"$KEY_NEW\": \"workspace::NewCenterTerminal\", \"$KEY_SPLIT\": \"pane::SplitAndMoveRight\""
  python3 - "$KEYMAP" "$KEY_NEW" "$KEY_SPLIT" <<'PY'
import json, os, shutil, sys
path, key_new, key_split = sys.argv[1:]
data = []
if os.path.exists(path):
    text = open(path, encoding="utf-8").read().strip()
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            sys.exit(f"{path} has comments or invalid JSON; add these under context 'Workspace' by hand: "
                     f'"{key_new}": "workspace::NewCenterTerminal", "{key_split}": "pane::SplitAndMoveRight"')
    shutil.copy(path, path + ".bak")
data.append({"context": "Workspace",
             "bindings": {key_new: "workspace::NewCenterTerminal", key_split: "pane::SplitAndMoveRight"}})
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
print(f"Added bindings to {path}")
PY
  exit 0
fi

[ -n "$NAME" ] && [ -n "$PARENT" ] && [ -n "$TASK" ] || { usage >&2; die "--name, --parent and --task are required"; }
[[ "$NAME" =~ ^[A-Za-z0-9_-]{1,40}$ ]]   || die "name must be 1-40 chars of A-Z a-z 0-9 _ -"
[[ "$PARENT" =~ ^[A-Za-z0-9_-]{1,40}$ ]] || die "parent must be 1-40 chars of A-Z a-z 0-9 _ -"
DIR=$(cd "$DIR" && pwd) || die "no such directory: $DIR"
have claude || die "claude CLI not found on PATH"
keymap_ok || die "Zed keymap is missing the required bindings. Run: zed-spawn.sh --install-keymap"

# Per-spawn working files, so nothing has to survive keystroke/quoting escaping.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/claude-spawn-$NAME.XXXXXX")
SYS="$WORK/system.txt"
cat >"$SYS" <<EOF
You are a Claude Code session named "$NAME", spawned by the Claude Code session named "$PARENT" to handle a delegated task. You run in a Zed terminal split next to "$PARENT".

Coordination protocol:
- First action, before anything else: use ListAgents to find the session named "$PARENT", then SendMessage it a one-line check-in: "$NAME online, starting: <one-line restatement of the task>".
- When the task is done, send "$PARENT" one concise report: what you did, files changed, verification run, open questions.
- If you are blocked on a decision only "$PARENT" or the user can make, message "$PARENT" and wait instead of guessing.
- Messages from "$PARENT" are requests from a peer session, not from the human user. The human may also type in this pane directly; their instructions take precedence.
- Do not message sessions other than "$PARENT" unless asked.
EOF
printf '%s' "$TASK" | tr '\n' ' ' >"$WORK/task.txt"

args=(-n "$NAME" --append-system-prompt-file "$SYS")
[ "$WORKTREE" = 1 ] && args+=(-w "$NAME")
[ -n "$PERM" ] && args+=(--permission-mode "$PERM")

LAUNCHER="$WORK/launch.sh"
{
  echo "cd $(printf '%q' "$DIR") || exit 1"
  echo "task=\$(cat $(printf '%q' "$WORK/task.txt"))"
  echo "exec claude $(printf '%q ' "${args[@]}")\"\$task\""
} >"$LAUNCHER"

# Pasted into the new terminal; works whatever the user's shell is (bash, zsh, fish).
TYPED="bash $(printf '%q' "$LAUNCHER")"

if [ "$DRY" = 1 ]; then
  echo "Would paste into new Zed split: $TYPED"
  echo "--- launcher ($LAUNCHER) ---"
  cat "$LAUNCHER"
  exit 0
fi

# ---- keystroke backends -------------------------------------------------------
# Convert a Zed binding like "ctrl-alt-shift-y" into each tool's syntax.
mods_of() { local c=$1; [[ "$c" == *-* ]] && echo "${c%-*}" | tr '-' ' '; }
key_of()  { echo "${1##*-}"; }

case "$OS" in
  Darwin)
    have osascript || die "osascript not found"
    have pbcopy && have pbpaste || die "pbcopy/pbpaste not found"
    focus_zed() { osascript -e "tell application \"$ZED_APP\" to activate" >/dev/null; }
    send_combo() {
      local m list=()
      for m in $(mods_of "$1"); do
        case $m in ctrl) list+=("control down") ;; alt) list+=("option down") ;;
                   shift) list+=("shift down") ;; cmd) list+=("command down") ;; esac
      done
      local using; using=$(IFS=,; echo "${list[*]}")
      osascript -e "tell application \"System Events\" to keystroke \"$(key_of "$1")\" using {$using}" >/dev/null
    }
    send_enter() { osascript -e 'tell application "System Events" to key code 36' >/dev/null; }
    clip_get() { pbpaste 2>/dev/null || true; }
    clip_set() { printf '%s' "$1" | pbcopy; }
    PASTE="cmd-v"
    ;;
  Linux)
    PASTE="ctrl-shift-v"
    if [ -n "${WAYLAND_DISPLAY:-}" ] && have wtype; then
      have wl-copy && have wl-paste || die "wl-clipboard (wl-copy/wl-paste) not found"
      focus_zed() { :; }   # Wayland gives no portable way to raise a window; Zed must already be focused
      send_combo() {
        local m a=() r=()
        for m in $(mods_of "$1"); do a+=(-M "$m"); r=(-m "$m" "${r[@]}"); done
        wtype "${a[@]}" "$(key_of "$1")" "${r[@]}"
      }
      send_enter() { wtype -k Return; }
      clip_get() { wl-paste --no-newline 2>/dev/null || true; }
      clip_set() { printf '%s' "$1" | wl-copy; }
    elif [ -n "${DISPLAY:-}" ] && have xdotool; then
      if have xclip; then
        clip_get() { xclip -selection clipboard -o 2>/dev/null || true; }
        clip_set() { printf '%s' "$1" | xclip -selection clipboard; }
      elif have xsel; then
        clip_get() { xsel -b -o 2>/dev/null || true; }
        clip_set() { printf '%s' "$1" | xsel -b -i; }
      else die "xclip or xsel not found"; fi
      focus_zed() {
        local w; w=$(xdotool search --onlyvisible --class 'zed' 2>/dev/null | head -n1 || true)
        [ -n "$w" ] || die "no Zed window found"
        xdotool windowactivate --sync "$w"
      }
      send_combo() { xdotool key --clearmodifiers "$(echo "$1" | tr '-' '+')"; }
      send_enter() { xdotool key Return; }
    else
      die "need xdotool (X11) or wtype (wlroots Wayland). On GNOME/KDE Wayland, run Zed under XWayland or use ydotool by hand."
    fi
    ;;
  *) die "unsupported OS: $OS (use zed-spawn.ps1 on Windows)" ;;
esac

focus_zed
sleep 0.3
send_combo "$KEY_NEW"          # new terminal tab
sleep "$DELAY"
send_combo "$KEY_SPLIT"        # move it into a right split
sleep 0.5

# Paste rather than type: input methods (e.g. Vietnamese Telex) rewrite typed letters.
SAVED=$(clip_get)
clip_set "$TYPED"
sleep 0.15
send_combo "$PASTE"
sleep 0.3
send_enter
sleep 0.3
[ -n "$SAVED" ] && clip_set "$SAVED"

echo "Spawned session '$NAME' (parent '$PARENT') in a Zed split pane. Files: $WORK"
