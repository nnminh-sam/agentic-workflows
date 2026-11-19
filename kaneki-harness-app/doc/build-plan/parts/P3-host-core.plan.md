# P3 · Host core: plan

Date 2026-11-18. Milestones: M1 (read side), runner reused by M2/M3. Paths below are relative to
`D:/workspace/HAISE/agentic_workspace/agentic-workflows/` unless noted. Event and IPC names are descriptive;
**P1 owns the final names** and the JSON schemas (marked `P1:` where I depend on them).

## 1. Goal and Done when

**Goal.** A thin native Host that finds the workspace, harness and Python; runs allow-listed `sdlc.py` commands safely;
watches ticket files and pushes events; and keeps a cache so the UI never waits on Python for a re-render.
All state rules stay in the harness (D1).

**Done when**
1. Starting the app on `D:/workspace/HAISE/testing/todo-nestjs-backend` shows `TODO-1` and `TODO-1-2` from cache in
   under 100 ms after the first `sdlc tickets` call returns, and a missing Python / harness / profile shows a named
   error (code + fix hint), not a crash.
2. `run_command` rejects any subcommand or flag outside the allowlist without spawning a process, and a fixture test
   proves no argument is ever joined into a shell string.
3. Appending a line to a fixture `decisions.jsonl` emits exactly one `decision-added` within 500 ms; an atomic
   `state.json` replace emits one `ticket-changed`; a watcher overflow triggers a full rescan.
4. Running a write twice at once on the same ticket never starts two Python processes from the Host; a held harness
   lock comes back as `AppError{code:"LOCKED"}` with owner command and age, after a short bounded retry.
5. A `--note` containing Vietnamese text round-trips byte-exact into `decisions.jsonl` (on a fixture workspace).
6. A second app launch focuses the first window; no TCP port is listening (`netstat` check).

## 2. Scope

**In:** discovery and validation; command runner + allowlist; per-ticket write queue; file watcher + decision
tailing; ticket cache; IPC registration layer and error envelope; logging; single instance; config file; module layout.

**Out:** session spawning (P6, but it reuses the runner's process helper and `AppError`), cost-log parsing (P7), UI
(P4), gate dialog flow (P5; it only calls `run_command`), the data schemas themselves (P1), `claude` child processes.

## 3. Inputs and outputs

**Inputs from other parts**
- P1: JSON types `TicketSummary` (the `board.summarize` shape minus `events`, `harness/board.py:17-62`),
  `TicketBrief` (the `sdlc tickets` row, `harness/board.py:87-104`), `DecisionEvent`
  (`kaneki-harness/contracts/decision-event.schema.json`), `AppError`, the IPC/event name table, and the command
  allowlist schema (see 4.2). Until P1 lands I use the working names below.
- P2/P8 (packaging): the stack is Go + Wails (D10, closed); the Host source tree location under `kaneki-harness-app/`.

**Outputs (working names; P1 may rename)**

| Kind | Name | Payload / result |
|---|---|---|
| invoke | `workspace.get` / `workspace.set(path)` / `workspace.recent` | `WorkspaceState` (4.1) |
| invoke | `env.check` | `{python, harnessHome, profile, problems[]}` |
| invoke | `tickets.list` | `TicketBrief[]` from cache |
| invoke | `ticket.get(ticket)` | `TicketSummary` + `decisions: DecisionEvent[]` (full tail, from file) |
| invoke | `ticket.refresh(ticket)` | forces a re-summarise via Python; returns `TicketSummary` |
| invoke | `command.run({argv, ticket?, timeoutMs?})` | `{ok:true, data}` or `AppError`; also `command.preview(argv)` returns the exact display string (D2) |
| invoke | `command.cancel(runId)` | cancel a running command |
| invoke | `config.get` / `config.set` | app settings (4.6) |
| invoke | `fs.open_in_editor(path)` | thin wrapper for D9, validated to stay inside workspace |
| event | `ticket-changed` | `{ticket, reason: "state"\|"artifact"\|"lock"\|"created"\|"removed", summary?}` |
| event | `decision-added` | `{ticket, event: DecisionEvent, index}` |
| event | `ticket-lock-changed` | `{ticket, locked, owner?: {command,pid,sinceMs}}` |
| event | `tickets-rescanned` | `{tickets: TicketBrief[]}` after overflow / workspace switch |
| event | `command-output` (optional) | `{runId, stream, line}` for long commands |
| event | `host-error` | `AppError` for background faults (watcher died, Python vanished) |
| type | `AppError` | `{code, message, hint?, detail?, exitCode?, retryable}` |

## 4. Design

### 4.1 Workspace and harness discovery

Facts: `sdlc.py` finds the harness home from `$SDLC_HOME`, else `.harness-home` next to itself, else a parent that
has `graph/lifecycle.json` and `harness/` (`kaneki-harness/skills/kaneki-flow/scripts/sdlc.py:13-27`, failure is
`{"error": "G7: harness home not found…"}` with exit 2, lines 26-27). The profile comes from `--profile`, else
`$SDLC_PROFILE`, else `./sdlc.profile.json` relative to cwd (`harness/cli.py:35-37`). The ticket root is
`profile.paths.ticketRoot`, relative to the profile's folder (`harness/cli.py:40-42`; real value `.sdlc/tickets`,
`testing/todo-nestjs-backend/sdlc.profile.json:20`).

