"""D7: questions for the user as data, asked in one batch; the plan cannot close over an open HIGH question."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402


class QuestionTests(Base):
    T = "Q-T1"

    def setUp(self) -> None:
        super().setUp()
        code, res = run("init", "--ticket", self.T, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        for node in ("prepare", "investigate"):
            self.complete(self.T, node)

    def ask(self, *extra: str, node: str = "investigate", cls: str = "business", sev: str = "HIGH",
            q: str = "Should a disabled feature 404 or redirect?") -> tuple[int, dict]:
        return run("ask", "--ticket", self.T, "--node", node, "--class", cls, "--severity", sev, "--question", q,
                   "--recommend", "404", *extra, *self.p)

    def test_batch_gate_and_answers(self) -> None:
        code, q1 = self.ask("--why", "changes routing and tests", "--option", "redirect to /")
        self.assertEqual((code, q1["id"], q1["status"], q1["late"]), (0, "Q-1", "open", False))
        code, dup = self.ask()
        self.assertTrue(dup.get("duplicate"))
        code, q2 = self.ask(sev="LOW", cls="infra", q="Reuse the local Postgres container?")
        self.assertEqual(q2["id"], "Q-2")

        code, b = run("questions", "--ticket", self.T, *self.p)
        self.assertEqual(code, 0, b)
        self.assertEqual([q["id"] for q in b["open"]], ["Q-1", "Q-2"])  # HIGH first
        self.assertEqual(b["openHigh"], ["Q-1"])
        self.assertIn("ONE ASK_USER round", b["how"])

        # the plan node cannot close over an open HIGH question, not even by the user
        self.write_artifacts(self.T, "fix-plan")
        code, res = self.close("fix-plan")
        self.assertEqual(code, 2)
        self.assertIn("G1", res["error"])
        self.assertIn("Q-1", res["error"])

        code, res = run("answer", "--ticket", self.T, "--id", "Q-1", "--answer", "404", *self.p)
        self.assertEqual(code, 2)  # the controller does not answer for the user
        code, res = run("answer", "--ticket", self.T, "--id", "Q-1", "--adopt", *self.p)
        self.assertEqual(code, 2)  # nor adopt a HIGH recommendation
        code, res = run("answer", "--ticket", self.T, "--id", "Q-1", "--answer", "404", "--actor", "user", *self.p)
        self.assertEqual((code, res["status"], res["openHigh"]), (0, "answered", []))
        code, res = run("answer", "--ticket", self.T, "--id", "Q-2", "--adopt", *self.p)
        self.assertEqual((code, res["status"]), (0, "adopted"))

        md = (self.tdir(self.T) / "questions.md").read_text(encoding="utf-8")
        self.assertIn("Q-1 · HIGH · business · investigate · ANSWERED (user", md)
        self.assertIn("Answer: 404", md)
        code, res = self.close("fix-plan")
        self.assertEqual(code, 0, res)

        events = [json.loads(x)["event"] for x in
                  (self.tdir(self.T) / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events.count("question"), 2)
        self.assertEqual(events.count("answer"), 2)

        # after the plan: a business question is late and reaches reflect
        code, late = self.ask(node="coding", q="Also hide the menu entry?")
        self.assertEqual((code, late["late"]), (0, True))
        self.assertIn("warning", late)
        code, sig = run("reflect-signals", "--ticket", self.T, *self.p)
        self.assertEqual(code, 0, sig)
        ids = [x["id"] for x in sig["signals"]["questions"]]
        self.assertIn("q:late:Q-3", ids)

    def test_judgeable_is_not_a_user_question(self) -> None:
        code, res = self.ask(cls="judgeable", q="Is partial coverage of TC-4 acceptable?")
        self.assertEqual(code, 2)
        self.assertIn("review authority", res["error"])
        self.assertFalse((self.tdir(self.T) / "questions.json").exists())

    def close(self, node: str) -> tuple[int, dict]:
        plan = run("plan", "--ticket", self.T, "--node", node, *self.p)[1]
        for unit in plan["units"]:
            code, pk = run("packet", "--ticket", self.T, "--node", node, "--unit", unit["id"], *self.p)
            if code:
                return code, pk
            Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
            Path(pk["resultPath"]).write_text("status: ok\n")
            verdict = ("--verdict", "PASS") if unit["role"] == "reviewer" or plan["pattern"].startswith("staged") else ()
            run("record", "--ticket", self.T, "--node", node, "--unit", unit["id"], "--result", pk["resultPath"],
                "--model", unit["model"], "--tokens", "1000", *verdict, *self.p)
        return run("gate", "--ticket", self.T, "--node", node, "--status", "approved", "--actor", "user", *self.p)


if __name__ == "__main__":
    unittest.main()
