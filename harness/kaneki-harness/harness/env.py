"""The harness-owned evidence environment and the prepare baseline (plan E3, B3, B4).

feature-flags lost two evidence runs and two hours to the environment: the local Postgres (max_connections=20) was
exhausted by the Neon proxy in front of it, the agent wrote its own connection reaper and server scripts, and the
profile's baseUrl pointed at production. Agents should never start servers or touch database connections; the driver
does, from the profile:

    "environment": {
      "evidence": {
        "repo": "my-kaneki",                                   # whose worktree runs the app (default: the first)
        "envFile": ".sdlc/test-env/my-kaneki.evidence.env",    # the reusable test account + env (never printed)
        "database": {"container": "my-kaneki-postgres-1", "user": "postgres", "maxConnections": 100,
                     "name": "kaneki_{ticket}", "urlVars": ["DATABASE_URL", "DATABASE_URL_UNPOOLED"]},
        "migrate": "node --import tsx scripts/db-migrate.ts",
        "server": {"command": "node node_modules/next/dist/bin/next dev -p {port}", "port": 3100,
                   "health": "/", "readyTimeoutSeconds": 180},
        "seed": "node \\"{envDir}/seed.mjs\\" {baseUrl}",
        "env": {"NEXT_TELEMETRY_DISABLED": "1"}
      },
      "baseline": {"install": {"my-kaneki": "npm ci"}, "timeoutSeconds": 900}
    }

Commands (all print one JSON object; secrets never appear in it):
- `sdlc env check`  (B4 preflight, read-only): env file present, docker and the container up, connection headroom
  (max_connections against the configured floor and against connections in use), the server port free.
- `sdlc env up`     database sized (ALTER SYSTEM + one container restart when max_connections is below the floor),
  the ticket's own database created, migrations, the app server started in the background with the env loaded,
  a health wait, the seed. Idempotent: a live server of this ticket is reused. Writes `<ticketDir>/env.json`.
- `sdlc env status` the recorded server and a health probe.
- `sdlc env exec -- <cmd>` a command in the worktree with the env loaded (repo e2e tests, one-off checks), so no
  unit ever reads the env file or writes a `.env.local`.
- `sdlc env down [--drop-db]` stops the server (its whole process tree) and, when asked, drops the ticket database.
- `sdlc baseline`   (B3) per repository: install, then lint, unit tests and build on the checkout as the ticket
  found it, recorded in `<ticketDir>/baseline.json` so later nodes know which failures were already there.

Every failed step carries a class: `infra` (docker, database, port, health) or `setup` (install, migrate, seed).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import HarnessError

ENV_FILE = "env.json"
BASELINE_FILE = "baseline.json"
DEFAULT_TIMEOUT = 900
TAIL_LINES = 20


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- configuration ---------------------------------------------------------------------------------------------

def config(profile: dict[str, Any]) -> dict[str, Any] | None:
    return ((profile.get("environment") or {}).get("evidence")) or None


def _require(s: Any) -> dict[str, Any]:
    cfg = config(s.profile)
    if not cfg:
        raise HarnessError("G7: the profile has no environment.evidence block: the harness has no evidence "
                           "environment to manage for this project (kaneki-onboarding writes it)")
    return cfg


def _repo(s: Any, cfg: dict[str, Any], repo_id: str | None = None) -> dict[str, Any]:
    want = repo_id or cfg.get("repo")
    repos = s.ctx["repositories"]
    repo = next((r for r in repos if r["id"] == want), None) if want else (repos[0] if repos else None)
    if repo is None:
        raise HarnessError(f"G7: unknown repository {want!r} for the evidence environment")
    return repo


def _checkout(repo: dict[str, Any]) -> Path:
    return Path(repo.get("worktree") or repo["path"])


def _abs(s: Any, p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (s.pdir / path)


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.removeprefix("export ").partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def db_name(cfg: dict[str, Any], ticket: str) -> str | None:
    db = cfg.get("database") or {}
    if not db.get("name"):
        return None
    slug = re.sub(r"[^a-z0-9_]", "_", ticket.lower())
    return db["name"].format(ticket=slug)[:63]


def with_database(url: str, name: str) -> str:
    """The same connection URL pointing at database `name`."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def port(cfg: dict[str, Any]) -> int | None:
    srv = cfg.get("server") or {}
    return int(srv["port"]) if srv.get("port") else None


