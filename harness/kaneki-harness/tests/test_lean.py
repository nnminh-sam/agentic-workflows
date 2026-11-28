"""M5: the standard lane (D8) and the lean controller (D9): handoff page, Sonnet controller, immutable closed nodes."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import HOME, Base, run  # noqa: E402
from test_runner import RunnerBase  # noqa: E402

from harness import graph as G  # noqa: E402
from harness.profile import build_context, load_profile  # noqa: E402


class StandardLane(Base):
    def test_trace_skips_the_analysis_nodes_and_inlines_drafts(self) -> None:
        profile = load_profile(self.profile)
        graph = G.load_graph(HOME, profile, self.tmp)
        ctx = build_context(profile, "T", "feature", None, None, None)
        ctx["lane"] = "standard"
        order = G.trace(graph, ctx)
        self.assertEqual(order[:5], ["prepare", "feature-plan", "testcase", "testcase-review", "coding"])
        self.assertNotIn("requirement-analysis", order)
        self.assertTrue(G.is_inline(graph, ctx, "delivery-drafts"))
        code, res = run("init", "--ticket", "STD-1", "--kind", "feature", "--lane", "standard", *self.p)
        self.assertEqual(code, 0, res)
        code, plan = run("plan", "--ticket", "STD-1", "--node", "feature-plan", *self.p)
        self.assertEqual(code, 0, plan)
        self.assertEqual([(u["id"], u["tier"]) for u in plan["units"]], [("worker", "deep"), ("reviewer", "deep")])

    def test_handoff_page_and_closed_nodes(self) -> None:
        t = "STD-2"
        run("init", "--ticket", t, "--kind", "bug", "--lane", "standard", *self.p)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        run("ask", "--ticket", t, "--node", "investigate", "--class", "business", "--severity", "LOW",
            "--question", "Keep the old URL?", "--recommend", "yes", *self.p)
        code, res = run("handoff", "--ticket", t, *self.p)
        self.assertEqual(code, 0, res)
        text = Path(res["path"]).read_text(encoding="utf-8")
        self.assertIn("Ready now (start every one): fix-plan", text)
        self.assertIn("OPEN Q-1 (LOW, business): Keep the old URL?", text)
        self.assertIn("gate investigate", text)
        self.assertLess(res["lines"], 45)

        run("answer", "--ticket", t, "--id", "Q-1", "--adopt", *self.p)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review", "coding", "code-review", "execute-and-evidence", "final-review",
                     "delivery-drafts"):
            self.complete(t, node)
        msg = self.tdir(t) / "drafts" / "commit-msg.md"
        msg.write_text(msg.read_text(encoding="utf-8") + "\nCo-Authored-By: someone <x@y.z>\n", encoding="utf-8")
        code, res = run("gate", "--ticket", t, "--node", "deliverable-ready", "--status", "approved", "--actor", "user",
                        *self.p)
        self.assertEqual(code, 2)
        self.assertIn("artifacts of closed nodes changed after their gate", res["error"])
        self.assertIn("delivery-drafts/drafts/commit-msg.md", res["error"])


class LeanController(RunnerBase):
    def test_controller_sessions_run_on_sonnet_by_default(self) -> None:
        t = "LC-1"
        self.lite_ticket(t)
        run("run", "--ticket", t, "--host", "claude", "--max-nodes", "1", *self.p)
        argv = self.bg_calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.config({"controllerModel": None})
        run("run", "--ticket", t, "--host", "claude", "--max-nodes", "1", *self.p)
        self.assertNotIn("--model", self.bg_calls()[-1]["argv"])


if __name__ == "__main__":
    unittest.main()
