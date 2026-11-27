"""M4: trusted evidence judges (J1-J5), the small path (D4) and the ready list (D6)."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402

from harness import judge as J  # noqa: E402
from harness.cli import Session  # noqa: E402

PY = Path(sys.executable).as_posix()


class JudgeBase(Base):
    T = "J-1"

    def setUp(self) -> None:
        super().setUp()
        code, res = run("init", "--ticket", self.T, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        self.td = self.tdir(self.T)
        # the product: a guard on line 2; a real test and a vacuous one
        (self.repo / "flags.py").write_text("def page(enabled):\n    if not enabled: return 404\n    return 200\n")
        (self.repo / "check_test.py").write_text(
            "import sys\nfrom flags import page\nsys.exit(0 if page(False) == 404 else 1)\n")
        (self.repo / "vacuous_test.py").write_text("import sys\nsys.exit(0)\n")
        tc = {"schemaVersion": 1, "ticket": self.T,
              "criteria": [{"id": "AC-1", "text": "a disabled page answers 404", "source": "ticket.md:3"}],
              "cases": [self.case(1, "critical", "check_test.py"), self.case(2, "normal", "check_test.py"),
                        self.case(3, "critical", "vacuous_test.py")]}
        (self.td / "testcases.json").write_text(json.dumps(tc), encoding="utf-8")
        (self.td / "requirements.md").write_text("# Requirements\n\nR-1: a disabled page answers 404.\n", encoding="utf-8")
        self.evidence({"TC-1.a1": "PASS", "TC-2.a1": "PASS", "TC-3.a1": "PASS"})
        (self.td / "evidence").mkdir(exist_ok=True)
        (self.td / "evidence" / "run.json").write_text(json.dumps(
            {"cases": {f"TC-{n}": {"status": "PASS", "log": ""} for n in (1, 2, 3)}}), encoding="utf-8")
        (self.td / "mutations.json").write_text(json.dumps({
            "TC-1.a1": {"file": "flags.py", "line": 2, "find": "if not enabled: return 404", "replace": "pass"},
            "TC-3.a1": {"file": "flags.py", "line": 2, "find": "if not enabled: return 404", "replace": "pass"}}),
            encoding="utf-8")

    def case(self, n: int, risk: str, test: str) -> dict:
        return {"id": f"TC-{n}", "title": f"case {n}", "surface": "cli", "risk": risk, "criteria": ["AC-1"],
                "checks": [{"id": f"TC-{n}.a1", "expect": "404", "value": 404}], "inScope": True, "reason": None,
                "run": {"kind": "repo-test", "command": f'"{PY}" {test}'}}

    def evidence(self, statuses: dict[str, str]) -> None:
        checks = {cid: {"case": cid.split(".")[0], "status": st, "observed": 404 if st == "PASS" else 200,
                        "expected": 404, "source": {"kind": "test", "ref": "check_test.py:3"}}
                  for cid, st in statuses.items()}
        (self.td / "evidence.json").write_text(json.dumps(
            {"schemaVersion": 1, "ticket": self.T, "at": "x", "checks": checks}), encoding="utf-8")

    def s(self) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=self.T, host=None))

    def verdict(self, v: str = "PASS", conf: str = "High", **kw) -> dict:
        return {"verdict": v, "confidence": conf, "class": "judgeable", "reason": "r",
                "requirement": {"ref": "requirements.md:3", "quote": "a disabled page answers 404"},
                "evidence": {"ref": "check_test.py:3", "quote": "page(False) == 404"}, **kw}

    def judgement(self, judge: str, verdicts: dict, **extra) -> None:
        (self.td / J.FILES[judge]).write_text(json.dumps(
            {"schemaVersion": 1, "ticket": self.T, "judge": judge, "verdicts": verdicts, **extra}), encoding="utf-8")


class FactsAndVerdicts(JudgeBase):
    def test_mutation_kills_the_real_guard_and_flags_the_vacuous_test(self) -> None:
        original = (self.repo / "flags.py").read_bytes()
        code, res = run("mutate", "--ticket", self.T, *self.p)
        self.assertEqual(code, 1, res)  # TC-3's test survives
        self.assertEqual(res["survived"], ["TC-3.a1"])
        self.assertEqual((self.repo / "flags.py").read_bytes(), original)  # always restored
        fx = J.facts(self.s())["checks"]
        self.assertEqual(fx["TC-1.a1"]["mutation"], "killed")
        self.assertEqual(fx["TC-1.a1"]["rules"], [])
        self.assertTrue(any(r.startswith("EVD-M") for r in fx["TC-3.a1"]["rules"]))
        self.assertEqual(fx["TC-2.a1"]["rules"], [])  # not critical: no mutation needed

    def test_interrupted_mutation_is_restored_first(self) -> None:
        bdir = self.td / "evidence" / "mutation-backup"
        bdir.mkdir(parents=True)
        original = (self.repo / "flags.py").read_bytes()
        (bdir / "TC-1.a1.orig").write_bytes(original)
        (bdir / "TC-1.a1.json").write_text(json.dumps({"path": str(self.repo / "flags.py")}), encoding="utf-8")
        (self.repo / "flags.py").write_text("broken\n")
        self.assertEqual(J.restore_leftovers(self.s()), [str(self.repo / "flags.py")])
        self.assertEqual((self.repo / "flags.py").read_bytes(), original)

    def test_the_judge_cannot_overrule_the_facts(self) -> None:
        self.evidence({"TC-1.a1": "FAIL", "TC-2.a1": "UNOBSERVED", "TC-3.a1": "PASS"})
        self.judgement("a", {"TC-1.a1": self.verdict(), "TC-2.a1": self.verdict(), "TC-3.a1": self.verdict()})
        text = "\n".join(J.judgement_problems(self.s(), "a"))
        self.assertIn("TC-1.a1: the driver observed a FAIL (EVD-B); the verdict must be FAIL", text)
        self.assertIn("TC-2.a1: PASS is not allowed: EVD-A", text)
        self.assertIn("TC-3.a1: PASS is not allowed", text)  # no killed mutation yet (EVD-M)

    def test_citations_confidence_and_escalations(self) -> None:
        run("mutate", "--ticket", self.T, *self.p)
        bad_quote = self.verdict(requirement={"ref": "requirements.md:3", "quote": "every page redirects"})
        self.judgement("a", {"TC-1.a1": bad_quote, "TC-2.a1": self.verdict(conf="Low"),
                             "TC-3.a1": self.verdict("ESCALATE", **{"class": "business"})})
        text = "\n".join(J.judgement_problems(self.s(), "a"))
        self.assertIn("TC-1.a1: requirement citation: the quote is not at `requirements.md:3`", text)
        self.assertIn("TC-2.a1: PASS at Low confidence must ESCALATE", text)
        self.assertIn("TC-3.a1: escalated but not asked", text)
        self.judgement("a", {"TC-1.a1": self.verdict(), "TC-2.a1": self.verdict("ESCALATE", **{"class": "judgeable"}),
                             "TC-3.a1": self.verdict("FAIL")})
        text = "\n".join(J.judgement_problems(self.s(), "a"))
        self.assertIn("TC-2.a1: ESCALATE is for business, infra or critical-logic", text)
        self.assertIn("TC-3.a1: FAIL needs routeTo", text)


class TwoJudges(JudgeBase):
    def setUp(self) -> None:
        super().setUp()
        (self.td / "mutations.json").write_text(json.dumps(
            {"TC-1.a1": {"none": "fixture"}, "TC-3.a1": {"none": "fixture"}}), encoding="utf-8")

    def test_disagreement_escalates_until_the_user_answers(self) -> None:
        a = {c: self.verdict() for c in ("TC-1.a1", "TC-2.a1", "TC-3.a1")}
        self.judgement("a", a)
        self.judgement("b", {"TC-1.a1": self.verdict(), "TC-3.a1": self.verdict("FAIL", routeTo="coding")})
        probs = J.gate_problems(self.s())
        self.assertEqual(len(probs), 1, probs)
        self.assertTrue(probs[0].startswith("G1: judges disagree on TC-3.a1 (A PASS, B FAIL)"))
        code, q = run("ask", "--ticket", self.T, "--node", "execute-and-evidence", "--class", "critical-logic",
                      "--severity", "HIGH", "--question", "TC-3.a1: is the vacuous test acceptable?", "--recommend",
                      "no", *self.p)
        self.assertEqual(code, 0, q)
        self.assertTrue(J.gate_problems(self.s()))  # asked, not answered
        run("answer", "--ticket", self.T, "--id", q["id"], "--answer", "accept PASS", "--actor", "user", *self.p)
        self.assertEqual(J.gate_problems(self.s()), [])
        self.assertEqual(J.record_verdicts(self.s()), 5)

    def test_judge_b_must_cover_the_critical_checks(self) -> None:
        self.judgement("a", {c: self.verdict() for c in ("TC-1.a1", "TC-2.a1", "TC-3.a1")})
        self.judgement("b", {"TC-1.a1": self.verdict()})
        self.assertIn("evidence-judgement-2.json: no verdict for ['TC-3.a1']", "\n".join(J.gate_problems(self.s())))

    def test_fail_routes_to_the_owning_node(self) -> None:
        self.evidence({"TC-1.a1": "PASS", "TC-2.a1": "FAIL", "TC-3.a1": "PASS"})
        fail = self.verdict("FAIL", routeTo="coding",
                            evidence={"ref": "check_test.py:3", "quote": "page(False) == 404"})
        self.judgement("a", {"TC-1.a1": self.verdict(), "TC-2.a1": fail, "TC-3.a1": self.verdict()})
        self.judgement("b", {"TC-1.a1": self.verdict(), "TC-3.a1": self.verdict()})
        probs = J.gate_problems(self.s())
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("FAIL verdicts route to their owning node: {'TC-2.a1': 'coding'}", probs[0])

    def test_calibration_tightens_confidence(self) -> None:
        self.judgement("a", {c: self.verdict(conf="Medium") for c in ("TC-1.a1", "TC-2.a1", "TC-3.a1")})
        self.judgement("b", {"TC-1.a1": self.verdict(), "TC-3.a1": self.verdict()})
        self.assertEqual(J.judgement_problems(self.s(), "a"), [])  # Medium stands while judges are calibrated
        J.record_verdicts(self.s())
        code, res = run("judge-miss", "--ticket", self.T, "--check", "TC-1.a1", "--reason",
                        "final review: requirements.md:3 says 404, the page redirected", *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["strict"])
        text = "\n".join(J.judgement_problems(self.s(), "a"))
        self.assertIn("PASS at Medium confidence must ESCALATE (judge miss rate is high", text)


class SmallPathAndReady(Base):
    def test_tests_only_fix_skips_code_review_and_refuses_source_changes(self) -> None:
        t = "SP-1"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review", "coding", "code-review"):
            self.complete(t, node)
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual(st["ready"][0], "execute-and-evidence")  # D6: every ready node, not only the next
        code, res = run("invalidate", "--ticket", t, "--node", "coding", "--reason", "weak test", "--small", "tests",
                        *self.p)
        self.assertEqual(code, 0, res)
        self.assertNotIn("code-review", res["stale"])
        (self.repo / "test_main.py").write_text("assert True\n")
        self.write_artifacts(t, "coding")
        code, chk = run("check", "--ticket", t, "--node", "coding", *self.p)
        self.assertEqual(code, 0, chk)  # only a test file changed
        (self.repo / "main.py").write_text("print('sneaky source change')\n")
        code, chk = run("check", "--ticket", t, "--node", "coding", *self.p)
        self.assertEqual(code, 1)
        self.assertTrue(any("non-tests files changed" in p and "main.py" in p for p in chk["problems"]), chk)
        code, res = run("invalidate", "--ticket", t, "--node", "code-review", "--reason", "x", "--small", "docs", *self.p)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
