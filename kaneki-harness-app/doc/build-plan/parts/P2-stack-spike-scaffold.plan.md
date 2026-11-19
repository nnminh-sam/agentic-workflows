# P2 · Wails setup and project scaffold (M0)

D10 is closed: **Go + Wails**, owner decision 2026-11-18. No Rust, no Tauri, no Dear ImGui, no comparison spike.
(The file name keeps "stack-spike" so links do not break; the "spike" here is the small Wails slice of section 4.)

## 1. Goal and Done when

**Goal.** Set up the Go + Wails toolchain on this machine, prove with a small spike slice that Wails does what P3
and P4 need, then leave `kaneki-harness-app/` scaffolded so P3 (Host core) and P4 (UI shell) can start immediately.

**Done when**
- The Wails spike slice (section 4) runs: behaviours 1-7 are checked and the measurement table (section 4.4) is filled in.
- D10 is recorded as closed in `kaneki-harness/docs/desktop-app/plan.md` section 6 using the decision-record template (section 9).
- `kaneki-harness-app/` has the scaffold of section 6: `wails dev` opens a window showing the design system
  with hot reload, `wails build` produces a release exe, and `.gitignore` keeps build output out of git.
- The spike code is kept under `spikes/wails/` (not deleted) so the numbers can be reproduced.

## 2. Scope

**In:** prerequisites check, install plan (Go + Wails CLI on D:), Wails spike slice, measurement script, Telex check,
decision record, scaffold, frontend-tooling recommendation.
**Out:** real screens (P4), Host features beyond the slice (P3), installer/updater/signing (P8), UI framework choice (P4).
Not in scope by owner decision: any second stack (Tauri/Rust) and any ImGui prototype.

## 3. Inputs and outputs

**Inputs from other parts:** none blocking. Soft inputs: P3 wants the IPC naming convention (section 6.4); P1 supplies
the `sdlc tickets` shape (already known: `{"tickets":[{ticket, kind, lane, strip[], nextPending, next, tokensTotal,...}]}`,
`_context.md` line 33).

**Outputs:**
- Decision record in plan.md section 6 (D10 closed).
- Scaffold (section 6) and the IPC convention: `invoke(name, args)` / `on(name, payload)`, wrapped in one file
  `ui/host-bridge.js` so the UI never imports Wails APIs directly (the seam, section 6.4).