def base_url(cfg: dict[str, Any]) -> str | None:
    p = port(cfg)
    return f"http://localhost:{p}" if p else None


def values(s: Any, cfg: dict[str, Any], repo: dict[str, Any]) -> dict[str, str]:
    env_dir = _abs(s, cfg["envFile"]).parent if cfg.get("envFile") else s.pdir
    return {"ticket": s.ctx["ticket"], "port": str(port(cfg) or ""), "baseUrl": base_url(cfg) or "",
            "envDir": env_dir.as_posix(), "worktree": _checkout(repo).as_posix(), "db": db_name(cfg, s.ctx["ticket"]) or ""}


def fmt(template: str, vals: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), template)


def process_env(s: Any, cfg: dict[str, Any]) -> dict[str, str]:
    """os.environ + the env file (with the ticket's database) + the profile's extra env. Never printed."""
    env = dict(os.environ)
    if cfg.get("envFile"):
        f = _abs(s, cfg["envFile"])
        if not f.is_file():
            raise HarnessError(f"G7: the evidence env file is missing: {f} (create it once; it is reused by every ticket)")
        env.update(parse_env_file(f))
    name = db_name(cfg, s.ctx["ticket"])
    if name:
        for var in (cfg.get("database") or {}).get("urlVars", ["DATABASE_URL"]):
            if env.get(var):
                env[var] = with_database(env[var], name)
    env.update({k: str(v) for k, v in (cfg.get("env") or {}).items()})
    return env


# --- process helpers ---------------------------------------------------------------------------------------------

def _tail(text: str, n: int = TAIL_LINES) -> str:
    return "\n".join(text.rstrip().splitlines()[-n:])


