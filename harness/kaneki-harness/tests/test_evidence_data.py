"""M3: evidence as data. testcases.json / evidence.json (C1), the evidence() helper (C3), declarative steps (E2) and
the project's evidence kit (E5)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402

from harness import steps as ST  # noqa: E402

HOME = Path(__file__).resolve().parent.parent
PY = Path(sys.executable).as_posix()


class App(BaseHTTPRequestHandler):
    """A tiny app: POST /login sets a session cookie; POST /api/flags/lms toggles a flag; /learning/* is 404 when lms
    is off, else 200 with the subject title; /api/me is JSON."""
    flags = {"lms": True}

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: str, ctype: str = "text/html", cookie: str | None = None) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        return "sid=ok" in (self.headers.get("Cookie") or "")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/login":
            if body == {"email": "a@test", "password": "pw"}:
                return self._send(200, "{}", "application/json", "sid=ok; Path=/")
            return self._send(401, "{}", "application/json")
        if self.path.startswith("/api/flags/") and self._authed():
            App.flags[self.path.rsplit("/", 1)[1]] = bool(body.get("on"))
            return self._send(200, json.dumps(App.flags), "application/json")
        return self._send(404, "")

    def do_GET(self):
        if self.path == "/api/me":
            return self._send(200 if self._authed() else 401, json.dumps({"user": {"email": "a@test"}}), "application/json")
        if self.path.startswith("/learning"):
            if not self._authed():
                return self._send(302, "", cookie=None)
            if not App.flags["lms"]:
                return self._send(404, "<h1>Not found</h1>")
            return self._send(200, "<h1>FF Subject</h1>")
        return self._send(200, "ok")


KIT = {
    "schemaVersion": 1,
    "accounts": {"userA": {"emailVar": "T_EMAIL", "passwordVar": "T_PASSWORD"}},
    "login": [{"http": {"method": "POST", "url": "/login", "json": {"email": "${email}", "password": "${password}"}}}],
    "helpers": {"flag": {"description": "turn a feature on or off", "params": ["name", "on"],
                         "steps": [{"http": {"method": "POST", "url": "/api/flags/${with.name}",
                                             "json": {"on": "${with.on}"}}}]}},
}
ENV = {"T_EMAIL": "a@test", "T_PASSWORD": "pw"}


class StepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), App)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()

    def setUp(self) -> None:
        App.flags = {"lms": True}
        self.dir = Path(tempfile.mkdtemp(prefix="steps-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, doc: dict) -> Path:
        p = self.dir / f"{doc['case']}.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def runit(self, doc: dict) -> tuple[int, list[dict], str]:
        obs = self.dir / "obs.jsonl"
        obs.unlink(missing_ok=True)
        log = self.dir / "run.log"
        code = ST.run_file(self.write(doc), {"accounts": {}, "helpers": {}, **KIT}, self.base, ENV, log, obs,
                           lambda cmd, lg: None, rel=f"steps/{doc['case']}.json")
        rows = [json.loads(x) for x in obs.read_text().splitlines()] if obs.exists() else []
        return code, rows, log.read_text(encoding="utf-8")

    def test_validation_refuses_before_anything_runs(self) -> None:
        doc = {"case": "TC-9", "as": "nobody", "steps": [{"page": "click", "url": "/x"}, {"use": "missing"},
                                                         {"use": "flag", "with": {"name": "lms"}}],
               "expect": [{"check": "a1", "status": 404}, {"check": "TC-9.a7", "status": 200}]}
        problems = ST.validate(doc, KIT, ["TC-9.a1"])
        text = "\n".join(problems)
        for want in ("no account `nobody`", "page `click` needs a browser", "no helper `missing`",
                     "helper `flag` needs ['on']", "TC-9.a7 is not a check of TC-9"):
            self.assertIn(want, text)
        self.assertTrue(ST.validate({"case": "TC-9", "expect": []}, KIT))  # schema: at least one expectation
        code, rows, log = self.runit(doc)
        self.assertEqual((code, rows), (2, []))
        self.assertIn("STEP-ERROR: step file invalid", log)

    def test_feature_off_returns_404_for_every_param(self) -> None:
        doc = {"case": "TC-61", "params": ["/learning", "/learning/ff-subject"], "as": "userA",
               "setup": [{"use": "flag", "with": {"name": "lms", "on": False}}],
               "steps": [{"page": "goto", "url": "${P}"}],
               "expect": [{"check": "a1", "status": 404}, {"check": "a2", "textAbsent": ["FF Subject"]}],
               "teardown": [{"use": "flag", "with": {"name": "lms", "on": True}}]}
        code, rows, log = self.runit(doc)
        self.assertEqual(code, 0, log)
        self.assertEqual([(r["check"], r["pass"], r.get("param")) for r in rows],
                         [("TC-61.a1", True, "/learning"), ("TC-61.a2", True, "/learning"),
                          ("TC-61.a1", True, "/learning/ff-subject"), ("TC-61.a2", True, "/learning/ff-subject")])
        self.assertEqual(rows[0]["observed"], 404)
        self.assertEqual(rows[0]["ref"], "steps/TC-61.json#expect[0]")
        self.assertTrue(App.flags["lms"])  # teardown ran

    def test_product_failure_and_json_capture(self) -> None:
        doc = {"case": "TC-62", "as": "userA", "steps": [{"http": {"url": "/api/me"}},
                                                        {"capture": {"name": "email", "json": "user.email"}},
                                                        {"page": "goto", "url": "/learning/x"}],
               "expect": [{"check": "a1", "status": 404}]}
        code, rows, _ = self.runit(doc)
        self.assertEqual(code, 1)  # lms is on: 200, not 404: a real FAIL
        self.assertEqual((rows[0]["pass"], rows[0]["observed"], rows[0]["expected"]), (False, 200, 404))

    def test_server_down_is_infra(self) -> None:
        doc = {"case": "TC-63", "steps": [{"http": {"url": "http://127.0.0.1:9/nothing"}}],
               "expect": [{"check": "a1", "status": 200}]}
        code, _, log = self.runit(doc)
        self.assertEqual(code, 3)
        from harness import evidence as EV
        self.assertEqual(EV.classify(code, log), "infra")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class NodeHelperTests(unittest.TestCase):
    def test_evidence_helper_records_value_and_test_line(self) -> None:
        d = Path(tempfile.mkdtemp(prefix="evh-"))
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copy(HOME / "adapters" / "evidence" / "node" / "kaneki-evidence.mjs", d / "kaneki-evidence.mjs")
        (d / "flags.test.mjs").write_text(
            'import { evidence } from "./kaneki-evidence.mjs";\n'
            'const ok = evidence("TC-61.a1", { observed: 404, expected: 404 });\n'
            'evidence("TC-61.a2", { observed: { a: 1 }, expected: { a: 2 } });\n'
            'evidence("TC-61.a3", { observed: "x" });\n'
            'if (!ok) process.exit(5);\n', encoding="utf-8")
        out = d / "obs.jsonl"
        p = subprocess.run(["node", "flags.test.mjs"], cwd=d, env={**os.environ, "KANEKI_EVIDENCE_FILE": str(out)})
        self.assertEqual(p.returncode, 0)
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual([(r["check"], r["pass"]) for r in rows], [("TC-61.a1", True), ("TC-61.a2", False), ("TC-61.a3", None)])
        self.assertEqual(rows[0]["ref"], "flags.test.mjs:2")
        # outside a run it does nothing
        out.unlink()
        p = subprocess.run(["node", "flags.test.mjs"], cwd=d, env={k: v for k, v in os.environ.items()
                                                                   if k != "KANEKI_EVIDENCE_FILE"})
        self.assertEqual((p.returncode, out.exists()), (0, False))
        bad = subprocess.run(["node", "-e", 'import("./kaneki-evidence.mjs").then(m => m.evidence("x1", {}))'], cwd=d,
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)


class GateParserTests(Base):
    def tc(self, **over) -> dict:
        case = {"id": "TC-1", "title": "t", "surface": "web", "risk": "critical", "criteria": ["AC-1"],
                "checks": [{"id": "TC-1.a1", "expect": "404"}], "inScope": True, "reason": None,
                "run": {"kind": "repo-test", "command": "x"}}
        case.update(over)
        return {"schemaVersion": 1, "ticket": "T", "criteria": [{"id": "AC-1", "text": "a"}, {"id": "AC-2", "text": "b"}],
                "cases": [case]}

    def test_testcases_rules(self) -> None:
        from harness.evidence import testcases_problems
        probs = testcases_problems(json.dumps(self.tc(checks=[{"id": "TC-2.a1", "expect": "x"}],
                                                      run={"kind": "manual"})))
        text = "\n".join(probs)
        for want in ("belong to another case", "critical case must run", "criteria with no in-scope case: ['AC-2']"):
            self.assertIn(want, text)
        self.assertTrue(testcases_problems("{not json"))
        self.assertTrue(any(p.startswith("schema:") for p in testcases_problems(json.dumps({"schemaVersion": 1}))))

    def test_superseded_data_is_moved_aside(self) -> None:
        t = "M3-4"
        run("init", "--ticket", t, "--kind", "feature", "--lane", "full", *self.p)
        for node in ("prepare", "requirement-analysis", "impact-analysis"):
            self.complete(t, node)
        self.complete(t, "feature-plan", actor="user")
        self.complete(t, "testcase")
        self.assertTrue((self.tdir(t) / "testcases.json").is_file())
        code, res = run("invalidate", "--ticket", t, "--node", "testcase", "--reason", "wrong value", *self.p)
        self.assertEqual(code, 0, res)
        self.assertFalse((self.tdir(t) / "testcases.json").exists())  # the rerun must write it again
        self.assertTrue((self.tdir(t) / "superseded" / "testcases.g1.json").is_file())


class EndToEndTests(Base):
    """`sdlc evidence run` from testcases.json: a repo test recording through the Python helper, a step file, and a
    manual case, merged into evidence.json."""

    def test_run_from_testcases(self) -> None:
        t = "M3-1"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        tdir = self.tdir(t)
        helper = HOME / "adapters" / "evidence" / "python" / "kaneki_evidence.py"
        (self.repo / "case_test.py").write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(helper.parent)!r})\n"
            "from kaneki_evidence import evidence\n"
            "evidence('TC-1.a1', observed=404, expected=404)\n"
            "evidence('TC-2.a1', observed=200, expected=404)\n"
            "sys.exit(0 if sys.argv[1] == 'TC-1' else 1)\n", encoding="utf-8")
        (tdir / "steps").mkdir(parents=True)
        (tdir / "steps" / "TC-3.json").write_text(json.dumps(
            {"case": "TC-3", "steps": [{"http": {"url": "http://127.0.0.1:9/x"}}], "expect": [{"check": "a1", "status": 200}]}),
            encoding="utf-8")

        def case(n: int, kind: str, checks: int = 1, **run_: str) -> dict:
            return {"id": f"TC-{n}", "title": f"case {n}", "surface": "api", "risk": "critical" if n == 1 else "normal",
                    "criteria": ["AC-1"], "checks": [{"id": f"TC-{n}.a{i}", "expect": "404", "value": 404}
                                                     for i in range(1, checks + 1)],
                    "inScope": True, "reason": None, "run": {"kind": kind, **run_}}
        tc = {"schemaVersion": 1, "ticket": t, "criteria": [{"id": "AC-1", "text": "disabled pages are 404"}],
              "cases": [case(1, "repo-test", checks=2, command=f'"{PY}" case_test.py {{case}}'),
                        case(2, "repo-test", command=f'"{PY}" case_test.py {{case}}'),
                        case(3, "steps", file="steps/TC-3.json"),
                        case(4, "manual")]}
        (tdir / "testcases.json").write_text(json.dumps(tc), encoding="utf-8")
        code, res = run("evidence", "run", "--ticket", t, *self.p)
        self.assertEqual(code, 1, res)  # TC-3 hit a dead server: infra, retried, left INFRA
        ev = json.loads((tdir / "evidence.json").read_text(encoding="utf-8"))["checks"]
        self.assertEqual(ev["TC-1.a1"]["status"], "PASS")
        self.assertEqual(ev["TC-1.a1"]["source"]["ref"], "case_test.py:4")
        self.assertEqual(ev["TC-1.a2"]["status"], "UNOBSERVED")  # the test passed but never looked at a2
        self.assertEqual((ev["TC-2.a1"]["status"], ev["TC-2.a1"]["observed"]), ("FAIL", 200))
        self.assertEqual(ev["TC-3.a1"]["status"], "NOT-RUN")
        self.assertEqual(ev["TC-4.a1"]["status"], "MANUAL")
        self.assertEqual(res["checks"], {"PASS": 1, "UNOBSERVED": 1, "FAIL": 1, "NOT-RUN": 1, "MANUAL": 1})

        # C2: the read-only notebook from the data
        code, rep = run("report", "--ticket", t, *self.p)
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["verdicts"], {"partial": 1, "fail": 1, "not-run": 2})  # TC-1 partly proven (a2 unobserved)
        html = Path(rep["path"]).read_text(encoding="utf-8")
        data = json.loads(html.split("const DATA = ", 1)[1].split(";</script>", 1)[0].replace("<\\/", "</"))
        tc1 = next(c for c in data["cases"] if c["id"] == "TC-1")
        self.assertEqual(tc1["evidence"][0]["path"], "case_test.py")
        self.assertIn("TC-1.a2: the case ran but recorded nothing", tc1["auditNote"])
        self.assertNotIn("tester-notebook:${D.ticket}", html)  # no review state: a view, not a gate

    def test_bad_step_file_refuses_the_whole_run(self) -> None:
        t = "M3-2"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        tdir = self.tdir(t)
        (tdir / "steps").mkdir(parents=True)
        (tdir / "steps" / "TC-1.json").write_text(json.dumps(
            {"case": "TC-1", "steps": [{"use": "toggle_lms"}], "expect": [{"check": "a1", "status": 404}]}), encoding="utf-8")
        tc = {"schemaVersion": 1, "ticket": t, "criteria": [{"id": "AC-1", "text": "x"}],
              "cases": [{"id": "TC-1", "title": "t", "surface": "web", "risk": "high", "criteria": ["AC-1"],
                         "checks": [{"id": "TC-1.a1", "expect": "404"}], "inScope": True, "reason": None,
                         "run": {"kind": "steps", "file": "steps/TC-1.json"}}]}
        (tdir / "testcases.json").write_text(json.dumps(tc), encoding="utf-8")
        code, res = run("evidence", "run", "--ticket", t, *self.p)
        self.assertEqual(code, 2)
        self.assertIn("no helper `toggle_lms`", res["error"])
        self.assertFalse((tdir / "evidence" / "run.json").exists())

    def test_helper_is_copied_into_the_checkout(self) -> None:
        t = "M3-3"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        code, res = run("evidence", "helper", "--ticket", t, *self.p)
        if code == 2 and "no evidenceHelper" in res.get("error", ""):
            self.skipTest("the test profile's stack has no evidence helper")
        self.assertEqual(code, 0, res)
        self.assertTrue(Path(res["path"]).is_file())
        code, again = run("evidence", "helper", "--ticket", t, *self.p)
        self.assertFalse(again["written"])


if __name__ == "__main__":
    unittest.main()
