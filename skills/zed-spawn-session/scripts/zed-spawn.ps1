<#
.SYNOPSIS
  Spawn an independent Claude Code session in a new Zed terminal, placed in a
  right-hand split pane, pre-wired to talk to a parent session over
  cross-session messaging (ListAgents / SendMessage).

.DESCRIPTION
  Zed has no command API, so this drives it with keystrokes:
    1. Ctrl+Alt+Shift+1  -> workspace::NewCenterTerminal (new terminal tab)
    2. Ctrl+Alt+Shift+2  -> pane::SplitAndMoveRight      (move it to a right split)
    3. types a command that runs a generated launcher, which starts `claude`
  Those two bindings must exist in %APPDATA%\Zed\keymap.json (use -InstallKeymap).

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
  [switch]$InstallKeymap,     # add the two required bindings to Zed's keymap.json, then exit
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$keymapPath = Join-Path $env:APPDATA 'Zed\keymap.json'
$bindings = [ordered]@{
  'ctrl-alt-shift-1' = 'workspace::NewCenterTerminal'
  'ctrl-alt-shift-2' = 'pane::SplitAndMoveRight'
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
  if (Test-Keymap) { Write-Output "Zed keymap already has the bindings: $keymapPath"; return }
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
You are a Claude Code session named "$Name", spawned by the Claude Code session named "$Parent" to handle a delegated task. You run in a Zed terminal split next to "$Parent".

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

$claudeArgs = @('-n', $Name, '--append-system-prompt-file', $sysFile)
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
  Write-Output "Would type into new Zed split: $typed"
  Write-Output "--- launcher ($launcher) ---"
  Get-Content -LiteralPath $launcher
  return
}

$zed = Get-Process -Name Zed -ErrorAction SilentlyContinue |
  Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $zed) { throw "No Zed window found. Open Zed first." }

$shell = New-Object -ComObject WScript.Shell
if (-not $shell.AppActivate($zed.Id)) { throw "Could not focus the Zed window." }
Start-Sleep -Milliseconds 300

$shell.SendKeys('^%+1')                 # new terminal tab
Start-Sleep -Milliseconds $DelayMs
$shell.SendKeys('^%+2')                 # move it into a right split
Start-Sleep -Milliseconds 500

# Paste rather than type: input methods such as Vietnamese Telex rewrite typed letters
# (e.g. "test" -> "tét"), but leave pasted text and Ctrl shortcuts alone.
$savedClip = $null
try { $savedClip = Get-Clipboard -Raw -ErrorAction Stop } catch {}
Set-Clipboard -Value $typed
Start-Sleep -Milliseconds 150
$shell.SendKeys('^+v')                  # terminal::Paste in Zed
Start-Sleep -Milliseconds 300
$shell.SendKeys('{ENTER}')
Start-Sleep -Milliseconds 300
if ($null -ne $savedClip) { Set-Clipboard -Value $savedClip }

Write-Output "Spawned session '$Name' (parent '$Parent') in a Zed split pane. Files: $work"