- Spike IPC names (spike-only; the scaffold uses P1's catalogue): `list_tickets`, `run_sdlc(args[])`, event `decisions-changed {ticket}`, `toast(title, body)`.
- `scripts/measure.ps1` (reusable by P8 for regression budgets).

## 4. Spike slice and measurement

### 4.1 Wails: what to verify

The UI runs in the system WebView2 (already installed, runtime 157.0.4341.46, registry
`EdgeUpdate\Clients\{F3017226-...}` pv value), so memory is dominated by WebView2. The Host is a Go exe. Wails on
Windows is pure Go with the WebView2 loader embedded, so no C++ toolchain is needed.

| Topic | Wails (Go Host) |
|---|---|
| Current stable | v2.x stable (ASSUMPTION: 2.10-2.11); v3 exists but alpha/beta as of my knowledge (ASSUMPTION; re-check in P2-T01) |
| Toolchain to add | Go SDK about 0.25-0.5 GB (ASSUMPTION), `wails` CLI about 50 MB, build cache under 1 GB |
| WebView2 | uses the system runtime; installer can bootstrap it (P8) |
| Notifications | v2 core has no notification API; use a Go lib (`beeep` or `go-toast`), or v3 built-in (ASSUMPTION) |
| Single instance | v2 has the `SingleInstanceLock` option (ASSUMPTION), v3 too |
| File watching | `fsnotify` in the Host |
| Child processes | `os/exec`, simple |
| Updater | no first-party updater in v2 (ASSUMPTION); roll our own or skip (local tool, can ship a zip); P8 decides |
| Permission model | none; any bound Go method is callable by the UI, so bind one `App` struct only |
| Hot reload dev | `wails dev` |
| IPC | bound Go methods + `runtime.EventsEmit/EventsOn` |

**Newcomer concepts (brief).**
- *WebView2*: Edge's rendering engine as a Windows component. The "window" is a thin native shell around it; your UI is a web page.
- *Host process*: the native Go exe. Does what a browser page cannot: spawn `python`, watch files, show toasts.
- *Binding*: a Go method exposed to the page by Wails. Anything bound is callable by the page, so keep the bound surface small.

**Why a spike and not just a scaffold:** the ASSUMPTION rows above (toast library, single instance, v2 vs v3) and
behaviours 1-7 in 4.2 are what P3/P4/P5 rely on. If one fails (for example the toast does not show from the built
exe), the fix is a different Go library or the Wails v3 route, recorded in the decision record, not a new stack.

### 4.2 Spike slice (Wails)

Directory: `kaneki-harness-app/spikes/wails/`. UI is one `index.html` + one
`app.js` (no bundler), linking `../../design-system/tokens.css` and `components.css` copied or symlinked at dev time.

Behaviours (each one a pass/fail line in `spikes/results.md`; P3 and P4 depend on all of them):
1. **Window** opens with title "Kaneki spike", size 1100x700, shows a `.k-*` header from the design system.
2. **Ticket list**: on load the Host runs `python <repo>/kaneki-harness/skills/kaneki-flow/scripts/sdlc.py tickets` with
   cwd = `D:/workspace/HAISE/testing/todo-nestjs-backend` (read-only command), parses JSON, UI renders one row per
   ticket (`ticket`, `lane`, `nextPending`, `tokensTotal`). Expect TODO-1 and TODO-1-2.
3. **Watch**: Host watches `.sdlc/tickets/*/decisions.jsonl` (non-recursive per ticket dir, debounce 200 ms) and emits
   `decisions-changed {ticket}`; UI appends a line with a timestamp. Test WITHOUT touching the real workspace: point
   the watcher at a scratch copy `spikes/_fixture/.sdlc/tickets/T/decisions.jsonl` and `echo >>` to it. (The real workspace stays read-only.)
4. **Toast**: a "Test toast" button, and also on every `decisions-changed`, shows a native Windows toast.
   Note: unpackaged exes may need an AppUserModelID for toasts to show under dev; record whether toast appears in
   `dev` vs the built exe.
5. **Run button**: runs `python sdlc.py gate --help` (help only, changes nothing), streams stdout to a `<pre>`.
   Child must not flash a console window (Go: `SysProcAttr{HideWindow:true, CreationFlags:0x08000000}`, the `CREATE_NO_WINDOW` flag).
6. **Telex field**: a `<textarea>` labelled "note"; type `Vieejt Nam` -> "Việt Nam", `đường`, `ươ`, tone marks after a
   word is complete. Also test with Windows built-in Vietnamese keyboard and, if installed, Unikey/EVKey (record which).
7. **Idle**: after load, do nothing for 5 minutes (window visible, not minimized; then repeat minimized).

### 4.3 Measurement script (`kaneki-harness-app/scripts/measure.ps1`, to be written in P2-T05)

```powershell
param([string]$Exe, [int]$IdleSeconds = 300, [string]$Label = "run")
# Cold start: time from Process.Start to the app writing "READY" (spike UI/Host calls a tiny
# `host_ready` IPC that creates %TEMP%\kaneki-ready.flag after first paint via requestAnimationFrame x2).
Remove-Item "$env:TEMP\kaneki-ready.flag" -ErrorAction SilentlyContinue
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process $Exe -PassThru
while (-not (Test-Path "$env:TEMP\kaneki-ready.flag")) { Start-Sleep -Milliseconds 20; if ($sw.Elapsed.TotalSeconds -gt 15) { break } }
$coldMs = $sw.ElapsedMilliseconds

function Get-Tree($rootPid) {   # the exe + all descendants (msedgewebview2.exe children)
  $all = Get-CimInstance Win32_Process
  $ids = @($rootPid); $queue = @($rootPid)
  while ($queue) { $cur,$queue = $queue; $kids = $all | ? ParentProcessId -eq $cur | % ProcessId; $ids += $kids; $queue += $kids }
  $ids | Get-Unique
}
Start-Sleep 20   # settle
$t0 = Get-Date; $cpu0 = 0; $samples = @()
$ids = Get-Tree $p.Id
$cpuStart = ($ids | % { (Get-Process -Id $_ -EA SilentlyContinue).CPU } | Measure-Object -Sum).Sum
Start-Sleep ($IdleSeconds - 20)
$ids = Get-Tree $p.Id
$procs = $ids | % { Get-Process -Id $_ -EA SilentlyContinue }
$cpuEnd = ($procs.CPU | Measure-Object -Sum).Sum
$ws   = [math]::Round((($procs.WorkingSet64 | Measure-Object -Sum).Sum)/1MB,1)
# Private memory is the fairer number (shared DLL pages inflate working set):
$priv = [math]::Round((($procs.PrivateMemorySize64 | Measure-Object -Sum).Sum)/1MB,1)
$cpuPct = [math]::Round(100 * ($cpuEnd - $cpuStart) / (($IdleSeconds-20) * [Environment]::ProcessorCount), 3)
[pscustomobject]@{Label=$Label; ColdStartMs=$coldMs; Procs=$procs.Count; WorkingSetMB=$ws; PrivateMB=$priv; IdleCpuPct=$cpuPct;
  ExeMB=[math]::Round((Get-Item $Exe).Length/1MB,1)} | Format-Table -AutoSize
Stop-Process -Id $p.Id -Force   # closes children too (WebView2 exits with its owner)
```

Notes: this measures the Wails exe only, as a baseline against the plan's targets (no second stack to compare). Report both Working Set (what Task Manager roughly shows, the plan's metric) and Private bytes (honest number).
Run 3 times, report the median; first run after reboot is excluded as an outlier but noted. Close other Edge windows
first, since shared WebView2 processes from other apps are not children and are not counted. The ready-flag is a
spike-only hook; the real app should use a log timestamp instead.

