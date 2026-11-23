"""Unattended runner: `sdlc run` / `tick` / `recover` / `autonomy` / `unpark` (plan M3·3, P5).

One node per fresh agent session, in the ticket's workspace (the folder holding the profile):
- Claude Code: `claude --bg -n kaneki:<ticket>:<node>:<runId> <prompt>`, a background session the user can
  `claude attach <id>` to when it asks a question;
- Codex: `codex exec <prompt>` as a foreground child of the runner (the same argv `sdlc dispatch` builds);
- Antigravity: never (dispatch.INTERACTIVE_ONLY_HOSTS, plan decision D6).

Rules this module keeps:
- the CLI is the only writer: every decision event goes through `Ticket.log` under the ticket lock;
- gates are never crossed by automation: a node that closes on the user (boundary user, a fired gate, waitingOn user)
  is never started; the runner parks the ticket and notifies instead, and never runs `sdlc gate --actor user`;
- node completion is the node's `gate` event in decisions.jsonl, never what the session says;
- prompts are fixed templates that carry only a validated ticket id and node name, never ticket text;
- the runner never ships, commits, pushes or deletes branches.

Files (all under the ticket folder, which never ships):
- `run/lease.json`  the run lease (runId, host, node, sessionId / childPid, runnerPid, startedAt, heartbeatAt), separate
  from the 10-minute command lock: the agent's own `sdlc` calls keep taking that one;
- `run/<runId>.jsonl` the run log; `run/attempts.json` failures per node and generation (bounded retries);
- `run/stop-<runId>` a stop request from `sdlc run --stop`.
Per workspace: `.sdlc/runner.local.json` (config), `.sdlc/run/backoff-<host>.json` (usage-limit back-off),
`.sdlc/inbox.jsonl` (notifications).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import HarnessError

TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RUNNABLE_HOSTS = ("claude", "codex")
RUN_RESULTS = ("done", "parked", "failed", "stopped")

DEFAULTS: dict[str, Any] = {
    "host": None,               # runner host when --host is not given (default: the active binding)
    "maxConcurrent": 1,         # live runs across tickets that tick allows (memory is tight)
    "pollSeconds": 15,          # how often a run checks the decision log and the agent session
    "nodeTimeoutMinutes": 180,  # a node still running after this is stopped (failed: timeout)
    "retries": 1,               # restarts of a node after a failure / orphan before the ticket parks
    "backoffMinutes": 60,       # usage-limit back-off when the error names no retry time
    "leaseStaleSeconds": 900,   # a lease whose heartbeat is older than this has a dead runner
    "claudeArgs": [],           # extra `claude --bg` arguments, e.g. ["--permission-mode", "acceptEdits"]
    "codexTier": "routine",     # tier of the host binding a Codex controller session runs on
    "notifyCommand": None,      # argv with {title} {message} {ticket} {kind}; [] disables; None = notify-hook
    "notifyHook": None,         # notify.py path (default ~/.claude/hooks/notify-hook/notify.py when present)
}

CLAUDE_PROMPT = (
    "Use the kaneki-flow skill for ticket {ticket}. Run exactly one node: {node}. "
    "First run the driver status command for the ticket and check that nextPending is {node}; if it is not, stop and "
    "say so. Do the {node} node as the skill describes, close its gate with the driver, then stop: do not start the "
    "next node. If the node needs a decision from the user (an ASK_USER question, a user-boundary gate, G4 or any "
    "other fired gate), ask it in this session and wait for the answer; cross a user gate with --actor user only "
    "after the user answered here. Never commit, push, ship, open a pull request or delete a branch."
)
CODEX_PROMPT = (
    "Use the kaneki-flow skill for ticket {ticket}. Run exactly one node: {node}. "
    "First run the driver status command for the ticket and check that nextPending is {node}; if it is not, stop and "
    "say so. Do the {node} node as the skill describes, close its gate with the driver, then stop: do not start the "
    "next node. If the node needs a decision from the user (an ASK_USER question, a user-boundary gate, G4 or any "
    "other fired gate), write the question to the ticket's questions file as the skill says and stop without "
    "closing the gate; never use --actor user. Never commit, push, ship, open a pull request or delete a branch."
)

USAGE_LIMIT_RE = re.compile(r"usage limit|rate[ _-]?limit|limit reached|hit your limit|quota exceeded|"
                            r"too many requests|\b429\b|insufficient_quota", re.I)
_RETRY_IN_RE = re.compile(r"(?:try again|retry|resets?|available again)\s+(?:in|after)\s+(\d+)\s*"
                          r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b", re.I)
_RESET_AT_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_RESET_EPOCH_RE = re.compile(r"limit reached\|(\d{10})\b", re.I)

CLAUDE_ENDED = {"done", "completed", "complete", "stopped", "exited", "finished", "idle"}
CLAUDE_WAITING = {"blocked", "waiting", "needs-input", "needs_input", "input", "paused", "awaiting-input"}
CLAUDE_FAILED = {"failed", "error", "errored", "crashed", "killed"}


# --- small helpers ---------------------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat(text).timestamp() if text else None
    except (TypeError, ValueError):
        return None


def check_ticket_id(ticket: str) -> str:
    if not TICKET_RE.match(ticket or ""):
        raise HarnessError(f"runner refuses ticket id {ticket!r}: only letters, digits, '.', '_' and '-' (max 64)")
    return ticket


def check_node_name(node: str) -> str:
    if not NODE_RE.match(node or ""):
        raise HarnessError(f"runner refuses node name {node!r}")
    return node


def node_prompt(host: str, ticket: str, node: str) -> str:
    """The fixed prompt of one node session: only the validated ticket id and node name are substituted."""
    template = CLAUDE_PROMPT if host == "claude" else CODEX_PROMPT
    return template.format(ticket=check_ticket_id(ticket), node=check_node_name(node))


def session_name(ticket: str, node: str, run_id: str) -> str:
    return f"kaneki:{check_ticket_id(ticket)}:{check_node_name(node)}:{run_id}"


def new_run_id() -> str:
    return "r" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(20):  # Windows: a reader can hold the target for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))


def pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: the process exists
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def kill_pid(pid: Any) -> None:
    try:
        os.kill(int(pid), signal.SIGTERM)  # TerminateProcess on Windows
    except (OSError, TypeError, ValueError):
        pass


def executable(host: str) -> list[str]:
    """The agent CLI as an argv prefix: `$SDLC_CLAUDE` / `$SDLC_CODEX` (a JSON array or a command string, as
    `sdlc dispatch --command` takes it) when set, else the binary on PATH. Tests point these at a fake."""
    from .dispatch import split_command
    override = (os.environ.get(f"SDLC_{host.upper()}") or "").strip()
    if override:
        return split_command(override)
    found = shutil.which(host)
    if not found:
        raise HarnessError(f"runner: `{host}` is not on PATH (set SDLC_{host.upper()} to its path)")
    return [found]


def usage_limit(text: str) -> bool:
    return bool(USAGE_LIMIT_RE.search(text or ""))


def retry_after(text: str, default_minutes: float, now: float | None = None) -> float:
    """Epoch seconds when a usage-limited host may be tried again: from the error text when it names a time, else
    now + default. Clamped to [1 minute, 24 hours]."""
    now = time.time() if now is None else now
    text = text or ""
    when: float | None = None
    m = _RESET_EPOCH_RE.search(text)
    if m:
        when = float(m.group(1))
    m = None if when else _RETRY_IN_RE.search(text)
    if m:
        unit = m.group(2).lower()[0]
        when = now + int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[unit]
    m = None if when else _RESET_AT_RE.search(text)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        local = datetime.fromtimestamp(now).replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
        if local.timestamp() <= now:
            local += timedelta(days=1)
        when = local.timestamp()
    if when is None:
        when = now + default_minutes * 60
    return min(max(when, now + 60), now + 24 * 3600)


# --- budget hook (M3·2 implements it) -------------------------------------------------------------------

def budget_allows(ctx: dict[str, Any]) -> tuple[bool, str | None]:
    """Whether a node may start (`ctx["phase"] == "start"`) or keep running (`"running"`).

    `ctx`: profile (Path), ticket, node, host, runId, phase, startedAt (epoch, running only). Returns
    `(allowed, reason)`. The budget track (M3·2) replaces this body; until then everything is allowed. A refusal at
    start parks the ticket (reason `budget`); a refusal while running stops the session and parks."""
    return True, None


# --- configuration, notifications, back-off ----------------------------------------------------------------

def load_config(pdir: Path) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    data = read_json(pdir / ".sdlc" / "runner.local.json")
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg


def workspace_run_dir(pdir: Path) -> Path:
    return pdir / ".sdlc" / "run"


def backoff_path(pdir: Path, host: str) -> Path:
    return workspace_run_dir(pdir) / f"backoff-{host}.json"


def active_backoff(pdir: Path, host: str, now: float | None = None) -> dict[str, Any] | None:
    data = read_json(backoff_path(pdir, host))
    now = time.time() if now is None else now
    if isinstance(data, dict) and (parse_iso(data.get("until")) or 0) > now:
        return data
    return None


def set_backoff(pdir: Path, host: str, until: float, reason: str, ticket: str, run_id: str) -> dict[str, Any]:
    data = {"host": host, "until": datetime.fromtimestamp(until, timezone.utc).replace(microsecond=0).isoformat(),
            "reason": reason, "ticket": ticket, "runId": run_id, "setAt": now_iso()}
    write_json(backoff_path(pdir, host), data)
    return data


def _default_notify_hook(cfg: dict[str, Any]) -> Path | None:
    configured = cfg.get("notifyHook")
    if configured == "":
        return None
    path = Path(configured) if configured else Path.home() / ".claude" / "hooks" / "notify-hook" / "notify.py"
    return path if path.is_file() else None


def notify(pdir: Path, cfg: dict[str, Any], kind: str, ticket: str, message: str, **extra: Any) -> dict[str, Any]:
    """Append an inbox entry (`.sdlc/inbox.jsonl`) and fire the configured notifier without waiting for it. Never
    raises: a notification problem must not change what the runner did."""
    entry = {"at": now_iso(), "kind": kind, "ticket": ticket, "message": message, **extra}
    try:
        inbox = pdir / ".sdlc" / "inbox.jsonl"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        with inbox.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    title = f"kaneki {ticket}: {kind}"
    try:
        command = cfg.get("notifyCommand")
        if isinstance(command, list):
            if command:
                argv = [str(a).replace("{title}", title).replace("{message}", message)
                        .replace("{ticket}", ticket).replace("{kind}", kind) for a in command]
                _spawn_quiet(argv, None)
        else:
            hook = _default_notify_hook(cfg)
            if hook:
                payload = json.dumps({"message": f"{title}: {message}", "notification_type": "kaneki_runner",
                                      "cwd": str(pdir)})
                _spawn_quiet([sys.executable, str(hook)], payload)
    except (OSError, ValueError):
        pass
    return entry


# Detached children (notifier, tick runs) are never waited for; holding them here keeps Popen from warning that a
# child still runs when the object is collected.
_DETACHED: list[subprocess.Popen] = []


def _detach_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": 0x00000008 | 0x00000200 | 0x08000000}  # DETACHED | NEW_GROUP | NO_WINDOW
    return {"start_new_session": True}


def _spawn_quiet(argv: list[str], stdin_text: str | None) -> None:
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detach_kwargs())
    _DETACHED.append(proc)
    if stdin_text is not None and proc.stdin:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass


# --- the agent CLIs --------------------------------------------------------------------------------------

class ClaudeCLI:
    """`claude --bg` / `agents --json --all` / `stop` / `logs`, through `executable("claude")`."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def _run(self, args: list[str], timeout: int = 60, **kw: Any) -> subprocess.CompletedProcess:
        return subprocess.run(executable("claude") + args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **kw)

    def start(self, name: str, prompt: str, cwd: Path, env: dict[str, str]) -> tuple[str | None, str]:
        extra = [str(a) for a in (self.cfg.get("claudeArgs") or [])]
        proc = self._run(["--bg", "-n", name, *extra, prompt], timeout=120, cwd=str(cwd), env=env)
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        sid = None
        if proc.returncode == 0:
            m = re.search(r"\b([0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?)\b", proc.stdout or "")
            sid = m.group(1) if m else None
            if sid is None:  # the id format changed: find the session by its unique name
                entry = self.find(name=name)
                sid = entry.get("id") if entry else None
        return sid, text

    def sessions(self) -> list[dict[str, Any]] | None:
        """Every session `claude agents --json --all` lists, or None when the call failed (unknown, not empty)."""
        try:
            proc = self._run(["agents", "--json", "--all"])
            data = json.loads(proc.stdout or "null")
        except (OSError, subprocess.SubprocessError, ValueError, HarnessError):
            return None
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else None

    def find(self, sid: str | None = None, name: str | None = None,
             listing: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        listing = self.sessions() if listing is None else listing
        for entry in listing or []:
            if sid and sid in (entry.get("id"), entry.get("sessionId")):
                return entry
            if name and entry.get("name") == name:
                return entry
        return None

    def status(self, sid: str) -> str:
        """`running` | `waiting` | `ended` | `failed` | `gone` | `unknown` (the listing call failed)."""
        listing = self.sessions()
        if listing is None:
            return "unknown"
        return classify_claude(self.find(sid=sid, listing=listing))

    def stop(self, sid: str) -> None:
        try:
            self._run(["stop", sid])
        except (OSError, subprocess.SubprocessError, HarnessError):
            pass

    def logs(self, sid: str) -> str:
        try:
            proc = self._run(["logs", sid])
            return (proc.stdout or "") + "\n" + (proc.stderr or "")
        except (OSError, subprocess.SubprocessError, HarnessError):
            return ""


def classify_claude(entry: dict[str, Any] | None) -> str:
    if entry is None:
        return "gone"
    state = str(entry.get("state") or entry.get("status") or "").lower()
    if state in CLAUDE_FAILED:
        return "failed"
    if state in CLAUDE_ENDED:
        return "ended"
    if state in CLAUDE_WAITING:
        return "waiting"
    return "running"


def codex_argv(home: Path, pdir: Path, profile: dict[str, Any], prompt: str, tier: str) -> list[str]:
    """`codex exec` for one controller node: the argv `sdlc dispatch` builds for the codex runner (model, effort of the
    binding's tier), with `codex` replaced by `executable("codex")`."""
    from . import hosts as H
    from .dispatch import build_argv
    exe = H.resolve_execution(home, pdir, profile, "_runner", "_runner", tier, cli_host="codex")
    argv = build_argv(exe, prompt, "", "", tier=tier, runner_override="codex")
    return executable("codex") + argv[1:]


# --- ticket-level state --------------------------------------------------------------------------------------

def run_dir(ticket_dir: Path) -> Path:
    return ticket_dir / "run"


def lease_path(ticket_dir: Path) -> Path:
    return run_dir(ticket_dir) / "lease.json"


def read_lease(ticket_dir: Path) -> dict[str, Any] | None:
    data = read_json(lease_path(ticket_dir))
    if data is None and lease_path(ticket_dir).exists():
        return {"corrupt": True}
    return data if isinstance(data, dict) else None


def lease_alive(lease: dict[str, Any] | None, cfg: dict[str, Any], now: float | None = None) -> bool:
    """A lease is alive while its runner process runs and its heartbeat is fresh."""
    if not lease or lease.get("corrupt"):
        return False
    now = time.time() if now is None else now
    beat = parse_iso(lease.get("heartbeatAt")) or 0
    return pid_alive(lease.get("runnerPid")) and now - beat < float(cfg.get("leaseStaleSeconds") or 900)


def runner_view(ticket_dir: Path, events: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The board's `runner` field of one ticket (read-only): autonomy, the open park and the lease."""
    state = runner_state(events)
    lease = read_lease(ticket_dir)
    parked = state["parked"]
    return {
        "autonomy": state["autonomy"],
        "parked": {k: parked.get(k) for k in ("at", "reason", "node", "host", "sessionId", "attach", "message")}
        if parked else None,
        "lease": None if not lease else {
            **{k: lease.get(k) for k in ("runId", "host", "node", "sessionId", "childPid", "startedAt", "heartbeatAt",
                                         "trigger")},
            "alive": lease_alive(lease, cfg or DEFAULTS)},
    }


def runner_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    """`autonomy` (the last autonomy event; off when none) and `parked` (the open park event, with its log index)."""
    autonomy, parked = False, None
    for i, ev in enumerate(events):
        kind = ev.get("event")
        if kind == "autonomy":
            autonomy = bool(ev.get("enabled"))
        elif kind == "park":
            parked = {**ev, "_index": i}
        elif kind == "unpark":
            parked = None
    return {"autonomy": autonomy, "parked": parked}


def log_event(ticket: Any, event: dict[str, Any], wait: float = 120.0) -> dict[str, Any]:
    """Append one decision event under the ticket lock, waiting while an agent's own `sdlc` command holds it."""
    deadline = time.time() + wait
    while True:
        try:
            with ticket.lock("runner"):
                return ticket.log(event)
        except HarnessError as exc:
            if "locked by another session" not in str(exc) or time.time() > deadline:
                raise
            time.sleep(0.3)


def attempts_path(ticket_dir: Path) -> Path:
    return run_dir(ticket_dir) / "attempts.json"


def failures(ticket_dir: Path, node: str, generation: int) -> int:
    data = read_json(attempts_path(ticket_dir)) or {}
    rec = data.get(node) or {}
    return int(rec.get("failures") or 0) if rec.get("generation") == generation else 0


def count_failure(ticket_dir: Path, node: str, generation: int, reset: bool = False) -> int:
    data = read_json(attempts_path(ticket_dir)) or {}
    n = 0 if reset else failures(ticket_dir, node, generation) + 1
    data[node] = {"generation": generation, "failures": n, "at": now_iso()}
    write_json(attempts_path(ticket_dir), data)
    return n


def run_log(ticket_dir: Path, run_id: str, kind: str, **fields: Any) -> None:
    path = run_dir(ticket_dir) / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now_iso(), "kind": kind, **fields}, ensure_ascii=False) + "\n")