Resolution order, first hit wins, each recorded with its source so the settings screen can say "from X":

| Item | Order |
|---|---|
| Workspace (profile path) | 1. last-used `config.workspace` 2. `config.recent[]` picker 3. `$SDLC_PROFILE` (if file exists) 4. CLI arg `--workspace <dir>` 5. none, show the "open a workspace" empty state |
| Harness script (`sdlc.py`) | 1. `config.sdlcPath` 2. `<workspace>/.claude/skills/kaneki-flow/scripts/sdlc.py` (installed copy, has `.harness-home`) 3. `~/.claude/skills/kaneki-flow/scripts/sdlc.py` 4. `$SDLC_HOME/skills/kaneki-flow/scripts/sdlc.py` (ASSUMPTION: `sdlc install` copies the skill there; `harness/install.py:30,64` writes `.harness-home` into installed `scripts/`, verify the exact target) |
| Harness home | not resolved by the Host. It is passed through unchanged: the Host sets `SDLC_HOME` only if `config.harnessHome` is set; otherwise `sdlc.py` does its own lookup. The Host validates by running `sdlc.py validate` once (read-only) |
| Python | 1. `config.python` 2. `py -3` (launcher; run `py -3 -c "import sys;print(sys.executable)"`) 3. `python` on PATH 4. `python3`. Probe with `-c "import sys;print(sys.version_info[:3], sys.executable)"`; require >= 3.10 (harness uses `X | Y` annotations, `harness/state.py` and `cli.py` use `list[str] | None`, `cli.py:1184`). Resolve once to an absolute `sys.executable` and cache it, so later runs do not pay the `py` launcher cost |

Validation (`env.check`, run at startup and on settings change), each failure is a separate `AppError` code with a
hint:

| Code | Trigger | Hint shown |
|---|---|---|
| `PYTHON_MISSING` / `PYTHON_TOO_OLD` | probe fails / < 3.10 | "Install Python 3.10+ or set the path in Settings" |
| `HARNESS_NOT_FOUND` | no `sdlc.py` found, or run returns `G7: harness home not found` | "Run `sdlc install` for this project or set the harness path" |
| `PROFILE_MISSING` | profile file absent | "Pick a folder that has sdlc.profile.json" |
| `PROFILE_INVALID` | `sdlc.py validate` returns `{"error":…}` | show the harness text verbatim |
| `NO_TICKET_ROOT` | ticket root dir missing | not an error, empty list + "create a ticket" hint |

The Host reads `sdlc.profile.json` itself only for `paths.ticketRoot` (one JSON key). Ticket root default
`.sdlc/tickets` if the key is absent (ASSUMPTION, `load_profile` may add defaults: `harness/profile.py` not read).

### 4.2 Command runner

**Shape.** `command.run({argv: string[], ticket?: string, timeoutMs?: number})`. `argv` excludes the program; the
Host prepends `[python, sdlcPath]` and appends `--profile <abs profile path>` always (never relies on cwd, but also
sets cwd = workspace root so any relative path the harness prints resolves).

**Allowlist** is data, in a file generated from P1 (`allowlist.json` embedded in the binary), mirroring the parser
at `harness/cli.py:1010-1161`. Each entry: subcommand, class (`read` or `write`), whether `--ticket` is required,
and per-flag rules (name, takes value, regex or enum, required).

