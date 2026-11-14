"""The local web app: library (paths, topics, docs, sessions) + the quiz, over one JSON API.

Two ways to run it, same app and routes:
  lk web     long-lived; registers itself in <home>/server.json so `lk serve` can reuse it.
  lk serve   one session (what the skills call): reuses a running `lk web` if there is one, else starts
             its own server that exits when that session is closed or after the idle timeout. Either way
             it prints SESSION_RESULT=<json> when the learner closes the session.

Every route is a thin call into service.py. Binds 127.0.0.1 only; requests must carry a local Host header
(blocks DNS rebinding) and POSTs a same-origin Origin (blocks cross-site form posts).

GET  /api/health | /api/overview | /api/paths | /api/paths/<slug> | /api/topics | /api/topics/<slug>
GET  /api/topics/<slug>/doc?file=research.md       raw Markdown
GET  /read/<slug>?file=research.md                 the doc rendered as a page (lk/reader.py)
GET  /api/sessions?topic=&mode=&limit=             history
POST /api/sessions {kind, topic|path, milestone, n, limit}
GET  /api/sessions/<id>                            questions without answers + saved answers
POST /api/sessions/<id>/answer {uid, response, confidence, time_ms}
POST /api/sessions/<id>/finish                     grade in the background
GET  /api/sessions/<id>/result                     poll until status applied / grade_failed
POST /api/sessions/<id>/done                       learner closed it (ends `lk serve`)
GET  /api/recall | /api/mistakes?topic=&limit= | /api/sources?q=
"""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import paths, service, sessions
from .lock import locked
from .store import LkError, now_iso, read_json, write_json

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

SEG = r"([^/]+)"


def _q(q: dict, k: str, default=None):
    return q.get(k, default)


