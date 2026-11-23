"""`sdlc web`: a local, read-only board of every ticket under the profile, refreshed live.

- Binds 127.0.0.1 only. It never writes ticket state: approvals stay in Claude Code (the gate records who approved).
- `POST /api/open` opens a file in the editor (default `zed`; override with SDLC_EDITOR), optionally at a `line` and
  `column`. It needs the per-run token embedded in the page, and only opens files under the ticket root or a profile
  repository.
- `POST /api/focus` brings the editor to the front on the workspace (the profile's folder): `zed <workspace>` focuses
  the Zed window that has it open, or opens one. Same token; the body is ignored, so it can open nothing else. The
  board's "Continue in Zed" copies the node prompt to the clipboard and calls it.
- The page (`web_page.html`) is self-contained: at request time the three design-system files
  (`app/design-system/{tokens.css,components.css,icons.js}` under the harness home, an allowlist) are inlined in place
  of their `/*__DS:<name>__*/` markers, so the board works offline with no CDN and no static-file route.
- Read-only JSON (additive to contract version 1): `/api/tickets`, `/api/ticket`, `/api/ticket-detail` (the
  `sdlc ticket-detail` summary), `/api/node` (board.node_view), `/api/transcripts` (sessions.transcripts, cached per
  ticket by a stat-only fingerprint of the logs it reads), `/api/inflight` (board.inflight_report) and `/api/cost`
  (cost.ticket_report with `id` [`node`, `since`, `until`], or cost.range_report with `since` alone, e.g. `since=today`
  for the top bar; cost.py caches by a stat-only fingerprint, and the board re-checks that fingerprint at most every
  COST_RECHECK_SECONDS). A ticket id must name a folder directly under the ticket root.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import board, cost, sessions
from .contract import CONTRACT_VERSION
from .errors import HarnessError

MAX_FILE_BYTES = 512 * 1024
TX_TTL_SECONDS = 300      # a cached transcripts answer is re-read at least this often, even if no log changed
TX_CACHE_ENTRIES = 64     # (ticket, node, maxSteps) answers kept; the oldest goes first
MAX_STEPS_LIMIT = 1000
COST_RECHECK_SECONDS = 10  # a /api/cost answer is served as-is this long before its log fingerprint is checked again
PAGE = Path(__file__).with_name("web_page.html")
DESIGN_SYSTEM = Path(__file__).resolve().parent.parent / "app" / "design-system"
DS_FILES = ("tokens.css", "components.css", "icons.js")  # the only files the page may inline
_PLACEHOLDER = re.compile(r"__TOKEN__|__PROJECT__|__WORKSPACE__|/\*__DS:([A-Za-z0-9_.-]+)__\*/")


def editor_command() -> list[str] | None:
    raw = os.environ.get("SDLC_EDITOR")
    if raw:
        return shlex.split(raw, posix=os.name != "nt")
    for name in ("zed", "zeditor", "zed.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    if os.name == "nt":  # the Windows installer puts the CLI here without touching PATH
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Zed" / "bin" / "zed.exe"
        if local.is_file():
            return [str(local)]
    return None


def editor_flags(cmd: list[str]) -> list[str]:
    """`-e` (`--existing`: open in the running Zed window) when the editor is the auto-detected Zed CLI; an editor set
    with SDLC_EDITOR gets no extra flag (it carries its own)."""
    if os.environ.get("SDLC_EDITOR"):
        return []
    return ["-e"] if Path(cmd[0]).stem.lower() in ("zed", "zeditor") else []


def _script_json(value: Any) -> str:
    """JSON safe to place inside a <script> element (no `</script>` break-out)."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def design_system_file(name: str) -> str:
    """The text of one allowlisted design-system file, for inlining into the page; a comment when it is missing,
    unknown, outside the design-system folder, or would end the element it is inlined into."""
    if name not in DS_FILES:
        return f"/* {name}: not a design-system file */"
    path = (DESIGN_SYSTEM / name).resolve()
    if path.parent != DESIGN_SYSTEM.resolve() or not path.is_file():
        return f"/* {name}: missing from the harness home */"
    text = path.read_text(encoding="utf-8")
    if re.search(r"</(style|script)", text, re.I):
        return f"/* {name}: refused (contains a closing tag) */"
    return text


def render_page(token: str, project: str, workspace: dict[str, Any]) -> str:
    """web_page.html with its placeholders filled in ONE pass, so no inserted text is scanned for placeholders again:
    `__TOKEN__` (the per-run token), `__PROJECT__` and `__WORKSPACE__` (JSON), `/*__DS:<name>__*/` (a design-system
    file, inlined)."""
    def fill(m: re.Match[str]) -> str:
        if m.group(1):
            return design_system_file(m.group(1))
        return {"__TOKEN__": token, "__PROJECT__": _script_json(project),
                "__WORKSPACE__": _script_json(workspace)}[m.group(0)]
    return _PLACEHOLDER.sub(fill, PAGE.read_text(encoding="utf-8"))


