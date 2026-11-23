"""The board page of `sdlc web` (M2 steps 3 and 5): the page is served self-contained (design-system files inlined from
an allowlist, placeholders filled in one pass, no external URL), and `POST /api/focus` needs the per-run token and only
ever brings the editor up on the workspace folder."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import web  # noqa: E402

EXTERNAL = re.compile(r"""(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//|@import|url\(\s*["']?\s*(?:https?:)?//"""
                      r"""|fetch\(\s*["'](?:https?:)?//""", re.I)


class PageServer(Base):
    def setUp(self) -> None:
        super().setUp()
        code, res = run("init", "--ticket", "PG-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        env = mock.patch.dict(os.environ, {"SDLC_EDITOR": "fake-zed --flag"})
        env.start()
        self.addCleanup(env.stop)
        self.board = web.Board(self.profile)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.board))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def page(self) -> str:
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.headers["Content-Type"], "text/html; charset=utf-8")
            return r.read().decode("utf-8")

    def post(self, path: str, body: object, token: str | None = None) -> tuple[int, dict]:
        headers = {} if token is None else {"X-Kaneki-Token": token}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class PageTests(PageServer):
    def test_page_is_self_contained_with_the_token(self) -> None:
        html = self.page()
        self.assertIn(f'var TOKEN = "{self.board.token}";', html)
        self.assertIn('var PROJECT = "Demo";', html)
        for left in ("__TOKEN__", "__PROJECT__", "__WORKSPACE__", "__DS:"):
            self.assertNotIn(left, html)
        # the three design-system files are inlined, not linked
        ds = web.DESIGN_SYSTEM
        for name in web.DS_FILES:
            self.assertIn((ds / name).read_text(encoding="utf-8").strip()[:200], html, name)
        self.assertNotIn("<link", html)
        self.assertIsNone(EXTERNAL.search(html), EXTERNAL.search(html) and EXTERNAL.search(html).group(0))
        self.assertNotRegex(html, r"<script[^>]+src=")
        # the workspace and the focus command the dialog shows are the ones the server will run
        m = re.search(r"var WORKSPACE = (\{.*?\});", html)
        self.assertIsNotNone(m)
        info = json.loads(m.group(1))
        self.assertEqual(Path(info["path"]), self.profile.parent.resolve())
        self.assertEqual(info["focus"], self.board.focus_argv())

    def test_placeholders_are_filled_in_one_pass_and_escaped(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="sdlc-page-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        page = tmp / "page.html"
        page.write_text("<script>var P = __PROJECT__; var T = '__TOKEN__';</script><style>/*__DS:tokens.css__*/</style>"
                        "<style>/*__DS:secrets.txt__*/</style>", encoding="utf-8")
        with mock.patch.object(web, "PAGE", page):
            out = web.render_page("tok/*__DS:tokens.css__*/", "</script><b>/*__DS:icons.js__*/__TOKEN__", {"path": "x"})
        self.assertNotIn("</script><b>", out, "a project name cannot end the script element")
        self.assertIn("\\u003c/script\\u003e", out)
        self.assertEqual(out.count("KanekiIcons"), 0, "text that was inserted is not scanned for markers again")
        self.assertIn("tok/*__DS:tokens.css__*/", out)
        self.assertIn("/* secrets.txt: not a design-system file */", out)
        self.assertEqual(out.count(":root"), (web.DESIGN_SYSTEM / "tokens.css").read_text(encoding="utf-8").count(":root"))

    def test_design_system_files_are_confined(self) -> None:
        self.assertIn("not a design-system file", web.design_system_file("../harness/web.py"))
        self.assertIn("not a design-system file", web.design_system_file("gallery.html"))
        tmp = Path(tempfile.mkdtemp(prefix="sdlc-ds-"))
        try:
            (tmp / "tokens.css").write_text("a{}</style><script>alert(1)</script>", encoding="utf-8")
            with mock.patch.object(web, "DESIGN_SYSTEM", tmp):
                self.assertIn("refused", web.design_system_file("tokens.css"))
                self.assertIn("missing", web.design_system_file("icons.js"))
        finally:
            (tmp / "tokens.css").unlink()
            tmp.rmdir()

    def test_no_static_route(self) -> None:
        for path in ("/tokens.css", "/app/design-system/tokens.css", "/ds/tokens.css", "/web_page.html"):
            try:
                urllib.request.urlopen(self.base + path)
                self.fail(path)
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404, path)


class FocusTests(PageServer):
    def test_needs_the_token(self) -> None:
        with mock.patch.object(web.subprocess, "Popen") as popen:
            self.assertEqual(self.post("/api/focus", {})[0], 403)
            self.assertEqual(self.post("/api/focus", {}, token="nope")[0], 403)
            self.assertEqual(self.post("/api/focus", {}, token="")[0], 403)
            popen.assert_not_called()
        try:
            urllib.request.urlopen(self.base + "/api/focus")
            self.fail("GET /api/focus")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_only_opens_the_workspace(self) -> None:
        ws = str(self.profile.parent.resolve())
        with mock.patch.object(web.subprocess, "Popen") as popen:
            for body in ({}, {"path": str(self.repo / "main.py")}, {"path": "C:/Windows", "argv": ["calc"]}, ["x"]):
                code, res = self.post("/api/focus", body, token=self.board.token)
                self.assertEqual(code, 200, res)
                self.assertEqual(res["focused"], ws)
            self.assertEqual(popen.call_count, 4)
            for call in popen.call_args_list:
                self.assertEqual(call.args[0], ["fake-zed", "--flag", ws])

    def test_no_editor(self) -> None:
        with mock.patch.object(web, "editor_command", return_value=None), \
                mock.patch.object(web.subprocess, "Popen") as popen:
            code, res = self.post("/api/focus", {}, token=self.board.token)
            self.assertEqual(code, 400, res)
            self.assertIn("no editor CLI", res["error"])
            popen.assert_not_called()

    def test_auto_detected_zed_gets_no_existing_flag(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SDLC_EDITOR", None)
            with mock.patch.object(web.shutil, "which", side_effect=lambda n: "/opt/zed/bin/zed" if n == "zed" else None):
                self.assertEqual(self.board.focus_argv(), ["/opt/zed/bin/zed", str(self.profile.parent.resolve())])


if __name__ == "__main__":
    unittest.main()