### 4.4 Wails measurements (baseline, not a comparison)

Measured on this machine against the plan's targets. One column only.

| Criterion | Target | Wails | Notes |
|---|---|---|---|
| Idle memory, working set sum (MB) | < 80 (goal, not gate) | | process count: |
| Idle memory, private bytes (MB) | report | | |
| Idle memory, minimized (MB) | report | | |
| Cold start to first paint, median of 3 (ms) | < 1000 (gate) | | |
| Idle CPU 5 min (% of all cores) | ~0 | | |
| Toolchain disk added (GB, before/after on D:) | report | | |
| First cold build time / incremental rebuild (s) | report | | |
| Release exe (MB) | report | | |
| Vietnamese Telex in textarea | pass (gate) | | IME tested: |
| Toast shows in dev / built exe | pass (gate) | | |
| Child process without console flash | pass | | |
| Watch event latency (ms, echo to event) | < 500 | | |
| Hours: spike build, first-timer | report | | |

If a gate row fails, record the fix (different library, v3 route) in the decision record. If the memory goal fails,
the number is probably WebView2 itself: propose a revised budget (for example 150 MB) in the record.

## 5. Install plan (needs user approval before any command is run)

**Constraint found:** C: has only 9.9 GB free, D: has 14.4 GB (read-only probe). Keep everything on D: by setting
the environment variables BEFORE installing (the repo is on D:, so build output already is).

State today: Node 22.23.2, npm 10.9.8, Python 3.12.10, git, winget present; no Go, cmake, pnpm; WebView2 157.0.4341.46.

**Go + Wails (about 0.5 GB)**
```
# Go SDK: use the zip (not the MSI) so it lands on D:; unzip to D:\toolchains\go-sdk
setx GOROOT      D:\toolchains\go-sdk
setx GOPATH      D:\toolchains\go            # module cache and the `wails` binary live here
setx GOCACHE     D:\toolchains\go-cache      # build cache (can reach about 1 GB)
# add D:\toolchains\go-sdk\bin and D:\toolchains\go\bin to the user PATH, then reopen the shell
go install github.com/wailsapp/wails/v2/cmd/wails@latest   # pin the exact version after confirming current stable
wails doctor                                # reports missing prerequisites (WebView2, npm, etc.)
```
`winget install GoLang.Go` is the alternative but installs to `C:\Program Files\Go` (about 250-500 MB ASSUMPTION);
prefer the zip. An antivirus exclusion for `D:\toolchains` can speed up the first build.

