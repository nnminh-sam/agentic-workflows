"""Model tiers: light fallback, staged code review, escalation, live-only inputs, context estimate."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402


class TierTests(Base):
    def set_models(self, **extra) -> None:
        prof = json.loads(self.profile.read_text())
        prof["models"].update(extra.pop("models", {}))
        prof.update(extra)
        self.profile.write_text(json.dumps(prof))

    def to_code_review(self, t: str) -> None:
        code, res = run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        for node in ("prepare", "investigate", "fix-plan", "testcase", "testcase-review", "coding"):
            self.complete(t, node, actor="user" if node == "fix-plan" else "controller")

    def unit(self, t: str, node: str, unit: str, model: str, verdict: str | None = None) -> tuple[int, dict]:
        code, pk = run("packet", "--ticket", t, "--node", node, "--unit", unit, *self.p)
        if code:
            return code, pk
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n")
        return run("record", "--ticket", t, "--node", node, "--unit", unit, "--result", pk["resultPath"],
                   "--model", model, "--tokens", "10", *(("--verdict", verdict) if verdict else ()), *self.p)

    def fail_review(self, t: str) -> dict:
        code, res = self.unit(t, "code-review", "technical-review", "sonnet", "FAIL")
        self.assertEqual(code, 0, res)
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "code-review", "--status", "needs-changes",
                        "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)
        return res

    def test_light_falls_back_to_routine(self) -> None:
        run("init", "--ticket", "L-1", "--kind", "bug", *self.p)
        code, plan = run("plan", "--ticket", "L-1", "--node", "coding", *self.p)
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan["units"][0]["tier"], plan["units"][0]["model"], plan["units"][0]["agentType"]),
                         ("light", "sonnet", "kaneki-light"))

    def test_light_model_and_record_check(self) -> None:
        self.set_models(models={"light": "haiku"})
        run("init", "--ticket", "L-2", "--kind", "bug", *self.p)
        code, plan = run("plan", "--ticket", "L-2", "--node", "prepare", *self.p)
        self.assertEqual(plan["units"][0]["model"], "haiku")
        code, res = self.unit("L-2", "prepare", "worker", "sonnet")
        self.assertEqual(code, 2)
        self.assertIn("planned on haiku (light)", res["error"])

    def test_unknown_tier_in_profile_is_refused(self) -> None:
        self.set_models(models={"tiny": "haiku"})
        code, res = run("validate", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("models.tiny is not a tier", res["error"])

    def test_reviews_run_in_parallel(self) -> None:
        """D3: technical and semantic review start together; neither waits for the other."""
        self.to_code_review("S-1")
        code, plan = run("plan", "--ticket", "S-1", *self.p)
        self.assertEqual(plan["node"], "code-review")
        self.assertEqual([(u["id"], u["model"]) for u in plan["units"]],
                         [("technical-review", "sonnet"), ("semantic-review", "opus")])
        self.assertFalse(any("ready" in u or "after" in u for u in plan["units"]))  # nothing staged
        code, res = self.unit("S-1", "code-review", "semantic-review", "opus", "PASS")
        self.assertEqual(code, 0, res)  # before any technical record
        code, res = self.unit("S-1", "code-review", "technical-review", "sonnet", "PASS")
        self.assertEqual(code, 0, res)
        self.write_artifacts("S-1", "code-review")
        code, res = run("gate", "--ticket", "S-1", "--node", "code-review", "--status", "approved", *self.p)
        self.assertEqual(code, 0, res)

    def test_coding_escalates_after_two_failed_rounds(self) -> None:
        self.set_models(models={"light": "haiku"})
        self.to_code_review("E-1")
        self.fail_review("E-1")
        code, plan = run("plan", "--ticket", "E-1", "--node", "coding", *self.p)
        self.assertEqual(plan["units"][0]["model"], "haiku")  # one failed round: still light
        self.assertNotIn("escalated", plan["units"][0])
        self.complete("E-1", "coding")
        self.fail_review("E-1")
        code, plan = run("plan", "--ticket", "E-1", "--node", "coding", *self.p)
        u = plan["units"][0]
        self.assertEqual((u["tier"], u["model"], u["agentType"]), ("routine", "sonnet", "kaneki-worker"))
        self.assertEqual(u["escalated"]["from"], "light")
        code, res = self.unit("E-1", "coding", "worker", "haiku")
        self.assertEqual(code, 2)
        self.assertIn("planned on sonnet (routine)", res["error"])
        code, res = self.unit("E-1", "coding", "worker", "sonnet")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["recorded"]["tier"], "routine")

    def test_inputs_are_handed_over_live_only(self) -> None:
        self.to_code_review("I-1")
        self.fail_review("I-1")
        impl = self.tdir("I-1") / "implementation.md"
        self.assertIn("## SUPERSEDED:", impl.read_text(encoding="utf-8"))
        old = impl.read_text(encoding="utf-8")
        self.complete("I-1", "coding")  # write_artifacts replaces the file; put the old rounds back above it
        impl.write_text(old + "\n" + impl.read_text(encoding="utf-8"), encoding="utf-8")
        code, pk = run("packet", "--ticket", "I-1", "--node", "code-review", "--unit", "technical-review", *self.p)
        self.assertEqual(code, 0, pk)
        text = Path(pk["path"]).read_text(encoding="utf-8")
        self.assertIn("live round only", text)
        copy = next(Path(pk["path"]).parent.glob("*.inputs/implementation.md"))
        live = copy.read_text(encoding="utf-8")
        self.assertNotIn("## SUPERSEDED:", live)
        self.assertNotIn("kaneki:token-usage", live)
        self.assertIn("## Rule self-check", live)

    def test_context_warning(self) -> None:
        self.set_models(contextWindow={"deep": 40000})
        run("init", "--ticket", "W-1", "--kind", "bug", *self.p)
        self.complete("W-1", "prepare")
        code, pk = run("packet", "--ticket", "W-1", "--node", "investigate", "--unit", "worker", *self.p)
        self.assertEqual(code, 0, pk)
        self.assertNotIn("contextWarning", pk)
        self.assertTrue((self.tdir("W-1") / "logs").is_dir())
        env = self.tdir("W-1") / "environment.md"
        env.write_text(env.read_text(encoding="utf-8") + "x" * 48000, encoding="utf-8")
        code, pk = run("packet", "--ticket", "W-1", "--node", "investigate", "--unit", "worker", *self.p)
        self.assertGreater(pk["handedTokens"], 10000)
        self.assertIn("40,000-token window assumed for deep", pk["contextWarning"])

if __name__ == "__main__":
    unittest.main()
