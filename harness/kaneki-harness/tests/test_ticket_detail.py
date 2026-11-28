"""`sdlc ticket-detail`: one ticket as the app's TicketDetail, the board summary plus node cards (P1-T05)."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import board  # noqa: E402
from harness.cli import Session, main  # noqa: E402
from harness.state import missing_prerequisites  # noqa: E402

TOP_EXTRAS = {"trace", "loopCap", "generationMax", "loopBacks"}
NODE_EXTRAS = {"plannedUnits", "unitRecords", "agents", "tokensAllGenerations", "tokensEarlier", "loopBackTo", "reopens",
               "gates", "missing", "waiting", "resultFiles"}


class TicketDetailTests(Base):
    # --- helpers ---------------------------------------------------------------------------------------
    def session(self, ticket: str) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=ticket))

    def snapshot(self) -> dict[str, str]:
        root = self.tmp / "tickets"
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    def detail(self, ticket: str, *extra: str) -> dict:
        """Run `sdlc ticket-detail` and check it wrote nothing under the ticket root."""
        before = self.snapshot()
        code, res = run("ticket-detail", "--ticket", ticket, *extra, *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.snapshot(), before, "ticket-detail wrote to the ticket folder")
        return res

    def node(self, res: dict, name: str) -> dict:
        return next(n for n in res["nodes"] if n["node"] == name)

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

    def to_code_review(self, t: str) -> None:
        self.init(t, kind="bug")
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review"):  # D1: test cases before coding
            self.complete(t, node)
        self.complete(t, "coding")

    def fail_technical(self, t: str) -> None:
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        self.record(t, "code-review", "technical-review", "FAIL")

    # --- tests ------------------------------------------------------------------------------------------
    def test_parity_with_summarize(self) -> None:
        """Every key of the board summary is there with the same value; the extras are added on top."""
        t = "D-PAR"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        res = self.detail(t)
        plain = board.summarize_ticket(self.profile, t)
        self.assertEqual(set(res) - set(plain), TOP_EXTRAS)
        for key, value in plain.items():
            if key != "nodes":
                self.assertEqual(res[key], value, key)
        self.assertIn("strip", res)  # kept (user decision, P1-T05 Q1)
        self.assertEqual(len(res["nodes"]), len(plain["nodes"]))
        for full, brief in zip(res["nodes"], plain["nodes"]):
            self.assertEqual(set(full) - set(brief), NODE_EXTRAS)
            self.assertEqual({k: full[k] for k in brief}, brief)
        self.assertEqual(res["trace"], ["scope", "change", "check", "delivery-drafts", "deliverable-ready", "reflect"])
        self.assertEqual((res["loopCap"], res["generationMax"], res["loopBacks"]), (2, 1, []))

    def test_default_outputs_unchanged(self) -> None:
        """detail=False (STATUS.md, `sdlc tickets`, /api/ticket) carries none of the extras."""
        t = "D-DEF"
        self.init(t, lane="lite")
        plain = board.summarize_ticket(self.profile, t)
        self.assertFalse(TOP_EXTRAS & set(plain))
        self.assertFalse(any(NODE_EXTRAS & set(n) for n in plain["nodes"]))
        code, res = run("tickets", *self.p)
        self.assertEqual(code, 0, res)
        self.assertFalse(TOP_EXTRAS & set(res["tickets"][0]))

    def test_node_card_simple(self) -> None:
        t = "D-CARD"
        self.init(t, lane="lite")
        res = self.detail(t)
        change = self.node(res, "change")
        s = self.session(t)
        self.assertEqual(change["missing"], missing_prerequisites(s.graph, s.order, s.state, "change"))
        self.assertIn("approved-plan (from scope)", change["missing"])  # scope not settled yet
        self.assertEqual((change["plannedUnits"], change["unitRecords"], change["agents"]), ([], [], []))
        check = self.node(res, "check")
        self.assertEqual(check["plannedUnits"], [{"id": "quick-review", "role": "worker", "tier": "routine",
                                                  "model": "sonnet", "host": "profile"}])
        self.assertEqual((check["loopBackTo"], check["reopens"]), ("change", None))
        self.assertIsInstance(check["gates"], list)
        self.complete(t, "scope", actor="user")
        self.assertEqual(self.node(self.detail(t), "change")["missing"], [])

    def test_waiting_is_board_waiting(self) -> None:
        t = "D-WAIT"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        self.write_artifacts(t, "change")
        res = self.detail(t)
        s = self.session(t)
        for n in res["nodes"]:
            self.assertEqual(n["waiting"], board.waiting(s, n["node"]), n["node"])
        strip = {x["node"]: x["waiting"] for x in res["strip"]}
        self.assertEqual({n["node"]: n["waiting"] for n in res["nodes"]}, strip)

    def test_escalation_generations_loop_backs_and_results(self) -> None:
        """Two failed code-review rounds: coding moves to routine, earlier tokens move to tokensEarlier, the
        needs-changes gate is one loop-back, result files of both generations are listed in order."""
        prof = json.loads(self.profile.read_text())
        prof["models"]["light"] = "haiku"
        self.profile.write_text(json.dumps(prof))
        t = "D-ESC"
        self.to_code_review(t)
        self.fail_technical(t)
        self.gate(t, "code-review", "needs-changes", "--note", "round 1")
        self.complete(t, "coding")
        self.fail_technical(t)  # second failed round, not closed yet

        res = self.detail(t)
        coding = self.node(res, "coding")
        self.assertEqual(len(coding["plannedUnits"]), 1)
        u = coding["plannedUnits"][0]
        self.assertEqual((u["id"], u["tier"], u["model"]), ("worker", "routine", "sonnet"))
        self.assertEqual(u["escalated"]["from"], "light")
        self.assertEqual(coding["generation"], 2)
        self.assertEqual([r["generation"] for r in coding["unitRecords"]], [2])
        self.assertEqual((coding["tokensEarlier"], coding["tokensAllGenerations"], coding["tokens"]), (1000, 2000, 1000))
        self.assertEqual([(a["unit"], a["tokens"]) for a in coding["agents"]], [("worker", 1000)])

        review = self.node(res, "code-review")
        self.assertEqual(review["loopBackTo"], "coding")
        self.assertEqual([(f["generation"], f["unit"], f["round"]) for f in review["resultFiles"]],
                         [(1, "technical-review", 1), (2, "technical-review", 1)])
        self.assertTrue(all(Path(f["path"]).is_file() and f["name"].startswith("code-review.") for f in review["resultFiles"]))
        self.assertTrue(all(f["name"].startswith("coding.") for f in coding["resultFiles"]))
        self.assertEqual([f["generation"] for f in coding["resultFiles"]], [1, 2])

        self.assertEqual(res["generationMax"], 2)
        self.assertEqual(len(res["loopBacks"]), 1)
        lb = res["loopBacks"][0]
        self.assertEqual((lb["node"], lb["byGateVerdict"], lb["byNode"]), ("coding", "needs-changes", "code-review"))
        self.assertIn("code-review", lb["stale"])
        self.assertTrue(lb["reason"].startswith("code-review NEEDS CHANGES"))
        self.assertEqual(self.node(res, "investigate")["resultFiles"][0]["unit"], "worker")

    def test_manual_invalidate_has_no_gate(self) -> None:
        t = "D-INV"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        code, res = run("invalidate", "--ticket", t, "--node", "change", "--reason", "wrong file", *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "change")  # the next gate is on the reopened node itself, not a loop-back gate
        lb = self.detail(t)["loopBacks"]
        self.assertEqual(len(lb), 1)
        self.assertEqual((lb[0]["node"], lb[0]["reason"], lb[0]["byGateVerdict"], lb[0]["byNode"]),
                         ("change", "wrong file", None, None))

    def test_triage_reopen_is_a_gate_loop_back(self) -> None:
        """After a PR review, triage-review approved with --reopen logs the invalidate before its gate."""
        t = "D-TRI"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        for node in ("change", "check", "delivery-drafts"):
            self.complete(t, node)
        self.complete(t, "deliverable-ready", actor="user")
        review = self.tmp / "review.md"
        review.write_text("RC-1: rename variable\n")
        code, res = run("handback", "--ticket", t, "--review-file", str(review), *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "read-review")
        self.complete(t, "triage-review", actor="user", extra=("--reopen", "change"))
        lb = self.detail(t)["loopBacks"]
        self.assertEqual(len(lb), 1)
        self.assertEqual((lb[0]["node"], lb[0]["byGateVerdict"], lb[0]["byNode"]), ("change", "approved", "triage-review"))
        self.assertTrue(lb[0]["reason"].startswith("review round "))

    def test_manual_invalidate_before_unrelated_gate(self) -> None:
        """check F1: a manual invalidate followed directly by a gate that invalidated nothing (triage-review approved
        --reopen none, or a needs-changes gate whose own invalidate is logged later) is not attributed to that gate."""
        t = "D-F1"
        self.init(t, lane="lite")
        s = self.session(t)
        manual = {"event": "invalidate", "at": "2026-01-01T00:00:00+00:00", "node": "change",
                  "stale": ["change", "check"], "reason": "wrong file"}
        triage = {"event": "gate", "at": "2026-01-01T00:00:01+00:00", "node": "triage-review", "verdict": "approved"}
        review = {"event": "gate", "at": "2026-01-01T00:00:01+00:00", "node": "check", "verdict": "needs-changes"}
        for gate in (triage, review):
            lb = board._loop_backs(s, [manual, {"event": "state-write"}, gate])
            self.assertEqual((lb[0]["byGateVerdict"], lb[0]["byNode"]), (None, None), gate["node"])
        caused = {**manual, "reason": "check NEEDS CHANGES: round 1"}
        lb = board._loop_backs(s, [caused, {"event": "state-write"}, review])
        self.assertEqual((lb[0]["byGateVerdict"], lb[0]["byNode"]), ("needs-changes", "check"))

    def test_events_limit(self) -> None:
        t = "D-EV"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        full = self.detail(t)["events"]
        self.assertGreater(len(full), 1)
        self.assertEqual(self.detail(t, "--events", "1")["events"], full[-1:])
        self.assertEqual(self.detail(t, "--events", "0")["events"], [])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["ticket-detail", "--ticket", t, "--events", "-1", *self.p])

    def test_unknown_ticket_is_an_error(self) -> None:
        code, res = run("ticket-detail", "--ticket", "NOPE", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("error", res)

    def test_contract_lists_ticket_detail(self) -> None:
        code, res = run("contract")
        self.assertEqual(code, 0, res)
        self.assertIn("ticket-detail", res["commands"])


if __name__ == "__main__":
    unittest.main()