| Class | Subcommands in v1 | Notes |
|---|---|---|
| read | `tickets`, `status`, `plan`, `validate`, `digest`, `status-of`, `receipts` (no `--reissue`), `lang` (no `--set/--clear`) | `READ_ONLY` set is `harness/cli.py:1167-1168` |
| write, M2/M3 | `gate`, `invalidate`, `lane --set`, `record`, `init`, `worktree`, `lang --set/--clear` | exact flags/enums at `cli.py:1028-1031,1051-1074,1076-1079,1018-1026` |
| write, rarely | `handback`, `receipts --reissue` | behind an "advanced" flag; not in M2 |
| **blocked** | `ship`, `ship-request`, `review-fetch`, `git`, `install`, `zed-setup`, `onboard-write`, `web`, `status --accept-current-state` | D6 (never ship/push); `install`/`onboard-write` are in `READ_ONLY` yet write files (`cli.py:1167`); `status --accept-current-state` writes a state-write event (`cli.py:302-308`) and must be a distinct, explicit, user-typed-reason action later, not v1; `web` opens a port (D8) |

Validation rules, all enforced before spawning:
- argv[0] must be an allowlisted subcommand; every token must be a known flag for it or the value of the previous
  value-taking flag; unknown or repeated-where-not-allowed flags reject (`BAD_ARGS`).