Uninstall: delete `D:\toolchains\go-sdk`, `D:\toolchains\go`, `D:\toolchains\go-cache`, remove the env vars
(`setx GOPATH ""` and so on), delete `spikes/wails/build/bin`.

Record disk free on D: before and after the install (`Get-PSDrive D`).

## 6. Scaffold layout (Wails)

`kaneki-harness-app/` already contains `design-system/` and `doc/`.

```
kaneki-harness-app/
  design-system/          existing, source of truth (tokens.json -> tokens.css via build_tokens.py)
  doc/                    existing (build-plan/, design-system.html, adr/ for decision notes)
  host/                   Host source (Go): go.mod, main.go, app.go, wails.json
    *.go, internal/       modules named after P3: sdlc_runner, watcher, sessions, toast, ipc
  ui/
    index.html
    src/
      host-bridge.js      THE ONLY file that imports Wails APIs; exports invoke(name,args), on(name,cb)
      main.js, views/, state/    (P4 owns the structure under src/)
    vendor/               (none by default)
    public/ds/            design-system files copied here at build/dev start (see 6.2)
  contracts/              JSON schemas for IPC payloads + copies/links to harness schemas (P1)
  fixtures/               sample workspace snapshot: .sdlc/tickets/* copied from todo-nestjs-backend (read-only copy,
                          sanitized), used by tests and the watcher spike
  tests/                  host unit tests (`go test` files live beside the code), ui tests, e2e smoke
  scripts/                measure.ps1, sync-ds.mjs, dev.ps1
  spikes/                 wails/ spike code and results.md (kept, not shipped)
  package.json            scripts: dev, build, sync-ds, test
  README.md               created at P2-T09: prerequisites + the 4 commands
  .gitignore
```

### 6.1 `.gitignore` (add at `kaneki-harness-app/.gitignore`)
```
node_modules/
dist/
host/build/bin/         # wails build output
host/frontend/dist/
spikes/**/build/bin/
spikes/**/node_modules/
ui/public/ds/           # generated copy of design-system
*.log
.env
```
Also add `__pycache__/` is already untracked in the repo root status; not this part's concern.

### 6.2 How the UI consumes `design-system/`
**Recommendation: copy at dev/build start** with `scripts/sync-ds.mjs` (copies `tokens.css`, `components.css`,
`icons.js` to `ui/public/ds/`; runs `python design-system/build_tokens.py --check` first so stale tokens fail loudly).
Why: (a) Wails serves only the UI folder as app assets, so files outside it are not reachable in the release
build; (b) symlinks are fragile on Windows (need developer mode); (c) one-way copy keeps `design-system/` the single
source and avoids edits in the copy (the folder is gitignored, so edits would be lost, which is the desired signal).
Alternative (serve as-is via a Wails asset path) works in dev but needs the same copy for release, so copy always.
`gallery-data.js` and `gallery.html` are not copied (50 KB of gallery-only data).

### 6.3 Dev loop and commands
- `npm run dev` -> `wails dev` (serves the UI, builds the Host in debug, opens the window; UI edits hot reload; Go edits rebuild in a few seconds).
- `npm run build` -> sync-ds, then `wails build` giving a single exe (no installer; P8 owns packaging).
- `npm run sync-ds`, `npm test` (UI tests + host tests).

### 6.4 IPC seam and security defaults (to stop rework in P3/P4)
- `ui/host-bridge.js` exports `invoke(name, args)` and `on(name, cb)` (Wails bindings plus a browser stub
  for fixtures). The UI never imports Wails APIs directly; only this file knows the stack, so a later port costs about 1 day, not a rewrite.
- Host exposes a fixed list of commands only; no generic "run any shell". `run_sdlc(args[])` takes an argument array
  (never a shell string) and rejects unknown subcommands outside an allowlist (read-only ones plus the confirmed writes of P5).
