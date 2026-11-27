"""Budgets and gate G8 (harness/budget.py, plan M3·2): profile `budgets`, `budget.check`, the G8 refusal of
`sdlc packet`, the user's one-node override, runner enforcement, `sdlc budget` and the STATUS / board fields.

Usage is synthetic: `cost.rows` (the cached rows `cost.measured` filters) is patched with hand-made rows, so the real
`measured()` filtering (ticket, node, local day, unit + controller kinds, unpriced tokens) is what the checks see."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402
from test_runner import RunnerBase  # noqa: E402

from harness import board, budget, cost, schema  # noqa: E402
from harness.cli import Session  # noqa: E402
from harness.errors import HarnessError  # noqa: E402
from harness.profile import load_profile  # noqa: E402

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def row(ticket: str, node: str | None, tokens: int, usd: float | None = None, *, kind: str = "unit",
        src: str | None = None, day: str = TODAY) -> dict:
    src = src or ("reported" if usd is not None else "unknown")
    return {"ticket": ticket, "node": node, "kind": kind, "dispatchId": None, "agent": "claude", "model": "m",
            "src": src, "day": day, "msgs": 1, "usd": usd, "unknownSteps": 0,
            "input": tokens, "output": 0, "cacheRead": 0, "cacheWrite": 0}


class Synthetic:
    """Patch cost.rows with `self.rows` and switch off budget's 2-second memo."""

    def synth(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.rows_calls = 0

        def fake_rows(profile_path, ticket=None):
            self.rows_calls += 1
            return {"rows": list(self.rows), "reported": {}, "tickets": sorted({r["ticket"] for r in self.rows}),
                    "prices": {}, "fingerprint": "synthetic"}
        p = mock.patch.object(cost, "rows", fake_rows)
        p.start()
        self.addCleanup(p.stop)
        m = mock.patch.object(budget, "MEMO_SECONDS", 0)
        m.start()
        self.addCleanup(m.stop)
        budget._memo.clear()

    def set_budgets(self, block: dict | None) -> None:
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        if block is None:
            prof.pop("budgets", None)
        else:
            prof["budgets"] = block
        self.profile.write_text(json.dumps(prof), encoding="utf-8")


class CheckTests(Synthetic, Base):
    def check(self, node: str | None = "change", **kw) -> dict:
        return budget.check(self.profile, "B-1", node, events=[], generation=1, **kw)

    def test_empty_budget_block_means_no_checks_and_no_cost_read(self) -> None:
        self.synth([row("B-1", "change", 10 ** 9, 999.0)])
        self.set_budgets({})
        res = self.check()
        self.assertEqual((res["configured"], res["allowed"], res["level"]), (False, True, "ok"))
        self.assertEqual(self.rows_calls, 0)

    def test_no_budget_block_means_default_caps(self) -> None:
        """A4: a profile that never set budgets still stops a runaway node."""
        self.synth([row("B-1", "change", 16_000_000, None)])
        res = self.check()
        self.assertEqual((res["configured"], res["source"], res["allowed"], res["over"]), (True, "default", False, ["node"]))
        self.assertEqual(res["caps"]["node"], budget.DEFAULTS["node"]["default"])
        self.assertEqual(budget.caps_for(budget.DEFAULTS, "execute-and-evidence")["node"]["minutes"], 60)

    def test_minutes_cap(self) -> None:
        """A4: the wall clock of the node's round, from its first packet."""
        self.synth([row("B-1", "execute-and-evidence", 100, None)])
        self.set_budgets({"node": {"execute-and-evidence": {"minutes": 60}}})
        t0 = 1_800_000_000.0
        res = budget.check(self.profile, "B-1", "execute-and-evidence", now=t0 + 30 * 60, events=[], generation=1,
                           started=t0)
        self.assertEqual((res["level"], res["usage"]["node"]["minutes"], res["secondsLeft"]), ("ok", 30.0, 1800))
        res = budget.check(self.profile, "B-1", "execute-and-evidence", now=t0 + 61 * 60, events=[], generation=1,
                           started=t0)
        self.assertEqual((res["allowed"], res["over"]), (False, ["node"]))
        self.assertIn("time cap reached", budget.refusal(res))
        # no start recorded yet: the clock has not started, nothing to refuse
        res = budget.check(self.profile, "B-1", "execute-and-evidence", now=t0, events=[], generation=1)
        self.assertEqual(res["level"], "ok")
        self.assertIsNone(res.get("deadline"))

    def test_minutes_only_on_node_caps(self) -> None:
        errs = budget.config_errors({"ticket": {"minutes": 5}, "node": {"default": {"minutes": 0}}})
        self.assertTrue(any("budgets.ticket.minutes is unknown" in e for e in errs), errs)
        self.assertTrue(any("budgets.node.default.minutes must be a number > 0" in e for e in errs), errs)

    def test_ticket_cap(self) -> None:
        self.synth([row("B-1", "scope", 100, 30.0), row("B-1", "change", 100, 12.0), row("OTHER", "change", 100, 50.0)])
        self.set_budgets({"ticket": {"usd": 40}})
        res = self.check()
        self.assertEqual((res["level"], res["allowed"], res["over"]), ("over", False, ["ticket"]))
        self.assertEqual(res["usage"]["ticket"]["usd"], 42.0)  # the other ticket is not counted
        self.assertIn("ticket B-1: $42.00 of $40.00 (USD cap reached)", res["reasons"])
        self.set_budgets({"ticket": {"usd": 100}})
        self.assertEqual(self.check()["level"], "ok")

    def test_node_cap_counts_units_and_controller_and_merges_default(self) -> None:
        self.synth([row("B-1", "change", 9_000_000, 3.0), row("B-1", "change", 7_000_000, 2.0, kind="controller"),
                    row("B-1", "scope", 50_000_000, 30.0)])
        self.set_budgets({"node": {"default": {"usd": 6, "tokens": 15_000_000}, "check": {"usd": 8}}})
        res = self.check("change")
        self.assertEqual(res["usage"]["node"]["tokens"], 16_000_000)  # 9M unit + 7M controller
        self.assertEqual((res["level"], res["over"]), ("over", ["node"]))
        self.assertEqual(res["caps"]["node"], {"usd": 6, "tokens": 15_000_000})
        self.assertEqual(self.check("check")["caps"]["node"], {"usd": 8, "tokens": 15_000_000})  # key by key
        self.assertEqual(self.check("scope")["over"], ["node"])
        self.assertEqual(self.check(None)["usage"].get("node"), None)  # no node: no node scope

    def test_daily_cap_counts_only_this_local_day_across_tickets(self) -> None:
        self.synth([row("B-1", "change", 100, 20.0), row("OTHER", "x", 100, 45.0),
                    row("B-1", "change", 100, 500.0, day=YESTERDAY)])
        self.set_budgets({"daily": {"usd": 60}})
        res = self.check()
        self.assertEqual(res["usage"]["daily"]["usd"], 65.0)
        self.assertEqual((res["level"], res["over"], res["day"]), ("over", ["daily"], TODAY))
        self.assertEqual(self.check(now=date.today() + timedelta(days=1))["level"], "ok")  # a new day

    def test_warn_at(self) -> None:
        self.synth([row("B-1", "change", 100, 33.0)])
        self.set_budgets({"ticket": {"usd": 40}})  # 82.5% >= default 0.8
        res = self.check()
        self.assertEqual((res["level"], res["allowed"]), ("warn", True))
        self.assertIn("82% of the cap", budget.warning(res))
        self.set_budgets({"ticket": {"usd": 40}, "warnAt": 0.9})
        self.assertEqual(self.check()["level"], "ok")

    def test_partial_usd_falls_back_to_the_token_cap(self) -> None:
        self.synth([row("B-1", "change", 1_000_000, 5.0), row("B-1", "change", 4_000_000)])  # 4M unpriced
        self.set_budgets({"ticket": {"usd": 40, "tokens": 6_000_000}})
        res = self.check()
        use = res["usage"]["ticket"]
        self.assertEqual((use["usd"], use["unpricedTokens"], use["partial"]), (5.0, 4_000_000, True))
        self.assertEqual((res["level"], res["allowed"]), ("warn", True))  # partial USD always warns, never refuses
        self.assertTrue(any("USD is partial" in r and "token cap governs" in r for r in res["reasons"]))
        self.rows.append(row("B-1", "change", 2_000_000))  # 7M tokens >= 6M token cap
        res = self.check()
        self.assertEqual((res["level"], res["allowed"]), ("over", False))
        self.set_budgets({"ticket": {"usd": 40}})  # no token cap: unpriced usage is not capped, only warned
        res = self.check()
        self.assertEqual((res["level"], res["allowed"]), ("warn", True))
        self.assertTrue(any("no token cap" in r for r in res["reasons"]))

    def test_action_warn_never_refuses(self) -> None:
        self.synth([row("B-1", "change", 100, 99.0)])
        self.set_budgets({"ticket": {"usd": 40}, "action": "warn"})
        res = self.check()
        self.assertEqual((res["level"], res["allowed"]), ("over", True))
        self.assertTrue(budget.warning(res).startswith("G8: over budget (action warn)"))

    def test_override_matches_node_generation_and_scopes(self) -> None:
        ev = {"event": "budget-override", "node": "change", "generation": 1, "scopes": ["ticket"], "actor": "user",
              "reason": "r"}
        self.synth([row("B-1", "change", 100, 99.0)])
        self.set_budgets({"ticket": {"usd": 40}, "daily": {"usd": 1000}})
        ok = budget.check(self.profile, "B-1", "change", events=[ev], generation=1)
        self.assertEqual((ok["allowed"], ok["override"]["scopes"]), (True, ["ticket"]))
        self.assertFalse(budget.check(self.profile, "B-1", "change", events=[ev], generation=2)["allowed"])
        self.assertFalse(budget.check(self.profile, "B-1", "check", events=[ev], generation=1)["allowed"])
        self.set_budgets({"ticket": {"usd": 40}, "daily": {"usd": 50}})  # a scope the override did not cover
        self.assertFalse(budget.check(self.profile, "B-1", "change", events=[ev], generation=1)["allowed"])
        self.assertFalse(budget.check(self.profile, "B-1", "change", events=[{**ev, "actor": "controller"}],
                                      generation=1)["allowed"])

    def test_unreadable_usage_warns_instead_of_refusing(self) -> None:
        self.set_budgets({"ticket": {"usd": 1}})
        with mock.patch.object(cost, "measured", side_effect=OSError("disk gone")), \
                mock.patch.object(budget, "MEMO_SECONDS", 0):
            budget._memo.clear()
            res = self.check()
        self.assertEqual((res["allowed"], res["level"]), (True, "warn"))


class ProfileSchemaTests(Base):
    def load_with(self, block) -> dict:
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof["budgets"] = block
        self.profile.write_text(json.dumps(prof), encoding="utf-8")
        return load_profile(self.profile)

    def test_valid_block_loads(self) -> None:
        block = {"ticket": {"usd": 40, "tokens": 60_000_000},
                 "node": {"default": {"usd": 6, "tokens": 15_000_000}, "code-review": {"usd": 8}},
                 "daily": {"usd": 60, "tokens": 120_000_000}, "warnAt": 0.8, "action": "park"}
        self.assertEqual(self.load_with(block)["budgets"], block)
        self.assertEqual(self.load_with({})["budgets"], {})

    def test_invalid_blocks_are_refused(self) -> None:
        for block, needle in (({"ticket": {"usd": -1}}, "budgets.ticket.usd"),
                              ({"ticket": {"dollars": 1}}, "budgets.ticket.dollars is unknown"),
                              ({"daily": {"tokens": 1.5}}, "budgets.daily.tokens"),
                              ({"node": {"default": 5}}, "budgets.node.default"),
                              ({"warnAt": 1.5}, "budgets.warnAt"),
                              ({"action": "stop"}, "budgets.action"),
                              ({"weekly": {}}, "budgets.weekly is unknown"),
                              ([], "budgets must be an object")):
            with self.subTest(block=block):
                with self.assertRaises(HarnessError) as cm:
                    self.load_with(block)
                self.assertIn(needle, str(cm.exception))

    def test_schema_itself_rejects_bad_shapes(self) -> None:
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof["budgets"] = {"ticket": {"usd": 40}, "action": "warn"}
        self.assertEqual(schema.errors(prof, "project-profile.schema.json"), [])
        prof["budgets"] = {"ticket": {"usd": "40"}, "action": "nope"}
        self.assertTrue(schema.errors(prof, "project-profile.schema.json"))


class PacketGateTests(Synthetic, Base):
    T = "G8-1"

    def setUp(self) -> None:
        super().setUp()
        code, res = run("init", "--ticket", self.T, "--kind", "feature", "--lane", "full", *self.p)
        self.assertEqual(code, 0, res)

    def packet(self, node: str = "prepare", unit: str = "worker") -> tuple[int, dict]:
        return run("packet", "--ticket", self.T, "--node", node, "--unit", unit, *self.p)

    def log_lines(self) -> list[str]:
        return (self.tdir(self.T) / "decisions.jsonl").read_text(encoding="utf-8").splitlines()

    def test_no_block_packet_unchanged(self) -> None:
        self.synth([row(self.T, "prepare", 10 ** 9, 999.0)])
        self.set_budgets({})
        with mock.patch.object(budget, "check", side_effect=AssertionError("empty block: no check")):
            code, res = self.packet()
        self.assertEqual(code, 0, res)
        self.assertNotIn("budget", res)

    def test_over_refuses_with_g8_and_changes_nothing(self) -> None:
        self.synth([row(self.T, "prepare", 100, 41.5)])
        self.set_budgets({"ticket": {"usd": 40}})
        before = self.log_lines()
        state = (self.tdir(self.T) / "state.json").read_bytes()
        code, res = self.packet()
        self.assertEqual(code, 2)
        err = res["error"]
        self.assertTrue(err.startswith(f"G8: budget over for {self.T}/prepare: ticket {self.T}: $41.50 of $40.00"), err)
        self.assertIn("raise budgets.ticket in the profile", err)
        self.assertIn(f"sdlc budget --ticket {self.T} --allow-node prepare --reason", err)
        self.assertIn("--actor user", err)
        self.assertEqual(self.log_lines(), before)  # no event
        self.assertEqual((self.tdir(self.T) / "state.json").read_bytes(), state)  # no state change
        self.assertFalse(list((self.tdir(self.T) / "packets").glob("prepare.*.md")))

    def test_node_scope_message_names_the_node_cap(self) -> None:
        self.synth([row(self.T, "prepare", 20_000_000, kind="controller")])
        self.set_budgets({"node": {"default": {"tokens": 15_000_000}}})
        code, res = self.packet()
        self.assertEqual(code, 2)
        self.assertIn("node prepare: 20,000,000 of 15,000,000 tokens (tokens cap reached)", res["error"])
        self.assertIn("budgets.node.prepare (or budgets.node.default)", res["error"])

    def test_warn_renders_with_a_warning(self) -> None:
        self.synth([row(self.T, "prepare", 100, 35.0)])
        self.set_budgets({"ticket": {"usd": 40}})
        code, res = self.packet()
        self.assertEqual(code, 0, res)
        self.assertEqual(res["budget"]["level"], "warn")
        self.assertTrue(res["budgetWarning"].startswith("G8 warning: ticket G8-1: $35.00 of $40.00"))

    def test_override_lets_exactly_that_node_through_once(self) -> None:
        self.synth([row(self.T, "prepare", 100, 41.5)])
        self.set_budgets({"ticket": {"usd": 40}})
        code, res = run("budget", "--ticket", self.T, "--allow-node", "prepare", "--reason", "finish it", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("--actor user", res["error"])
        code, res = run("budget", "--ticket", self.T, "--allow-node", "prepare", "--actor", "user", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("--reason is required", res["error"])
        code, res = run("budget", "--ticket", self.T, "--allow-node", "prepare", "--reason", "finish it",
                        "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["node"], res["generation"], res["override"]), ("prepare", 1, ["ticket"]))
        ev = json.loads(self.log_lines()[-1])
        self.assertEqual((ev["event"], ev["node"], ev["scopes"], ev["actor"], ev["reason"]),
                         ("budget-override", "prepare", ["ticket"], "user", "finish it"))
        self.assertEqual(schema.errors(ev, "decision-event.schema.json"), [])
        code, res = self.packet()
        self.assertEqual(code, 0, res)
        self.assertEqual((res["budget"]["level"], res["budget"]["override"]["node"]), ("over", "prepare"))
        self.assertIn("user override", res["budgetWarning"])
        code, res = self.packet("requirement-analysis")  # another node is not covered
        self.assertEqual(code, 2)
        self.assertTrue(res["error"].startswith("G8:"))
        s = Session(argparse.Namespace(profile=str(self.profile), ticket=self.T, host=None))
        events, _ = s.ticket.verify_log()
        nxt = budget.check(self.profile, self.T, "prepare", events=events, generation=2)  # the next round
        self.assertFalse(nxt["allowed"])

    def test_override_refused_when_nothing_is_over(self) -> None:
        self.synth([])
        self.set_budgets({"ticket": {"usd": 40}})
        code, res = run("budget", "--ticket", self.T, "--allow-node", "prepare", "--reason", "x", "--actor", "user", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("not over any budget", res["error"])
        code, res = run("budget", "--ticket", self.T, "--allow-node", "prepare", "--reason", "x", "--actor", "user",
                        "--scope", "all", *self.p)
        self.assertEqual(code, 0, res)

    def test_packet_starts_the_round_clock_and_names_the_deadline(self) -> None:
        self.synth([row(self.T, "prepare", 1_000, None)])
        code, res = self.packet()
        self.assertEqual(code, 0, res)
        self.assertEqual(res["budget"]["source"], "default")
        self.assertEqual(res["budget"]["secondsLeft"], budget.DEFAULTS["node"]["default"]["minutes"] * 60)
        self.assertIn("Time cap: this node's round ends at", Path(res["path"]).read_text(encoding="utf-8"))
        st = json.loads((self.tdir(self.T) / "state.json").read_text(encoding="utf-8"))["nodes"]["prepare"]["startedAt"]
        self.assertEqual(st["generation"], 1)
        code, res2 = self.packet()  # the clock is not restarted by a second packet of the round
        st2 = json.loads((self.tdir(self.T) / "state.json").read_text(encoding="utf-8"))["nodes"]["prepare"]["startedAt"]
        self.assertEqual(st, st2)

    def test_sdlc_budget_status_is_read_only(self) -> None:
        self.synth([row(self.T, "prepare", 1_000, 10.0)])
        self.set_budgets({})
        code, res = run("budget", "--ticket", self.T, *self.p)
        self.assertEqual((code, res["configured"]), (0, False))
        self.set_budgets({"ticket": {"usd": 40, "tokens": 2_000}, "daily": {"usd": 60}})
        before = self.log_lines()
        code, res = run("budget", "--ticket", self.T, *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["node"], res["level"], res["allowed"]), ("prepare", "ok", True))
        self.assertEqual(res["caps"]["ticket"], {"usd": 40, "tokens": 2_000})
        self.assertEqual(res["usage"]["ticket"]["tokens"], 1_000)
        self.assertEqual(res["usage"]["daily"]["usd"], 10.0)
        self.assertTrue(res["status"].startswith("**Budget:** ok"))
        self.assertEqual(self.log_lines(), before)

    def test_status_md_and_board_fields(self) -> None:
        self.synth([row(self.T, "prepare", 1_000, 35.0)])
        self.set_budgets({})
        s = Session(argparse.Namespace(profile=str(self.profile), ticket=self.T, host=None))
        self.assertIsNone(board.summarize(s)["budget"])
        self.set_budgets({"ticket": {"usd": 40}})
        s = Session(argparse.Namespace(profile=str(self.profile), ticket=self.T, host=None))
        b = board.summarize(s)["budget"]
        self.assertEqual((b["level"], b["node"], b["caps"]["ticket"]), ("warn", "prepare", {"usd": 40}))
        self.assertEqual(b["usage"]["ticket"]["usd"], 35.0)
        board.write_status(s)
        md = (self.tdir(self.T) / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("**Budget:** ⚠️ warn · ticket $35.00/$40.00", md)
        items = board.list_tickets(self.profile)
        self.assertEqual(items[0]["budget"]["level"], "warn")
        from harness.web import Board
        rep = Board(self.profile).cost(self.T, None, None, None)
        self.assertEqual(rep["budget"]["level"], "warn")


class RunnerBudgetTests(Synthetic, RunnerBase):
    def test_parks_at_start_notifies_budget_then_runs_after_override(self) -> None:
        t = "RB-1"
        self.lite_ticket(t)
        self.synth([row(t, "change", 100, 50.0)])
        self.set_budgets({"ticket": {"usd": 40}})
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["result"], res["reason"]), ("parked", "budget"))
        park = self.kinds(t, "park")[-1]
        self.assertEqual((park["reason"], park["node"]), ("budget", "change"))
        self.assertIn("G8: budget over", park["message"])
        note = self.inbox()[-1]
        self.assertEqual((note["kind"], note["reason"]), ("budget", "budget"))  # title: "kaneki RB-1: budget"
        self.assertEqual(self.bg_calls(), [])
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(res["result"], "parked")  # still parked: nothing changed
        self.assertEqual(self.bg_calls(), [])
        code, res = run("budget", "--ticket", t, "--allow-node", "change", "--reason", "ok", "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["result"], "done")
        self.assertEqual(self.kinds(t, "unpark")[-1]["reason"], "budget-override")
        self.assertEqual(len(self.bg_calls()), 1)

    def test_stops_and_parks_while_running(self) -> None:
        t = "RB-2"
        self.lite_ticket(t)
        self.synth([])
        self.set_budgets({"node": {"default": {"tokens": 1_000_000}}})
        self.scenario({"change": "hang"})
        from harness import runner
        original = runner.ClaudeCLI.start

        def start_then_spend(cli, *a, **kw):
            out = original(cli, *a, **kw)
            self.rows.append(row(t, "change", 2_000_000))  # the session spends past the node cap
            return out
        with mock.patch.object(runner.ClaudeCLI, "start", start_then_spend):
            code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["result"], res["reason"]), ("parked", "budget"))
        sid = self.bg_calls() and next(iter(self.sessions()))
        self.assertEqual(self.sessions()[sid]["state"], "stopped")
        end = self.kinds(t, "run-end")[-1]
        self.assertEqual((end["result"], end["reason"]), ("parked", "budget"))
        park = self.kinds(t, "park")[-1]
        self.assertEqual(park["reason"], "budget")
        self.assertIn("budget stopped change", park["message"])
        self.assertEqual(self.inbox()[-1]["kind"], "budget")
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())

    def test_empty_block_allows(self) -> None:
        from harness import runner
        self.synth([row("X", "change", 10 ** 9, 999.0)])
        self.set_budgets({})
        ok, why = runner.budget_allows({"profile": self.profile, "ticket": "X", "node": "change", "host": "claude",
                                        "phase": "start", "runId": None})
        self.assertEqual((ok, why), (True, None))
        self.assertEqual(self.rows_calls, 0)


if __name__ == "__main__":
    unittest.main()