def run(cmd: str, cwd: Path, env: dict[str, str] | None, log: Path, timeout: int) -> dict[str, Any]:
    """One shell command, its output to `log`; {cmd, exit, seconds, tail, log}."""
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        fh.write(f"$ {cmd}\n(cwd {cwd})\n\n")
        fh.flush()
        try:
            proc = subprocess.run(cmd, shell=True, cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = None
            fh.write(f"\nTIMEOUT after {timeout}s\n")
    text = log.read_text(encoding="utf-8", errors="replace")
    return {"cmd": cmd, "exit": code, "seconds": round(time.monotonic() - t0, 1), "tail": _tail(text), "log": str(log)}


def _docker(cfg: dict[str, Any]) -> list[str]:
    return shlex.split((cfg.get("database") or {}).get("docker") or "docker")


def _capture(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    return p.returncode, (p.stdout + p.stderr).strip()


def psql(cfg: dict[str, Any], sql: str, database: str = "postgres") -> tuple[int, str]:
    db = cfg["database"]
    return _capture(_docker(cfg) + ["exec", db["container"], "psql", "-U", db.get("user", "postgres"), "-d", database,
                                    "-tAc", sql])


def container_running(cfg: dict[str, Any]) -> tuple[bool, str]:
    code, out = _capture(_docker(cfg) + ["inspect", "-f", "{{.State.Running}}", cfg["database"]["container"]])
    return code == 0 and out.strip().lower() == "true", out


def wait_db(cfg: dict[str, Any], seconds: int = 90) -> bool:
    db = cfg["database"]
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        code, _ = _capture(_docker(cfg) + ["exec", db["container"], "pg_isready", "-U", db.get("user", "postgres")])
        if code == 0:
            return True
        time.sleep(1)
    return False


def port_open(p: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, p)) == 0


def probe(url: str, timeout: float = 5.0) -> int | None:
    """The HTTP status of `url` (any response counts as the server answering), or None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        code, out = _capture(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        return code == 0 and str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        _capture(["taskkill", "/PID", str(pid), "/T", "/F"])
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        pass


def start_background(cmd: str, cwd: Path, env: dict[str, str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("w", encoding="utf-8", errors="replace")
    fh.write(f"$ {cmd}\n(cwd {cwd}, started {_now()})\n\n")
    fh.flush()
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kw)
    return proc.pid


# --- state file ----------------------------------------------------------------------------------------------------

def _state_path(s: Any) -> Path:
    return s.ticket.dir / ENV_FILE


def load_state(s: Any) -> dict[str, Any]:
    try:
        return json.loads(_state_path(s).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(s: Any, data: dict[str, Any]) -> None:
    _state_path(s).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _step(name: str, ok: bool, cls: str | None = None, **kw: Any) -> dict[str, Any]:
    row = {"step": name, "ok": ok, **kw}
    if not ok:
        row["class"] = cls or "infra"
    return row


# --- B4: preflight -------------------------------------------------------------------------------------------------

def check(s: Any, repo_id: str | None = None) -> dict[str, Any]:
    """Read-only preflight: what would make `env up` (and so the evidence run) fail on the environment."""
    cfg = _require(s)
    repo = _repo(s, cfg, repo_id)
    steps: list[dict[str, Any]] = []
    checkout = _checkout(repo)
    steps.append(_step("checkout", checkout.is_dir(), "setup", path=str(checkout)))
    if cfg.get("envFile"):
        f = _abs(s, cfg["envFile"])
        steps.append(_step("env-file", f.is_file(), "setup", path=str(f)))
    db = cfg.get("database")
    if db:
        up, detail = container_running(cfg)
        steps.append(_step("container", up, detail=None if up else f"{db['container']} is not running ({detail[:200]})"))
        if up:
            code, out = psql(cfg, "select current_setting('max_connections')::int, (select count(*) from pg_stat_activity)")
            if code != 0:
                steps.append(_step("connections", False, detail=out[:300]))
            else:
                mx, used = (int(x) for x in out.strip().split("|"))
                floor = int(db.get("maxConnections") or 0)
                ok = mx >= floor and used < mx * 0.8
                steps.append(_step("connections", ok, maxConnections=mx, inUse=used, floor=floor,
                                   detail=None if ok else ("max_connections below the profile floor: `sdlc env up` "
                                                           "raises it" if mx < floor else "over 80% of connections in use")))
    p = port(cfg)
    if p:
        mine = load_state(s)
        busy = port_open(p)
        ours = busy and pid_alive(mine.get("pid")) and mine.get("port") == p
        steps.append(_step("port", not busy or ours, port=p, detail=None if (not busy or ours) else
                           f"port {p} is taken by another process"))
    problems = [st for st in steps if not st["ok"]]
    return {"ok": not problems, "ticket": s.ctx["ticket"], "repo": repo["id"], "steps": steps,
            "fixable": [st["step"] for st in problems if st["step"] == "connections" and st.get("maxConnections", 0) < st.get("floor", 0)]}


# --- E3: up / down / status / exec ---------------------------------------------------------------------------------

def _health_url(cfg: dict[str, Any]) -> str | None:
    b = base_url(cfg)
    return (b + ((cfg.get("server") or {}).get("health") or "/")) if b else None


def status(s: Any) -> dict[str, Any]:
    cfg = _require(s)
    st = load_state(s)
    alive = pid_alive(st.get("pid"))
    code = probe(_health_url(cfg)) if alive and _health_url(cfg) else None
    return {"ok": alive and code is not None and code < 500, "ticket": s.ctx["ticket"], "pid": st.get("pid"),
            "alive": alive, "health": code, "baseUrl": st.get("baseUrl"), "db": st.get("db"),
            "startedAt": st.get("startedAt")}


def _size_database(cfg: dict[str, Any], steps: list[dict[str, Any]]) -> bool:
    db = cfg["database"]
    up, detail = container_running(cfg)
    if not up:
        steps.append(_step("container", False, detail=f"{db['container']} is not running ({detail[:200]})"))
        return False
    floor = int(db.get("maxConnections") or 0)
    code, out = psql(cfg, "show max_connections")
    if code != 0:
        steps.append(_step("max-connections", False, detail=out[:300]))
        return False
    current = int(out.strip() or 0)
    if floor and current < floor:
        code, out = psql(cfg, f"alter system set max_connections = {floor}")
        if code != 0:
            steps.append(_step("max-connections", False, detail=out[:300]))
            return False
        _capture(_docker(cfg) + ["restart", db["container"]], timeout=120)
        if not wait_db(cfg):
            steps.append(_step("max-connections", False, detail="the database did not come back after the restart"))
            return False
        steps.append(_step("max-connections", True, before=current, after=floor, restarted=True))
    else:
        steps.append(_step("max-connections", True, value=current))
    return True


def _create_database(cfg: dict[str, Any], name: str, steps: list[dict[str, Any]]) -> bool:
    code, out = psql(cfg, f"select 1 from pg_database where datname = '{name}'")
    if code != 0:
        steps.append(_step("database", False, detail=out[:300]))
        return False
    if out.strip() == "1":
        steps.append(_step("database", True, database=name, created=False))
        return True
    code, out = psql(cfg, f'create database "{name}"')
    steps.append(_step("database", code == 0, database=name, created=code == 0, detail=None if code == 0 else out[:300]))
    return code == 0


def up(s: Any, repo_id: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    cfg = _require(s)
    repo = _repo(s, cfg, repo_id)
    checkout = _checkout(repo)
    vals = values(s, cfg, repo)
    logs = s.ticket.dir / "logs"
    current = status(s)
    if current["ok"]:
        return {"ok": True, "reused": True, **{k: current[k] for k in ("pid", "baseUrl", "db", "startedAt")}}
    if current.get("alive"):
        kill_tree(current["pid"])  # a server of this ticket that does not answer: start over
    steps: list[dict[str, Any]] = []
    result: dict[str, Any] = {"ok": False, "ticket": s.ctx["ticket"], "repo": repo["id"], "baseUrl": base_url(cfg),
                              "steps": steps}

    def done(ok: bool) -> dict[str, Any]:
        result["ok"] = ok
        failed = next((st for st in steps if not st["ok"]), None)
        if failed:
            result["failedStep"], result["class"] = failed["step"], failed.get("class")
        _save_state(s, {**load_state(s), **{k: result.get(k) for k in ("baseUrl", "repo")},
                        "steps": steps, "at": _now(), "ok": ok})
        return result

    try:
        env = process_env(s, cfg)
    except HarnessError as exc:
        steps.append(_step("env-file", False, "setup", detail=str(exc)))
        return done(False)
    name = db_name(cfg, s.ctx["ticket"])
    if cfg.get("database"):
        if not _size_database(cfg, steps) or (name and not _create_database(cfg, name, steps)):
            return done(False)
        result["db"] = name
    for key in ("migrate",):
        if cfg.get(key):
            r = run(fmt(cfg[key], vals), checkout, env, logs / f"env-{key}.log", timeout)
            steps.append(_step(key, r["exit"] == 0, "setup", **r))
            if r["exit"] != 0:
                return done(False)
    srv = cfg.get("server") or {}
    if srv.get("command"):
        p = port(cfg)
        if p and port_open(p):
            steps.append(_step("port", False, detail=f"port {p} is taken by another process"))
            return done(False)
        pid = start_background(fmt(srv["command"], vals), checkout, env, logs / "env-server.log")
        url = _health_url(cfg)
        limit = int(srv.get("readyTimeoutSeconds") or 180)
        t0, code = time.monotonic(), None
        while time.monotonic() - t0 < limit:
            code = probe(url) if url else None
            if code is not None and code < 500:
                break
            if not pid_alive(pid):
                break
            time.sleep(1)
        ready = code is not None and code < 500
        steps.append(_step("server", ready, pid=pid, health=code, seconds=round(time.monotonic() - t0, 1),
                           log=str(logs / "env-server.log"),
                           detail=None if ready else "the server did not answer the health URL in time"))
        if not ready:
            kill_tree(pid)
            return done(False)
        result.update(pid=pid, port=p, startedAt=_now())
    if cfg.get("seed"):
        r = run(fmt(cfg["seed"], vals), checkout, env, logs / "env-seed.log", timeout)
        steps.append(_step("seed", r["exit"] == 0, "setup", **r))
        if r["exit"] != 0:
            return done(False)
    res = done(True)
    _save_state(s, {"baseUrl": base_url(cfg), "repo": repo["id"], "pid": result.get("pid"), "port": result.get("port"),
                    "db": result.get("db"), "startedAt": result.get("startedAt"), "steps": steps, "at": _now(), "ok": True})
    return res


def down(s: Any, drop_db: bool = False) -> dict[str, Any]:
    cfg = _require(s)
    st = load_state(s)
    stopped = False
    if pid_alive(st.get("pid")):
        kill_tree(st["pid"])
        stopped = True
    dropped = None
    name = st.get("db") or db_name(cfg, s.ctx["ticket"])
    if drop_db and name and cfg.get("database"):
        code, out = psql(cfg, f'drop database if exists "{name}" with (force)')
        dropped = name if code == 0 else None
        if code != 0:
            return {"ok": False, "stopped": stopped, "class": "infra", "detail": out[:300]}
    _save_state(s, {**st, "pid": None, "stoppedAt": _now(), **({"db": None} if dropped else {})})
    return {"ok": True, "ticket": s.ctx["ticket"], "stopped": stopped, "droppedDb": dropped}


def exec_in(s: Any, argv: list[str], repo_id: str | None = None) -> int:
    cfg = _require(s)
    repo = _repo(s, cfg, repo_id)
    if not argv:
        raise HarnessError("usage: sdlc env exec --ticket T -- <command> [args]")
    env = process_env(s, cfg)
    vals = values(s, cfg, repo)
    env.setdefault("BASE_URL", vals["baseUrl"])
    cmd = [fmt(a, vals) for a in argv]
    return subprocess.run(cmd if len(cmd) > 1 else cmd[0], shell=len(cmd) == 1, cwd=str(_checkout(repo)), env=env).returncode


# --- B3: baseline --------------------------------------------------------------------------------------------------

def _clean(cmd: str) -> str:
    """A stack command without its per-change placeholders ({paths}, {testPaths}): the whole repository."""
    return re.sub(r"\s*\{(paths|testPaths)\}", "", cmd).strip()


def baseline(s: Any, stacks: dict[str, dict[str, Any]], timeout: int | None = None) -> dict[str, Any]:
    """Install, then lint / unit tests / build on each repository checkout as the ticket found it. Test and lint
    failures are recorded, not refused: they are the baseline later nodes compare against. A failed install is a
    refusal (coding would be blocked)."""
    from .state import _git
    bcfg = (s.profile.get("environment") or {}).get("baseline") or {}
    limit = int(timeout or bcfg.get("timeoutSeconds") or DEFAULT_TIMEOUT)
    logs = s.ticket.dir / "logs"
    repos = []
    ok = True
    for r in s.ctx["repositories"]:
        checkout = _checkout(r)
        cmds = (stacks.get(r["id"]) or {}).get("commands") or {}
        install = (bcfg.get("install") or {}).get(r["id"]) or cmds.get("install")
        try:
            head = _git(str(checkout), "rev-parse", "HEAD").strip()
        except Exception:  # noqa: BLE001
            head = None
        row: dict[str, Any] = {"repo": r["id"], "checkout": str(checkout), "head": head, "results": {}}
        if install:
            res = run(install, checkout, None, logs / f"baseline-{r['id']}-install.log", limit)
            row["results"]["install"] = res
            if res["exit"] != 0:
                ok = False
                row["blocked"] = "install failed"
                repos.append(row)
                continue
        for key in ("lint", "unitTest", "build"):
            if cmds.get(key):
                row["results"][key] = run(_clean(cmds[key]), checkout, None, logs / f"baseline-{r['id']}-{key}.log", limit)
        repos.append(row)
    data = {"ticket": s.ctx["ticket"], "at": _now(), "ok": ok, "repos": repos,
            "summary": {r["repo"]: {k: v["exit"] for k, v in r["results"].items()} for r in repos}}
    (s.ticket.dir / BASELINE_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data
