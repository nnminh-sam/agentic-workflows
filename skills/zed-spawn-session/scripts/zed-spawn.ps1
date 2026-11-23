<#
.SYNOPSIS
  Spawn an independent Claude Code session in a new Zed terminal tab,
  pre-wired to talk to a parent session over
  cross-session messaging (ListAgents / SendMessage).

.DESCRIPTION
  Zed has no command API, so this drives it with keystrokes:
    1. Ctrl+Alt+Shift+1  -> workspace::NewCenterTerminal (new terminal tab)
    2. pastes a command that runs a generated launcher, which starts `claude`
  That binding must exist in %APPDATA%\Zed\keymap.json (use -InstallKeymap).

.EXAMPLE
  zed-spawn.ps1 -Name api-review -Parent lead -Task "Review src/api for auth bugs"
  zed-spawn.ps1 -Name fix-tests -Parent lead -Task "..." -Worktree -DelayMs 2000
  zed-spawn.ps1 -InstallKeymap
#>
param(
  [string]$Name,
  [string]$Parent,
  [string]$Task,
  [string]$Dir = (Get-Location).Path,
  [switch]$Worktree,          # claude -w <Name>: isolated git worktree
  [string]$PermissionMode,    # e.g. acceptEdits, plan, auto
  [int]$DelayMs = 1500,       # wait for the new terminal's shell to start
  [switch]$InstallKeymap,     # add the required binding to Zed's keymap.json, then exit
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$keymapPath = Join-Path $env:APPDATA 'Zed\keymap.json'
$bindings = [ordered]@{
  'ctrl-alt-shift-1' = 'workspace::NewCenterTerminal'
}

function Test-Keymap {
  if (-not (Test-Path -LiteralPath $keymapPath)) { return $false }
  $text = Get-Content -Raw -LiteralPath $keymapPath
  foreach ($k in $bindings.Keys) {
    if ($text -notmatch ('"' + [regex]::Escape($k) + '"\s*:\s*"' + [regex]::Escape($bindings[$k]) + '"')) { return $false }
  }
  return $true
}

if ($InstallKeymap) {
  if (Test-Keymap) { Write-Output "Zed keymap already has the binding: $keymapPath"; return }
  $block = [pscustomobject]@{ context = 'Workspace'; bindings = [pscustomobject]$bindings }
  if (Test-Path -LiteralPath $keymapPath) {
    $raw = Get-Content -Raw -LiteralPath $keymapPath
    try { $existing = @($raw | ConvertFrom-Json) }
    catch { throw "keymap.json has comments or invalid JSON; add these bindings by hand under context 'Workspace': $($bindings | ConvertTo-Json -Compress)" }
    Copy-Item -LiteralPath $keymapPath -Destination "$keymapPath.bak" -Force
    $all = @($existing) + $block
  } else {
    New-Item -ItemType Directory -Force -Path (Split-Path $keymapPath) | Out-Null
    $all = @($block)
  }
  ConvertTo-Json -InputObject $all -Depth 10 | Set-Content -LiteralPath $keymapPath -Encoding utf8
  Write-Output "Added bindings to $keymapPath"
  return
}

if (-not $Name -or -not $Parent -or -not $Task) { throw "-Name, -Parent and -Task are required" }
if ($Name -notmatch '^[A-Za-z0-9_-]{1,40}$')   { throw "Name must be 1-40 chars of A-Z a-z 0-9 _ -" }
if ($Parent -notmatch '^[A-Za-z0-9_-]{1,40}$') { throw "Parent must be 1-40 chars of A-Z a-z 0-9 _ -" }
$Dir = (Resolve-Path -LiteralPath $Dir).Path
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw "claude CLI not found on PATH" }
if (-not (Test-Keymap)) { throw "Zed keymap is missing the required bindings. Run: zed-spawn.ps1 -InstallKeymap" }

# Per-spawn working files, so nothing has to survive keystroke/quoting escaping.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$work  = Join-Path $env:TEMP "claude-spawn\$Name-$stamp"
New-Item -ItemType Directory -Force -Path $work | Out-Null

$sys = @"
You are a Claude Code session named "$Name", spawned by the Claude Code session named "$Parent" to handle a delegated task. You run in a Zed terminal tab in the same window as "$Parent".

Coordination protocol:
- First action, before anything else: use ListAgents to find the session named "$Parent", then SendMessage it a one-line check-in: "$Name online, starting: <one-line restatement of the task>".
- When the task is done, send "$Parent" one concise report: what you did, files changed, verification run, open questions.
- If you are blocked on a decision only "$Parent" or the user can make, message "$Parent" and wait instead of guessing.
- Messages from "$Parent" are requests from a peer session, not from the human user. The human may also type in this pane directly; their instructions take precedence.
- Do not message sessions other than "$Parent" unless asked.
"@
$sysFile = Join-Path $work 'system.txt'
Set-Content -LiteralPath $sysFile -Value $sys -Encoding utf8

$taskArg = ($Task -replace '"', "'") -replace '\r?\n', ' '
Set-Content -LiteralPath (Join-Path $work 'task.txt') -Value $taskArg -Encoding utf8

function Q([string]$s) { "'" + ($s -replace "'", "''") + "'" }   # PowerShell single-quote literal