- `--ticket` value matches `^[A-Za-z0-9][A-Za-z0-9._-]*$` and the folder `<ticketRoot>/<id>` exists (path traversal:
  reject `/`, `\`, `..`). `--node` must be in the 20-node list from `graph/lifecycle.json` (P1 supplies the list).
- Enums (`--status`, `--verdict`, `--kind`, `--lane`) checked against the argparse `choices`.
- Free text (`--note`, `--reason`, `--result`): any UTF-8, max 8 KB, no NUL. It is passed as a single argv element.
- **No shell.** Spawn with an argv array (Go
  `exec.Command(python, args...)`). Never `cmd /c`, never `sh -c`, never a concatenated string.
- `--actor user` on `gate` is set by the Host when the call comes from a UI gate action (the human approving), and
  defaulted to `controller` otherwise is not allowed from the UI: the UI sends only user actions. ASSUMPTION: P5 owns
  this; the Host just validates the enum.

**Process settings (Windows).**
- No console flash: Go `SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}` (`CREATE_NO_WINDOW`).
- Encoding: set env `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` (the harness already reconfigures stdout to UTF-8,
  `cli.py:1186-1191`, but argv decoding on Windows needs `PYTHONUTF8` for the `-X utf8` path; `CreateProcessW`
  passes UTF-16 so Vietnamese argv is safe, the env only protects stdin and stdout). Decode stdout/stderr as UTF-8
  (lossy fallback + log a warning).
- Env: inherit, then override `SDLC_PROFILE` is not set (we pass `--profile`), `SDLC_HOME` only if configured.
  Strip `PYTHONPATH` and `PYTHONHOME` (so a user shell cannot shadow `harness`).
- Timeout: default 30 s for read, 120 s for write (a `gate` runs checks? ASSUMPTION; measure in the P2 spike), hard cap 600 s.
  On timeout or cancel: Windows has no SIGTERM for a console-less child, so kill the process tree
  (a Job Object is preferred: assign the child to a Job with
  `KILL_ON_JOB_CLOSE` via `golang.org/x/sys/windows`; fallback `taskkill /T /F`). Killing mid-write can leave
  `.lock/` (stale for 600 s, `harness/state.py:25`) and a `state.tmp`: after a kill the Host checks `.lock/owner.json`
  pid equals the killed pid and, only then, reports `LOCK_ORPHANED` with a hint (it does NOT delete the lock; the
  harness breaks stale locks itself, `state.py:154-170`). Recommend cancel is disabled for write commands after they
  have started (they are short), only reads and P6 sessions are cancelable.
- Output: stdout capped at 8 MB; stderr at 1 MB (truncate with a marker).

**Result parsing and exit-code mapping.** The harness prints one JSON line on stdout (`out()`, `cli.py:29-30`); on
`HarnessError` it prints `{"error": "..."}` and returns 2 (`cli.py:1193-1198`). Argparse errors also exit 2 but write
usage to stderr with empty stdout. An uncaught Python exception exits 1 with a traceback on stderr. `cmd_git` returns
git's own exit code and non-JSON output (blocked anyway).

| Observation | Result |
|---|---|
| exit 0, stdout parses as JSON | `ok` with `data` |
| exit 0, stdout not JSON | `BAD_OUTPUT` (include first 500 chars) |
| exit 2 + stdout `{"error": s}` | map by `s` prefix: `G7: ticket is locked` -> `LOCKED` (retryable), `G7: ticket integrity check failed` -> `INTEGRITY`, `G7: harness home not found` -> `HARNESS_NOT_FOUND`, `ticket not initialised` -> `NO_SUCH_TICKET`, other `G<n>:` -> `GATE_REFUSED{gate:G<n>}` (the harness refusing is a normal outcome shown to the user), else `HARNESS_ERROR` |
| exit 2, empty stdout, stderr has `usage:` | `BAD_ARGS` (a Host/allowlist bug, log loudly) |
| exit 1 / other | `HARNESS_CRASH` with stderr tail (4 KB) |
| timeout / cancel | `TIMEOUT` / `CANCELED` |
| spawn failure (ENOENT) | `PYTHON_MISSING` |

(Gate numbering `G7` is from the strings read at `state.py:122-173`; the full code list should be taken from P1.)

**Write serialisation and the harness lock.**
- The harness lock is `<ticketDir>/.lock/` made with `mkdir` (atomic), holding `owner.json {pid, command, since}`;
  held for the whole read-modify-write; stale after 600 s and then broken with a `lock-broken` event
  (`harness/state.py:148-178`). It is taken only for non-`READ_ONLY` commands with `--ticket`
  (`cli.py:1172-1181`). A refused lock raises `G7: ticket is locked by another session (...)` *before* the command
  body runs (`state.py:165-167`, `cli.py:1193-1195`), so retrying is always safe: nothing was written.
- Host layer: a per-ticket async mutex (`HashMap<ticket, Mutex>` / `map[string]*sync.Mutex`) so the app's own writes
  to one ticket run one at a time and in order; different tickets run in parallel. Reads bypass the mutex.
- If the harness still returns `LOCKED` (a `claude` session or CLI holds it): retry with backoff 250 ms, 500 ms,
  1 s, 2 s (total about 4 s), then surface `AppError{code:"LOCKED", retryable:true, detail:{command,pid,ageSec}}`
  parsed from the message. The UI shows "Ticket is busy: `record` running for 12 s" and a Retry button. Never delete
  `.lock` from the Host.
- `ticket-lock-changed` is derived from the watcher seeing `.lock` appear and disappear (4.3), so the UI can show a
  "session is writing" badge without asking Python.
- Unlocked writers exist (`status --accept-current-state`, `install`, `onboard-write` are in `READ_ONLY`,
  `cli.py:1167`); they are blocked in v1, so this gap does not reach users. Flag it to the harness owner.
- After any successful write, the harness has already rewritten `STATUS.md` (`cli.py:1202-1211`, `board.py:163-166`)
  and state.json/decisions; the Host does not refresh the cache from the command result but waits for the watcher
  event (single path to the cache, avoids double updates). A short `ticket.refresh` fallback runs if no event
  arrives within 1.5 s (watcher missed it).

### 4.3 File watcher

**What is written (verified).**
- `state.json`: written to `state.tmp` then `tmp.replace(state.json)` with up to 20 retries on `PermissionError`,
  then a `state-write` line is appended (`harness/state.py:133-147`). So readers see create `state.tmp`, then a
  rename over `state.json`; on Windows a reader can hit a sharing violation while the file is being replaced.
- `decisions.jsonl`: append-only, `open("a")`, one JSON line per event with `prev` = sha256 of the previous line
  (`state.py:93-104`). Never rewritten (hash chain checked at `state.py:78-91`). Lines are UTF-8, `ensure_ascii=False`.
- `STATUS.md` rewritten after every write command (`board.py:163-166`): ignore it (derived).
- Artifacts (`plan.md`, `code-review.md`, ...) are written with `write_text` (non-atomic, `usage.py:97`), so a read
  can see a half-written file: the Host never parses artifacts, it only reports "changed".
- `.lock/` dir created and removed around every write command (`state.py:156,173-178`).

**Mechanism.** One recursive watcher on `<ticketRoot>`. Go: `fsnotify` (ReadDirectoryChangesW on Windows; not recursive: add each `<ticketRoot>/<ticket>` dir, and add the root to catch new tickets;
fsnotify's Windows buffer is 64 KB, so overflow is real). A bounded channel feeds one watcher goroutine.

Algorithm:
1. Normalise raw events to `(ticket, kind)` where kind is derived from the *file name*: `state.json`/`state.tmp` ->
   `state`; `decisions.jsonl` -> `log`; `.lock` -> `lock`; other `*.md`/`results/`/`packets/` -> `artifact`; `STATUS.md`
   dropped. Anything outside `<ticketRoot>/<id>/` dropped.
2. Debounce per ticket, trailing 150 ms with a max wait of 500 ms (a gate command touches state, log, STATUS.md,
   artifacts within about 50 ms; coalesce into one pass).
3. On each flush for a ticket, in this order:
   a. `log`: tail `decisions.jsonl` (below), emit `decision-added` per new line.
   b. `state` (or any decision added, since `state-write` always follows a state change): re-summarise (4.4), emit
      one `ticket-changed{reason:"state", summary}`.
   c. `lock`: `stat` `.lock`; if present read `owner.json` (retry once after 50 ms, it is written just after the mkdir,
      `state.py:173`); emit `ticket-lock-changed`.
   d. only `artifact` kinds: emit `ticket-changed{reason:"artifact"}` without calling Python.
4. New ticket folder: when `state.json` first appears (`init` writes it), emit `ticket-changed{reason:"created"}` and
   start an offset at 0 for its log. Removed folder: drop cache entry, emit `reason:"removed"`.
5. **Overflow / error / unknown**: on a watcher error, a dropped-events notice, or a rename event outside the known
   names, do a full rescan: re-list the ticket root, reset every offset (re-validate, 3 below), run `sdlc tickets`,
   replace the cache, emit `tickets-rescanned`. Also rescan on workspace switch, on resume from sleep (a timer gap
   > 60 s between ticks, ASSUMPTION it indicates suspend), and every 10 min as a safety net (cheap; one Python call).
   This is a belt-and-braces check, not polling for D4: normal updates stay event-driven.

**Tailing `decisions.jsonl` (offsets).** Per ticket keep `{offset: int64, lineIndex: int, firstLineHash}`.
- Open with share-read + share-write + share-delete flags (Go `os.Open` already
  uses FILE_SHARE_READ|WRITE|DELETE on Windows) so the Host never blocks the harness's append or the `rmtree`.
- If `len < offset` (truncated/replaced) or the first line's sha256 differs from `firstLineHash`: reset offset 0 and
  re-emit as a `ticket.get`-style reload (`ticket-changed{reason:"state"}`), not as new decisions. This also flags
  tampering to the user (the harness will report an integrity error on load, `state.py:78-91`).
- Else seek to `offset`, read to EOF, split on `\n`; **a final segment without `\n` is held back** (writer is mid-append;
  `write(json+"\n")` is not guaranteed atomic) and re-read on the next event. Parse each complete line as JSON;
  a parse failure emits `host-error{code:"LOG_CORRUPT"}` once and stops tailing that ticket until a rescan.
- Validate against `decision-event.schema.json` (P1 supplies a compiled validator); on schema mismatch still deliver
  the event with `unknown:true` so the UI never hides something (the 15 event kinds are enumerated at
  `contracts/decision-event.schema.json`, lines 9-33; a newer harness may add more).
- The Host does **not** verify the hash chain (harness owns it). It does compare `prev` with a running sha256 of the
  last line it read and sets `chainOk:false` on mismatch (cheap, helps the UI show a warning without Python).
- Memory: cache holds only the last 200 events per ticket; `ticket.get` reads the whole file on demand.

### 4.4 Caching

- Cache: `Map<ticket, {brief: TicketBrief, summary?: TicketSummary, mtimeState, offset…}>` behind a read-write lock.
- **Cold start / rescan:** one `sdlc tickets` call (about 0.5 s as stated in `_context.md`; it summarises each ticket
  and verifies each log, `board.py:87-104`, so cost grows with ticket count; fine for v1). Return `tickets.list` from
  cache immediately after.
- **Single-ticket refresh** (watcher flush on `state`): the cheapest way that reuses the board shape is
  `sdlc status-of --path <ticketDir>/state.json`, which returns `summarize_ticket(...)` minus `events`
  (`cli.py:961-985`). It costs one Python start (about 0.3-0.5 s, ASSUMPTION; measure), so: the Host first emits
  `ticket-changed{reason:"state"}` with `stale:true` plus the newly tailed decisions (UI can already update the
  activity feed and show a "refreshing" shimmer), then the `summary` arrives in a second `ticket-changed`
  (`stale:false`). Alternative considered: re-deriving the summary in Go: rejected (duplicates graph logic, violates
  "keep logic in the harness", and can drift from the board).
- Coalescing: at most one in-flight refresh per ticket; events arriving meanwhile set a dirty flag and trigger one more.
- Never call Python from the watcher thread; use a small worker pool (size 2) shared with the runner's read path.
- `ticket.get` = cached summary + decisions read from file (offset 0) so the history is exact. `integrity` problems
  from the summary (`board.py:60`) pass through untouched.
- Cache is memory only; no on-disk cache in v1 (the file system is the truth).

### 4.5 IPC layer (Wails)

Host code registers handlers against a small `Ipc` Go interface so only one adapter file (`app.go`) touches Wails.

| Concern | Wails (Go) |
|---|---|
| Invoke handlers | Methods on a bound struct passed to `wails.Run(options.App{Bind: []any{app}})`; JS gets generated `window.go.main.App.X` bindings |
| Events to UI | `runtime.EventsEmit(ctx, "ticket-changed", payload)`; UI `EventsOn` |
| Error to UI | method returns `(T, error)`; Wails stringifies errors, so return `{ok:false,error:AppError}` as a normal value instead (decision: always use the envelope) |
| Single instance | `options.SingleInstanceLock{UniqueId, OnSecondInstanceLaunch}` (ASSUMPTION; P2-T01 verifies) |
| Logging | `logger.Logger` or `zerolog` + `lumberjack` |
| Path helpers | `os.UserConfigDir()` |
| Toasts | a Go Windows toast lib (`beeep`/`go-toast`) or Wails v3 notifications (ASSUMPTION; P2-T01 verifies; P5/P6 own) |

- **Envelope (same on the wire for every call):** every invoke returns `{ok:true, data}` or `{ok:false, error:AppError}`;
  the UI wrapper (`P4`) turns it into a throw. This avoids Wails' error stringification.
- **Validation:** inputs deserialised into typed Go structs generated from P1 schemas (structs + `santhosh-tekuri/jsonschema` or generated types, checked by a `go test` against the JSON schemas).
  Reject unknown fields (`DisallowUnknownFields`). Outbound events are validated in debug builds only.
- **Contract test:** a single `ipc-contract.json` (from P1) lists names + payload schema ids; a test asserts every
  registered handler/event appears in it and vice versa.
- No `window` globals beyond the framework's bridge; no `eval`; CSP set by P2/P8 (`default-src 'self'`).

### 4.6 Cross-cutting

- **Config file:** `%APPDATA%\KanekiHarness\config.json` (keep the folder name fixed). Fields: `workspace`, `recent[]` (max 8), `python`, `sdlcPath`, `harnessHome`,
  `editorCommand` (default `zed`; D9), `commandTimeoutsMs`, `logLevel`, `schemaVersion: 1`. Written atomically
  (write `config.json.tmp`, rename); a corrupt file is renamed to `config.json.bad-<ts>` and defaults used (toast).
- **Logging:** `%APPDATA%\KanekiHarness\logs\host.log`, rolling daily, 7 files or 10 MB total; JSON lines with
  `ts, level, target, ticket?, runId?, msg`. Log every command: argv with free-text values replaced by
  `<text:N bytes>` (notes may contain private context), exit code, duration, error code. Never log env.
- **Single instance:** second launch sends its args (`--workspace`) to the first, which focuses and switches workspace
  if requested.
- **Security (D8):** no listening socket (the Wails IPC is in-process; the `wails dev` server is dev-only and the release build must not use it). Allowlist and argv validation as in 4.2. `fs.open_in_editor` and any path argument must
  canonicalise inside the workspace or ticket root (reject symlink escape via `canonicalize`/`EvalSymlinks`). Spawn
  editor via argv array too. The `.sdlc/tickets` text is untrusted (a ticket body can contain anything): the Host
  never interprets it; the UI must escape it (P4).
- **Read-only startup:** startup must call only read-class commands (`validate`, `tickets`); a test asserts this.

### 4.7 Module layout

| Module | Responsibility | Go package |
|---|---|---|
| `config` | load/save config, recents | `internal/config` |
| `discover` | python/sdlc/profile probes, `env.check` | `internal/discover` |
| `allowlist` | data + validator | `internal/allowlist` |
| `runner` | spawn, timeout, kill tree, parse, exit map, retry | `internal/runner` |
| `queue` | per-ticket write mutexes | `internal/runner` (same pkg) |
| `watcher` | fsnotify, debounce, rescan | `internal/watch` |
| `tail` | decisions offset tailing | `internal/watch` |
| `cache` | ticket map, refresh coalescing | `internal/cache` |
| `errors` | `AppError`, codes, mapping | `internal/apperr` |
| `ipc` | handlers + events + envelope (Wails adapter) | `app.go`, `internal/ipc` |
| `logging` | rolling log | `internal/logx` |
| `platform` | `CREATE_NO_WINDOW`, Job Object, share modes | `internal/platform` |

Go deps: stdlib + `fsnotify`, `x/sys/windows`, `lumberjack`.

## 5. Tasks

| ID | Task | Size | Depends on | Done when |
|---|---|---|---|---|
| P3-T01 | Host skeleton: module layout, `Ipc` adapter, `AppError`, envelope, logging, config load/save | M | P2-T09 (scaffold) | app starts, `config.get` works, `host.log` rotates in a test |
| P3-T02 | `discover`: Python probe, sdlc locate, profile, `env.check` and error codes | M | T01 | table in 4.1 covered by unit tests with fake PATH / missing files |
| P3-T03 | `allowlist` data + validator from P1 schema (flags, enums, ticket regex, node list, text limits) | M | P1 allowlist schema | negative-test table: each blocked subcommand and 15+ malformed argv rejected pre-spawn |
| P3-T04 | `runner`: spawn with argv array, `CREATE_NO_WINDOW`, UTF-8 env, timeout, Job Object kill, caps, JSON parse | L | T02, T03 | runs `tickets` on a fixture; kill test leaves no orphan `python.exe` |
| P3-T05 | Exit-code and error-text mapping (4.2 table) + fake harness script for tests | S | T04 | every row of the table has a test using a stub `sdlc.py` |
| P3-T06 | Per-ticket queue + `LOCKED` retry/backoff + lock-owner parse | M | T04 | two concurrent writes serialise; held-lock fixture returns `LOCKED` with owner after about 4 s |
| P3-T07 | `cache` + `ticket.refresh` via `status-of --path`, coalescing, worker pool | M | T04 | burst of 10 file events gives 1-2 Python calls |
| P3-T08 | `watcher`: recursive watch, name classification, debounce, overflow rescan, created/removed tickets | L | T07 | fixture tests in 6 pass; atomic replace emits one event |
| P3-T09 | `tail`: offsets, partial-line hold-back, rotation reset, schema check, chain flag | M | T08 | append/partial/truncate/corrupt fixture cases pass |
| P3-T10 | IPC handlers and events wired to Wails (names from P1), contract test | M | T04, T08, P1 | contract test green; UI (P4) can list tickets and receive events |
| P3-T11 | Single instance, `fs.open_in_editor`, path canonicalisation | S | T01 | second launch focuses first; path-escape tests |
| P3-T12 | Hardening pass: startup read-only test, no-port check, log redaction test, resume-from-sleep rescan | S | T10 | `netstat` shows no listener; log has no note text |
| P3-T13 | Manual run on the real workspace (read-only) and a scratch copy for write tests (4.6 of tests) | M | T10 | checklist in section 6 signed off |

## 6. Tests and verification

**Unit (no Python needed):** allowlist accept/reject matrix; exit-code mapping with a stub `sdlc.py` that prints
canned JSON and exit codes (including non-UTF-8 bytes and empty stdout); ticket id and path-traversal rejection;
tail logic over byte buffers (split mid-line, multi-byte UTF-8 split across reads, truncate, schema-invalid line);
debounce timing with a fake clock.

**Fixture-based:** copy `D:/workspace/HAISE/testing/todo-nestjs-backend` ticket folders (read the copy) into a temp
workspace. Drive a *copy* with a script that mimics the harness write order (create `state.tmp`, rename, append a log
line, create/remove `.lock`) and assert emitted events. Add a flood test (5,000 small file events) to force overflow.
Concurrency: spawn the stub holding `.lock/` and verify `LOCKED` and retry timing.

**Manual against the real workspace (read-only):** from the app: `tickets.list` matches
`python …/sdlc.py tickets` output run by hand; `ticket.get TODO-1-2` shows the same `nextPending` as
`sdlc.py status --ticket TODO-1-2`; open the workspace with no Python on PATH (rename setting) and read the error.
Do not run write commands there; write-path tests (`gate`, `record`, Vietnamese `--note`) run only against a scratch
workspace created with `sdlc init` by the user.

**Exit checks:** no console window flash (screen record one `tickets` call); idle CPU about 0 with the watcher on;
memory of Host process reported in the P2 spike.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Watcher misses events (Windows buffer overflow, AV/indexer) | rescan on error + 10 min safety rescan + 1.5 s post-write fallback refresh |
| 0.5 s Python start makes every refresh feel slow | optimistic `stale:true` event first; coalescing; later option: a long-lived harness helper (a P1/harness change, out of scope) |
| `status-of` per-ticket refresh shape differs from `tickets` brief | ASSUMPTION flagged; P1 defines both as projections of one `TicketSummary`; contract test |
| Partial `state.json` read (replace race) | the Host does not parse `state.json`; Python does and retries are in the harness (`state.py:140-146`) |
| Killing a write mid-run leaves a stale lock (10 min) | disable cancel on writes; report `LOCK_ORPHANED`; harness breaks stale locks and logs it |
| Free text in `--note` reaches Python with wrong encoding | `CreateProcessW` UTF-16 path + `PYTHONUTF8=1`; round-trip test T13 |
| Allowlist drifts from the CLI parser | generate from P1 schema; CI test runs `sdlc.py <cmd> --help` for each allowlisted entry and fails on unknown flags |
| Unlocked writers in `READ_ONLY` (`install`, `onboard-write`, `status --accept-current-state`) | blocked in v1; report to harness owner |
| Wails `fsnotify` not recursive, buffer overflow or Windows quirks | watcher module hides the library; fixtures cover overflow and rescan |

## 8. Decisions needed from the user

1. **Python interpreter policy.** Recommend: auto-detect (`py -3`, then `python`), cache the absolute path, expose an
   override in Settings. Alternative: require an explicit setting on first run.
2. **Per-ticket refresh via `status-of --path` (0.3-0.5 s) with an optimistic stale event**, vs asking the harness
   owner for a faster `sdlc summary --ticket T` or a long-lived helper process. Recommend: ship v1 with `status-of`, add
   the helper only if the P2 spike shows lag.
3. **Retry on `LOCKED`.** Recommend: bounded auto-retry (about 4 s) then a Retry button; never remove the lock.
   Alternative: fail immediately.
4. **Cancel on write commands.** Recommend: not cancelable after start (short, and a kill risks a stale lock).
5. **Allowlist breadth for v1.** Recommend: read set + `gate`, `invalidate`, `lane --set`, `record` for M2/M3; `init`,
   `worktree`, `handback`, `receipts --reissue` later; ship/git/install/onboard/accept-current-state/web blocked.
6. **Config folder name** (`%APPDATA%\KanekiHarness`) and whether the Host may write anything inside the workspace
   (recommend: never; all app state lives in `%APPDATA%`).

## 9. Evidence

- `kaneki-harness/skills/kaneki-flow/scripts/sdlc.py:1-34` (home resolution, error JSON, exit 2).
- `kaneki-harness/harness/cli.py:29-30` (`out`), `35-37` (profile path), `40-42` (ticket root), `302-308`
  (`--accept-current-state`), `956-985` (`tickets`, `status-of`), `1010-1161` (parsers: flags and enums),
  `1167-1168` (`READ_ONLY`, `NO_STATUS_REFRESH`), `1172-1181` (`_ticket_lock`), `1184-1200` (`main`, UTF-8, error
  to JSON, rc 2), `1202-1211` (`_refresh_status`).
- `kaneki-harness/harness/state.py:25` (`LOCK_STALE_SECONDS = 600`), `64-69` (paths), `78-91` (hash chain),
  `93-104` (append-only log), `106-131` (`load`, integrity), `133-147` (`save`: `.tmp` + `replace`, 20 retries),
  `148-178` (`.lock/` mkdir, `owner.json`, stale break, `lock-broken`).
- `kaneki-harness/harness/board.py:17-62` (`summarize` shape, `integrity`), `78-104` (`summarize_ticket`,
  `list_tickets`), `163-166` (`write_status`), `harness/usage.py:97` (non-atomic artifact write).
- `kaneki-harness/contracts/decision-event.schema.json:1-40` (event kinds, `prev` pattern).
- `kaneki-harness/docs/desktop-app/plan.md:31-46` (D1-D11), `65-80` (interfaces), `82-99` (D10, closed: Go/Wails).
- `testing/todo-nestjs-backend/sdlc.profile.json:20` (`ticketRoot`), ticket folder listing of `TODO-1-2`
  (state.json, decisions.jsonl, STATUS.md, packets/, results/, ...).
- Not read: `harness/profile.py`, `harness/install.py` beyond grep hits, `harness/web.py` body, `graph/lifecycle.json`.
- No sdlc command was run; no file other than this plan was created.