# (method, pattern, handler(server, match, query, body)) — handlers return JSON-able data.
ROUTES = [
    ("GET", rf"/api/health", lambda s, m, q, b: service.health()),
    ("GET", rf"/api/overview", lambda s, m, q, b: service.overview()),
    ("GET", rf"/api/paths", lambda s, m, q, b: service.list_paths()),
    ("GET", rf"/api/paths/{SEG}", lambda s, m, q, b: service.get_path(m[1])),
    ("GET", rf"/api/topics", lambda s, m, q, b: service.list_topics()),
    ("GET", rf"/api/topics/{SEG}", lambda s, m, q, b: service.get_topic(m[1])),
    ("GET", rf"/api/topics/{SEG}/doc", lambda s, m, q, b: service.read_doc(m[1], _q(q, "file"))),
    ("GET", rf"/api/sessions", lambda s, m, q, b: service.list_sessions(_q(q, "topic"), _q(q, "mode"),
                                                                         _q(q, "limit", 50))),
    ("GET", rf"/api/sessions/{SEG}", lambda s, m, q, b: service.session_view(m[1])),
    ("GET", rf"/api/sessions/{SEG}/result", lambda s, m, q, b: service.result(m[1])),
    ("GET", rf"/api/recall", lambda s, m, q, b: service.recall_due(_q(q, "limit", 30))),
    ("GET", rf"/api/mistakes", lambda s, m, q, b: service.mistakes(_q(q, "topic"), _q(q, "limit", 50))),
    ("GET", rf"/api/sources", lambda s, m, q, b: service.search_sources(_q(q, "q", ""), _q(q, "limit", 10))),
    ("POST", rf"/api/sessions", lambda s, m, q, b: service.start_session(
        b.get("kind"), topic=b.get("topic"), path=b.get("path"), milestone=b.get("milestone"),
        n=b.get("n", 10), limit=b.get("limit"))),
    ("POST", rf"/api/sessions/{SEG}/answer", lambda s, m, q, b: service.answer(
        m[1], b["uid"], b.get("response"), b.get("confidence"), b.get("time_ms"))),
    ("POST", rf"/api/sessions/{SEG}/finish", lambda s, m, q, b: s.start_grading(m[1])),
    ("POST", rf"/api/sessions/{SEG}/done", lambda s, m, q, b: s.close(m[1])),
]
ROUTES = [(meth, re.compile(f"^{pat}$"), fn) for meth, pat, fn in ROUTES]


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, watch: str | None = None, grade_fn=None):
        super().__init__(addr, Handler)
        self.watch = watch          # `lk serve`: the session whose close ends this server
        self.grade_fn = grade_fn
        self.lock = threading.Lock()
        self.grading: dict[str, threading.Thread] = {}
        self.last_seen = time.time()
        port = self.server_address[1]
        self.allowed_hosts = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]", addr[0])}

    @property
    def url(self) -> str:
        return f"http://{self.server_address[0]}:{self.server_address[1]}/"

    def busy(self) -> bool:
        return any(t.is_alive() for t in self.grading.values())

    def start_grading(self, session: str) -> dict:
        sid = sessions.load(service.sid(session))["id"]  # 400 on a bad or unknown id
        with self.lock:
            t = self.grading.get(sid)
            if not (t and t.is_alive()):
                t = threading.Thread(target=self._grade, args=(sid,), daemon=True)
                self.grading[sid] = t
                t.start()
        return {"status": "grading"}

    def _grade(self, sid: str) -> None:
        try:
            service.finish(sid, self.grade_fn)
        except LkError:
            pass  # status grade_failed + error are stored in the session file
        except Exception as e:  # never let a bug leave the UI polling forever
            with locked():
                s = sessions.load(sid)
                s["status"], s["error"] = "grade_failed", f"internal error: {e!r}"
                sessions.save(s)

    def close(self, session: str) -> dict:
        b = service.close_session(session)
        if self.watch and b["id"] == self.watch:
            threading.Thread(target=self.shutdown, daemon=True).start()
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server: AppServer

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------ helpers
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _html(self, text: str, code=200):
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise LkError("request too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise LkError("body must be a JSON object")
        return data

    def _static(self, rel: str):
        rel = rel.lstrip("/") or "index.html"
        p = (paths.WEB_DIR / rel).resolve()
        if paths.WEB_DIR.resolve() not in p.parents or not p.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self._send(200, p.read_bytes(), ctype + ("; charset=utf-8" if p.suffix in (".html", ".js", ".css") else ""))

    def _host_ok(self) -> bool:
        # DNS rebinding: a page on evil.example resolving to 127.0.0.1 still sends Host: evil.example.
        if (self.headers.get("Host") or "").lower() in self.server.allowed_hosts:
            return True
        self._json({"error": "bad host"}, 403)
        return False

    def _origin_ok(self) -> bool:
        # Cross-site POSTs (another page in your browser poking localhost).
        origin = self.headers.get("Origin")
        if origin and not origin.endswith("//" + (self.headers.get("Host") or "")):
            self._json({"error": "bad origin"}, 403)
            return False
        return True

    def _dispatch(self, method: str):
        self.server.last_seen = time.time()
        if not self._host_ok() or (method == "POST" and not self._origin_ok()):
            return
        url = urlsplit(self.path)
        raw = url.path  # match on raw segments, decode each one: %2F can't split a segment
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET":
                m = re.match(r"^/read/([^/]+)$", raw)
                if m:
                    try:
                        return self._html(service.render_doc(unquote(m[1]), query.get("file")))
                    except LkError as e:
                        return self._html(f"<!doctype html><meta charset=utf-8><title>Not found</title>"
                                          f"<p>{_esc(str(e))}</p><p><a href='/'>Back to the library</a></p>", 404)
            if raw.startswith("/api/"):
                body = self._body() if method == "POST" else {}
                for meth, rx, fn in ROUTES:
                    m = rx.match(raw)
                    if meth == method and m:
                        args = [m[0], *(unquote(g) for g in m.groups())]
                        return self._json(fn(self.server, args, query, body))
                return self._json({"error": "not found"}, 404)
            if method == "GET":
                return self._static(unquote(raw))
            return self._json({"error": "not found"}, 404)
        except (LkError, KeyError, json.JSONDecodeError) as e:
            return self._json({"error": str(e) if not isinstance(e, KeyError) else f"missing field {e}"}, 400)
        except Exception as e:  # local single-user app: show the bug instead of a blank page
            return self._json({"error": f"internal error: {e!r}"}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- browser (platform boundary, §9a)

def is_wsl() -> bool:
    return sys.platform.startswith("linux") and "microsoft" in platform.release().lower()


def open_browser(url: str) -> bool:
    """Open the quiz in the user's GUI browser without ever hijacking the terminal.

    - WSL: the Linux side usually has no browser; hand the URL to Windows.
    - Linux/BSD without a display (SSH, container): don't open anything. webbrowser would fall
      back to a text browser (lynx/w3m) in the terminal the server is running in.
    - macOS / Windows / Linux desktop: the standard webbrowser module.
    """
    try:
        if is_wsl():
            for cmd in (["wslview", url], ["explorer.exe", url]):
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
            return False
        if sys.platform not in ("win32", "darwin") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
        return webbrowser.open(url)
    except Exception:
        return False


def open_file(path) -> bool:
    """Open a local HTML file in the GUI browser (same rules as open_browser). On WSL the Linux path is
    translated to a Windows path first, because the Windows browser can't read /home/... directly."""
    p = Path(path).resolve()
    if is_wsl():
        try:
            win = subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return False
        for cmd in (["wslview", str(p)], ["explorer.exe", win]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        return False
    return open_browser(p.as_uri())


# ---------------------------------------------------------------- running it

def registry_path() -> Path:
    return paths.home() / "server.json"


def find_running() -> str | None:
    """URL of a live `lk web` serving this data home, else None."""
    info = read_json(registry_path(), None) or {}
    url = info.get("url")
    if not url:
        return None
    try:
        with urllib.request.urlopen(url + "api/health", timeout=1.5) as r:
            h = json.loads(r.read())
    except Exception:
        return None
    return url if h.get("home") == str(paths.home()) else None


def _bind(port: int | None, watch: str | None = None, grade_fn=None) -> AppServer:
    cfg = paths.config()["server"]
    for p in ([port] if port else [cfg["port"] + i for i in range(10)] + [0]):
        try:
            return AppServer((cfg["host"], p), watch, grade_fn)
        except OSError:
            continue
    raise LkError("could not bind a local port")


def _watchdog(srv: AppServer, idle_min: int):
    while True:
        time.sleep(30)
        if time.time() - srv.last_seen > idle_min * 60 and not srv.busy():
            srv.shutdown()
            return


def _wait_closed(sid: str, idle_min: int) -> dict:
    """`lk serve` on top of a running `lk web`: wait for the learner to close the session."""
    last = time.time()
    seen = None
    try:
        while True:
            time.sleep(1)
            s = sessions.load(sid)
            if s.get("closed"):
                return s
            if s.get("updated") != seen:
                seen, last = s.get("updated"), time.time()
            elif time.time() - last > idle_min * 60 and s["status"] != "grading":
                return s
    except KeyboardInterrupt:
        return sessions.load(sid)


def serve(sid: str, open_browser_flag: bool = True, port: int | None = None, grade_fn=None) -> dict:
    """One session, for the skills: print QUIZ_URL=, block until it's closed, return the session."""
    idle = paths.config()["server"]["idle_timeout_min"]
    running = None if (port or grade_fn) else find_running()
    if running:
        url = f"{running}#/session/{sid}"
        print(f"QUIZ_URL={url}", flush=True)
        print(f"SESSION={sid}", flush=True)
        if open_browser_flag and not open_browser(url):
            print(f"Open {url} in your browser.", flush=True)
        return _wait_closed(sid, idle)
    srv = _bind(port, watch=sid, grade_fn=grade_fn)
    url = f"{srv.url}#/session/{sid}"
    print(f"QUIZ_URL={url}", flush=True)
    print(f"SESSION={sid}", flush=True)
    threading.Thread(target=_watchdog, args=(srv, idle), daemon=True).start()
    if open_browser_flag and not open_browser(url):
        print(f"Open {url} in your browser.", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return sessions.load(sid)


def web(open_browser_flag: bool = True, port: int | None = None) -> None:
    """The long-lived library app. Ctrl+C stops it."""
    existing = find_running()
    if existing and not port:
        print(f"APP_URL={existing}", flush=True)
        print("Already running for this data home.", flush=True)
        if open_browser_flag:
            open_browser(existing)
        return
    srv = _bind(port)
    reg = {"url": srv.url, "pid": os.getpid(), "home": str(paths.home()), "started": now_iso()}
    write_json(registry_path(), reg)
    print(f"APP_URL={srv.url}", flush=True)
    print("Serving the learning-kit library. Ctrl+C to stop.", flush=True)
    if open_browser_flag and not open_browser(srv.url):
        print(f"Open {srv.url} in your browser.", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if (read_json(registry_path(), None) or {}).get("pid") == os.getpid():
            try:
                registry_path().unlink()
            except OSError:
                pass


QuizServer = AppServer  # the pre-1.4 name