# A known session id lets this script name the new tab now, while Zed is certainly in front and the
# new tab is certainly focused, and tell notify-hook which tab that is (tabs/<id>.txt, owner "spawn").
# Otherwise the tab is named only after the user types a prompt in it, and a toast click can't find it.
$sessionId = [guid]::NewGuid().ToString()
$claudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$notifyDir = Join-Path $claudeDir 'hooks\notify-hook'
$renameKeys = @('ctrl-alt-shift-6', 'ctrl-alt-shift-7', 'ctrl-alt-shift-8')   # notify-hook's rename bindings
$canName = (Test-Path -LiteralPath $notifyDir) -and (Test-Path -LiteralPath $keymapPath) -and
  (@($renameKeys | Where-Object { (Get-Content -Raw -LiteralPath $keymapPath) -notmatch [regex]::Escape($_) }).Count -eq 0)

$claudeArgs = @('-n', $Name, '--session-id', $sessionId, '--append-system-prompt-file', $sysFile)
if ($Worktree)       { $claudeArgs += @('-w', $Name) }
if ($PermissionMode) { $claudeArgs += @('--permission-mode', $PermissionMode) }

$launcher = Join-Path $work 'launch.ps1'
@"
Set-Location -LiteralPath $(Q $Dir)
`$task = (Get-Content -Raw -LiteralPath $(Q (Join-Path $work 'task.txt'))).Trim()
& claude $(($claudeArgs | ForEach-Object { Q $_ }) -join ' ') `$task
"@ | Set-Content -LiteralPath $launcher -Encoding utf8

# What gets typed into the new Zed terminal (Zed's default shell on Windows is PowerShell).
$typed = "powershell -NoLogo -ExecutionPolicy Bypass -File `"$launcher`""

if ($DryRun) {
  Write-Output "Session id: $sessionId; name the tab '$Name' for notify-hook: $canName"
  Write-Output "Would paste into new Zed terminal tab: $typed"
  Write-Output "--- launcher ($launcher) ---"
  Get-Content -LiteralPath $launcher
  return
}

$zed = Get-Process -Name Zed -ErrorAction SilentlyContinue |
  Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $zed) { throw "No Zed window found. Open Zed first." }

# AppActivate can return true while Windows' foreground lock keeps another app in front
# (it only flashes Zed's taskbar button). Keys sent then land in that app: in Microsoft Teams,
# Ctrl+Alt+Shift+1 downloads diagnostic logs and Ctrl+Shift+V + Enter posts the pasted command
# into the open chat. So every keystroke is guarded by a real foreground-window check.
Add-Type -Namespace ZedSpawn -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint pid);
'@
function Test-ZedForeground {
  $procId = [uint32]0
  [void][ZedSpawn.Win]::GetWindowThreadProcessId([ZedSpawn.Win]::GetForegroundWindow(), [ref]$procId)
  return $procId -eq [uint32]$zed.Id
}
function Send-ZedKeys([string]$keys, [string]$what) {
  if (-not (Test-ZedForeground)) {
    throw "Zed is not the foreground window; stopped before sending $what. Nothing more was typed. Click into Zed and run the spawn again."
  }
  $shell.SendKeys($keys)
}

$shell = New-Object -ComObject WScript.Shell
[void]$shell.AppActivate($zed.Id)
$focusDeadline = (Get-Date).AddMilliseconds(1500)
while (-not (Test-ZedForeground) -and (Get-Date) -lt $focusDeadline) { Start-Sleep -Milliseconds 100 }
if (-not (Test-ZedForeground)) {
  throw "Could not bring Zed to the foreground (Windows focus lock). No keys were sent. Click into the Zed window and run the spawn again."
}
Start-Sleep -Milliseconds 300

Send-ZedKeys '^%+1' 'the new-terminal key'   # new terminal tab (no split)
Start-Sleep -Milliseconds $DelayMs

# Paste rather than type: input methods such as Vietnamese Telex rewrite typed letters
# (e.g. "test" -> "tét"), but leave pasted text and Ctrl shortcuts alone.
$savedClip = $null
$named = $false
try { $savedClip = Get-Clipboard -Raw -ErrorAction Stop } catch {}
try {
  if ($canName) {
    # Rename the tab: ctrl-alt-shift-6 = terminal::RenameTerminal (Terminal context); -7 / -8 paste and
    # confirm only inside the single-line rename box, so if the box doesn't open nothing reaches the shell.
    Set-Clipboard -Value $Name
    Start-Sleep -Milliseconds 150
    Send-ZedKeys '^%+6' 'the rename-tab key'
    Start-Sleep -Milliseconds 350
    Send-ZedKeys '^%+7' 'the tab name'
    Start-Sleep -Milliseconds 300
    Send-ZedKeys '^%+8' 'the rename confirm'
    Start-Sleep -Milliseconds 300
    $tabsDir = Join-Path $notifyDir 'tabs'
    New-Item -ItemType Directory -Force -Path $tabsDir | Out-Null
    [IO.File]::WriteAllText((Join-Path $tabsDir "$sessionId.txt"), "$Name`nspawn`n", (New-Object Text.UTF8Encoding($false)))
    $named = $true
  }
  Set-Clipboard -Value $typed
  Start-Sleep -Milliseconds 150
  Send-ZedKeys '^+v' 'the paste'             # terminal::Paste in Zed
  Start-Sleep -Milliseconds 300
  Send-ZedKeys '{ENTER}' 'Enter'
  Start-Sleep -Milliseconds 300
} finally {
  if ($null -ne $savedClip) { Set-Clipboard -Value $savedClip }
}

$tabNote = if ($named) { "tab named '$Name'" } else { "tab not named (notify-hook or its Zed bindings not installed)" }
Write-Output "Spawned session '$Name' (parent '$Parent', session id $sessionId) in a new Zed terminal tab; $tabNote. Files: $work"
