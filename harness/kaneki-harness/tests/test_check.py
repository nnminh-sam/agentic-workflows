"""B2: `sdlc check` runs the gate's content checks as a read-only dry run, so a unit can fix what the gate would refuse
before it hands back; cited bare file names that are unique in the worktree resolve.

On feature-flags the gate refused hand-backs for a missing `## Changes` section, a missing `Verdict:` line, a
proposals.json schema error and citations written as bare file names (`flags.ts:41`)."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import checks as C  # noqa: E402


class CheckCommandTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.t = "CK-1"
        code, res = run("init", "--ticket", self.t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)

    def check(self, node: str, unit: str | None = None) -> tuple[int, dict]:
        before = self.snapshot()
        code, res = run("check", "--ticket", self.t, "--node", node, *(["--unit", unit] if unit else []), *self.p)
        self.assertEqual(self.snapshot(), before, "check wrote into the ticket")
        return code, res

    def snapshot(self) -> dict[str, str]:
        return {str(p.relative_to(self.tmp)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.tdir(self.t).rglob("*")) if p.is_file()}

    def test_worker_checks_until_clean(self) -> None:
        code, res = self.check("prepare", "worker")
        self.assertEqual(code, 1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["dispatchId"], "prepare.g1.worker.r1")
        self.assertTrue(any(p.startswith("result file missing") for p in res["problems"]), res)
        self.assertTrue(any(p.startswith("artifacts missing") for p in res["problems"]), res)
        plan = self.write_artifacts(self.t, "prepare")
        env = self.tdir(self.t) / "environment.md"
        env.write_text(env.read_text(encoding="utf-8").replace("## Surfaces", "## Something else"), encoding="utf-8")
        result = self.tdir(self.t) / "results" / "prepare.g1.worker.r1.md"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("status: ok\n", encoding="utf-8")
        code, res = self.check("prepare", "worker")
        self.assertEqual(res["problems"], ["environment.md: missing section `## Surfaces`"], res)
        self.write_artifacts(self.t, "prepare")
        code, res = self.check("prepare", "worker")
        self.assertEqual((code, res["ok"], res["problems"]), (0, True, []), res)
        self.assertIn(str(env), res["checked"])
        self.assertTrue(plan["artifacts"])

    def test_reviewer_needs_a_verdict_line(self) -> None:
        result = self.tdir(self.t) / "results" / "fix-plan.g1.reviewer.r1.md"  # the plan reviewer (D2)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("Looks fine.\n", encoding="utf-8")
        code, res = self.check("fix-plan", "reviewer")
        self.assertTrue(any("no `Verdict:" in p for p in res["problems"]), res)
        self.assertFalse(any("artifacts missing" in p for p in res["problems"]), "a reviewer leaves no artifacts")
        result.write_text("Verdict: PASS\n", encoding="utf-8")
        code, res = self.check("fix-plan", "reviewer")
        self.assertEqual((code, res["problems"]), (0, []), res)

    def test_verdict_line_of_a_review_artifact(self) -> None:
        self.write_artifacts(self.t, "code-review", verdict="APPROVED")
        art = self.tdir(self.t) / "code-review.md"
        art.write_text("# code-review\n\nAll good.\n", encoding="utf-8")
        code, res = self.check("code-review")
        self.assertIn("code-review.md: no `Verdict: APPROVED | NEEDS CHANGES` line", res["problems"])
        art.write_text("# code-review\n\nVerdict: NEEDS CHANGES\n", encoding="utf-8")
        code, res = self.check("code-review")
        self.assertEqual(res["problems"], [], "either verdict passes the dry run; the gate matches it to --status")


class BareCitationTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        for rel in ("src/lib/features/flags.ts", "src/app/graph/page.tsx", "src/app/quiz/page.tsx",
                    "node_modules/x/flags.ts"):
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(f"line {i}" for i in range(1, 51)) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unique_bare_names_resolve(self) -> None:
        text = "`flags.ts:41` `graph/page.tsx:19-20` `src/app/quiz/page.tsx:3`"
        self.assertEqual(C.citations(text, [self.repo], [self.repo]), [])
        # without search roots only exact relative paths resolve (proposal evidence stays strict)
        self.assertEqual(C.citations("`flags.ts:41`", [self.repo]), ["`flags.ts:41` (file not found)"])

    def test_ambiguous_missing_and_out_of_range(self) -> None:
        text = "`page.tsx:1` `nope.ts:1` `flags.ts:99`"
        self.assertEqual(C.citations(text, [self.repo], [self.repo]), [
            "`flags.ts:99` (file has 50 lines)", "`nope.ts:1` (file not found)",
            "`page.tsx:1` (ambiguous: 2 files match; cite the repo-relative path)"])


if __name__ == "__main__":
    unittest.main()
