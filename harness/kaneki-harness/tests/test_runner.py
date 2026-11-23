"""The unattended runner (harness/runner.py): sdlc run / tick / recover / autonomy / unpark / run --stop.

Never starts a real claude or codex: SDLC_CLAUDE / SDLC_CODEX point at tests/fakes/fake_agent.py, which simulates
`claude --bg` (prints an id), `agents --json --all`, `stop`, `logs` and `codex exec`, and closes gates through the
real driver."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import board, runner, schema  # noqa: E402
from harness.cli import Session  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_agent.py"


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class RunnerBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.fake_dir = self.tmp / "fake"
        self.env = mock.patch.dict(os.environ, {
            "SDLC_CLAUDE": json.dumps([sys.executable, str(FAKE), "claude"]),
            "SDLC_CODEX": json.dumps([sys.executable, str(FAKE), "codex"]),
            "FAKE_AGENT_DIR": str(self.fake_dir),
            "FAKE_AGENT_SCENARIO": "{}",
        })
        self.env.start()
        os.environ.pop("SDLC_HOST", None)
        self.addCleanup(self.env.stop)
        self.config({})

    def config(self, extra: dict) -> None:
        cfg = {"pollSeconds": 0.01, "notifyCommand": [], "retries": 1, **extra}
        path = self.tmp / ".sdlc" / "runner.local.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def scenario(self, plan: dict) -> None:
        os.environ["FAKE_AGENT_SCENARIO"] = json.dumps(plan)

    def lite_ticket(self, ticket: str, autonomy: bool = False) -> None:
        code, res = run("init", "--ticket", ticket, "--kind", "feature", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        self.complete(ticket, "scope", actor="user")
        if autonomy:
            code, res = run("autonomy", "--ticket", ticket, "--set", "on", *self.p)
            self.assertEqual(code, 0, res)

    def events(self, ticket: str) -> list[dict]:
        lines = (self.tdir(ticket) / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        evs = [json.loads(line) for line in lines if line.strip()]
        for ev in evs:  # every event the runner wrote is valid against the decision-event schema
            self.assertEqual(schema.errors(ev, "decision-event.schema.json"), [], ev)
        return evs

    def kinds(self, ticket: str, *names: str) -> list[dict]:
        return [e for e in self.events(ticket) if e["event"] in names]

    def calls(self) -> list[dict]:
        path = self.fake_dir / "calls.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def bg_calls(self) -> list[dict]:
        return [c for c in self.calls() if "--bg" in c["argv"] or c["argv"][1:2] == ["exec"]]

    def sessions(self) -> dict:
        path = self.fake_dir / "sessions.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def inbox(self) -> list[dict]:
        path = self.tmp / ".sdlc" / "inbox.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def status(self, ticket: str) -> dict:
        code, res = run("status", "--ticket", ticket, "--compact", *self.p)
        self.assertEqual(code, 0, res)
        return res

    def summary(self, ticket: str) -> dict:
        return board.summarize(Session(argparse.Namespace(profile=str(self.profile), ticket=ticket, host=None)))

    def orphan_lease(self, ticket: str, node: str, session_id: str | None = "deadbeef", host: str = "claude") -> str:
        """A lease left by a runner that died (machine shutdown): run-start logged, runner pid gone."""
        s = Session(argparse.Namespace(profile=str(self.profile), ticket=ticket, host=None))
        run_id = runner.new_run_id()
        runner.log_event(s.ticket, {"event": "run-start", "runId": run_id, "node": node, "host": host,
                                    "generation": s.state["nodes"][node]["generation"], "trigger": "tick"})
        lease = {"runId": run_id, "ticket": ticket, "host": host, "node": node,
                 "generation": s.state["nodes"][node]["generation"], "runnerPid": dead_pid(),
                 "sessionId": session_id, "childPid": None, "trigger": "tick",
                 "startedAt": runner.now_iso(), "heartbeatAt": runner.now_iso()}
        runner.write_json(runner.lease_path(s.ticket.dir), lease)
        return run_id


class RunTests(RunnerBase):
    def test_runs_one_node_to_its_gate(self) -> None:
        t = "R-ONE"
        self.lite_ticket(t)
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["result"], "done")
        self.assertEqual([n["node"] for n in res["nodes"]], ["change"])
        self.assertEqual(self.status(t)["nextPending"], "check")  # one node only (--max-nodes 1)
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())
        start, end = self.kinds(t, "run-start"), self.kinds(t, "run-end")
        self.assertEqual((start[0]["node"], start[0]["host"], start[0]["trigger"]), ("change", "claude", "manual"))
        self.assertEqual((end[0]["result"], end[0]["runId"]), ("done", start[0]["runId"]))
        sid = res["nodes"][0]["sessionId"]
        self.assertEqual(self.sessions()[sid]["state"], "stopped")  # the finished session was stopped
        call = self.bg_calls()[0]
        self.assertEqual(Path(call["cwd"]).resolve(), self.tmp.resolve())  # the workspace, nothing else
        self.assertEqual(call["host"], "claude")
        name = call["argv"][call["argv"].index("-n") + 1]
        self.assertEqual(name, f"kaneki:{t}:change:{start[0]['runId']}")
        self.assertEqual(call["argv"][-1], runner.node_prompt("claude", t, "change"))
        log = runner.run_dir(self.tdir(t)) / f"{start[0]['runId']}.jsonl"
        self.assertEqual([json.loads(l)["kind"] for l in log.read_text(encoding="utf-8").splitlines()][-1], "end")

    def test_continues_to_the_next_node(self) -> None:
        t = "R-TWO"
        self.lite_ticket(t)
        code, res = run("run", "--ticket", t, "--host", "claude", "--max-nodes", "2", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual([n["node"] for n in res["nodes"]], ["change", "check"])
        self.assertEqual(self.status(t)["nextPending"], "delivery-drafts")
        self.assertEqual(len(self.bg_calls()), 2)  # one fresh session per node

    def test_parks_at_a_user_boundary_and_unparks_after_the_decision(self) -> None:
        t = "R-USER"
        self.lite_ticket(t)
        code, res = run("run", "--ticket", t, "--host", "claude", "--max-nodes", "10", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["result"], "parked")
        self.assertEqual((res["reason"], res["node"]), ("user-boundary", "deliverable-ready"))
        self.assertEqual([n["node"] for n in res["nodes"]], ["change", "check", "delivery-drafts"])
        park = self.kinds(t, "park")[-1]
        self.assertEqual((park["reason"], park["node"]), ("user-boundary", "deliverable-ready"))
        self.assertEqual(self.inbox()[-1]["kind"], "parked")
        self.assertEqual(self.summary(t)["runner"]["parked"]["reason"], "user-boundary")
        calls = len(self.bg_calls())
        code, again = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual((code, again["result"]), (0, "parked"))
        self.assertEqual(len(self.bg_calls()), calls)  # no session started for a user gate
        self.assertEqual(len(self.kinds(t, "park")), 1)  # parked once, not on every pass
        self.assertFalse(any(e.get("actor") == "user" for e in self.kinds(t, "gate")
                             if e["node"] != "scope"))  # the runner never crossed a user gate
        self.complete(t, "deliverable-ready", actor="user")  # the person decides
        code, done = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual((code, done["result"]), (0, "complete"))
        self.assertEqual(self.kinds(t, "unpark")[-1]["reason"], "progressed")

    def test_parks_when_the_session_asks_and_shows_the_attach_command(self) -> None:
        t = "R-ASK"
        self.lite_ticket(t)
        self.scenario({"change": "ask"})
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["result"], res["reason"]), ("parked", "session-waiting"))
        sid = res["nodes"][0]["sessionId"]
        self.assertEqual(res["nodes"][0]["attach"], f"claude attach {sid}")
        self.assertEqual(self.sessions()[sid]["state"], "blocked")  # left running for the user
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())
        self.assertEqual(self.kinds(t, "run-end")[-1]["result"], "parked")
        self.assertIn(f"claude attach {sid}", self.inbox()[-1]["message"])
        # the user attaches and answers; the session closes the gate itself
        self.complete(t, "change")
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual((code, res["result"]), (0, "done"))
        self.assertEqual(res["nodes"][0]["node"], "check")
        self.assertEqual(self.sessions()[sid]["state"], "stopped")  # the answered session was stopped on unpark

    def test_antigravity_is_refused(self) -> None:
        t = "R-AGY"
        self.lite_ticket(t)
        code, res = run("run", "--ticket", t, "--host", "antigravity", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("refuses host 'antigravity'", res["error"])
        self.assertEqual(self.calls(), [])
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())
        self.config({"host": "antigravity"})
        code, res = run("tick", "--foreground", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("antigravity", res["error"])

    def test_a_live_lease_prevents_a_second_run(self) -> None:
        t = "R-LEASE"
        self.lite_ticket(t, autonomy=True)
        lease = {"runId": "rX", "host": "claude", "node": "change", "runnerPid": os.getpid(),
                 "startedAt": runner.now_iso(), "heartbeatAt": runner.now_iso()}
        runner.write_json(runner.lease_path(self.tdir(t)), lease)
        code, res = run("run", "--ticket", t, "--host", "claude", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("already running", res["error"])
        code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["started"], [])
        self.assertEqual(res["skipped"][0]["why"], "leased")
        self.assertTrue(self.summary(t)["runner"]["lease"]["alive"])
        self.assertEqual(self.bg_calls(), [])

    def test_codex_node_runs_with_codex_exec(self) -> None:
        t = "R-CODEX"
        self.lite_ticket(t)
        code, res = run("run", "--ticket", t, "--host", "codex", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["result"], "done")
        call = self.bg_calls()[0]
        self.assertEqual(call["argv"][:2], ["codex", "exec"])
        self.assertIn("-m", call["argv"])
        self.assertEqual(call["argv"][-1], runner.node_prompt("codex", t, "change"))
        self.assertEqual(call["host"], "codex")
        self.assertEqual(self.kinds(t, "run-start")[0]["host"], "codex")

    def test_codex_without_a_gate_parks(self) -> None:
        t = "R-CODEX-ASK"
        self.lite_ticket(t)
        self.scenario({"change": "ask"})
        code, res = run("run", "--ticket", t, "--host", "codex", *self.p)
        self.assertEqual((code, res["result"], res["reason"]), (0, "parked", "no-gate"))
        self.assertIsNone(res["nodes"][0]["attach"])

    def test_stop_ends_the_session_and_releases_the_lease(self) -> None:
        t = "R-STOP"
        self.lite_ticket(t)
        self.scenario({"change": "hang"})
        # a runner that hangs on a session; stopped from "another terminal" (its pid is dead, so stop finishes it)
        sid = "cafe0001"
        self.fake_dir.mkdir(parents=True, exist_ok=True)
        (self.fake_dir / "sessions.json").write_text(json.dumps(
            {sid: {"id": sid, "name": "x", "state": "running", "kind": "background"}}), encoding="utf-8")
        run_id = self.orphan_lease(t, "change", sid)
        code, res = run("run", "--stop", "--ticket", t, *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["stopped"], res["runId"]), ("run", run_id))
        self.assertEqual(self.sessions()[sid]["state"], "stopped")
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())
        end = self.kinds(t, "run-end")[-1]
        self.assertEqual((end["result"], end["runId"]), ("stopped", run_id))
        code, res = run("run", "--stop", "--ticket", t, *self.p)
        self.assertEqual(code, 2)  # nothing left to stop

    def test_prompt_takes_only_validated_ids(self) -> None:
        with self.assertRaises(Exception):
            runner.node_prompt("claude", "T; rm -rf /", "change")
        with self.assertRaises(Exception):
            runner.node_prompt("claude", "T-1", "Change Now")
        self.assertNotIn("{", runner.node_prompt("codex", "T-1", "code-review"))


class TickTests(RunnerBase):
    def test_tick_skips_opted_out_parked_and_locked_tickets(self) -> None:
        self.lite_ticket("A-OFF")
        self.lite_ticket("B-PARK", autonomy=True)
        self.lite_ticket("C-LOCK", autonomy=True)
        self.lite_ticket("D-GO", autonomy=True)
        s = Session(argparse.Namespace(profile=str(self.profile), ticket="B-PARK", host=None))
        runner.log_event(s.ticket, {"event": "park", "reason": "budget", "node": "change", "message": "test"})
        lock = self.tdir("C-LOCK") / ".lock"
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "command": "record", "since": time.time()}))
        code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        why = {r["ticket"]: r["why"] for r in res["skipped"]}
        self.assertEqual(why, {"A-OFF": "opted-out", "B-PARK": "parked", "C-LOCK": "locked"})
        self.assertEqual([r["ticket"] for r in res["started"]], ["D-GO"])
        self.assertEqual(res["started"][0]["nodes"][0]["node"], "change")
        self.assertEqual(self.status("D-GO")["nextPending"], "check")  # one node per ticket per tick
        self.assertEqual(self.kinds("D-GO", "run-start")[0]["trigger"], "tick")

    def test_max_concurrent_and_autonomy_off(self) -> None:
        self.lite_ticket("E-1", autonomy=True)
        self.lite_ticket("E-2", autonomy=True)
        code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(len(res["started"]), 1)  # default maxConcurrent 1
        self.assertEqual(res["skipped"][0]["why"], "max-concurrent")
        code, res = run("autonomy", "--ticket", "E-2", "--set", "off", "--reason", "pause", *self.p)
        self.assertEqual((code, res["autonomy"], res["previous"]), (0, False, True))
        code, res = run("autonomy", "--ticket", "E-2", *self.p)
        self.assertEqual(res["autonomy"], False)
        self.assertEqual([e["enabled"] for e in self.kinds("E-2", "autonomy")], [True, False])
        self.assertFalse(self.summary("E-2")["runner"]["autonomy"])

    def test_detached_tick_starts_a_run_that_holds_the_lease(self) -> None:
        t = "J-BG"
        self.lite_ticket(t, autonomy=True)
        code, res = run("tick", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["started"][0]["ticket"], res["started"][0]["spawned"]), (t, True))
        deadline = time.time() + 120
        while time.time() < deadline and not self.kinds(t, "run-end"):
            time.sleep(0.5)
        self.assertEqual(self.kinds(t, "run-end")[-1]["result"], "done")
        self.assertEqual(self.kinds(t, "run-start")[-1]["trigger"], "tick")
        while time.time() < deadline and runner.lease_path(self.tdir(t)).exists():
            time.sleep(0.2)
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())

    def test_budget_hook_refusal_parks(self) -> None:
        t = "K-BUDGET"
        self.lite_ticket(t, autonomy=True)
        seen = []

        def deny(ctx: dict) -> tuple[bool, str]:
            seen.append(ctx)
            return False, "ticket budget spent"

        with mock.patch.object(runner, "budget_allows", deny):
            code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["skipped"][0]["why"], "parked")
        self.assertEqual(self.kinds(t, "park")[-1]["reason"], "budget")
        self.assertEqual((seen[0]["phase"], seen[0]["node"], seen[0]["host"]), ("start", "change", "claude"))
        self.assertEqual(self.bg_calls(), [])

    def test_usage_limit_backs_off(self) -> None:
        self.lite_ticket("F-USE", autonomy=True)
        self.scenario({"change": "usage"})
        code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        node = res["started"][0]["nodes"][0]
        self.assertEqual((node["result"], node["reason"]), ("failed", "usage-limit"))
        marker = json.loads((self.tmp / ".sdlc" / "run" / "backoff-claude.json").read_text(encoding="utf-8"))
        wait = runner.parse_iso(marker["until"]) - time.time()
        self.assertTrue(25 * 60 < wait <= 30 * 60 + 5, wait)  # "resets in 30 minutes"
        self.assertEqual(self.inbox()[-1]["kind"], "backoff")
        self.scenario({})
        calls = len(self.bg_calls())
        code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertIn("backoff", res)
        self.assertEqual(res["started"], [])
        self.assertEqual(len(self.bg_calls()), calls)  # nothing started until the retry time
        code, res = run("run", "--ticket", "F-USE", "--host", "claude", *self.p)
        self.assertEqual(res["result"], "backoff")

    def test_codex_usage_limit_and_start_failure(self) -> None:
        self.lite_ticket("G-USE")
        self.scenario({"change": "usage"})
        code, res = run("run", "--ticket", "G-USE", "--host", "codex", *self.p)
        self.assertEqual((code, res["nodes"][0]["reason"]), (0, "usage-limit"))
        marker = json.loads((self.tmp / ".sdlc" / "run" / "backoff-codex.json").read_text(encoding="utf-8"))
        self.assertGreater(runner.parse_iso(marker["until"]) - time.time(), 115 * 60)  # "Try again in 2 hours"
        self.scenario({"change": "nostart"})
        code, res = run("run", "--ticket", "G-USE", "--host", "claude", *self.p)
        self.assertEqual((code, res["nodes"][0]["reason"]), (0, "usage-limit"))
        self.assertTrue((self.tmp / ".sdlc" / "run" / "backoff-claude.json").is_file())

    def test_retry_after_parsing(self) -> None:
        now = 1_700_000_000.0
        self.assertEqual(runner.retry_after("rate limit, retry after 90 seconds", 60, now), now + 90)
        self.assertEqual(runner.retry_after("usage limit", 60, now), now + 3600)
        self.assertEqual(runner.retry_after("Claude AI usage limit reached|1700000600", 60, now), 1700000600)
        self.assertEqual(runner.retry_after("try again in 3 days hours", 60, now), now + 3600)
        self.assertLessEqual(runner.retry_after("resets 5pm", 60, now), now + 24 * 3600)
        self.assertTrue(runner.usage_limit("You've hit your usage limit"))
        self.assertFalse(runner.usage_limit("node closed"))


class RecoverTests(RunnerBase):
    def test_orphan_restarts_once_then_parks(self) -> None:
        t = "H-ORPHAN"
        self.lite_ticket(t, autonomy=True)
        run_id = self.orphan_lease(t, "change")
        code, res = run("recover", "--foreground", *self.p)
        self.assertEqual(code, 0, res)
        entry = res["recovered"][0]
        self.assertEqual((entry["action"], entry["why"], entry["runId"]), ("failed", "orphan", run_id))
        self.assertEqual(entry["restart"]["result"], "done")  # restarted at nextPending in a fresh session
        self.assertEqual(entry["restart"]["nodes"][0]["node"], "change")
        ends = self.kinds(t, "run-end")
        self.assertEqual([(e["result"], e.get("reason")) for e in ends], [("failed", "orphan"), ("done", None)])
        self.assertEqual(self.status(t)["nextPending"], "check")
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())

    def test_orphan_retries_are_bounded(self) -> None:
        t = "H-BOUND"
        self.lite_ticket(t, autonomy=True)
        self.scenario({"change": "vanish"})  # every fresh session disappears too
        self.orphan_lease(t, "change")
        code, res = run("recover", "--foreground", *self.p)
        self.assertEqual(code, 0, res)
        restart = res["recovered"][0]["restart"]
        self.assertEqual((restart["result"], restart["nodes"][0]["reason"]), ("failed", "orphan"))  # one retry
        self.assertEqual(len(self.bg_calls()), 1)
        code, res = run("tick", "--foreground", "--host", "claude", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["skipped"][0]["why"], "parked")
        self.assertEqual(self.kinds(t, "park")[-1]["reason"], "retries-exhausted")
        self.assertEqual(len(self.bg_calls()), 1)  # no third attempt
        code, res = run("unpark", "--ticket", t, "--reason", "fixed the cause", *self.p)
        self.assertEqual((code, res["was"]["reason"]), (0, "retries-exhausted"))
        self.assertIsNone(self.summary(t)["runner"]["parked"])

    def test_gate_already_closed_just_releases(self) -> None:
        t = "H-GATE"
        self.lite_ticket(t, autonomy=True)
        run_id = self.orphan_lease(t, "change")
        self.complete(t, "change")  # the session closed the gate before the machine went down
        code, res = run("recover", "--foreground", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["recovered"][0]["action"], "released")
        self.assertEqual(self.kinds(t, "run-end")[-1], {**self.kinds(t, "run-end")[-1], "runId": run_id,
                                                         "result": "done"})
        self.assertEqual(self.bg_calls(), [])  # nothing restarted
        self.assertFalse(runner.lease_path(self.tdir(t)).exists())

    def test_waiting_session_without_runner_parks(self) -> None:
        t = "H-WAIT"
        self.lite_ticket(t)
        sid = "beef0002"
        self.fake_dir.mkdir(parents=True, exist_ok=True)
        (self.fake_dir / "sessions.json").write_text(json.dumps(
            {sid: {"id": sid, "name": "x", "state": "blocked", "kind": "background", "_logs": "question?"}}),
            encoding="utf-8")
        self.orphan_lease(t, "change", sid)
        code, res = run("recover", "--foreground", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["recovered"][0]["action"], res["recovered"][0]["attach"]),
                         ("parked", f"claude attach {sid}"))

    def test_orphan_without_autonomy_is_not_restarted(self) -> None:
        t = "H-OFF"
        self.lite_ticket(t)
        self.orphan_lease(t, "change")
        code, res = run("recover", "--foreground", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["recovered"][0]["restart"], "skipped (autonomy off)")
        self.assertEqual(self.bg_calls(), [])


if __name__ == "__main__":
    unittest.main()
