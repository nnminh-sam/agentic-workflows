"""Reflect node (M4·1): trace position, learning.reflect modes, signals, proposals gate, handback restart, ship."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import graph as G  # noqa: E402
from harness import learning as L  # noqa: E402
from harness import schema  # noqa: E402
from harness.profile import build_context, load_profile  # noqa: E402
from test_lifecycle import Base, run  # noqa: E402


class ReflectBase(Base):
    def set_mode(self, mode: str | None) -> None:
        prof = json.loads(self.profile.read_text())
        if mode is None:
            prof.pop("learning", None)
        else:
            prof["learning"] = {"reflect": mode}
        self.profile.write_text(json.dumps(prof))

    def events(self, t: str) -> list[dict]:
        return [json.loads(l) for l in (self.tdir(t) / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if l]

    def lite_clean(self, t: str) -> dict:
        """A lite ticket where nothing goes wrong; returns the deliverable-ready gate result."""
        code, res = run("init", "--ticket", t, "--kind", "bug", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        self.complete(t, "check")
        self.complete(t, "delivery-drafts")
        return self.complete(t, "deliverable-ready", actor="user")

    def lite_with_loop(self, t: str) -> dict:
        """A lite ticket whose check loops back once (a signal); returns the deliverable-ready gate result."""
        code, res = run("init", "--ticket", t, "--kind", "bug", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        self.write_artifacts(t, "check", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "check", "--status", "needs-changes", "--note", "edge case",
                        *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "change")
        self.complete(t, "check")
        self.complete(t, "delivery-drafts")
        return self.complete(t, "deliverable-ready", actor="user")

    def record_reflect_unit(self, t: str) -> dict:
        code, pk = run("packet", "--ticket", t, "--node", "reflect", "--unit", "worker", *self.p)
        self.assertEqual(code, 0, pk)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n")
        code, rec = run("record", "--ticket", t, "--node", "reflect", "--unit", "worker", "--result", pk["resultPath"],
                        "--model", "sonnet", *self.p)
        self.assertEqual(code, 0, rec)
        return pk

    def write_reflection(self, t: str, proposals: list[dict] | dict) -> None:
        (self.tdir(t) / "reflection.md").write_text(
            "# reflect\n\n## Signals\n- check looped back once\n\n## Causes\n- the scope missed an edge case\n\n"
            "## Proposals\n- see proposals.json\n", encoding="utf-8")
        doc = proposals if isinstance(proposals, dict) else {"schemaVersion": 1, "ticket": t, "proposals": proposals}
        (self.tdir(t) / "proposals.json").write_text(json.dumps(doc), encoding="utf-8")

    def good_proposal(self, t: str, ref: str) -> dict:
        return {"id": "scope-edge-cases", "type": "lesson", "title": "Name the edge cases in the scope",
                "body": "When a lite scope touches main.py, list the edge cases the check will try, so the change "
                        "covers them in its first round.",
                "evidence": [{"ticket": t, "ref": ref}],
                "scope": {"nodes": ["scope", "change"], "stacks": ["python"], "paths": ["main.py"]},
                "metric": "check loop-backs per lite ticket", "baseline": 1, "status": "proposed"}


class TraceTests(ReflectBase):
    def test_reflect_closes_every_trace(self) -> None:
        profile = load_profile(self.profile)
        graph = G.load_graph(HOME, profile, self.tmp)
        ctx = build_context(profile, "T", "bug", None, None, None)
        self.assertEqual(ctx["reflect"], "on-signal")
        full = G.trace(graph, ctx)
        self.assertEqual(full[-2:], ["deliverable-ready", "reflect"])
        review = G.trace(graph, {**ctx, "reviewRound": 1})
        self.assertEqual(review[-2:], ["respond-review", "reflect"])
        lite = G.trace(graph, {**ctx, "lane": "lite"})
        self.assertEqual(lite[-2:], ["deliverable-ready", "reflect"])
        self.assertEqual(G.trace(graph, {**ctx, "lane": "lite", "reviewRound": 2})[-2:], ["respond-review", "reflect"])
        for mode in ("always", "on-signal"):
            self.assertIn("reflect", G.trace(graph, {**ctx, "reflect": mode}))
        self.assertNotIn("reflect", G.trace(graph, {**ctx, "reflect": "off"}))
        self.assertNotIn("reflect", G.trace(graph, {**ctx, "reflect": "off", "lane": "lite", "reviewRound": 1}))
        spec = graph["nodes"]["reflect"]
        self.assertEqual((spec["layer"], spec["boundary"], spec["units"]),
                         ("learning", "controller", [{"id": "worker", "role": "worker", "tier": "routine"}]))
        self.assertIn("release-ready", spec["requires"])
        self.assertEqual(spec["artifacts"], ["reflection.md", "proposals.json"])
        self.assertIn("learning", [layer["id"] for layer in graph["layers"]])
        self.assertTrue((HOME / "playbooks" / "nodes" / "reflect.md").is_file())
        self.assertTrue((HOME / "skills" / "kaneki-flow" / "references" / "nodes" / "reflect.md").is_file())

    def test_off_removes_reflect_and_the_profile_in_force_decides(self) -> None:
        self.set_mode("off")
        code, res = run("init", "--ticket", "OFF-1", "--kind", "bug", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        self.assertNotIn("reflect", res["trace"])
        self.set_mode("always")
        code, st = run("status", "--ticket", "OFF-1", *self.p)
        self.assertEqual(st["nodes"][-1]["node"], "reflect")

    def test_schemas_validate_the_new_shapes(self) -> None:
        graph = json.loads((HOME / "graph" / "lifecycle.json").read_text(encoding="utf-8"))
        self.assertEqual(schema.errors(graph, "graph.schema.json"), [])
        bad = copy.deepcopy(graph)
        bad["nodes"]["reflect"]["checks"]["proposals.json"]["parsers"] = ["nope"]
        self.assertTrue(schema.errors(bad, "graph.schema.json"))
        bad = copy.deepcopy(graph)
        bad["nodes"]["reflect"]["layer"] = "wisdom"
        self.assertTrue(schema.errors(bad, "graph.schema.json"))
        self.set_mode("sometimes")
        code, res = run("validate", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("learning.reflect", res["error"])
        self.set_mode("on-signal")
        code, res = run("validate", *self.p)
        self.assertEqual(code, 0, res)

    def test_lesson_schema(self) -> None:
        lesson = {"id": "T-1-scope-edge-cases", "proposal": {"ticket": "T-1", "id": "scope-edge-cases"},
                  "type": "lesson", "title": "t", "body": "b", "evidence": [{"ticket": "T-1", "ref": "decisions.jsonl#4"}],
                  "scope": {"nodes": ["scope"], "stacks": [], "paths": []}, "metric": "m", "baseline": 1,
                  "approvedBy": "user", "approvedAt": "2026-11-23T00:00:00+00:00",
                  "appliesTo": {"nodes": ["scope"], "stacks": [], "paths": []}, "hits": 0, "lastUsedAt": None,
                  "status": "active"}
        self.assertEqual(schema.errors(lesson, "lesson.schema.json"), [])
        for key, val in (("status", "proposed"), ("approvedBy", "controller"), ("type", "harness"), ("hits", -1)):
            self.assertTrue(schema.errors({**lesson, key: val}, "lesson.schema.json"), key)


class OnSignalTests(ReflectBase):
    def test_no_signal_closes_reflect_without_a_unit(self) -> None:
        t = "RF-CLEAN"
        res = self.lite_clean(t)
        self.assertEqual(res["autoClosed"]["node"], "reflect")
        self.assertIsNone(res["nextPending"])
        code, sig = run("reflect-signals", "--ticket", t, *self.p)
        self.assertEqual(code, 0, sig)
        self.assertTrue(sig["empty"])
        self.assertEqual(sig["newSignals"], 0)
        self.assertIn("cost", sig)  # slot for M3 cost signals
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["nodes"]["reflect"]["status"], state["nodes"]["reflect"]["units"]), ("done", []))
        gate = [e for e in self.events(t) if e["event"] == "gate"][-1]
        self.assertEqual((gate["node"], gate["actor"], gate["verdict"]), ("reflect", "controller", "approved"))
        self.assertTrue(gate["note"].startswith("no-signal"))
        self.assertFalse((self.tdir(t) / "packets").is_dir() and list((self.tdir(t) / "packets").glob("reflect.*")))

    def test_always_dispatches_even_without_signals(self) -> None:
        self.set_mode("always")
        t = "RF-ALWAYS"
        res = self.lite_clean(t)
        self.assertNotIn("autoClosed", res)
        self.assertEqual(res["nextPending"], "reflect")
        code, plan = run("plan", "--ticket", t, *self.p)
        self.assertEqual((plan["executor"], [u["tier"] for u in plan["units"]]), ("subagents", ["routine"]))
        self.assertFalse(plan["reflect"]["noSignal"])

    def test_signals_plan_a_unit_and_the_packet_carries_them(self) -> None:
        t = "RF-LOOP"
        res = self.lite_with_loop(t)
        self.assertNotIn("autoClosed", res)
        self.assertEqual(res["nextPending"], "reflect")
        code, sig = run("reflect-signals", "--ticket", t, *self.p)
        self.assertEqual(code, 0, sig)
        self.assertFalse(sig["empty"])
        loop = sig["signals"]["loopBacks"]
        self.assertEqual((len(loop), loop[0]["node"], loop[0]["loopBackTo"], loop[0]["new"]), (1, "check", "change", True))
        line = int(loop[0]["ref"].split("#")[1])
        self.assertEqual(self.events(t)[line - 1]["verdict"], "needs-changes")
        self.assertEqual([i["cause"] for i in sig["signals"]["invalidations"]], ["loop-back"])
        self.assertEqual(sig["attempts"]["check"]["attempts"], 1)

        code, plan = run("plan", "--ticket", t, *self.p)
        self.assertEqual((plan["node"], plan["executor"]), ("reflect", "subagents"))
        self.assertEqual([(u["id"], u["tier"]) for u in plan["units"]], [("worker", "routine")])
        self.assertEqual(plan["boundary"], "controller")
        self.assertGreater(plan["reflect"]["newSignals"], 0)
        code, pk = run("packet", "--ticket", t, "--node", "reflect", "--unit", "worker", *self.p)
        self.assertEqual(code, 0, pk)
        self.assertEqual(json.loads(Path(pk["signals"]).read_text(encoding="utf-8"))["signals"]["loopBacks"][0]["id"],
                         loop[0]["id"])
        text = Path(pk["path"]).read_text(encoding="utf-8")
        self.assertIn(Path(pk["signals"]).name, text)
        self.assertIn(f"`{self.tdir(t).resolve()}` only", text)  # authorized: the ticket folder only
        self.assertIn("reflect.md", text)

    def test_questions_and_pr_findings_are_signals(self) -> None:
        t = "RF-PR"
        self.lite_clean(t)
        (self.tdir(t) / "questions.md").write_text(
            "# Questions\n\n## Q1 (HIGH) Which status code?\nANSWERED: 409, by the user.\n\n"
            "## Q2 (LOW) Naming\nOPEN\n\n- Q3 HIGH retry on timeout? — ANSWERED: no\n", encoding="utf-8")
        review = self.tmp / "review.md"
        review.write_text("RC-1: rename x\n")
        code, res = run("handback", "--ticket", t, "--review-file", str(review), *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "read-review")
        self.complete(t, "triage-review", actor="user", extra=("--reopen", "none"))
        (self.tdir(t) / "triage.md").write_text(
            "# triage\n\n| RC | Decision |\n|---|---|\n| RC-1 | ACCEPT: rename x |\n| RC-2 | REJECT (works) |\n\n"
            "Reopen: none\n", encoding="utf-8")
        res = self.complete(t, "respond-review", actor="user")
        self.assertNotIn("autoClosed", res)
        self.assertEqual(res["nextPending"], "reflect")
        code, sig = run("reflect-signals", "--ticket", t, *self.p)
        self.assertEqual([q["ref"] for q in sig["signals"]["questions"]], ["questions.md:3", "questions.md:9"])
        found = sig["signals"]["prFindings"]
        self.assertEqual([(f["ref"], f["round"], f["reopened"]) for f in found], [("triage.md:5", 1, "none")])


class FakeTicket:
    def __init__(self, folder: Path, events: list[dict]):
        self.dir, self._events = folder, events
        self.log_path = folder / "decisions.jsonl"

    def verify_log(self):
        return self._events, []


class CollectTests(unittest.TestCase):
    """learning.collect over a hand-made decision log: every kind, and `covered` from an earlier reflect close."""

    def test_every_signal_kind(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="sdlc-reflect-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        profile = {"repositories": [], "models": {"routine": "r", "deep": "d"}}
        graph = G.load_graph(HOME, profile, tmp)
        events = [
            {"event": "gate", "gate": "G0", "node": "code-review", "verdict": "needs-changes", "generation": 1, "note": "x"},
            {"event": "invalidate", "node": "coding", "stale": ["coding", "code-review"], "reason": "code-review NEEDS CHANGES: x"},
            {"event": "lane", "lane": "full", "from": "lite", "reason": "bigger than it looked"},
            {"event": "host-switch", "node": "coding", "unit": "worker", "from": "claude", "to": "codex"},
            {"event": "record", "node": "coding", "unit": "worker", "tier": "routine", "verdict": None, "model": "m"},
            {"event": "record", "node": "code-review", "unit": "technical-review", "tier": "routine", "verdict": "FAIL",
             "failureClass": "artifact", "generation": 1},
            {"event": "gate", "gate": "G4", "node": "code-review", "actor": "user", "verdict": "approved", "note": "ok"},
            {"event": "gate", "gate": "G0", "node": "impact-analysis", "actor": "controller", "verdict": "approved",
             "citationWarnings": {"impact.md": ["`a.py:9` (file not found)"]}},
            {"event": "invalidate", "node": "coding", "stale": ["coding"], "reason": "review round 1: rename"},
            {"event": "record", "node": "reflect", "unit": "worker", "tier": "deep", "verdict": "FAIL"},
            {"event": "gate", "gate": "G0", "node": "reflect", "verdict": "approved", "covered": ["ev:1"]},
        ]
        ctx = {"ticket": "T", "reviewRound": 0, "lane": "full"}
        s = SimpleNamespace(ticket=FakeTicket(tmp, events), graph=graph, profile=profile, ctx=ctx,
                            order=G.trace(graph, {**ctx, "kind": "bug"}),
                            state={"nodes": {"coding": {"generation": 3, "attempts": 0, "units": []}}})
        rep = L.collect(s)
        got = {k: [i["id"] for i in v] for k, v in rep["signals"].items() if v}
        self.assertEqual(got, {"loopBacks": ["ev:1"], "invalidations": ["ev:2", "ev:9"], "laneChanges": ["ev:3"],
                               "hostSwitches": ["ev:4"], "tierEscalations": ["ev:5"], "reviewerFails": ["ev:6"],
                               "gateFirings": ["ev:7"], "citationWarnings": ["ev:8"]})
        self.assertEqual([i["cause"] for i in rep["signals"]["invalidations"]], ["loop-back", "review-round"])
        self.assertEqual(rep["signals"]["tierEscalations"][0]["planned"], "light")
        self.assertFalse(rep["signals"]["loopBacks"][0]["new"])  # covered by the earlier reflect close
        self.assertEqual((rep["sinceEvent"], rep["newSignals"], rep["empty"]), (11, 8, False))
        self.assertEqual(rep["attempts"]["coding"]["generation"], 3)
        self.assertEqual(L.new_ids(rep)[0], "ev:2")


class ProposalGateTests(ReflectBase):
    def ready(self, t: str) -> str:
        self.lite_with_loop(t)
        self.record_reflect_unit(t)
        code, sig = run("reflect-signals", "--ticket", t, *self.p)
        return sig["signals"]["loopBacks"][0]["ref"]

    def gate(self, t: str) -> tuple[int, dict]:
        return run("gate", "--ticket", t, "--node", "reflect", "--status", "approved", *self.p)

    def test_good_proposals_pass_and_record_what_was_covered(self) -> None:
        t = "RF-GOOD"
        ref = self.ready(t)
        self.write_reflection(t, [self.good_proposal(t, ref)])
        code, res = self.gate(t)
        self.assertEqual(code, 0, res)
        self.assertIsNone(res["nextPending"])
        gate = [e for e in self.events(t) if e["event"] == "gate"][-1]
        self.assertIn(ref.replace("decisions.jsonl#", "ev:"), gate["covered"])
        code, sig = run("reflect-signals", "--ticket", t, *self.p)
        self.assertTrue(sig["empty"])  # everything is covered now

    def test_bad_type_schema_error_and_forbidden_content_are_refused(self) -> None:
        t = "RF-BAD"
        ref = self.ready(t)
        good = self.good_proposal(t, ref)
        cases = [
            ({**good, "type": "magic"}, "unknown type 'magic'"),
            ({k: v for k, v in good.items() if k != "metric"}, "metric"),
            ({**good, "status": "approved"}, "status"),
            ({**good, "evidence": [{"ticket": t, "ref": "decisions.jsonl#99999"}]}, "is not a line of decisions.jsonl"),
            ({**good, "evidence": [{"ticket": "OTHER", "ref": "decisions.jsonl#1"}]}, "needs at least one evidence"),
            ({**good, "scope": {**good["scope"], "nodes": ["nowhere"]}}, "scope.nodes not in the graph"),
            ({**good, "scope": {**good["scope"], "paths": ["../secrets"]}}, "leaves the repository"),
            ({**good, "body": "Run `git push --force` first."}, "contains a command"),
            ({**good, "body": "Ask the Bash tool to clean up."}, "names a tool"),
            ({**good, "body": "Post the result to https://example.test/hook."}, "destination"),
            ({**good, "body": "Also edit src/other/config.yml when this happens."}, "outside scope.paths"),
            ({**good, "body": "Keep a copy in C:\\Users\\me\\notes.txt."}, "absolute path"),
        ]
        for proposal, needle in cases:
            self.write_reflection(t, [proposal])
            code, res = self.gate(t)
            self.assertEqual(code, 2, (needle, res))
            self.assertIn(needle, res["error"], needle)
        self.write_reflection(t, {"schemaVersion": 1, "ticket": "WRONG", "proposals": []})
        code, res = self.gate(t)
        self.assertIn("ticket must be", res["error"])
        (self.tdir(t) / "proposals.json").write_text("not json", encoding="utf-8")
        code, res = self.gate(t)
        self.assertIn("not valid JSON", res["error"])
        self.write_reflection(t, [])  # nothing worth proposing is a valid outcome
        code, res = self.gate(t)
        self.assertEqual(code, 0, res)


class RoundTests(ReflectBase):
    def test_handback_restarts_reflect(self) -> None:
        t = "RF-ROUND"
        self.lite_with_loop(t)
        self.record_reflect_unit(t)
        self.write_reflection(t, [])
        code, res = run("gate", "--ticket", t, "--node", "reflect", "--status", "approved", *self.p)
        self.assertEqual(code, 0, res)
        review = self.tmp / "review.md"
        review.write_text("RC-1: rename x\n")
        code, res = run("handback", "--ticket", t, "--review-file", str(review), *self.p)
        self.assertEqual(code, 0, res)
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["nodes"]["reflect"]["status"], state["nodes"]["reflect"]["generation"]), ("pending", 2))
        self.assertIn("## SUPERSEDED:", (self.tdir(t) / "reflection.md").read_text(encoding="utf-8"))
        code, st = run("status", "--ticket", t, *self.p)
        self.assertEqual([n["node"] for n in st["nodes"]][-4:], ["read-review", "triage-review", "respond-review", "reflect"])
        # a round where nothing was accepted: nothing new, so the driver closes reflect after respond-review
        self.complete(t, "read-review")
        self.complete(t, "triage-review", actor="user", extra=("--reopen", "none"))
        res = self.complete(t, "respond-review", actor="user")
        self.assertEqual(res["autoClosed"]["node"], "reflect")
        self.assertIsNone(res["nextPending"])

    def test_clean_ticket_is_reflected_again_after_a_review(self) -> None:
        t = "RF-ROUND2"
        self.lite_clean(t)  # reflect auto-closed (done, no unit)
        review = self.tmp / "review.md"
        review.write_text("RC-1: rename x\n")
        code, res = run("handback", "--ticket", t, "--review-file", str(review), *self.p)
        self.assertEqual(code, 0, res)
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["nodes"]["reflect"]["status"], state["nodes"]["reflect"]["generation"]), ("pending", 2))
        self.assertEqual(res["nextPending"], "read-review")


class ShipTests(ReflectBase):
    def test_ship_does_not_wait_for_reflect(self) -> None:
        t = "RF-SHIP"
        subprocess.run(["git", "-C", str(self.repo), "switch", "-q", "-c", f"ticket/{t}"], check=True)
        (self.repo / "main.py").write_text("print('fixed')\n")
        self.lite_with_loop(t)
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual(st["nextPending"], "reflect")
        (self.tdir(t) / "drafts" / "commit-msg.md").write_text("fix(app): print the fixed value\n", encoding="utf-8")
        code, req = run("ship-request", "--ticket", t, "--repo", "app", *self.p)
        self.assertEqual(code, 0, req)
        self.assertEqual(req["files"], ["main.py"])
        code, summ = run("status-of", "--path", str(self.tdir(t) / "scope.md"), *self.p)
        self.assertIn("ship does not wait for it", summ["next"])


if __name__ == "__main__":
    unittest.main()