- No open port (D8). Wails: bind one `App` struct, nothing else.
- Set the AppUserModelID/identifier to `com.nnminh.kaneki-harness` (ASSUMPTION on naming) so toasts group correctly.

## 7. Frontend tooling decision input

**Recommendation: plain ES modules, no bundler, for now; add Vite only if P4 chooses a framework that needs it.**
Evidence: the design system is plain CSS plus one `icons.js` and the mock is hand-written HTML with one inline script
(`design-system/app-mock.html` lines 7-8, 149-150), so nothing needs compiling; WebView2 supports ES modules,
`import`, `fetch` natively; zero `node_modules` keeps the installed footprint small and builds instant.
Consequences to state to P4: with no bundler there is no JSX/TS compile step; if P4 wants Svelte/Preact+JSX/TypeScript,
use Vite (the Wails templates support it; about 40 MB of node_modules). Node 22 is already present, so
Vite costs nothing extra to install. Rule for P4: if choosing a framework, prefer one that compiles away or is under
~10 KB (Svelte, Preact, Lit, or none) to protect the idle memory budget.

## 8. Tasks

| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P2-T01 | Verify prerequisites read-only: current Wails stable and Go version, Wails v2 vs v3 status, toast library and single-instance support, WebView2 runtime, free disk on C:/D:. Record in `spikes/results.md` | S | none | Versions replace every ASSUMPTION in section 4.1 |
| P2-T02 | User approves the install; set `GOROOT/GOPATH/GOCACHE` to D: | S | T01 | Env vars set, disk-free baseline recorded |
| P2-T03 | Install Go + Wails CLI under `D:\toolchains`, run `wails doctor` | S | T02 | Doctor reports no missing items; disk delta recorded |
| P2-T04 | Build the Wails spike slice (behaviours 1-6) with a fixture workspace for the watcher | M | T03 | Every behaviour in 4.2 checked, or failed with a reason and a fix |
| P2-T05 | Write `scripts/measure.ps1` and a ready-flag hook; dry-run on a trivial exe (e.g. notepad) to validate the process-tree summing | S | none | Script prints sane numbers; descendants counted |
| P2-T06 | Measure Wails (3 runs, plus the 5-min idle of behaviour 7) and fill the table in 4.4 | S | T04, T05 | Table filled |
| P2-T07 | Telex / Vietnamese IME test in the Wails slice, typed by the owner | S | T04 | Telex row filled by the owner, not the agent; IME named |
| P2-T08 | Decision record into plan.md section 6 (template in section 9): "D10 closed: Go/Wails, owner choice"; update the plan.md M0 row to `kaneki-harness-app/` | S | T06, T07 | Record written (commit only when the owner asks) |
| P2-T09 | Scaffold of section 6: dirs, `host-bridge.js` seam, `sync-ds.mjs`, package.json scripts, `.gitignore`, README | M | T08 | `wails dev` shows the design-system header; `wails build` makes the exe; a fresh clone follows the README |

Critical path about 2 days for a first-timer (Go install, first Wails build and the Telex test are the long poles).
The old task ids from when D10 was open are mapped in `00-build-plan.md` section 6.

## 9. Decision record template (paste into plan.md section 6)

```
### D10 outcome (closed 2026-11-18)
Decision: Go + Wails vX (exact version from P2-T01). Owner choice, no stack comparison run.
Considered and dropped by the owner: Tauri/Rust and C++/Dear ImGui (ImGui cannot reuse the HTML design system and has Telex/IME risk).
Machine: <CPU/RAM/OS build>, WebView2 <version>, spike commit <sha>.
Results (median of 3, 5-min idle): <paste table from kaneki-harness-app/spikes/results.md>
Gates: Telex <pass/fail>, toast in built exe <pass/fail>, cold start <ms>.
Costs accepted: toolchain <GB> on D:, first build <min>, plugin gaps <list> and how filled (toast library, single instance).
Reversal trigger: <e.g. idle memory over 150 MB after P4, or toast unreliable in the built exe>.
Seam: all Host APIs go through ui/host-bridge.js; a later port is estimated at <days>.
```

