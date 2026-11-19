"""board.waiting(): who a ticket waits on, one test per reason plus the overlaps the rule order decides (P1-T03)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import board  # noqa: E402
from harness.cli import Session  # noqa: E402


class WaitingTests(Base):
    # --- helpers ---------------------------------------------------------------------------------------
    def session(self, ticket: str) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=ticket))

    def snapshot(self) -> dict[str, str]:
        """sha256 of every file under the ticket root: waiting() must leave it unchanged."""
        root = self.tmp / "tickets"
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    def wait(self, ticket: str, node: str | None = None, s: Session | None = None) -> tuple[str, str | None, str]:
        s = s or self.session(ticket)
        before = self.snapshot()
        res = board.waiting(s, node)
        self.assertEqual(self.snapshot(), before, "waiting() wrote to the ticket folder")
        self.assertEqual(set(res), {"on", "node", "reason"})
        self.assertIn(res["reason"], board.WAITING_REASONS)
        return res["on"], res["node"], res["reason"]

    def init(self, ticket: str, kind: str = "feature", lane: str = "full") -> None:
        code, res = run("init", "--ticket", ticket, "--kind", kind, "--lane", lane, *self.p)
        self.assertEqual(code, 0, res)

    def record(self, ticket: str, node: str, unit: str, verdict: str | None = None) -> None:
        code, pk = run("packet", "--ticket", ticket, "--node", node, "--unit", unit, *self.p)
        self.assertEqual(code, 0, pk)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n")
        code, rec = run("record", "--ticket", ticket, "--node", node, "--unit", unit, "--result", pk["resultPath"],
                        "--model", pk["model"], "--tokens", "100", *(("--verdict", verdict) if verdict else ()), *self.p)
        self.assertEqual(code, 0, rec)

    def gate(self, ticket: str, node: str, status: str, *extra: str) -> dict:
        code, res = run("gate", "--ticket", ticket, "--node", node, "--status", status, *extra, *self.p)
        self.assertEqual(code, 0, res)
        return res

    def lite_through_change(self, t: str) -> None:
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")

    # --- one test per reason ----------------------------------------------------------------------------
    def test_integrity(self) -> None:
        t = "W-INT"
        self.init(t, lane="lite")
        s = self.session(t)  # loaded while the log was intact
        log = self.tdir(t) / "decisions.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"at": "', '"at": "edited ', 1)  # breaks the next line's `prev` hash
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(self.wait(t, s=s), ("none", "scope", "integrity"))

    def test_done_and_shipped(self) -> None:
        t = "W-DONE"
        self.lite_through_change(t)
        self.assertEqual(self.wait(t, "scope"), ("none", "scope", "done"))  # an explicitly passed, closed node
        self.complete(t, "check")
        self.complete(t, "delivery-drafts")
        self.complete(t, "deliverable-ready", actor="user")
        self.assertEqual(self.wait(t), ("none", None, "done"))
        s = self.session(t)
        s.state["shipped"] = {"app": {"commits": ["abc"]}}  # in memory only: kaneki-ship is not run here
        self.assertEqual(self.wait(t, s=s), ("none", None, "shipped"))

    def test_needs_run(self) -> None:
        t = "W-RUN"
        self.init(t, lane="lite")
        self.assertEqual(self.wait(t), ("agent", "scope", "needs-run"))  # controller node, scope.md not written
        self.init("W-RUN2")
        self.assertEqual(self.wait("W-RUN2"), ("agent", "prepare", "needs-run"))  # unit node, nothing recorded

    def test_running(self) -> None:
        t = "W-RUNNING"
        self.init(t)
        self.complete(t, "prepare")
        self.write_artifacts(t, "requirement-analysis")
        self.record(t, "requirement-analysis", "worker")  # the reviewer has not run yet
        self.assertEqual(self.wait(t), ("agent", "requirement-analysis", "running"))

    def test_user_boundary(self) -> None:
        t = "W-USER"
        self.init(t, lane="lite")
        self.write_artifacts(t, "scope")
        self.assertEqual(self.wait(t), ("user", "scope", "user-boundary"))  # controller node with its artifact
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        self.complete(t, "check")
        self.complete(t, "delivery-drafts")
        self.assertEqual(self.wait(t), ("user", "deliverable-ready", "user-boundary"))  # no artifacts at all

    def test_awaiting_gate(self) -> None:
        t = "W-GATE"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        self.assertEqual(self.wait(t), ("agent", "change", "needs-run"))
        self.write_artifacts(t, "change")
        self.assertEqual(self.wait(t), ("agent", "change", "awaiting-gate"))
        self.complete(t, "change")
        self.write_artifacts(t, "check")
        self.record(t, "check", "quick-review", "PASS")
        self.assertEqual(self.wait(t), ("agent", "check", "awaiting-gate"))

    def test_rejected_open(self) -> None:
        t = "W-REJ"
        self.init(t, lane="lite")
        self.write_artifacts(t, "scope")
        self.gate(t, "scope", "rejected", "--actor", "user")
        self.assertEqual(self.wait(t), ("user", "scope", "rejected-open"))

    def test_rejected_beats_needs_run(self) -> None:
        t = "W-REJ2"
        self.init(t)
        self.gate(t, "prepare", "rejected")  # nothing recorded: rejected-open wins over needs-run
        self.assertEqual(self.wait(t), ("user", "prepare", "rejected-open"))

    def test_loop_cap(self) -> None:
        t = "W-CAP"
        self.lite_through_change(t)
        self.write_artifacts(t, "check", verdict="NEEDS CHANGES")
        self.record(t, "check", "quick-review", "NEEDS CHANGES")
        self.assertEqual(self.wait(t), ("agent", "check", "awaiting-gate"))  # round 1 of 2: controller closes it
        self.gate(t, "check", "needs-changes")
        self.assertEqual(self.wait(t), ("agent", "change", "needs-run"))  # stale controller node: superseded text
        self.complete(t, "change")
        self.write_artifacts(t, "check", verdict="NEEDS CHANGES")
        self.record(t, "check", "quick-review", "NEEDS CHANGES")
        self.assertEqual(self.wait(t), ("user", "check", "loop-cap"))  # G4: the next needs-changes needs the user

    def test_staged_technical_fail(self) -> None:
        """A technical-review FAIL never runs semantic-review: the round is complete, not running."""
        t = "W-STAGED"
        self.init(t, kind="bug")
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        self.complete(t, "coding")
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        self.record(t, "code-review", "technical-review", "FAIL")
        self.assertEqual(self.wait(t), ("agent", "code-review", "awaiting-gate"))
        self.gate(t, "code-review", "needs-changes")
        self.complete(t, "coding")
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        self.record(t, "code-review", "technical-review", "FAIL")
        self.assertEqual(self.wait(t), ("user", "code-review", "loop-cap"))

    def test_reviewer_fail(self) -> None:
        t = "W-REVFAIL"
        self.init(t)
        self.complete(t, "prepare")
        self.write_artifacts(t, "requirement-analysis")
        self.record(t, "requirement-analysis", "worker")
        self.record(t, "requirement-analysis", "reviewer", "FAIL")
        self.assertEqual(self.wait(t), ("agent", "requirement-analysis", "reviewer-fail"))
        # reviewer FAIL beats a user boundary (triage-review has both): same node, boundary set to user in memory
        s = self.session(t)
        s.graph = copy.deepcopy(s.graph)
        s.graph["nodes"]["requirement-analysis"]["boundary"] = "user"
        self.assertEqual(self.wait(t, s=s), ("agent", "requirement-analysis", "reviewer-fail"))
        self.record(t, "requirement-analysis", "reviewer", "PASS")
        self.assertEqual(self.wait(t), ("agent", "requirement-analysis", "awaiting-gate"))

    def test_plan_auto_skip(self) -> None:
        t = "W-SKIP"
        self.init(t, kind="bug")
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.write_artifacts(t, "fix-plan")
        self.record(t, "fix-plan", "worker")
        self.assertEqual(self.wait(t), ("user", "fix-plan", "user-boundary"))
        s = self.session(t)
        s.profile = {**s.profile, "gates": {**s.profile.get("gates", {}), "autoSkipPlanApproval": True}}
        self.assertEqual(self.wait(t, s=s), ("agent", "fix-plan", "awaiting-gate"))

    def test_writes_nothing(self) -> None:
        """Every call above goes through wait(), which compares the ticket folder before and after; this test also
        checks the files the driver regenerates (state.json, decisions.jsonl, STATUS.md) byte for byte."""
        t = "W-RO"
        self.init(t, lane="lite")
        self.write_artifacts(t, "scope")
        names = ("state.json", "decisions.jsonl", "STATUS.md")
        before = {n: (self.tdir(t) / n).read_bytes() for n in names}
        listing = sorted(p.name for p in self.tdir(t).iterdir())
        for _ in range(2):
            board.waiting(self.session(t))
            board.waiting(self.session(t), "change")
        self.assertEqual({n: (self.tdir(t) / n).read_bytes() for n in names}, before)
        self.assertEqual(sorted(p.name for p in self.tdir(t).iterdir()), listing)


if __name__ == "__main__":
    unittest.main()
