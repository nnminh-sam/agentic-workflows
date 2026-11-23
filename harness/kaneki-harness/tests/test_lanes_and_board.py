"""Lite lane, lane escalation, STATUS.md, the web board and Zed tasks."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import HOME, Base, run  # noqa: E402

from harness import graph as G  # noqa: E402
from harness import web, zed  # noqa: E402
from harness.profile import build_context, load_profile  # noqa: E402

LITE = ["scope", "change", "check", "delivery-drafts", "deliverable-ready", "reflect"]


class LiteLaneTests(Base):
    def test_lite_trace(self) -> None:
        profile = load_profile(self.profile)
        graph = G.load_graph(HOME, profile, self.tmp)
        ctx = build_context(profile, "T", "feature", None, None, None)
        self.assertEqual(G.trace(graph, {**ctx, "lane": "lite"}), LITE)
        self.assertEqual(G.trace(graph, ctx)[0], "prepare")  # no lane = full (old tickets)
        with self.assertRaises(Exception):
            G.trace(graph, {**ctx, "lane": "nope"})

    def test_lite_cycle_in_place(self) -> None:
        t = "CSS-1"
        code, res = run("init", "--ticket", t, "--kind", "feature", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["trace"], LITE)
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(state["context"]["repositories"][0]["worktree"]).resolve(), self.repo.resolve())

        code, plan = run("plan", "--ticket", t, *self.p)
        self.assertEqual((plan["node"], plan["executor"], plan["boundary"]), ("scope", "controller", "user"))
        self.write_artifacts(t, "scope")
        code, res = run("gate", "--ticket", t, "--node", "scope", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("G0", res["error"])  # the user approves the scope
        self.complete(t, "scope", actor="user")

        (self.repo / "main.py").write_text("print('v2')\n")
        code, plan = run("plan", "--ticket", t, *self.p)
        self.assertEqual((plan["node"], plan["executor"], plan["units"]), ("change", "controller", []))
        self.complete(t, "change")

        code, plan = run("plan", "--ticket", t, *self.p)
        self.assertEqual(plan["executor"], "subagents")
        self.assertEqual([(u["id"], u["model"]) for u in plan["units"]], [("quick-review", "sonnet")])
        self.assertIn("scope.md", " ".join(i["file"] for i in plan["inputs"]))
        self.write_artifacts(t, "check", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "check", "--status", "needs-changes", *self.p)
        self.assertEqual((code, res["reopened"]), (0, "change"), res)
        self.complete(t, "change")
        res = self.complete(t, "check")
        self.assertIn("app", res["receipts"])

        code, plan = run("plan", "--ticket", t, *self.p)
        self.assertEqual((plan["node"], plan["executor"]), ("delivery-drafts", "controller"))
        self.complete(t, "delivery-drafts")
        self.complete(t, "deliverable-ready", actor="user")
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual((st["lane"], st["nextPending"]), ("lite", "reflect"))  # the check loop-back is a signal
        self.complete(t, "reflect")
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual((st["lane"], st["nextPending"]), ("lite", None))

        status_md = (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("lane `lite`", status_md)
        self.assertIn("[scope.md](scope.md)", status_md)
        code, res = run("tickets", *self.p)
        self.assertEqual([x["ticket"] for x in res["tickets"]], [t])
        code, res = run("status-of", "--path", str(self.tdir(t) / "scope.md"), *self.p)
        self.assertEqual((res["ticket"], res["settled"]), (t, 6))

    def test_escalate_lite_to_full(self) -> None:
        t = "CSS-2"
        run("init", "--ticket", t, "--kind", "bug", "--lane", "lite", *self.p)
        self.complete(t, "scope", actor="user")
        code, res = run("lane", "--ticket", t, "--set", "full", *self.p)
        self.assertEqual(code, 2)  # a reason is required
        code, res = run("lane", "--ticket", t, "--set", "full", "--reason", "touches the API", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["from"], res["nextPending"]), ("lite", "prepare"))
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["nodes"]["scope"]["status"], "done")  # kept, not in the trace any more
        code, res = run("status", "--ticket", t, *self.p)
        self.assertEqual((code, res["lane"]), (0, "full"), res)

    def test_lite_allows_profile_inside_the_repo(self) -> None:
        """Opening one repository in the editor: profile at its root. Full needs a worktree folder outside it; lite does not."""
        prof = json.loads(self.profile.read_text())
        prof["paths"]["ticketRoot"] = ".sdlc/tickets"
        prof["repositories"][0]["path"] = "."
        inside = self.repo / "sdlc.profile.json"
        inside.write_text(json.dumps(prof))
        code, res = run("init", "--ticket", "F-1", "--kind", "bug", "--profile", str(inside))
        self.assertEqual(code, 2)
        self.assertIn("worktreeRoot", res["error"])
        code, res = run("init", "--ticket", "L-1", "--kind", "bug", "--lane", "lite", "--profile", str(inside))
        self.assertEqual(code, 0, res)

    def test_unknown_lane_refused(self) -> None:
        code, res = run("init", "--ticket", "X", "--kind", "bug", "--lane", "tiny", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("unknown lane", res["error"])


class BoardTests(Base):
    def setUp(self) -> None:
        super().setUp()
        run("init", "--ticket", "WEB-1", "--kind", "feature", "--lane", "lite", *self.p)
        self.complete("WEB-1", "scope", actor="user")
        self.board = web.Board(self.profile)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.board))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_pages_and_api(self) -> None:
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(self.board.token.encode(), body)
        code, body = self.get("/api/tickets")
        data = json.loads(body)
        self.assertEqual(data["tickets"][0]["ticket"], "WEB-1")
        self.assertEqual(data["contractVersion"], 1)
        self.assertEqual(data["tickets"][0]["strip"][0], {
            "node": "scope", "status": "done", "verdict": "approved",  # the 3 keys this test checked before P1-T04
            "generation": 1, "boundary": "user", "waiting": {"on": "none", "node": "scope", "reason": "done"}})
        code, body = self.get("/api/ticket?id=WEB-1")
        detail = json.loads(body)
        self.assertEqual((detail["lane"], detail["nextPending"]), ("lite", "change"))
        self.assertTrue(any(e["event"] == "gate" for e in detail["events"]))
        code, body = self.get("/api/file?id=WEB-1&name=scope.md")
        self.assertEqual(code, 200)
        self.assertIn(b"## Change", body)

    def test_file_access_is_confined(self) -> None:
        for q in ("id=WEB-1&name=../../sdlc.profile.json", "id=..&name=sdlc.profile.json", "id=WEB-1&name=nope.md"):
            code, _ = self.get("/api/file?" + q)
            self.assertEqual(code, 400, q)

    def test_open_needs_token_and_allowed_path(self) -> None:
        def post(token: str, path: str) -> int:
            req = urllib.request.Request(self.base + "/api/open", data=json.dumps({"path": path}).encode(),
                                         headers={"X-Kaneki-Token": token}, method="POST")
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        self.assertEqual(post("wrong", str(self.tdir("WEB-1") / "scope.md")), 403)
        self.assertEqual(post(self.board.token, str(HOME / "README.md")), 400)  # outside the ticket root and repos


class ZedTests(Base):
    def test_setup_merges_tasks(self) -> None:
        tasks = self.tmp / ".zed" / "tasks.json"
        tasks.parent.mkdir()
        tasks.write_text(json.dumps([{"label": "mine", "command": "echo"}]))
        res = zed.setup(HOME, self.tmp)
        self.assertTrue(res["ok"], res)
        labels = [t["label"] for t in json.loads(tasks.read_text())]
        self.assertEqual(labels[0], "mine")
        self.assertIn("kaneki: board (web)", labels)
        zed.setup(HOME, self.tmp)  # idempotent
        self.assertEqual(len(json.loads(tasks.read_text())), len(labels))

    def test_jsonc_not_rewritten(self) -> None:
        tasks = self.tmp / ".zed" / "tasks.json"
        tasks.parent.mkdir()
        tasks.write_text("// my tasks\n[]\n")
        res = zed.setup(HOME, self.tmp)
        self.assertFalse(res["ok"])
        self.assertEqual(tasks.read_text(), "// my tasks\n[]\n")


if __name__ == "__main__":
    unittest.main()
