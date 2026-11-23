"""`sdlc schedule`: run `tick` and `recover` on a timer (plan M3·4).

Windows (primary): two per-user Task Scheduler tasks, created with `schtasks /Create /XML` (no admin):
- `kaneki tick <workspace-id>`    every N minutes (default 15);
- `kaneki recover <workspace-id>` at logon of the installing user (2 minutes after, so the network is up).
The task XML keeps the scheduler itself from overlapping a pass (`MultipleInstancesPolicy IgnoreNew`) and runs a
missed tick when the machine wakes (`StartWhenAvailable`). A logon trigger is limited to the installing user, which
is what lets a standard user create it.

macOS / Linux: one crontab line per pass (`*/N * * * *` for tick, `@reboot` for recover), each ending with the same
exact marker comment, so `--uninstall` removes only these lines. `--install` prints them; `--install --write` also
adds them to the user's crontab.

Both run a generated wrapper in `<workspace>/.sdlc/bin` (`sdlc-schedule.cmd` / `sdlc-schedule`), so the task command
is just `<wrapper> tick`: the wrapper goes through the project launcher (`sdlc.cmd` / `sdlc`, which finds Python
3.9+) and calls `sdlc schedule --pass tick|recover`. That pass:
- takes a per-workspace lock (`.sdlc/run/schedule.lock`): a pass never overlaps another pass in this workspace;
- returns at once, without loading the runner, when no ticket opted in and no run lease exists (cheap and silent);
- otherwise runs `Runner.tick()` / `Runner.recover()` (which keep their own run leases);
- writes one line per pass to `.sdlc/run/schedule.log` (the full JSON of the last pass is in `schedule.last`).

Schedule changes are workspace-level, never a ticket's: install / uninstall are logged to `schedule.log`, not to a
ticket's decision chain.

Tests never create real tasks or crontab entries: `$SDLC_SCHTASKS` / `$SDLC_CRONTAB` (a JSON array or a command
string) replace the executables, and every function takes the executable as a parameter as well.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape

from .errors import HarnessError

PASSES = ("tick", "recover")
WRAPPER_CMD = "sdlc-schedule.cmd"
WRAPPER_SH = "sdlc-schedule"
WRAPPER_MARK = "kaneki scheduled pass"
LOG_MAX_BYTES = 256 * 1024
LOCK_STALE_SECONDS = 2 * 3600
LOGON_DELAY = "PT2M"
TICK_TIME_LIMIT = "PT1H"
SAFE_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
OPT_IN_HINT = "nothing runs unattended until a ticket opts in: sdlc autonomy --ticket <T> --set on"

# schtasks "Last Result" codes worth naming (decimal, as LIST prints them).
LAST_RESULT = {"0": "ok", "1": "failed (exit 1)", "2": "failed (exit 2)", "3": "error (see schedule.log)",
               "267008": "ready", "267009": "running", "267011": "has not run yet",
               "267014": "terminated by the user", "-2147020576": "operator refused the request",
               "-2147216609": "an instance was already running"}


# --- names and paths ----------------------------------------------------------------------------------------

def platform_name(platform: str | None = None) -> str:
    if platform:
        if platform not in ("windows", "posix"):
            raise HarnessError(f"unknown schedule platform {platform!r} (windows|posix)")
        return platform
    return "windows" if os.name == "nt" else "posix"


def workspace_id(pdir: Path) -> str:
    """`<folder>-<hash6>`: readable, and unique per workspace path (two folders with the same name differ).
    Only the folder name and a hash of the path; nothing else from the machine or the profile."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", pdir.name).strip("-.") or "workspace"
    digest = hashlib.sha256(os.path.normcase(str(pdir)).encode("utf-8")).hexdigest()[:6]
    return f"{name[:40]}-{digest}"


def task_name(pdir: Path, kind: str) -> str:
    if kind not in PASSES:
        raise HarnessError(f"unknown scheduled pass {kind!r}")
    return f"kaneki {kind} {workspace_id(pdir)}"


def run_dir(pdir: Path) -> Path:
    return pdir / ".sdlc" / "run"


def log_path(pdir: Path) -> Path:
    return run_dir(pdir) / "schedule.log"


