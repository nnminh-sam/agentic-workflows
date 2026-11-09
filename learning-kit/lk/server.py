"""Local quiz server: serves web/ and a tiny JSON API for one session. Binds to 127.0.0.1.

GET  /api/session   questions (no answers) + saved answers, so a reload resumes
POST /api/answer    {uid, response, confidence, time_ms}
POST /api/finish    start grading in the background
GET  /api/result    {status, ...results} — poll until status is applied / grade_failed
POST /api/done      shut the server down (the CLI then prints the summary)
"""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths, sessions
from .store import LkError

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


class QuizServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, sid: str, grade_fn=None):
        super().__init__(addr, Handler)
        self.sid = sid
        self.grade_fn = grade_fn
        self.lock = threading.Lock()
        self.grading: threading.Thread | None = None
        self.last_seen = time.time()


class Handler(BaseHTTPRequestHandler):
    server: QuizServer

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------ helpers
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise LkError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _static(self, rel: str):
        rel = rel.lstrip("/") or "index.html"
        p = (paths.WEB_DIR / rel).resolve()
        if paths.WEB_DIR.resolve() not in p.parents or not p.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(p.name)[0] or "application/octet-stream")
                         + ("; charset=utf-8" if p.suffix in (".html", ".js", ".css") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _guard_origin(self) -> bool:
        # Reject cross-site POSTs (another page in your browser poking localhost).
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and not origin.endswith("//" + host):
            self._json({"error": "bad origin"}, 403)
            return False
        return True

    # ------------------------------------------------ routes
    def do_GET(self):
        self.server.last_seen = time.time()
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/session":
                return self._json(sessions.client_view(sessions.load(self.server.sid)))
            if path == "/api/result":
                s = sessions.load(self.server.sid)
                if s["status"] in ("applied", "grade_failed"):
                    return self._json(sessions.results_view(s))
                return self._json({"status": s["status"]})
        except LkError as e:
            return self._json({"error": str(e)}, 400)
        return self._static(path)

    def do_POST(self):
        self.server.last_seen = time.time()
        if not self._guard_origin():
            return
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if path == "/api/answer":
                with self.server.lock:
                    s = sessions.load(self.server.sid)
                    sessions.record_answer(s, body["uid"], body.get("response"), body.get("confidence"),
                                           body.get("time_ms"))
                return self._json({"ok": True})
            if path == "/api/finish":
                with self.server.lock:
                    if not (self.server.grading and self.server.grading.is_alive()):
                        self.server.grading = threading.Thread(target=self._grade, daemon=True)
                        self.server.grading.start()
                return self._json({"status": "grading"})
            if path == "/api/done":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
        except (LkError, KeyError, json.JSONDecodeError) as e:
            return self._json({"error": str(e)}, 400)
        self._json({"error": "not found"}, 404)

    def _grade(self):
        try:
            sessions.finish(self.server.sid, self.server.grade_fn)
        except LkError:
            pass  # status grade_failed + error are stored in the session file
        except Exception as e:  # never let a bug leave the UI polling forever
            s = sessions.load(self.server.sid)
            s["status"], s["error"] = "grade_failed", f"internal error: {e!r}"
            sessions.save(s)


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


def _watchdog(srv: QuizServer, idle_min: int):
    while True:
        time.sleep(30)
        if time.time() - srv.last_seen > idle_min * 60 and not (srv.grading and srv.grading.is_alive()):
            srv.shutdown()
            return


def serve(sid: str, open_browser_flag: bool = True, port: int | None = None, grade_fn=None) -> dict:
    cfg = paths.config()["server"]
    host = cfg["host"]
    srv = None
    for p in ([port] if port else [cfg["port"] + i for i in range(10)] + [0]):
        try:
            srv = QuizServer((host, p), sid, grade_fn)
            break
        except OSError:
            continue
    if srv is None:
        raise LkError("could not bind a local port")
    url = f"http://{host}:{srv.server_address[1]}/"
    print(f"QUIZ_URL={url}", flush=True)
    print(f"SESSION={sid}", flush=True)
    threading.Thread(target=_watchdog, args=(srv, cfg["idle_timeout_min"]), daemon=True).start()
    if open_browser_flag and not open_browser(url):
        print(f"Open {url} in your browser.", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return sessions.load(sid)