def _int(raw: Any, default: int, low: int, high: int) -> int:
    """A query / body integer within [low, high]; None or "" gives `default`; anything else raises ValueError."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        raise ValueError(f"not an integer: {raw!r}")
    value = int(raw)
    if not low <= value <= high:
        raise ValueError(f"{value} is out of range {low}..{high}")
    return value


class Board:
    def __init__(self, profile: Path):
        from .cli import ticket_root
        from .profile import _abs, load_profile
        self.profile = profile
        prof = load_profile(profile)
        self.root = ticket_root(prof, profile.parent).resolve()
        self.allowed = [self.root] + [Path(_abs(r["path"], prof)).resolve() for r in prof["repositories"]]
        self.project = prof["project"].get("name") or prof["project"]["id"]
        self.workspace = profile.parent.resolve()  # where `.zed/` and the agents' project folders live
        self.token = secrets.token_urlsafe(16)
        self._tx_cache: dict[tuple[str, str | None, int], tuple[str, float, dict[str, Any]]] = {}
        self._tx_lock = threading.Lock()
        self._cost_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._cost_lock = threading.Lock()

    def ticket_dir(self, ticket: str) -> Path:
        """The folder of an initialised ticket directly under the ticket root; anything else (`..`, a path, an unknown
        id) is refused, so a ticket id never reaches a file outside the ticket folders."""
        tdir = (self.root / ticket).resolve()
        if not ticket or tdir.parent != self.root or tdir.name != ticket or not (tdir / "state.json").is_file():
            raise HarnessError(f"no ticket {ticket!r} under the ticket root")
        return tdir

    def session(self, ticket: str) -> Any:
        import argparse
        from .cli import Session
        self.ticket_dir(ticket)
        return Session(argparse.Namespace(profile=str(self.profile), ticket=ticket))

    def transcripts(self, ticket: str, node: str | None, max_steps: int) -> dict[str, Any]:
        """sessions.transcripts, served from cache while no log it reads changed (sessions.log_fingerprint) and no
        session it flagged running could have stopped running (sessions.running_until); at most TX_TTL_SECONDS old."""
        self.ticket_dir(ticket)
        key = (ticket, node, max_steps)
        fp = sessions.log_fingerprint(self.profile, ticket)
        now = time.time()
        with self._tx_lock:
            hit = self._tx_cache.get(key)
        if hit and hit[0] == fp and now < hit[1]:
            return hit[2]
        res = sessions.transcripts(self.profile, ticket, node, max_steps=max_steps)
        until = sessions.running_until(res)
        expires = min(now + TX_TTL_SECONDS, until) if until is not None else now + TX_TTL_SECONDS
        with self._tx_lock:
            self._tx_cache.pop(key, None)
            self._tx_cache[key] = (fp, expires, res)
            while len(self._tx_cache) > TX_CACHE_ENTRIES:
                self._tx_cache.pop(next(iter(self._tx_cache)))
        return res

    def cost(self, ticket: str | None, node: str | None, since: str | None, until: str | None) -> dict[str, Any]:
        """cost.ticket_report / cost.range_report. cost.py answers from its fingerprint-keyed cache; this layer only
        spares the fingerprint (a stat of every log) on polls closer together than COST_RECHECK_SECONDS."""
        if ticket:
            self.ticket_dir(ticket)
        elif not since:
            raise HarnessError("cost needs id (a ticket) or since (a day)")
        elif node:
            raise HarnessError("node needs id")
        key = (ticket, node, cost.parse_day(since), cost.parse_day(until))
        now = time.time()
        with self._cost_lock:
            hit = self._cost_cache.get(key)
        if hit and now - hit[0] < COST_RECHECK_SECONDS:
            return hit[1]
        res = (cost.ticket_report(self.profile, ticket, node, since=since, until=until) if ticket
               else cost.range_report(self.profile, since or "", until))
        if ticket:  # the Cost tab's budget line: G8 of the next pending node (None without a budgets block)
            from .state import next_pending
            s = self.session(ticket)
            res = {**res, "budget": board.budget_view(s, next_pending(s.state, s.order))}
        with self._cost_lock:
            self._cost_cache.pop(key, None)
            self._cost_cache[key] = (now, res)
            while len(self._cost_cache) > TX_CACHE_ENTRIES:
                self._cost_cache.pop(next(iter(self._cost_cache)))
        return res

    def ticket_file(self, ticket: str, name: str) -> Path:
        tdir = (self.root / ticket).resolve()
        path = (tdir / name).resolve()
        if tdir.parent != self.root or not path.is_relative_to(tdir) or not path.is_file():
            raise HarnessError("not a file of this ticket")
        return path

    def openable(self, raw: str) -> Path:
        path = Path(raw).resolve()
        if not any(path.is_relative_to(a) for a in self.allowed) or not path.exists():
            raise HarnessError("only files under the ticket root or a profile repository can be opened")
        return path

    def focus_argv(self) -> list[str] | None:
        """The command `POST /api/focus` runs: the editor CLI with the workspace folder and nothing else (no `-e`, so
        Zed focuses the window that already has the workspace open instead of adding it to the front window)."""
        cmd = editor_command()
        return [*cmd, str(self.workspace)] if cmd else None

    def page_info(self) -> dict[str, Any]:
        return {"path": str(self.workspace), "focus": self.focus_argv()}


def make_handler(b: Board):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:  # quiet; the board polls every few seconds
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, board.dumps(obj), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    html = render_page(b.token, b.project, b.page_info())
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                elif url.path == "/api/tickets":
                    self._json({"contractVersion": CONTRACT_VERSION, "project": b.project, "editor": bool(editor_command()),
                                "tickets": board.list_tickets(b.profile)})
                elif url.path == "/api/ticket":
                    b.ticket_dir(q["id"])
                    self._json(board.summarize_ticket(b.profile, q["id"]))
                elif url.path == "/api/ticket-detail":
                    events = _int(q.get("events"), 40, 1, 10000)
                    self._json(board.summarize(b.session(q["id"]), events_limit=events, detail=True))
                elif url.path == "/api/node":
                    self._json(board.node_view(b.session(q["id"]), q["node"]))
                elif url.path == "/api/transcripts":
                    steps = _int(q.get("maxSteps"), sessions.MAX_STEPS, 1, MAX_STEPS_LIMIT)
                    self._json(b.transcripts(q["id"], q.get("node") or None, steps))
                elif url.path == "/api/cost":
                    self._json(b.cost(q.get("id") or None, q.get("node") or None, q.get("since") or None,
                                      q.get("until") or None))
                elif url.path == "/api/inflight":
                    self._json(board.inflight_report(b.profile))
                elif url.path == "/api/file":
                    path = b.ticket_file(q["id"], q["name"])
                    data = path.read_bytes()[:MAX_FILE_BYTES]
                    self._send(200, data, "text/plain; charset=utf-8")
                else:
                    self._json({"error": "not found"}, 404)
            except KeyError as exc:
                self._json({"error": f"missing parameter {exc}"}, 400)
            except (ValueError, HarnessError) as exc:
                self._json({"error": str(exc)}, 400)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route not in ("/api/open", "/api/focus"):
                self._json({"error": "not found"}, 404)
                return
            if not secrets.compare_digest(self.headers.get("X-Kaneki-Token") or "", b.token):
                self._json({"error": "bad token"}, 403)
                return
            if route == "/api/focus":
                self.rfile.read(_int(self.headers.get("Content-Length"), 0, 0, 1 << 16))  # ignored: nothing to choose
                argv = b.focus_argv()
                if not argv:
                    self._json({"error": "no editor CLI found: install Zed's `zed` CLI or set SDLC_EDITOR"}, 400)
                    return
                subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._json({"ok": True, "focused": str(b.workspace), "argv": argv})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                path = b.openable(str(body.get("path", "")))
                cmd = editor_command()
                if not cmd:
                    raise HarnessError("no editor CLI found: install Zed's `zed` CLI or set SDLC_EDITOR")
                target = str(path)
                if body.get("line") not in (None, ""):
                    target += f":{_int(body['line'], 1, 1, 10**9)}"
                    if body.get("column") not in (None, ""):
                        target += f":{_int(body['column'], 1, 1, 10**9)}"
                subprocess.Popen([*cmd, *editor_flags(cmd), target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._json({"ok": True, "opened": target})
            except (ValueError, TypeError, HarnessError) as exc:
                self._json({"error": str(exc)}, 400)

    return Handler


def serve(profile: Path, port: int, open_browser: bool) -> dict[str, Any]:
    b = Board(profile)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(b))
    except OSError as exc:
        raise HarnessError(f"cannot listen on 127.0.0.1:{port}: {exc} (is the board already running? try --port)") from exc
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(json.dumps({"ok": True, "url": url, "ticketRoot": str(b.root), "editor": editor_command()}), flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return {"ok": True, "stopped": url}