## 10. Tests / verification
- Measurement script validated on a known exe before trusting it (T05); numbers cross-checked once against Task Manager's Details tab (add "Memory (private working set)" column).
- Spike behaviours 1-7 are a manual checklist in `spikes/results.md`, each with pass/fail and a screenshot or log line.
- Watcher test uses the fixture folder only. The real workspace is touched solely by read-only `sdlc tickets` and `gate --help`.
- Scaffold: fresh-clone test (clone to a temp dir, follow README, `dev` and `build` succeed); `build_tokens.py --check` runs in `sync-ds`.
- Telex: typed by the owner with their normal IME; automated tests cannot cover it.

## 11. Risks

| Risk | Mitigation |
|---|---|
| C: nearly full (9.9 GB free); Go caches fill it | GOROOT/GOPATH/GOCACHE on D:, check space before the install, delete `spikes/wails/build/bin` when done |
| WebView2 baseline memory alone exceeds 80 MB (several msedgewebview2 processes, about 40-70 MB each is typical, ASSUMPTION) | Report both working set and private; the 80 MB is a goal, not a gate; propose a revised budget (e.g. 150 MB) in the record |
| Toast does not show from an unpackaged dev build | Test the built exe; set AppUserModelID; fall back to the Notification hook's existing native toast path as an interim |
| Telex fails in the Wails webview | Plan.md section 6 makes Telex a must-pass. Test the Windows keyboard and the owner's actual IME; there is no second stack to fall back to, so the answer would be an input-handling workaround in the UI |
| First Go/Wails build is slow or confusing and is mistaken for failure | Warn the user; an antivirus exclusion for `D:\toolchains` can help |
| Wails v2 vs v3 churn (v3 pre-release) | Use v2 stable for the spike; the `host-bridge.js` seam limits migration cost |
| Toast or single-instance libraries are weak in Wails v2 | Small Go libraries fill the gaps (section 4.1); record them in the decision record |

## 12. Decisions needed from the user

D10 itself is closed (Go + Wails, owner, 2026-11-18). The earlier questions on spike scope, ImGui, build
prerequisites for a second stack and the tie-break are closed with it. What remains:

1. **Install approval and drive:** approve installing Go + the Wails CLI, with toolchains on D: (`D:\toolchains`) because C: has 9.9 GB free. Recommendation: yes, D:.
2. **Frontend tooling:** plain ES modules without bundler for M0. Recommendation: yes; revisit in P4.
3. **Telex test:** can you do the typing test yourself (agents cannot)? Which IME do you use (Windows Vietnamese keyboard, Unikey, EVKey)? Recommendation: you test, 5 minutes.
4. **Memory target:** keep "< 80 MB incl. WebView2" as a hard gate or treat it as a goal if Wails lands around 100-150 MB? Recommendation: goal, not gate; hard gates are Telex, toast, cold start < 1 s.

## 13. Evidence
- `_context.md` lines 6-71 (hard rules, fixed decisions D1-D11, format); spec `specs/P2-stack-spike-scaffold.md` lines 1-28.
- `kaneki-harness/docs/desktop-app/plan.md`: section 5 lines 65-80 (interfaces), section 6 lines 82-99 (criteria table at 86-96, installed/not installed at 98-99), M0 row line 105.
- `kaneki-harness-app/design-system/`: files and sizes (components.css 34,345 B, tokens.css 6,320 B, icons.js 5,072 B, gallery-data.js 50,014 B, app-mock.html 19,637 B); `app-mock.html:7-8,149-150` plain `<link>`/`<script>` use; `build_tokens.py:1-5` (`--check`, `--contrast`).
- Local probes (read-only, 2026-11-18): Node v22.23.2, npm 10.9.8, Python 3.12.10, git present, winget present; go, cmake, cl, pnpm not found; WebView2 runtime 157.0.4341.46 (HKLM WOW6432Node EdgeUpdate); free space C: 9.9 GB, D: 14.4 GB.
- NOT verified (no web lookup done): current Wails version, Wails v2 notification/single-instance support, Go toast libraries, toolchain sizes. All marked ASSUMPTION; P2-T01 verifies them.