def bin_dir(pdir: Path) -> Path:
    return pdir / ".sdlc" / "bin"


def tool(name: str, override: Any = None) -> list[str]:
    """The schtasks / crontab executable as an argv prefix: the parameter, else `$SDLC_SCHTASKS` / `$SDLC_CRONTAB`
    (a JSON array or a command string), else the binary on PATH."""
    from .dispatch import split_command
    if override:
        return list(override) if isinstance(override, (list, tuple)) else split_command(str(override))
    env = (os.environ.get(f"SDLC_{name.upper()}") or "").strip()
    if env:
        return split_command(env)
    found = shutil.which(name)
    if not found:
        raise HarnessError(f"schedule: `{name}` is not on PATH (set SDLC_{name.upper()} to its path)")
    return [found]


def _exec(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, errors="replace", timeout=60,
                          stdin=None if stdin is not None else subprocess.DEVNULL)


# --- the schedule log -----------------------------------------------------------------------------------------

def _stamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def log_line(pdir: Path, text: str) -> str:
    """Append one line to `.sdlc/run/schedule.log` (rotated to `.1` past 256 KB). Never raises."""
    line = f"{_stamp()} {text}".replace("\r", " ").replace("\n", " ")
    path = log_path(pdir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return line


def log_tail(pdir: Path, n: int = 10) -> list[str]:
    try:
        lines = log_path(pdir).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


# --- what a pass would do --------------------------------------------------------------------------------------

def _ticket_root(ppath: Path) -> Path:
    from .cli import ticket_root
    from .profile import load_profile
    return ticket_root(load_profile(ppath), ppath.parent)


def pending_work(ppath: Path) -> dict[str, list[str]]:
    """Tickets with autonomy on and tickets holding a run lease, read cheaply (the decision log is parsed, not
    verified: this only decides whether the runner needs loading at all; the runner verifies)."""
    root = _ticket_root(ppath)
    opted, leased = [], []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not (d / "state.json").is_file():
            continue
        if (d / "run" / "lease.json").exists():
            leased.append(d.name)
        enabled = False
        try:
            with (d / "decisions.jsonl").open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    if '"autonomy"' not in raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(ev, dict) and ev.get("event") == "autonomy":
                        enabled = bool(ev.get("enabled"))
        except OSError:
            pass
        if enabled:
            opted.append(d.name)
    return {"optedIn": opted, "leased": leased}


# --- the per-workspace pass lock ---------------------------------------------------------------------------------

def _lock_path(pdir: Path) -> Path:
    return run_dir(pdir) / "schedule.lock"


def acquire_lock(pdir: Path, kind: str) -> dict[str, Any] | None:
    """Take the workspace pass lock; None when a live pass holds it. A lock whose process is gone (or older than
    two hours) is taken over."""
    from .runner import pid_alive, read_json
    path = _lock_path(pdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    mine = {"pid": os.getpid(), "pass": kind, "startedAt": _stamp(), "epoch": time.time()}
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = read_json(path)
            fresh = isinstance(held, dict) and time.time() - float(held.get("epoch") or 0) < LOCK_STALE_SECONDS
            if fresh and pid_alive(held.get("pid")):
                return None
            try:
                path.unlink()
            except OSError:
                return None
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(mine))
        return mine
    return None


def release_lock(pdir: Path, mine: dict[str, Any]) -> None:
    from .runner import read_json
    held = read_json(_lock_path(pdir))
    if isinstance(held, dict) and held.get("pid") == mine.get("pid") and held.get("epoch") == mine.get("epoch"):
        try:
            _lock_path(pdir).unlink()
        except OSError:
            pass


# --- one scheduled pass (what the task runs) -----------------------------------------------------------------

def _tick_summary(res: dict[str, Any]) -> str:
    started = [s.get("ticket") for s in res.get("started") or []]
    parts = [f"started {len(started)}" + (f" ({', '.join(map(str, started))})" if started else ""),
             f"skipped {len(res.get('skipped') or [])}"]
    recovered = [r for r in res.get("recover") or [] if r.get("action") not in (None, "none")]
    if recovered:
        parts.append(f"recovered {len(recovered)}")
    if res.get("backoff"):
        parts.append(f"backoff until {res['backoff'].get('until')}")
    return ", ".join(parts)


def _recover_summary(res: dict[str, Any]) -> str:
    acted = [f"{r.get('ticket')}:{r.get('action')}" for r in res.get("recovered") or []
             if r.get("action") not in (None, "none")]
    return f"recovered {len(acted)}" + (f" ({', '.join(acted)})" if acted else "")


def run_pass(ppath: Path, kind: str, runner_factory: Callable[[Path], Any] | None = None) -> tuple[int, dict[str, Any]]:
    """One scheduled `tick` or `recover`. Returns `(rc, result)`: rc 0 (ran, idle or skipped), 3 (failed; the error
    is already in schedule.log, so the wrapper does not log it twice)."""
    if kind not in PASSES:
        raise HarnessError(f"unknown scheduled pass {kind!r} (tick|recover)")
    ppath = Path(ppath).resolve()
    pdir = ppath.parent
    t0 = time.time()
    lock = acquire_lock(pdir, kind)
    if lock is None:
        line = log_line(pdir, f"{kind} skipped: the previous pass is still running")
        return 0, {"pass": kind, "result": "skipped", "why": "previous pass running", "log": line}
    try:
        work = pending_work(ppath)
        if not work["optedIn"] and not work["leased"]:
            line = log_line(pdir, f"{kind} idle: no ticket opted in")
            return 0, {"pass": kind, "result": "idle", "hint": OPT_IN_HINT, "log": line}
        if runner_factory is None:
            from .runner import Runner
            runner_factory = Runner
        r = runner_factory(ppath)
        res = r.tick() if kind == "tick" else r.recover()
        summary = _tick_summary(res) if kind == "tick" else _recover_summary(res)
        line = log_line(pdir, f"{kind} ok: {summary} ({time.time() - t0:.1f}s)")
        return 0, {"pass": kind, "result": "ran", **work, kind: res, "log": line}
    except Exception as exc:  # noqa: BLE001 - one bad pass must leave a line in the log, then the next pass tries again
        msg = str(exc) if isinstance(exc, HarnessError) else f"{type(exc).__name__}: {exc}"
        line = log_line(pdir, f"{kind} error: {msg[:400]}")
        return 3, {"pass": kind, "result": "error", "error": msg, "log": line}
    finally:
        release_lock(pdir, lock)


# --- wrappers -------------------------------------------------------------------------------------------------

def _check_profile_name(ppath: Path) -> str:
    if not SAFE_PROFILE_RE.match(ppath.name):
        raise HarnessError(f"schedule: profile file name {ppath.name!r} must use only letters, digits, '.', '_', '-'")
    return ppath.name


def wrapper_cmd(profile_name: str) -> bytes:
    """`.sdlc\\bin\\sdlc-schedule.cmd tick|recover`: run one pass through `sdlc.cmd` (which finds Python 3.9+), keep
    its output in `.sdlc\\run\\schedule.last`, and log a line itself only when the pass could not log one."""
    lines = [
        "@echo off",
        f"rem {WRAPPER_MARK} (tick | recover) for this workspace. Written by sdlc schedule --install; do not edit.",
        "setlocal",
        'set "SDLC_PASS=%~1"',
        'if "%SDLC_PASS%"=="" set "SDLC_PASS=tick"',
        'if /i not "%SDLC_PASS%"=="tick" if /i not "%SDLC_PASS%"=="recover" (',
        "  echo usage: sdlc-schedule.cmd tick^|recover",
        "  exit /b 64",
        ")",
        'set "SDLC_WS=%~dp0..\\.."',
        'set "SDLC_RUN=%~dp0..\\run"',
        'if not exist "%SDLC_RUN%" mkdir "%SDLC_RUN%"',
        'cd /d "%SDLC_WS%"',
        f'call "%~dp0sdlc.cmd" schedule --pass %SDLC_PASS% --profile "%SDLC_WS%\\{profile_name}" '
        '>"%SDLC_RUN%\\schedule.last" 2>&1',
        'set "SDLC_RC=%ERRORLEVEL%"',
        'if "%SDLC_RC%"=="0" exit /b 0',
        'if "%SDLC_RC%"=="3" exit /b 3',
        '>>"%SDLC_RUN%\\schedule.log" echo %DATE% %TIME% %SDLC_PASS% wrapper: exit %SDLC_RC%, see .sdlc\\run\\schedule.last',
        "exit /b %SDLC_RC%",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def wrapper_sh(profile_name: str, path_env: str | None) -> bytes:
    """`.sdlc/bin/sdlc-schedule tick|recover` (POSIX): cron starts with a bare PATH, so the installing shell's PATH
    is written in (it finds python3, claude and codex the way your terminal does)."""
    path_line = f"PATH={shlex.quote(path_env)}\nexport PATH\n" if path_env else ""
    script = (
        "#!/bin/sh\n"
        f"# {WRAPPER_MARK} (tick | recover) for this workspace. Written by sdlc schedule --install; do not edit.\n"
        f"{path_line}"
        'pass=${1:-tick}\n'
        'case "$pass" in tick|recover) ;; *) echo "usage: sdlc-schedule tick|recover" >&2; exit 64 ;; esac\n'
        'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'ws=$(CDPATH= cd -- "$here/../.." && pwd)\n'
        'run="$ws/.sdlc/run"\n'
        'mkdir -p "$run"\n'
        'cd "$ws" || exit 1\n'
        f'sh "$here/sdlc" schedule --pass "$pass" --profile "$ws/{profile_name}" >"$run/schedule.last" 2>&1\n'
        "rc=$?\n"
        'case $rc in 0|3) exit $rc ;; esac\n'
        "printf '%s %s wrapper: exit %s, see .sdlc/run/schedule.last\\n' \"$(date '+%Y-%m-%dT%H:%M:%S%z')\" "
        '"$pass" "$rc" >>"$run/schedule.log"\n'
        "exit $rc\n"
    )
    return script.encode("utf-8")


def _require_launcher(pdir: Path, platform: str) -> Path:
    launcher = bin_dir(pdir) / ("sdlc.cmd" if platform == "windows" else "sdlc")
    if not launcher.is_file():
        raise HarnessError(f"schedule needs the project launcher {launcher}; run `sdlc install --host <agent> "
                           f"--target {pdir}` first")
    return launcher


def _write_ours(path: Path, data: bytes, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if executable:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


def _remove_ours(path: Path) -> bool:
    """Delete a file this module wrote (it carries the wrapper mark or is a task XML of ours)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    text = raw.decode("utf-16", errors="ignore") if raw[:2] == b"\xff\xfe" else raw.decode("utf-8", errors="ignore")
    if WRAPPER_MARK not in text:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


# --- Windows: Task Scheduler ----------------------------------------------------------------------------------

def current_user() -> str | None:
    user = os.environ.get("USERNAME")
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if user and domain else user


def task_xml(pdir: Path, kind: str, every: int, user: str | None, start: datetime | None = None) -> str:
    wrapper = bin_dir(pdir) / WRAPPER_CMD
    who = f"<UserId>{escape(user)}</UserId>" if user else ""
    if kind == "tick":
        start = (start or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
        trigger = (f"<TimeTrigger><Repetition><Interval>PT{every}M</Interval>"
                   f"<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
                   f"<StartBoundary>{start.isoformat()}</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
        what = f"sdlc tick every {every} minutes"
    else:
        trigger = f"<LogonTrigger><Enabled>true</Enabled>{who}<Delay>{LOGON_DELAY}</Delay></LogonTrigger>"
        what = "sdlc recover at logon"
    desc = f"{WRAPPER_MARK}: {what} for {pdir}. Remove with: sdlc schedule --uninstall"
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        f"  <RegistrationInfo><Description>{escape(desc)}</Description></RegistrationInfo>\n"
        f"  <Triggers>{trigger}</Triggers>\n"
        f'  <Principals><Principal id="Author">{who}<LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
        "  <Settings>"
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<StartWhenAvailable>true</StartWhenAvailable>"
        "<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
        "<IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>"
        "<AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden>"
        "<RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>"
        f"<ExecutionTimeLimit>{TICK_TIME_LIMIT}</ExecutionTimeLimit><Priority>7</Priority>"
        "</Settings>\n"
        '  <Actions Context="Author"><Exec>'
        f'<Command>"{escape(str(wrapper))}"</Command><Arguments>{kind}</Arguments>'
        f"<WorkingDirectory>{escape(str(pdir))}</WorkingDirectory></Exec></Actions>\n"
        "</Task>\n"
    )


def xml_path(pdir: Path, kind: str) -> Path:
    return run_dir(pdir) / f"schedule-{kind}.xml"


def _cmdline(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


def parse_list(text: str) -> dict[str, str]:
    """`schtasks /Query /FO LIST /V` output -> {key: value} (first occurrence wins). Keys such as `Repeat: Every`
    contain a colon, so a key ends at the colon that the column padding follows."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^(\S.*?):\s{2,}(.*)$", line) or re.match(r"^([^:]+):\s*(.*)$", line)
        if m:
            out.setdefault(m.group(1).strip(), m.group(2).strip())
    return out


def _task_view(kind: str, name: str, proc: subprocess.CompletedProcess) -> dict[str, Any]:
    if proc.returncode != 0:
        return {"kind": kind, "name": name, "installed": False}
    info = parse_list(proc.stdout or "")
    last = info.get("Last Result")
    view = {"kind": kind, "name": name, "installed": True, "status": info.get("Status"),
            "nextRun": info.get("Next Run Time"), "lastRun": info.get("Last Run Time"), "lastResult": last,
            "lastResultText": LAST_RESULT.get(last or "", None if last is None else f"exit {last}"),
            "taskToRun": info.get("Task To Run"), "repeatEvery": info.get("Repeat: Every")}
    if not info.get("Status") and not info.get("Next Run Time"):
        view["raw"] = info  # a non-English Windows: the field names are localised
    return view


def _windows_install(pdir: Path, ppath: Path, every: int, dry_run: bool, schtasks: Any) -> dict[str, Any]:
    profile_name = _check_profile_name(ppath)
    exe = ["schtasks"] if dry_run and not (schtasks or os.environ.get("SDLC_SCHTASKS")) else tool("schtasks", schtasks)
    user = current_user()
    wrapper = bin_dir(pdir) / WRAPPER_CMD
    plan = []
    for kind in PASSES:
        xml = task_xml(pdir, kind, every, user)
        argv = [*exe, "/Create", "/TN", task_name(pdir, kind), "/XML", str(xml_path(pdir, kind)), "/F"]
        plan.append((kind, xml, argv))
    res: dict[str, Any] = {
        "platform": "windows", "workspace": str(pdir), "every": every, "dryRun": dry_run,
        "wrapper": str(wrapper), "user": user,
        "tasks": [{"kind": k, "name": task_name(pdir, k), "xml": str(xml_path(pdir, k)),
                   "runs": f'"{wrapper}" {k}',
                   "trigger": f"every {every} minutes" if k == "tick" else f"at logon of {user or 'you'} (+2 min)"}
                  for k, _, _ in plan],
        "commands": [_cmdline(a) for _, _, a in plan],
    }
    if dry_run:
        res["taskXml"] = {k: x for k, x, _ in plan}
        res["wrapperContent"] = wrapper_cmd(profile_name).decode("utf-8")
        return res
    _require_launcher(pdir, "windows")
    _write_ours(wrapper, wrapper_cmd(profile_name))
    created, failed = [], []
    for kind, xml, argv in plan:
        path = xml_path(pdir, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))  # schtasks /XML wants UTF-16 with a BOM
        proc = _exec(argv)
        (created if proc.returncode == 0 else failed).append(
            {"name": task_name(pdir, kind), "rc": proc.returncode, "output": ((proc.stdout or "") + (proc.stderr or "")).strip()})
    log_line(pdir, f"schedule install (windows): created {[c['name'] for c in created]}"
                   + (f", failed {[f['name'] for f in failed]}" if failed else "") + f", tick every {every} min")
    if failed:
        raise HarnessError("schtasks could not create " + "; ".join(f"{f['name']} (rc {f['rc']}): {f['output']}"
                                                                   for f in failed)
                           + (f". Created: {', '.join(c['name'] for c in created)}" if created else ""))
    res["created"] = created
    return res


def _windows_status(pdir: Path, schtasks: Any) -> dict[str, Any]:
    exe = tool("schtasks", schtasks)
    tasks = []
    for kind in PASSES:
        name = task_name(pdir, kind)
        tasks.append(_task_view(kind, name, _exec([*exe, "/Query", "/TN", name, "/FO", "LIST", "/V"])))
    return {"platform": "windows", "tasks": tasks}


def _windows_uninstall(pdir: Path, schtasks: Any) -> dict[str, Any]:
    exe = tool("schtasks", schtasks)
    removed, absent, failed = [], [], []
    for kind in PASSES:
        name = task_name(pdir, kind)  # exact names only: never a wildcard, never another task
        if _exec([*exe, "/Query", "/TN", name]).returncode != 0:
            absent.append(name)
            continue
        proc = _exec([*exe, "/Delete", "/TN", name, "/F"])
        (removed if proc.returncode == 0 else failed).append(name)
    files = [str(p) for p in (bin_dir(pdir) / WRAPPER_CMD, xml_path(pdir, "tick"), xml_path(pdir, "recover"))
             if _remove_ours(p)]
    log_line(pdir, f"schedule uninstall (windows): removed {removed}" + (f", failed {failed}" if failed else ""))
    if failed:
        raise HarnessError(f"schtasks could not delete {failed}; removed {removed}")
    return {"platform": "windows", "removed": removed, "absent": absent, "filesRemoved": files}


# --- POSIX: crontab ------------------------------------------------------------------------------------------

def cron_marker(pdir: Path, kind: str) -> str:
    return f"# {task_name(pdir, kind)}"


def cron_schedule(every: int) -> str:
    if every < 60:
        return f"*/{every} * * * *"
    if every % 60 == 0 and every < 1440:
        return f"0 */{every // 60} * * *"
    if every == 1440:
        return "0 0 * * *"
    raise HarnessError("on cron, --every must be below 60 or a whole number of hours (60, 120, ... 1440)")


def cron_lines(pdir: Path, every: int) -> list[str]:
    wrapper = str(bin_dir(pdir) / WRAPPER_SH)
    if "%" in wrapper or "\n" in wrapper:
        raise HarnessError("schedule: the workspace path contains '%' or a newline, which crontab cannot carry")
    run = f"/bin/sh {shlex.quote(wrapper)}"
    return [f"{cron_schedule(every)} {run} tick {cron_marker(pdir, 'tick')}",
            f"@reboot {run} recover {cron_marker(pdir, 'recover')}"]


def _is_ours(line: str, pdir: Path) -> bool:
    s = line.rstrip()
    return any(s.endswith(" " + cron_marker(pdir, k)) and WRAPPER_SH in s for k in PASSES)


def _crontab_read(exe: list[str]) -> str:
    proc = _exec([*exe, "-l"])
    if proc.returncode != 0:
        if "no crontab" in ((proc.stderr or "") + (proc.stdout or "")).lower():
            return ""
        raise HarnessError(f"crontab -l failed (rc {proc.returncode}): {(proc.stderr or proc.stdout or '').strip()}")
    return proc.stdout or ""


def _crontab_write(exe: list[str], text: str) -> None:
    proc = _exec([*exe, "-"], stdin=text)
    if proc.returncode != 0:
        raise HarnessError(f"crontab - failed (rc {proc.returncode}): {(proc.stderr or proc.stdout or '').strip()}")


def _posix_install(pdir: Path, ppath: Path, every: int, dry_run: bool, write: bool, crontab: Any) -> dict[str, Any]:
    profile_name = _check_profile_name(ppath)
    lines = cron_lines(pdir, every)
    wrapper = bin_dir(pdir) / WRAPPER_SH
    res: dict[str, Any] = {"platform": "posix", "workspace": str(pdir), "every": every, "dryRun": dry_run,
                           "wrapper": str(wrapper), "crontabLines": lines, "written": False}
    content = wrapper_sh(profile_name, os.environ.get("PATH") if os.name != "nt" else None)
    if dry_run:
        res["wrapperContent"] = content.decode("utf-8")
        res["next"] = "sdlc schedule --install --write adds these lines to your crontab"
        return res
    _require_launcher(pdir, "posix")
    _write_ours(wrapper, content, executable=True)
    if not write:
        res["next"] = ("add these lines with `crontab -e`, or run `sdlc schedule --install --write` to add them "
                       "(only lines with these markers are ever changed)")
        log_line(pdir, f"schedule install (posix): wrapper written, crontab not changed (tick every {every} min)")
        return res
    exe = tool("crontab", crontab)
    current = _crontab_read(exe)
    kept = [ln for ln in current.splitlines() if not _is_ours(ln, pdir)]
    _crontab_write(exe, "\n".join([*kept, *lines]) + "\n")
    res["written"] = True
    log_line(pdir, f"schedule install (posix): crontab lines written, tick every {every} min")
    return res


def _posix_status(pdir: Path, crontab: Any) -> dict[str, Any]:
    exe = tool("crontab", crontab)
    current = _crontab_read(exe).splitlines()
    tasks = []
    for kind in PASSES:
        found = [ln for ln in current if ln.rstrip().endswith(" " + cron_marker(pdir, kind)) and WRAPPER_SH in ln]
        tasks.append({"kind": kind, "name": task_name(pdir, kind), "installed": bool(found),
                      "line": found[0] if found else None})
    return {"platform": "posix", "tasks": tasks}


def _posix_uninstall(pdir: Path, crontab: Any) -> dict[str, Any]:
    exe = tool("crontab", crontab)
    current = _crontab_read(exe).splitlines()
    removed = [ln for ln in current if _is_ours(ln, pdir)]
    if removed:
        kept = [ln for ln in current if not _is_ours(ln, pdir)]
        _crontab_write(exe, ("\n".join(kept) + "\n") if kept else "")
    files = [str(p) for p in (bin_dir(pdir) / WRAPPER_SH,) if _remove_ours(p)]
    log_line(pdir, f"schedule uninstall (posix): removed {len(removed)} crontab line(s)")
    return {"platform": "posix", "removed": removed, "filesRemoved": files}


# --- entry points ---------------------------------------------------------------------------------------------

def _workspace(ppath: Path) -> tuple[Path, Path]:
    from .profile import load_profile
    ppath = Path(ppath).resolve()
    load_profile(ppath)  # a scheduled pass on a broken profile would only log errors: refuse now
    return ppath, ppath.parent


def install(ppath: Path, *, every: int = 15, dry_run: bool = False, write: bool = False,
            platform: str | None = None, schtasks: Any = None, crontab: Any = None) -> dict[str, Any]:
    if not 1 <= int(every) <= 1440:
        raise HarnessError("--every must be between 1 and 1440 minutes")
    ppath, pdir = _workspace(ppath)
    plat = platform_name(platform)
    res = (_windows_install(pdir, ppath, int(every), dry_run, schtasks) if plat == "windows"
           else _posix_install(pdir, ppath, int(every), dry_run, write, crontab))
    work = pending_work(ppath)
    res["optedIn"] = work["optedIn"]
    if not work["optedIn"]:
        res["hint"] = OPT_IN_HINT
    return res


def status(ppath: Path, *, platform: str | None = None, schtasks: Any = None, crontab: Any = None,
           lines: int = 10) -> dict[str, Any]:
    ppath, pdir = _workspace(ppath)
    plat = platform_name(platform)
    res = _windows_status(pdir, schtasks) if plat == "windows" else _posix_status(pdir, crontab)
    work = pending_work(ppath)
    res.update({"workspace": str(pdir), "installed": all(t["installed"] for t in res["tasks"]),
                "optedIn": work["optedIn"], "leased": work["leased"], "log": log_tail(pdir, lines)})
    if not work["optedIn"]:
        res["hint"] = OPT_IN_HINT
    return res


def uninstall(ppath: Path, *, platform: str | None = None, schtasks: Any = None, crontab: Any = None) -> dict[str, Any]:
    ppath, pdir = _workspace(ppath)
    plat = platform_name(platform)
    res = _windows_uninstall(pdir, schtasks) if plat == "windows" else _posix_uninstall(pdir, crontab)
    res["workspace"] = str(pdir)
    return res


def main_pass(ppath: Path, kind: str) -> int:
    """`sdlc schedule --pass tick|recover`: print the pass result as JSON; rc 0 or 3 (see run_pass)."""
    rc, res = run_pass(ppath, kind)
    sys.stdout.write(json.dumps(res, ensure_ascii=False, default=str) + "\n")
    return rc
