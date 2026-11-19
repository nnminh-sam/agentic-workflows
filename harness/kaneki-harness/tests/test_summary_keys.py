"""The keys P1-T04 adds to board.summarize / list_tickets / `sdlc tickets` (H3 top level + H5): title, waiting*,
lastEvent*, contractVersion and the extended strip. Every waiting value must equal board.waiting() called directly."""

from __future__ import annotations

import argparse
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import board  # noqa: E402
from harness.cli import Session  # noqa: E402
from harness.contract import CONTRACT_VERSION  # noqa: E402

STRIP_KEYS = {"node", "status", "verdict", "generation", "boundary", "waiting"}


class SummaryKeysTests(Base):
    def session(self, ticket: str) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=ticket))

    def snapshot(self) -> dict[str, str]:
        root = self.tmp / "tickets"
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    def init(self, ticket: str, lane: str = "lite") -> None:
        code, res = run("init", "--ticket", ticket, "--kind", "feature", "--lane", lane, *self.p)
        self.assertEqual(code, 0, res)

    def summary(self, ticket: str) -> dict:
        return board.summarize(self.session(ticket))

    def assert_waiting_matches(self, ticket: str, summary: dict) -> None:
        """waitingOn/Node/Reason and every strip item's waiting equal board.waiting() on a fresh session."""
        s = self.session(ticket)
        top = board.waiting(s)
        self.assertEqual((summary["waitingOn"], summary["waitingNode"], summary["waitingReason"]),
                         (top["on"], top["node"], top["reason"]))
        self.assertEqual([n["node"] for n in summary["strip"]], s.order)
        for item in summary["strip"]:
            self.assertEqual(set(item), STRIP_KEYS)
            self.assertEqual(item["waiting"], board.waiting(s, item["node"]))

    # --- waiting keys -----------------------------------------------------------------------------------
    def test_lite_scope_before_and_after_artifact(self) -> None:
        t = "S-SCOPE"
        self.init(t)
        before = self.summary(t)
        self.assertEqual((before["waitingOn"], before["waitingNode"], before["waitingReason"]),
                         ("agent", "scope", "needs-run"))
        self.assert_waiting_matches(t, before)
        self.write_artifacts(t, "scope")
        after = self.summary(t)
        self.assertEqual((after["waitingOn"], after["waitingNode"], after["waitingReason"]),
                         ("user", "scope", "user-boundary"))
        self.assert_waiting_matches(t, after)
        self.assertEqual(after["contractVersion"], CONTRACT_VERSION)
        self.assertEqual((after["lastEventKind"], after["lastEventAt"]), (before["lastEventKind"], before["lastEventAt"]))

    def test_finished_ticket(self) -> None:
        t = "S-DONE"
        self.init(t)
        self.complete(t, "scope", actor="user")
        for node in ("change", "check", "delivery-drafts"):
            self.complete(t, node)
        self.complete(t, "deliverable-ready", actor="user")
        summ = self.summary(t)
        self.assertEqual((summ["waitingOn"], summ["waitingNode"], summ["waitingReason"]), ("none", None, "done"))
        self.assert_waiting_matches(t, summ)
        self.assertTrue(all(i["waiting"] == {"on": "none", "node": i["node"], "reason": "done"} for i in summ["strip"]))
        self.assertEqual(summ["strip"][-1]["boundary"], "user")
        self.assertEqual(summ["lastEventKind"], "gate")

    def test_invalidate_event_and_strip_generation(self) -> None:
        t = "S-INV"
        self.init(t)
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        code, res = run("invalidate", "--ticket", t, "--node", "change", "--reason", "redo the edit", *self.p)
        self.assertEqual(code, 0, res)
        summ = self.summary(t)
        events, _ = self.session(t).ticket.verify_log()
        last = [e for e in events if e.get("event") not in board.HIDDEN_EVENTS][-1]
        self.assertEqual(last["event"], "invalidate")
        self.assertEqual((summ["lastEventKind"], summ["lastEventAt"]), ("invalidate", last["at"]))
        by_node = {i["node"]: i for i in summ["strip"]}
        self.assertEqual((by_node["change"]["status"], by_node["change"]["generation"]), ("stale", 2))
        self.assertEqual((by_node["scope"]["status"], by_node["scope"]["generation"]), ("done", 1))
        self.assertEqual((summ["waitingOn"], summ["waitingNode"], summ["waitingReason"]), ("agent", "change", "needs-run"))
        self.assert_waiting_matches(t, summ)

    # --- title ------------------------------------------------------------------------------------------
    def test_title_sources(self) -> None:
        t = "S-TITLE"
        self.init(t)
        self.assertIsNone(self.summary(t)["title"])  # neither ticket.md nor a tracker file
        tracker_file = self.tmp / "tickets" / f"{t}.md"  # local-file tracker, default dir `tickets`
        tracker_file.write_text("intro line\n# S-TITLE: `waiting()` in the rail\n\nbody\n", encoding="utf-8")
        self.assertEqual(self.summary(t)["title"], "`waiting()` in the rail")
        (self.tdir(t) / "ticket.md").write_text("# Copied by intake\n", encoding="utf-8")  # no prefix: kept whole
        self.assertEqual(self.summary(t)["title"], "Copied by intake")

    # --- list_tickets / sdlc tickets ---------------------------------------------------------------------
    def test_tickets_wrapper_items_and_error(self) -> None:
        self.init("S-OK")
        self.write_artifacts("S-OK", "scope")
        self.init("S-BAD")
        (self.tdir("S-BAD") / "state.json").write_text("{", encoding="utf-8")
        before = self.snapshot()
        code, res = run("tickets", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.snapshot(), before, "sdlc tickets wrote to the ticket folder")
        self.assertEqual(res["contractVersion"], CONTRACT_VERSION)
        items = {i["ticket"]: i for i in res["tickets"]}
        self.assertEqual(set(items["S-BAD"]), {"ticket", "error"})
        ok = items["S-OK"]
        self.assertNotIn("contractVersion", ok)
        for key in ("nodes", "events", "receipts", "repositories"):
            self.assertNotIn(key, ok)
        self.assertEqual((ok["waitingOn"], ok["waitingNode"], ok["waitingReason"]), ("user", "scope", "user-boundary"))
        self.assertIn("title", ok)
        self.assertIn("lastEventAt", ok)
        self.assertEqual(ok["lastEventKind"], "init")
        self.assertEqual(ok["strip"][0], {"node": "scope", "status": "pending", "verdict": None, "generation": 1,
                                          "boundary": "user",
                                          "waiting": {"on": "user", "node": "scope", "reason": "user-boundary"}})
        self.assert_waiting_matches("S-OK", ok)

    def test_waiting_reuses_given_problems(self) -> None:
        """summarize passes its one verify_log result to every waiting() call; the kwarg must not change answers."""
        t = "S-PROB"
        self.init(t)
        s = self.session(t)
        _, problems = s.ticket.verify_log()
        for node in [None, *s.order]:
            self.assertEqual(board.waiting(s, node, problems=problems), board.waiting(s, node))
        self.assertEqual(board.waiting(s, problems=["broken"]), {"on": "none", "node": "scope", "reason": "integrity"})


if __name__ == "__main__":
    unittest.main()