# --- the runner ----------------------------------------------------------------------------------------------

class Runner:
    def __init__(self, ppath: Path, cfg: dict[str, Any] | None = None, claude: ClaudeCLI | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        from .profile import load_profile
        self.ppath = Path(ppath).resolve()
        self.pdir = self.ppath.parent
        self.profile = load_profile(self.ppath)
        self.cfg = cfg or load_config(self.pdir)
        self.claude = claude or ClaudeCLI(self.cfg)
        self.sleep = sleep

    # -- lookups ---------------------------------------------------------------------------------------------
    def ticket_root(self) -> Path:
        from .cli import ticket_root
        return ticket_root(self.profile, self.pdir)

    def session(self, ticket: str) -> Any:
        from .cli import Session
        return Session(argparse.Namespace(profile=str(self.ppath), ticket=check_ticket_id(ticket), host=None))

    def ticket_dirs(self) -> list[Path]:
        root = self.ticket_root()
        return [d for d in sorted(root.iterdir()) if (d / "state.json").is_file()] if root.is_dir() else []

    def resolve_host(self, host: str | None) -> str:
        from . import hosts as H
        from .dispatch import INTERACTIVE_ONLY_HOSTS
        if not host:
            host = self.cfg.get("host") or H.active_host(self.pdir)[0]
        if host in INTERACTIVE_ONLY_HOSTS:
            raise HarnessError(f"the runner refuses host {host!r}: {INTERACTIVE_ONLY_HOSTS[host]}. Unattended runs use "
                               f"Claude Code or Codex (--host claude|codex).")
        if host not in RUNNABLE_HOSTS:
            raise HarnessError(f"the runner runs only {', '.join(RUNNABLE_HOSTS)} (got {host or 'no host'}); pass "
                               "--host claude|codex or set `host` in .sdlc/runner.local.json")
        return host

    def env(self, host: str) -> dict[str, str]:
        env = dict(os.environ)
        env["SDLC_HOST"] = host
        env["SDLC_PROFILE"] = str(self.ppath)
        return env

    def workspace(self) -> Path:
        """The only cwd a node session gets: the workspace folder holding the profile (where the skill is installed)."""
        if not self.pdir.is_dir():
            raise HarnessError(f"runner: workspace folder missing: {self.pdir}")
        return self.pdir

    # -- preflight -------------------------------------------------------------------------------------------
    def user_reason(self, s: Any, node: str) -> str | None:
        """Why `node` closes on the user (never started by the runner), or None."""
        from . import board
        from .cli import _effective_boundary
        wait = board.waiting(s, node)
        if wait["on"] == "user":
            return wait["reason"]
        if _effective_boundary(s, node) == "user":
            return "user-boundary"
        return None

    def maybe_unpark(self, s: Any, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Close the open park once the ticket moved on: a gate event after the park (the user answered, or closed
        the user gate). A park whose session is still listed is stopped then (its node closed). Returns the park
        still open, or None."""
        from .state import next_pending
        parked = runner_state(events)["parked"]
        if not parked:
            return None
        if parked.get("reason") not in ("user-boundary", "loop-cap", "rejected-open", "session-waiting", "no-gate"):
            return parked  # failures, budget, retries: the user unparks by hand
        later = events[parked["_index"] + 1:]
        moved = any(e.get("event") == "gate" for e in later) or (
            parked.get("node") and next_pending(s.state, s.order) != parked.get("node"))
        if not moved:
            return parked
        if parked.get("sessionId") and parked.get("host") == "claude":
            self.claude.stop(parked["sessionId"])
        log_event(s.ticket, {"event": "unpark", "reason": "progressed", "node": parked.get("node")})
        return None

    def park(self, s: Any, reason: str, node: str | None, message: str, *, host: str | None = None,
             session_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        attach = f"claude attach {session_id}" if session_id and host == "claude" else None
        ev: dict[str, Any] = {"event": "park", "reason": reason, "node": node, "message": message}
        for key, val in (("host", host), ("sessionId", session_id), ("attach", attach), ("runId", run_id)):
            if val:
                ev[key] = val
        if node:
            ev["generation"] = s.state["nodes"][node]["generation"]
        log_event(s.ticket, ev)
        notify(self.pdir, self.cfg, "parked", s.ctx["ticket"],
               message + (f" Attach: {attach}" if attach else " Decide: sdlc decide" if reason in
                          ("user-boundary", "loop-cap", "rejected-open") else ""),
               node=node, reason=reason, attach=attach)
        return {"result": "parked", "reason": reason, "node": node, "attach": attach, "message": message}

    def preflight(self, ticket: str, host: str, *, honour_park: bool = True) -> tuple[Any, str | None, dict[str, Any]]:
        """Load the ticket and decide whether its next node may start. Returns `(session, node, verdict)` where
        verdict is `{"ok": True}` or `{"ok": False, "skip": reason, ...}`; a node that closes on the user parks the
        ticket (once: a parked ticket is skipped until it moves on)."""
        s = self.session(ticket)
        events, problems = s.ticket.verify_log()
        if problems:
            return s, None, {"ok": False, "skip": "integrity", "problems": problems}
        if honour_park:
            parked = self.maybe_unpark(s, events)
            if parked:
                return s, parked.get("node"), {"ok": False, "skip": "parked", "reason": parked.get("reason"),
                                               "attach": parked.get("attach")}
        from .state import next_pending
        node = next_pending(s.state, s.order)
        if node is None:
            return s, None, {"ok": False, "skip": "complete"}
        check_node_name(node)
        why = self.user_reason(s, node)
        if why:
            return s, node, {"ok": False, "skip": "parked", **self.park(
                s, why, node, f"{node} waits on you ({why}); the runner never crosses a user gate.", host=host)}
        gen = s.state["nodes"][node]["generation"]
        if failures(s.ticket.dir, node, gen) > int(self.cfg.get("retries") or 0):
            return s, node, {"ok": False, "skip": "parked", **self.park(
                s, "retries-exhausted", node, f"{node} failed {failures(s.ticket.dir, node, gen)} time(s); "
                "look at the run logs, then `sdlc unpark`.", host=host)}
        allowed, why = budget_allows({"profile": self.ppath, "ticket": ticket, "node": node, "host": host,
                                      "phase": "start", "runId": None})
        if not allowed:
            return s, node, {"ok": False, "skip": "parked", **self.park(
                s, "budget", node, f"budget refuses {node}: {why}", host=host)}
        return s, node, {"ok": True}

    # -- lease -----------------------------------------------------------------------------------------------
    def take_lease(self, s: Any, lease: dict[str, Any]) -> None:
        path = lease_path(s.ticket.dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = read_lease(s.ticket.dir) or {}
            if lease_alive(held, self.cfg):
                raise HarnessError(f"{s.ctx['ticket']} is already running node {held.get('node')} (run "
                                   f"{held.get('runId')}, {held.get('host')}); stop it with "
                                   f"`sdlc run --stop --ticket {s.ctx['ticket']}`")
            raise HarnessError(f"{s.ctx['ticket']} has a stale run lease (run {held.get('runId')}, runner gone); "
                               "run `sdlc recover` first")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(lease, indent=2))

    def update_lease(self, s: Any, lease: dict[str, Any], **fields: Any) -> None:
        lease.update(fields, heartbeatAt=now_iso())
        write_json(lease_path(s.ticket.dir), lease)

    def release_lease(self, ticket_dir: Path, run_id: str | None) -> None:
        held = read_lease(ticket_dir)
        if held is not None and (run_id is None or held.get("runId") in (run_id, None)):
            try:
                lease_path(ticket_dir).unlink()
            except FileNotFoundError:
                pass

    # -- one ticket ------------------------------------------------------------------------------------------
    def run(self, ticket: str, *, max_nodes: int = 1, host: str | None = None, trigger: str = "manual") -> dict[str, Any]:
        check_ticket_id(ticket)
        host = self.resolve_host(host)
        if max_nodes < 1:
            raise HarnessError("--max-nodes must be 1 or more")
        backoff = active_backoff(self.pdir, host)
        if backoff:
            return {"ticket": ticket, "host": host, "result": "backoff", "until": backoff["until"],
                    "reason": backoff.get("reason"), "nodes": []}
        results: list[dict[str, Any]] = []
        outcome: dict[str, Any] = {"ticket": ticket, "host": host, "nodes": results}
        for _ in range(max_nodes):
            s, node, verdict = self.preflight(ticket, host)
            if not verdict["ok"]:
                outcome.update({k: v for k, v in verdict.items() if k not in ("ok", "skip", "result")})
                outcome["result"] = verdict["skip"]
                return outcome
            res = self.run_node(s, node, host, trigger)
            results.append(res)
            if res["result"] != "done":
                outcome.update({k: res.get(k) for k in ("reason", "attach", "retryAfter") if res.get(k)})
                outcome.update(result=res["result"], node=node)
                return outcome
        outcome["result"] = "done"
        outcome["note"] = f"--max-nodes {max_nodes} reached"
        return outcome

    def run_node(self, s: Any, node: str, host: str, trigger: str) -> dict[str, Any]:
        ticket = s.ctx["ticket"]
        run_id = new_run_id()
        gen = s.state["nodes"][node]["generation"]
        started = time.time()
        lease = {"runId": run_id, "ticket": ticket, "host": host, "node": node, "generation": gen,
                 "runnerPid": os.getpid(), "sessionId": None, "childPid": None, "trigger": trigger,
                 "startedAt": now_iso(), "heartbeatAt": now_iso()}
        self.take_lease(s, lease)
        try:
            start_ev = log_event(s.ticket, {"event": "run-start", "runId": run_id, "node": node, "host": host,
                                            "generation": gen, "trigger": trigger})
            run_log(s.ticket.dir, run_id, "start", node=node, host=host, generation=gen, trigger=trigger)
            mark = self._event_count(s)
            if host == "claude":
                res = self._run_claude(s, node, run_id, lease, mark, started)
            else:
                res = self._run_codex(s, node, run_id, lease, mark, started)
        except BaseException as exc:  # noqa: BLE001 - the lease must not outlive a crashed runner step
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                self._kill_agent(host, lease)
                res = {"result": "stopped", "reason": "interrupted"}
            else:
                res = {"result": "failed", "reason": f"runner error: {exc}"}
        return self.finish(s, node, host, run_id, lease, res)

    def finish(self, s: Any, node: str, host: str, run_id: str, lease: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
        gen = lease.get("generation") or s.state["nodes"][node]["generation"]
        result = res["result"]
        end = {"event": "run-end", "runId": run_id, "node": node, "host": host, "result": result}
        if res.get("reason"):
            end["reason"] = res["reason"]
        if lease.get("sessionId"):
            end["sessionId"] = lease["sessionId"]
        try:
            log_event(s.ticket, end)
        finally:
            self.release_lease(s.ticket.dir, run_id)
            stop_marker = run_dir(s.ticket.dir) / f"stop-{run_id}"
            if stop_marker.exists():
                stop_marker.unlink()
        if result == "done":
            count_failure(s.ticket.dir, node, gen, reset=True)
        elif result == "failed":
            res["failures"] = count_failure(s.ticket.dir, node, gen)
            if res.get("reason") != "usage-limit":
                notify(self.pdir, self.cfg, "failed", s.ctx["ticket"], f"{node} failed: {res.get('reason')}",
                       node=node, runId=run_id)
        if result == "parked":
            res.update(self.park(self.session(s.ctx["ticket"]), res.get("reason") or "session-waiting", node,
                                 res.get("message") or f"{node} needs you.", host=host,
                                 session_id=lease.get("sessionId"), run_id=run_id))
        run_log(s.ticket.dir, run_id, "end", **{k: v for k, v in res.items() if k != "event"})
        return {"runId": run_id, "node": node, "host": host, "sessionId": lease.get("sessionId"), **res}

    def _event_count(self, s: Any) -> int:
        events, _ = s.ticket.verify_log()
        return len(events)

    def gate_since(self, ticket: Any, node: str, mark: int) -> dict[str, Any] | None:
        events, _ = ticket.verify_log()
        return next((e for e in events[mark:] if e.get("event") == "gate" and e.get("node") == node), None)

    def _stop_requested(self, s: Any, run_id: str) -> bool:
        return (run_dir(s.ticket.dir) / f"stop-{run_id}").exists()

    def _backoff(self, s: Any, host: str, run_id: str, text: str) -> dict[str, Any]:
        until = retry_after(text, float(self.cfg.get("backoffMinutes") or 60))
        data = set_backoff(self.pdir, host, until, "usage-limit", s.ctx["ticket"], run_id)
        notify(self.pdir, self.cfg, "backoff", s.ctx["ticket"], f"{host} usage limit; runs wait until {data['until']}",
               host=host, until=data["until"])
        return {"result": "failed", "reason": "usage-limit", "retryAfter": data["until"]}

    def _running_checks(self, s: Any, node: str, host: str, run_id: str, started: float) -> dict[str, Any] | None:
        """Stop request, budget and timeout while a node runs: a result to end the run with, or None."""
        if self._stop_requested(s, run_id):
            return {"result": "stopped", "reason": "stop requested"}
        allowed, why = budget_allows({"profile": self.ppath, "ticket": s.ctx["ticket"], "node": node, "host": host,
                                      "phase": "running", "runId": run_id, "startedAt": started})
        if not allowed:
            return {"result": "parked", "reason": "budget", "message": f"budget stopped {node}: {why}", "_stop": True}
        if time.time() - started > float(self.cfg.get("nodeTimeoutMinutes") or 180) * 60:
            return {"result": "failed", "reason": "timeout"}
        return None

    def _run_claude(self, s: Any, node: str, run_id: str, lease: dict[str, Any], mark: int,
                    started: float) -> dict[str, Any]:
        ticket = s.ctx["ticket"]
        name = session_name(ticket, node, run_id)
        sid, text = self.claude.start(name, node_prompt("claude", ticket, node), self.workspace(), self.env("claude"))
        run_log(s.ticket.dir, run_id, "claude-start", name=name, sessionId=sid)
        if not sid:
            if usage_limit(text):
                return self._backoff(s, "claude", run_id, text)
            return {"result": "failed", "reason": "claude --bg printed no session id: " + text.strip()[-300:]}
        self.update_lease(s, lease, sessionId=sid)
        poll = float(self.cfg.get("pollSeconds") or 15)
        while True:
            if self.gate_since(s.ticket, node, mark):
                self.claude.stop(sid)
                return {"result": "done"}
            stop = self._running_checks(s, node, "claude", run_id, started)
            if stop:
                self.claude.stop(sid)
                if stop.pop("_stop", None):
                    lease["sessionId"] = None  # stopped: nothing to attach to
                return stop
            status = self.claude.status(sid)
            if status in ("ended", "waiting", "failed", "gone"):
                if self.gate_since(s.ticket, node, mark):  # the gate landed while the session finished
                    self.claude.stop(sid)
                    return {"result": "done"}
                if status == "gone":
                    return {"result": "failed", "reason": "orphan"}
                logs = self.claude.logs(sid)
                if usage_limit(logs):
                    self.claude.stop(sid)
                    return self._backoff(s, "claude", run_id, logs)
                if status == "failed":
                    return {"result": "failed", "reason": "session failed: " + logs.strip()[-300:]}
                return {"result": "parked", "reason": "session-waiting",
                        "message": f"{node}: the session stopped without closing its gate (it may be asking you)."}
            self.update_lease(s, lease)
            self.sleep(poll)

    def _run_codex(self, s: Any, node: str, run_id: str, lease: dict[str, Any], mark: int,
                   started: float) -> dict[str, Any]:
        ticket = s.ctx["ticket"]
        argv = codex_argv(Path(__file__).resolve().parent.parent, self.pdir, self.profile,
                          node_prompt("codex", ticket, node), str(self.cfg.get("codexTier") or "routine"))
        out_path = run_dir(s.ticket.dir) / f"{run_id}.codex.log"
        poll = float(self.cfg.get("pollSeconds") or 15)
        with out_path.open("w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.Popen(argv, cwd=str(self.workspace()), env=self.env("codex"), stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self.update_lease(s, lease, childPid=proc.pid)
            run_log(s.ticket.dir, run_id, "codex-start", childPid=proc.pid, log=str(out_path))
            stop = None
            while proc.poll() is None:
                stop = self._running_checks(s, node, "codex", run_id, started)
                if stop:
                    proc.kill()
                    proc.wait()
                    break
                self.update_lease(s, lease)
                self.sleep(poll)
        text = out_path.read_text(encoding="utf-8", errors="replace")
        if self.gate_since(s.ticket, node, mark):
            return {"result": "done"}
        if stop:
            stop.pop("_stop", None)
            return stop
        if usage_limit(text):
            return self._backoff(s, "codex", run_id, text)
        if proc.returncode != 0:
            return {"result": "failed", "reason": f"codex exec exit {proc.returncode}: " + text.strip()[-300:]}
        return {"result": "parked", "reason": "no-gate",
                "message": f"{node}: codex exec ended without closing the gate; open the ticket in Zed (Codex) to "
                           "answer its questions and continue."}

    def _kill_agent(self, host: str, lease: dict[str, Any]) -> None:
        if host == "claude" and lease.get("sessionId"):
            self.claude.stop(lease["sessionId"])
        if lease.get("childPid"):
            kill_pid(lease["childPid"])

    # -- stop ------------------------------------------------------------------------------------------------
    def stop(self, ticket: str) -> dict[str, Any]:
        s = self.session(ticket)
        lease = read_lease(s.ticket.dir)
        if not lease or lease.get("corrupt"):
            parked = runner_state(s.ticket.verify_log()[0])["parked"]
            if parked and parked.get("sessionId") and parked.get("host") == "claude":
                self.claude.stop(parked["sessionId"])
                return {"ticket": ticket, "stopped": "parked-session", "sessionId": parked["sessionId"],
                        "note": "the ticket stays parked; `sdlc unpark` when you want the runner to continue"}
            if lease and lease.get("corrupt"):
                self.release_lease(s.ticket.dir, None)
                return {"ticket": ticket, "stopped": "corrupt-lease"}
            raise HarnessError(f"{ticket} has no run lease (nothing is running)")
        run_id = lease.get("runId") or "unknown"
        marker = run_dir(s.ticket.dir) / f"stop-{run_id}"
        marker.write_text(now_iso(), encoding="utf-8")
        self._kill_agent(lease.get("host") or "", lease)
        if lease_alive(lease, self.cfg) and lease.get("runnerPid") != os.getpid():
            deadline = time.time() + 2 * float(self.cfg.get("pollSeconds") or 15) + 10
            while time.time() < deadline and lease_path(s.ticket.dir).exists():
                self.sleep(0.2)
            if not lease_path(s.ticket.dir).exists():
                return {"ticket": ticket, "stopped": "run", "runId": run_id, "by": "runner"}
            kill_pid(lease.get("runnerPid"))
        log_event(s.ticket, {"event": "run-end", "runId": run_id, "node": lease.get("node"), "host": lease.get("host"),
                             "result": "stopped", "reason": "sdlc run --stop"})
        run_log(s.ticket.dir, run_id, "end", result="stopped", reason="sdlc run --stop")
        self.release_lease(s.ticket.dir, run_id)
        if marker.exists():
            marker.unlink()
        return {"ticket": ticket, "stopped": "run", "runId": run_id, "sessionId": lease.get("sessionId")}

    # -- autonomy / unpark -----------------------------------------------------------------------------------
    def autonomy(self, ticket: str, setting: str | None, reason: str | None) -> dict[str, Any]:
        s = self.session(ticket)
        current = runner_state(s.ticket.verify_log()[0])["autonomy"]
        if setting is None:
            return {"ticket": ticket, "autonomy": current}
        enabled = setting == "on"
        ev = {"event": "autonomy", "enabled": enabled}
        if reason:
            ev["reason"] = reason
        s.ticket.log(ev)  # the caller holds the ticket lock (cli.main)
        return {"ticket": ticket, "autonomy": enabled, "previous": current}

    def unpark(self, ticket: str, reason: str | None) -> dict[str, Any]:
        s = self.session(ticket)
        events, _ = s.ticket.verify_log()
        parked = runner_state(events)["parked"]
        if not parked:
            raise HarnessError(f"{ticket} is not parked")
        gen = s.state["nodes"].get(parked.get("node") or "", {}).get("generation")
        if parked.get("reason") == "retries-exhausted" and parked.get("node") and gen:
            count_failure(s.ticket.dir, parked["node"], gen, reset=True)
        s.ticket.log({"event": "unpark", "reason": reason or "by user", "node": parked.get("node")})
        return {"ticket": ticket, "unparked": True, "was": {k: parked.get(k) for k in ("reason", "node", "attach")}}

    # -- recover ---------------------------------------------------------------------------------------------
    def recover(self, *, restart: bool = True, foreground: bool = True) -> dict[str, Any]:
        """Leases whose runner is gone: release when the node's gate exists; park when the session waits for the
        user; otherwise the run failed (orphan) and the node restarts in a fresh session when the ticket opted in
        and retries are left."""
        report: list[dict[str, Any]] = []
        for d in self.ticket_dirs():
            lease = read_lease(d)
            if lease is None:
                continue
            ticket = d.name
            if lease_alive(lease, self.cfg):
                report.append({"ticket": ticket, "action": "none", "why": "runner alive", "runId": lease.get("runId")})
                continue
            try:
                s = self.session(ticket)
            except HarnessError as exc:
                report.append({"ticket": ticket, "action": "none", "why": f"ticket does not load: {exc}"})
                continue
            if lease.get("corrupt"):
                self.release_lease(d, None)
                report.append({"ticket": ticket, "action": "released", "why": "corrupt lease"})
                continue
            run_id, node, host = lease.get("runId"), lease.get("node"), lease.get("host") or "claude"
            events, _ = s.ticket.verify_log()
            mark = next((i for i, e in enumerate(events) if e.get("event") == "run-start" and e.get("runId") == run_id),
                        len(events))
            sid = lease.get("sessionId")
            if self.gate_since(s.ticket, node, mark):
                if host == "claude" and sid:
                    self.claude.stop(sid)
                res = {"result": "done", "reason": "recovered: gate found"}
                self.finish(s, node, host, run_id, lease, res)
                report.append({"ticket": ticket, "action": "released", "why": "gate event exists", "runId": run_id})
                continue
            status = "gone"
            if host == "claude" and sid:
                status = self.claude.status(sid)
            elif lease.get("childPid") and pid_alive(lease["childPid"]):
                status = "running"
            if status in ("running", "unknown"):
                report.append({"ticket": ticket, "action": "none", "why": f"session {status} without its runner",
                               "runId": run_id, "sessionId": sid})
                continue
            if status in ("waiting", "ended"):
                logs = self.claude.logs(sid) if sid else ""
                if usage_limit(logs):
                    self.claude.stop(sid)
                    res = self._backoff(s, host, run_id, logs)
                else:
                    res = {"result": "parked", "reason": "session-waiting",
                           "message": f"{node}: the session stopped without closing its gate (runner gone)."}
                out = self.finish(s, node, host, run_id, lease, res)
                report.append({"ticket": ticket, "action": res["result"], "runId": run_id, "attach": out.get("attach")})
                continue
            res = self.finish(s, node, host, run_id, lease, {"result": "failed", "reason": "orphan"})
            entry: dict[str, Any] = {"ticket": ticket, "action": "failed", "why": "orphan", "runId": run_id,
                                     "failures": res.get("failures")}
            autonomy = runner_state(self.session(ticket).ticket.verify_log()[0])["autonomy"]
            if not restart:
                entry["restart"] = "skipped (--no-restart)"
            elif not autonomy:
                entry["restart"] = "skipped (autonomy off)"
            elif host not in RUNNABLE_HOSTS or active_backoff(self.pdir, host):
                entry["restart"] = "skipped (backoff or host)"
            else:
                entry["restart"] = (self.run(ticket, max_nodes=1, host=host, trigger="recover") if foreground
                                    else self.spawn(ticket, host, "recover"))
            report.append(entry)
        return {"recovered": report}

    # -- tick ------------------------------------------------------------------------------------------------
    def live_runs(self) -> int:
        return sum(1 for d in self.ticket_dirs() if lease_alive(read_lease(d), self.cfg))

    def tick(self, *, host: str | None = None, foreground: bool = False, recover: bool = True) -> dict[str, Any]:
        host = self.resolve_host(host)
        out: dict[str, Any] = {"host": host, "started": [], "skipped": []}
        if recover:
            out["recover"] = self.recover(foreground=foreground)["recovered"]
        backoff = active_backoff(self.pdir, host)
        if backoff:
            out["backoff"] = backoff
            return out
        cap = max(1, int(self.cfg.get("maxConcurrent") or 1))
        running = self.live_runs()
        from . import board
        for d in self.ticket_dirs():
            ticket = d.name
            if not TICKET_RE.match(ticket):
                out["skipped"].append({"ticket": ticket, "why": "invalid ticket id"})
                continue
            lease = read_lease(d)
            if lease is not None:
                out["skipped"].append({"ticket": ticket, "why": "leased" if lease_alive(lease, self.cfg)
                                       else "stale-lease (sdlc recover)"})
                continue
            if running >= cap:
                out["skipped"].append({"ticket": ticket, "why": "max-concurrent"})
                continue
            lock = board.ticket_lock(d, time.time())
            if lock and (lock.get("ageSeconds") or 0) < 600:
                out["skipped"].append({"ticket": ticket, "why": "locked", "command": lock.get("command")})
                continue
            try:
                s = self.session(ticket)
            except HarnessError as exc:
                out["skipped"].append({"ticket": ticket, "why": f"does not load: {exc}"})
                continue
            if not runner_state(s.ticket.verify_log()[0])["autonomy"]:
                out["skipped"].append({"ticket": ticket, "why": "opted-out"})
                continue
            try:
                _, node, verdict = self.preflight(ticket, host)
            except HarnessError as exc:
                out["skipped"].append({"ticket": ticket, "why": f"preflight: {exc}"})
                continue
            if not verdict["ok"]:
                out["skipped"].append({"ticket": ticket, "why": verdict["skip"], "node": node,
                                       **({"reason": verdict["reason"]} if verdict.get("reason") else {})})
                continue
            running += 1
            if foreground:
                out["started"].append(self.run(ticket, max_nodes=1, host=host, trigger="tick"))
                if active_backoff(self.pdir, host):
                    out["backoff"] = active_backoff(self.pdir, host)
                    break
            else:
                out["started"].append(self.spawn(ticket, host, "tick"))
        return out

    def spawn(self, ticket: str, host: str, trigger: str) -> dict[str, Any]:
        """Start `sdlc run --max-nodes 1` as a detached process (tick returns at once; the run holds the lease)."""
        home = Path(__file__).resolve().parent.parent
        argv = [sys.executable, str(home / "skills" / "kaneki-flow" / "scripts" / "sdlc.py"), "run",
                "--profile", str(self.ppath), "--ticket", check_ticket_id(ticket), "--max-nodes", "1",
                "--host", host, "--trigger", trigger]
        d = self.ticket_root() / ticket
        run_dir(d).mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, SDLC_HOME=str(home))
        with (run_dir(d) / "spawn.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} spawn {trigger}\n")
            fh.flush()
            proc = subprocess.Popen(argv, cwd=str(self.workspace()), env=env, stdin=subprocess.DEVNULL,
                                    stdout=fh, stderr=subprocess.STDOUT, **_detach_kwargs())
        _DETACHED.append(proc)
        return {"ticket": ticket, "spawned": True, "pid": proc.pid, "trigger": trigger}
