"""The board API of `sdlc web` (M2 steps 2 and 4): ticket-detail, node, transcripts (cached), in-flight units and
`POST /api/open` at a line. Every endpoint is read-only, confined to the ticket folders, and additive to contract v1.

Session logs here are SYNTHETIC fixtures (test_sessions helpers); no real conversation text."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402
from test_sessions import claude_rows, write_jsonl  # noqa: E402

from harness import board, sessions, web  # noqa: E402
from harness.cli import Session  # noqa: E402
from harness.contract import CONTRACT_VERSION  # noqa: E402


class ApiBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.homes = self.tmp / "homes"  # no real agent logs are ever read
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.homes / "claude"),
                                               "CODEX_HOME": str(self.homes / "codex"),
                                               "ANTIGRAVITY_CLI_ROOT": str(self.homes / "agy")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, ticket: str) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=ticket))

    def init(self, ticket: str, kind: str = "bug", lane: str = "full") -> None:
        code, res = run("init", "--ticket", ticket, "--kind", kind, "--lane", lane, *self.p)
        self.assertEqual(code, 0, res)

    def unit_of(self, ticket: str, node: str) -> str:
        code, plan = run("plan", "--ticket", ticket, "--node", node, *self.p)
        self.assertEqual(code, 0, plan)
        return plan["units"][0]["id"]

    def packet(self, ticket: str, node: str, unit: str) -> dict:
        code, pk = run("packet", "--ticket", ticket, "--node", node, "--unit", unit, *self.p)
        self.assertEqual(code, 0, pk)
        return pk

    def record(self, ticket: str, node: str, unit: str, pk: dict) -> None:
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n")
        code, rec = run("record", "--ticket", ticket, "--node", node, "--unit", unit, "--result", pk["resultPath"],
                        "--model", pk["model"], "--tokens", "100", *self.p)
        self.assertEqual(code, 0, rec)

    def snapshot(self) -> dict[str, str]:
        root = self.tmp / "tickets"
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}


class InFlightTests(ApiBase):
    def test_rendered_not_recorded_then_recorded_then_next_round(self) -> None:
        t = "IF-1"
        self.init(t)
        unit = self.unit_of(t, "prepare")
        self.assertEqual(board.in_flight(self.session(t)), [])
        pk = self.packet(t, "prepare", unit)
        rows = board.in_flight(self.session(t), now=time.time())
        self.assertEqual(len(rows), 1, rows)
        r = rows[0]
        self.assertEqual((r["ticket"], r["node"], r["unit"], r["generation"], r["round"], r["dispatchId"]),
                         (t, "prepare", unit, 1, 1, pk["dispatchId"]))
        self.assertEqual((r["tier"], r["model"]), (pk["tier"], pk["model"]))  # read from the packet's contract line
        self.assertEqual(r["host"], pk["execution"]["host"])
        self.assertEqual(Path(r["packetPath"]), Path(pk["path"]))
        self.assertFalse(r["resultExists"])
        self.assertGreaterEqual(r["ageSeconds"], 0)
        self.assertNotIn("ageSeconds", board.in_flight(self.session(t))[0])  # deterministic without `now`
        # the summary (ticket rail, sdlc tickets, STATUS.md) carries it
        code, res = run("tickets", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual([x["dispatchId"] for x in res["tickets"][0]["inFlight"]], [pk["dispatchId"]])
        self.assertIn("**In flight**", (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8"))

        self.record(t, "prepare", unit, pk)
        self.assertEqual(board.in_flight(self.session(t)), [])
        self.assertNotIn("**In flight**", (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8"))
        pk2 = self.packet(t, "prepare", unit)  # round 2 of the same generation
        self.assertEqual([(x["round"], x["dispatchId"]) for x in board.in_flight(self.session(t))],
                         [(2, pk2["dispatchId"])])

    def test_superseded_generation_and_done_node_are_ignored(self) -> None:
        t = "IF-2"
        self.init(t)
        unit = self.unit_of(t, "prepare")
        self.record(t, "prepare", unit, self.packet(t, "prepare", unit))
        self.packet(t, "prepare", unit)  # round 2 at generation 1, never recorded
        self.assertEqual([x["round"] for x in board.in_flight(self.session(t))], [2])
        code, res = run("invalidate", "--ticket", t, "--node", "prepare", "--reason", "redo", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.session(t).state["nodes"]["prepare"]["generation"], 2)
        self.assertEqual(board.in_flight(self.session(t)), [], "a generation-1 packet is superseded")
        pk = self.packet(t, "prepare", unit)
        self.assertEqual([x["generation"] for x in board.in_flight(self.session(t))], [2])
        self.record(t, "prepare", unit, pk)
        self.complete(t, "prepare")
        s = self.session(t)
        gen = s.state["nodes"]["prepare"]["generation"]
        (self.tdir(t) / "packets" / f"prepare.g{gen}.{unit}.r9.md").write_text(Path(pk["path"]).read_text())
        self.assertEqual(board.in_flight(s), [], "a packet left on a done node is not in flight")

    def test_stray_files_and_unknown_nodes(self) -> None:
        t = "IF-3"
        self.init(t)
        pdir = self.tdir(t) / "packets"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "nosuchnode.g1.worker.r1.md").write_text("x")
        (pdir / "notes.md").write_text("x")
        (pdir / "prepare.g1.worker.r1.rules.json").write_text("[]")
        self.assertEqual(board.in_flight(self.session(t)), [])

    def test_lock_and_report(self) -> None:
        self.init("IF-4")
        unit = self.unit_of("IF-4", "prepare")
        pk = self.packet("IF-4", "prepare", unit)
        self.init("IF-5", kind="feature", lane="lite")
        self.write_artifacts("IF-5", "scope")  # lite scope done by the controller: waits for the user's approval
        self.init("IF-6", kind="feature", lane="lite")  # nothing to report: not listed
        lock = self.tdir("IF-6") / ".lock"
        self.assertIsNone(board.ticket_lock(self.tdir("IF-6")))
        lock.mkdir()
        since = time.time() - 30
        (lock / "owner.json").write_text(json.dumps({"pid": 4242, "command": "record", "since": since}))
        before = self.snapshot()
        rep = board.inflight_report(self.profile)
        self.assertEqual(self.snapshot(), before, "the report wrote to the ticket folder")
        self.assertEqual(rep["contractVersion"], CONTRACT_VERSION)
        self.assertEqual([u["dispatchId"] for u in rep["units"]], [pk["dispatchId"]])
        by = {x["ticket"]: x for x in rep["tickets"]}
        self.assertEqual(set(by), {"IF-4", "IF-5", "IF-6"})
        self.assertEqual(by["IF-4"]["inFlight"], 1)
        self.assertEqual((by["IF-5"]["waitingOn"], by["IF-5"]["waitingReason"]), ("user", "user-boundary"))
        self.assertEqual(rep["needsYou"], 1)
        self.assertEqual((by["IF-6"]["lock"]["command"], by["IF-6"]["lock"]["pid"]), ("record", 4242))
        self.assertGreaterEqual(by["IF-6"]["lock"]["ageSeconds"], 29)
        self.assertIsNone(by["IF-4"]["lock"])
        self.assertTrue(by["IF-4"]["lastEventAt"])


class BoardApiTests(ApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.t = "API-1"
        self.init(self.t)
        self.unit = self.unit_of(self.t, "prepare")
        self.pk = self.packet(self.t, "prepare", self.unit)
        self.record(self.t, "prepare", self.unit, self.pk)
        self.init("API-2", kind="feature", lane="lite")
        self.write_artifacts("API-2", "scope")
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop("SDLC_EDITOR", None)
        self.addCleanup(env.stop)
        self.board = web.Board(self.profile)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.board))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def get(self, path: str, **query: str) -> tuple[int, dict]:
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def post(self, body: dict, token: str | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + "/api/open", data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Kaneki-Token": self.board.token if token is None else token})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    # --- GET -------------------------------------------------------------------------------------------
    def test_ticket_detail(self) -> None:
        code, res = self.get("/api/ticket-detail", id=self.t)
        self.assertEqual(code, 200, res)
        self.assertEqual(res, board.summarize(self.session(self.t), detail=True))
        self.assertEqual(res["hosts"], ["profile"])
        self.assertIn("trace", res)
        self.assertIn("plannedUnits", res["nodes"][0])
        self.assertIn("inFlight", res)

    def test_node(self) -> None:
        code, res = self.get("/api/node", id=self.t, node="prepare")
        self.assertEqual(code, 200, res)
        self.assertEqual(res["contractVersion"], CONTRACT_VERSION)
        u = next(x for x in res["units"] if x["unit"] == self.unit)
        self.assertEqual((u["rounds"], u["tokens"], u["generation"], u["status"], u["host"], u["recordedModel"]),
                         (1, 100, 1, "ok", self.pk["execution"]["host"], self.pk["model"]))
        self.assertEqual(Path(u["result"]), Path(self.pk["resultPath"]))
        self.assertTrue(u["resultExists"])
        self.assertIsNone(u["inFlight"])
        self.assertEqual(len(res["records"]), 1)
        self.assertTrue(all({"name", "path", "exists"} <= set(a) for a in res["artifacts"]))
        self.assertIn("record", [e["event"] for e in res["events"]])
        self.assertTrue(all(e.get("node") == "prepare" or "prepare" in (e.get("stale") or []) for e in res["events"]))
        self.assertIsNone(res["gateRequest"])  # the agent still owes work on prepare
        self.assertIn("plannedUnits", res)
        # a node that waits on the user carries the gate request
        code, res = self.get("/api/node", id="API-2", node="scope")
        self.assertEqual(code, 200, res)
        self.assertEqual(res["waiting"]["on"], "user")
        self.assertEqual(res["gateRequest"]["node"], "scope")
        self.assertTrue(any(c["id"] == "approve" for c in res["gateRequest"]["choices"]))
        code, res = self.get("/api/node", id=self.t, node="nope")
        self.assertEqual(code, 400)
        code, res = self.get("/api/node", id=self.t)
        self.assertEqual((code, res["error"]), (400, "missing parameter 'node'"))

    def test_node_in_flight(self) -> None:
        pk = self.packet(self.t, "prepare", self.unit)
        code, res = self.get("/api/node", id=self.t, node="prepare")
        self.assertEqual(code, 200, res)
        self.assertEqual([x["dispatchId"] for x in res["inFlight"]], [pk["dispatchId"]])
        self.assertEqual(next(x for x in res["units"] if x["unit"] == self.unit)["inFlight"]["round"], 2)

    def test_inflight(self) -> None:
        pk = self.packet(self.t, "prepare", self.unit)
        code, res = self.get("/api/inflight")
        self.assertEqual(code, 200, res)
        self.assertEqual([u["dispatchId"] for u in res["units"]], [pk["dispatchId"]])
        self.assertIn("ageSeconds", res["units"][0])
        self.assertEqual(res["needsYou"], 1)
        self.assertEqual({x["ticket"] for x in res["tickets"]}, {self.t, "API-2"})

    def test_transcripts_and_cache(self) -> None:
        ws = str(self.profile.parent.resolve())
        line = f"Read the packet at {self.pk['path']} (sha256 {self.pk['packetDigest']}) and execute it."
        log = write_jsonl(self.homes / "claude" / "projects" / sessions.claude_slug(ws) / "w-1.jsonl",
                          claude_rows("w-1", ws, line))
        old = time.time() - 3600  # not running: only a log change can refresh it
        os.utime(log, (old, old))
        with mock.patch.object(sessions, "collect", wraps=sessions.collect) as collect:
            code, res = self.get("/api/transcripts", id=self.t)
            self.assertEqual(code, 200, res)
            self.assertEqual([u["sessionId"] for u in res["nodes"]["prepare"]["units"]], ["w-1"])
            self.assertNotIn("steps", res["nodes"]["prepare"]["units"][0])
            self.assertEqual(self.get("/api/transcripts", id=self.t), (200, res))
            self.assertEqual(collect.call_count, 1, "the second poll is served from cache")
            code, one = self.get("/api/transcripts", id=self.t, node="prepare", maxSteps="2")
            self.assertEqual(code, 200, one)
            self.assertEqual(list(one["nodes"]), ["prepare"])
            self.assertLessEqual(len(one["nodes"]["prepare"]["units"][0]["steps"]), 2)
            self.assertEqual(collect.call_count, 2, "node and maxSteps are part of the cache key")
            write_jsonl(log, claude_rows("w-1", ws, line, tool_cmd="git status"))
            os.utime(log, (old + 5, old + 5))
            self.get("/api/transcripts", id=self.t)
            self.assertEqual(collect.call_count, 3, "a changed log refreshes the answer")
            with mock.patch.object(web.time, "time", return_value=time.time() + web.TX_TTL_SECONDS + 1):
                self.get("/api/transcripts", id=self.t)
            self.assertEqual(collect.call_count, 4, "an answer older than the TTL is re-read")
        for bad in ({"id": self.t, "maxSteps": "0"}, {"id": self.t, "maxSteps": "x"}, {"id": self.t, "node": "nope"}):
            self.assertEqual(self.get("/api/transcripts", **bad)[0], 400, bad)

    def test_running_session_expires_from_cache(self) -> None:
        at = "2026-11-23T10:00:00+00:00"
        res = {"nodes": {"a": {"units": [{"running": True, "updatedAt": at}], "controller": [{"running": False}]}}}
        self.assertEqual(sessions.running_until(res), sessions._epoch(at) + sessions.RUNNING_SECONDS + 1)
        self.assertIsNone(sessions.running_until({"nodes": {"a": {"units": [], "controller": []}}}))
        with mock.patch.object(sessions, "transcripts", return_value=res) as tx, \
                mock.patch.object(sessions, "log_fingerprint", return_value="same"):
            with mock.patch.object(web.time, "time", return_value=sessions._epoch(at) + 10):
                self.board.transcripts(self.t, None, 50)
                self.board.transcripts(self.t, None, 50)
            self.assertEqual(tx.call_count, 1)
            with mock.patch.object(web.time, "time", return_value=sessions._epoch(at) + sessions.RUNNING_SECONDS + 2):
                self.board.transcripts(self.t, None, 50)
            self.assertEqual(tx.call_count, 2, "a running flag that would flip refreshes the answer")

    def test_fingerprint_is_stat_only_and_tracks_state(self) -> None:
        fp = sessions.log_fingerprint(self.profile, self.t)
        self.assertEqual(fp, sessions.log_fingerprint(self.profile, self.t))
        pk = self.packet(self.t, "prepare", self.unit)
        self.record(self.t, "prepare", self.unit, pk)
        self.assertNotEqual(fp, sessions.log_fingerprint(self.profile, self.t), "a new record changes state.json")

    def test_read_only_and_confined(self) -> None:
        before = self.snapshot()
        for path, q in (("/api/ticket-detail", {"id": self.t}), ("/api/node", {"id": self.t, "node": "prepare"}),
                        ("/api/node", {"id": "API-2", "node": "scope"}), ("/api/transcripts", {"id": self.t}),
                        ("/api/inflight", {}), ("/api/ticket", {"id": self.t}), ("/api/tickets", {})):
            self.assertEqual(self.get(path, **q)[0], 200, path)
        self.assertEqual(self.snapshot(), before, "a GET wrote to the ticket folder")
        outside = self.tmp / "outside"
        (outside / "T").mkdir(parents=True)
        (outside / "T" / "state.json").write_text("{}")
        for bad in ("..", "../outside/T", str(outside / "T"), ".", "", "NOPE", "API-1/../API-2"):
            for path in ("/api/ticket-detail", "/api/node", "/api/transcripts", "/api/ticket"):
                code, res = self.get(path, id=bad, node="prepare")
                self.assertEqual(code, 400, (path, bad, res))

    # --- POST /api/open ------------------------------------------------------------------------------------
    def test_open_at_line_and_column(self) -> None:
        target = self.tdir(self.t) / "STATUS.md"
        with mock.patch.object(web, "editor_command", return_value=["/opt/zed/bin/zed"]), \
                mock.patch.object(web.subprocess, "Popen") as popen:
            self.assertEqual(self.post({"path": str(target), "line": 3}, token="wrong")[0], 403)
            self.assertEqual(self.post({"path": str(target), "line": 3}, token="")[0], 403)
            popen.assert_not_called()
            code, res = self.post({"path": str(target), "line": 12, "column": 4})
            self.assertEqual(code, 200, res)
            self.assertEqual(res["opened"], f"{target.resolve()}:12:4")
            self.assertEqual(popen.call_args[0][0], ["/opt/zed/bin/zed", "-e", f"{target.resolve()}:12:4"])
            code, res = self.post({"path": str(target), "line": "7"})
            self.assertEqual((code, res["opened"]), (200, f"{target.resolve()}:7"))
            code, res = self.post({"path": str(target)})
            self.assertEqual((code, res["opened"]), (200, str(target.resolve())))
            calls = popen.call_count
            for body in ({"path": str(target), "line": 0}, {"path": str(target), "line": "x"},
                         {"path": str(target), "line": [1]}, {"path": str(target), "line": 2, "column": -1},
                         {"path": str(target), "line": True}):
                self.assertEqual(self.post(body)[0], 400, body)
            for escape in (str(self.tmp / "sdlc.profile.json"), str(self.tdir(self.t) / ".." / ".." / "sdlc.profile.json")):
                self.assertEqual(self.post({"path": escape, "line": 1})[0], 400, escape)
            self.assertEqual(popen.call_count, calls, "nothing outside the ticket root / repositories opens")
        with mock.patch.dict(os.environ, {"SDLC_EDITOR": "code -g"}), mock.patch.object(web.subprocess, "Popen") as popen:
            self.assertEqual(self.post({"path": str(target), "line": 2})[0], 200)
            self.assertEqual(popen.call_args[0][0], ["code", "-g", f"{target.resolve()}:2"])  # no -e for other editors


class ContractTests(unittest.TestCase):
    def test_contract_version_unchanged(self) -> None:
        self.assertEqual(CONTRACT_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
