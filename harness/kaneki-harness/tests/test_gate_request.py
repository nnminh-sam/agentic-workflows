"""`sdlc gate-request` and the readiness walk it shares with `sdlc gate`: every refusal of the gate appears, word for
word, in the request; nothing is written by the request; `sdlc gate` itself behaves as before (P1-T06)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_hardening import Flow  # noqa: E402
from test_lifecycle import HOME, run  # noqa: E402

from harness import graph as G  # noqa: E402
from harness.cli import Session, gate_readiness, readiness_views, stale_preview, superseded_preview  # noqa: E402
from harness.profile import build_context, load_profile  # noqa: E402

KEYS = {"contractVersion", "ticket", "node", "generation", "attempts", "loopCap", "boundary", "gates", "question",
        "waitingReason", "readiness", "controllerReadiness", "reviewer", "units", "evidence", "ngItems", "choices"}
CHOICE_KEYS = {"id", "label", "allowed", "controllerAllowed", "argv", "reopenTargets", "staleIfChosen",
               "supersededIfChosen", "needsNote", "needsReason"}
LONG_ENGLISH = "fix: handle empty orders\n\nThe order total was computed before the empty check, so empty orders crashed.\n"


class GateRequestBase(Flow):
    # --- helpers ---------------------------------------------------------------------------------------
    def session(self, ticket: str) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=ticket))

    def snapshot(self) -> dict[str, str]:
        """sha256 of every file under the ticket root: a request must leave it unchanged."""
        root = self.tmp / "tickets"
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    def req(self, ticket: str, node: str | None = None) -> dict:
        before = self.snapshot()
        code, res = run("gate-request", "--ticket", ticket, *(("--node", node) if node else ()), *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.snapshot(), before, "gate-request wrote to the ticket folder")
        if res.get("node"):
            self.shape(res)
            if res["waitingReason"] in ("needs-run", "running", "reviewer-fail"):  # board.waiting agrees with the gate
                self.assertFalse(res["readiness"]["ok"], res["waitingReason"])
        return res

    def shape(self, res: dict) -> None:
        self.assertEqual(set(res), KEYS)
        self.assertEqual(json.dumps(res).count('"contractVersion"'), 1)  # once, at the top
        self.assertEqual(res["ngItems"], [])
        ids = [c["id"] for c in res["choices"]]
        self.assertEqual(ids[0], "approve")
        self.assertEqual(ids[-3:], ["reject", "stop", "reopen"])
        self.assertEqual(ids.count("needs-changes"), 1 if "needs-changes" in ids else 0)
        for c in res["choices"]:
            self.assertTrue(CHOICE_KEYS <= set(c), c["id"])
            self.assertEqual(c["argv"][:5], ["gate", "--ticket", res["ticket"], "--node", res["node"]])
            self.assertNotIn("--profile", c["argv"])
            self.assertEqual("whyNot" in c, not c["allowed"])
            self.assertEqual("controllerWhyNot" in c, not c["controllerAllowed"])
        self.assertNotIn("--reopen", self.choice(res, "approve")["argv"])

    def choice(self, res: dict, cid: str) -> dict:
        found = [c for c in res["choices"] if c["id"] == cid]
        self.assertEqual(len(found), 1, f"{cid} in {[c['id'] for c in res['choices']]}")
        return found[0]

    def gate(self, ticket: str, node: str, status: str, *extra: str, actor: str = "controller") -> tuple[int, dict]:
        return run("gate", "--ticket", ticket, "--node", node, "--status", status, "--actor", actor, *extra, *self.p)

    def agree(self, ticket: str, node: str, text: str, status: str = "approved", actor: str = "user",
              reopen: str | None = None) -> None:
        """`sdlc gate` run on the same state refuses with exactly the text the request showed."""
        code, res = self.gate(ticket, node, status, *(("--reopen", reopen) if reopen else ()), actor=actor)
        self.assertEqual(code, 2, res)
        self.assertEqual(res["error"], text)

    def record_all(self, ticket: str, node: str) -> None:
        code, plan = run("plan", "--ticket", ticket, "--node", node, *self.p)
        self.assertEqual(code, 0, plan)
        for unit in plan["units"]:
            code, pk = run("packet", "--ticket", ticket, "--node", node, "--unit", unit["id"], *self.p)
            self.assertEqual(code, 0, pk)
            Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
            Path(pk["resultPath"]).write_text("status: ok\n")
            verdict = ("--verdict", "PASS") if unit["role"] == "reviewer" or plan["pattern"].startswith("staged") else ()
            code, rec = run("record", "--ticket", ticket, "--node", node, "--unit", unit["id"], "--result", pk["resultPath"],
                            "--model", unit["model"], "--tokens", "1000", *verdict, *self.p)
            self.assertEqual(code, 0, rec)

    def init(self, ticket: str, kind: str = "bug", lane: str = "full") -> None:
        code, res = run("init", "--ticket", ticket, "--kind", kind, "--lane", lane, *self.p)
        self.assertEqual(code, 0, res)

    def edit_profile(self, **changes: object) -> None:
        prof = json.loads(self.profile.read_text())
        for key, value in changes.items():
            if isinstance(value, dict):
                prof[key] = {**prof.get(key, {}), **value}
            else:
                prof[key] = value
        self.profile.write_text(json.dumps(prof))

    def nodes_state(self, ticket: str) -> dict:
        return json.loads((self.tdir(ticket) / "state.json").read_text(encoding="utf-8"))["nodes"]


class BugTicketWalk(GateRequestBase):
    """One bug ticket walked from prepare to a triage round: each step is a row of the plan's matrix."""

    def test_walk(self) -> None:
        t = "GR-A"
        self.init(t)
        first = self.req(t)  # no --node: the next pending node
        self.assertEqual(first["node"], "prepare")
        self.assertEqual(first["waitingReason"], "needs-run")

        # row 2: not ready ends the walk with one problem; every choice refused
        res = self.req(t, "coding")
        self.assertEqual(len(res["readiness"]["problems"]), 1)
        self.assertTrue(res["readiness"]["problems"][0].startswith("G7: coding is not ready; missing"))
        self.assertEqual({c["allowed"] for c in res["choices"]}, {False})
        self.agree(t, "coding", res["readiness"]["problems"][0])
        self.agree(t, "coding", res["readiness"]["problems"][0], status="rejected")

        # row 6: units lacking
        res = self.req(t, "prepare")
        text = res["readiness"]["problems"][0]
        self.assertTrue(text.startswith("G7: prepare has no ok record for units"), text)
        self.agree(t, "prepare", text)
        # rejected / stopped check only readiness: the same node is allowed to be rejected or stopped
        self.assertTrue(self.choice(res, "reject")["allowed"])
        self.assertTrue(self.choice(res, "stop")["allowed"])

        # row 8: units ok, artifact absent; the content checks do not repeat "is missing"
        plan = self.write_artifacts(t, "prepare")
        self.record_all(t, "prepare")
        for art in plan["artifacts"]:
            (self.tdir(t) / art).unlink()
        res = self.req(t, "prepare")
        text = res["readiness"]["problems"][0]
        self.assertTrue(text.startswith("G7: prepare artifacts missing"), text)
        self.assertFalse([p for p in res["readiness"]["problems"] if "is missing" in p], res["readiness"]["problems"])
        self.assertEqual(res["readiness"]["problems"], [text])  # no "not rewritten" echo of the absent files either
        self.agree(t, "prepare", text)
        self.write_artifacts(t, "prepare")
        res = self.req(t, "prepare")
        self.assertTrue(res["readiness"]["ok"], res["readiness"])
        self.assertEqual(res["waitingReason"], "awaiting-gate")
        code, g = self.gate(t, "prepare", "approved")
        self.assertEqual(code, 0, g)

        # row 1: closed
        res = self.req(t, "prepare")
        self.assertTrue(res["readiness"]["problems"][0].startswith("prepare already closed at generation 1"))
        self.assertEqual({c["allowed"] for c in res["choices"]}, {False})
        self.agree(t, "prepare", res["readiness"]["problems"][0])
        self.assertEqual(res["waitingReason"], "done")

        # a citation that does not resolve on a node that is not a receipt node is a warning, not a problem
        plan = self.write_artifacts(t, "investigate")
        art = self.tdir(t) / plan["artifacts"][0]
        art.write_text(art.read_text(encoding="utf-8") + "\nSee `main.py:99`.\n", encoding="utf-8")
        self.record_all(t, "investigate")
        res = self.req(t, "investigate")
        self.assertTrue(res["readiness"]["ok"], res["readiness"])
        self.assertEqual(res["boundary"], "controller")
        self.assertEqual(res["question"], self.session(t).spec("investigate")["purpose"])
        code, g = self.gate(t, "investigate", "approved")  # identity of sdlc gate: citationWarnings stays second
        self.assertEqual(code, 0, g)
        self.assertEqual(list(g)[:2], ["node", "citationWarnings"])
        self.assertIn("main.py:99", json.dumps(g["citationWarnings"]))

        self.plan_node(t)
        self.coding_node(t)
        self.review_node(t)
        for node in ("testcase", "testcase-review", "execute-and-evidence"):
            self.complete(t, node)
        self.final_review(t)
        self.complete(t, "delivery-drafts")
        self.complete(t, "deliverable-ready", actor="user")
        self.triage_round(t)

    def plan_node(self, t: str) -> None:
        self.write_artifacts(t, "fix-plan")
        self.record_all(t, "fix-plan")
        plan_md = self.tdir(t) / "plan.md"
        good = plan_md.read_text(encoding="utf-8")
        # row 10: a missing section
        plan_md.write_text(good.replace("## Test intent", "## Notes"), encoding="utf-8")
        res = self.req(t, "fix-plan")
        text = res["readiness"]["problems"][0]
        self.assertIn("missing section `## Test intent`", text)
        self.assertEqual(res["controllerReadiness"]["problems"][0], text)
        self.agree(t, "fix-plan", text)
        # row 13: G0 after the plan auto-skip
        plan_md.write_text(good, encoding="utf-8")
        res = self.req(t, "fix-plan")
        self.assertTrue(res["readiness"]["ok"], res["readiness"])
        self.assertFalse(res["controllerReadiness"]["ok"])
        g0 = res["controllerReadiness"]["problems"][0]
        self.assertTrue(g0.startswith("G0: fix-plan closes on the user's approval"), g0)
        approve = self.choice(res, "approve")
        self.assertTrue(approve["allowed"])
        self.assertFalse(approve["controllerAllowed"])
        self.assertEqual(approve["controllerWhyNot"], g0)
        self.agree(t, "fix-plan", g0, actor="controller")
        self.assertEqual((res["boundary"], res["question"], res["waitingReason"]), ("user", "Approve the plan?", "user-boundary"))
        names = {e["name"]: e for e in res["evidence"]}
        self.assertTrue(names["plan.md"]["exists"])
        self.assertEqual(names["plan.md"]["source"], "artifact")
        self.assertIn("## Test intent", names["plan.md"]["headings"])
        self.assertIn("input", {e["source"] for e in res["evidence"]})
        # row 3: no loop-back
        no_back = "fix-plan has no loop-back; use rejected + invalidate"
        self.assertEqual(list(gate_readiness(self.session(t), "fix-plan", "needs-changes", "controller", None)), [no_back])
        self.assertNotIn("needs-changes", [c["id"] for c in res["choices"]])
        self.agree(t, "fix-plan", no_back, status="needs-changes")
        # the auto-skip: no G0, the node closes on the controller
        self.edit_profile(gates={"autoSkipPlanApproval": True})
        res = self.req(t, "fix-plan")
        self.assertTrue(res["controllerReadiness"]["ok"], res["controllerReadiness"])
        self.assertEqual((res["boundary"], res["waitingReason"]), ("controller", "awaiting-gate"))
        self.edit_profile(gates={"autoSkipPlanApproval": False})
        code, g = self.gate(t, "fix-plan", "approved", actor="user")
        self.assertEqual(code, 0, g)

    def coding_node(self, t: str) -> None:
        plan = self.write_artifacts(t, "coding")
        impl = self.tdir(t) / plan["artifacts"][0]
        impl.write_text(impl.read_text(encoding="utf-8") + "\nChanged `main.py:1` and `main.py:99`.\n", encoding="utf-8")
        self.record_all(t, "coding")
        # row 11: the same citation blocks on a receipt node
        res = self.req(t, "coding")
        text = res["readiness"]["problems"][0]
        self.assertIn("citations that do not resolve (receipt node)", text)
        self.assertIn("main.py:99", text)
        self.agree(t, "coding", text)
        self.write_artifacts(t, "coding")
        with mock.patch("harness.cli._commit_rule_problem", wraps=__import__("harness.cli", fromlist=["x"])._commit_rule_problem) as rule:
            res = self.req(t, "coding")
        self.assertTrue(res["readiness"]["ok"], res["readiness"])
        self.assertEqual(rule.call_count, 1)  # the commit rule runs once per request, whatever the number of views
        code, g = self.gate(t, "coding", "approved")
        self.assertEqual(code, 0, g)

    def review_node(self, t: str) -> None:
        # row 5: the verdict in the artifact must match the gate status
        self.write_artifacts(t, "code-review", verdict="APPROVED")
        res = self.req(t, "code-review")
        self.assertEqual([c["id"] for c in res["choices"]], ["approve", "needs-changes", "reject", "stop", "reopen"])
        nc = self.choice(res, "needs-changes")
        self.assertFalse(nc["allowed"])
        self.assertTrue(nc["whyNot"].startswith("G7: code-review:"), nc["whyNot"])
        self.assertEqual(nc["controllerWhyNot"], nc["whyNot"])
        self.agree(t, "code-review", nc["whyNot"], status="needs-changes")
        reopen = self.choice(res, "reopen")
        self.assertEqual(reopen["followUp"][0][:4], ["invalidate", "--ticket", t, "--node"])
        coding_target = next(x for x in reopen["reopenTargets"] if x["node"] == "coding")
        self.assertIn("code-review", coding_target["staleNodes"])

        # needs-changes round 1: staleIfChosen is what the gate returns, supersededIfChosen is what it supersedes
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        res = self.req(t, "code-review")
        nc = self.choice(res, "needs-changes")
        self.assertTrue(nc["allowed"] and nc["controllerAllowed"], nc)
        before = self.nodes_state(t)
        code, g = self.gate(t, "code-review", "needs-changes")
        self.assertEqual(code, 0, g)
        after = self.nodes_state(t)
        self.assertEqual(g["stale"], nc["staleIfChosen"])
        raised = [n for n in after if after[n]["generation"] > before[n]["generation"]]
        self.assertEqual(raised, nc["supersededIfChosen"])
        never_ran = [n for n in nc["staleIfChosen"] if n not in nc["supersededIfChosen"]]
        self.assertTrue(never_ran)
        for n in never_ran:
            self.assertEqual((before[n]["status"], before[n]["generation"], before[n]["units"]), ("pending", 1, []))

        # row 4: G4 at the cap, controller only
        self.complete(t, "coding")
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        res = self.req(t, "code-review")
        nc = self.choice(res, "needs-changes")
        self.assertTrue(nc["allowed"])
        self.assertNotIn("whyNot", nc)
        self.assertFalse(nc["controllerAllowed"])
        self.assertTrue(nc["controllerWhyNot"].startswith("G4: code-review NEEDS CHANGES after 2 rounds (cap 2, counting manual reopens of coding)"))
        self.agree(t, "code-review", nc["controllerWhyNot"], status="needs-changes", actor="controller")
        self.assertEqual((res["attempts"], res["loopCap"]), (1, 2))

        # `invalidate` returns what the reopen choice announced; a reopened node must be rewritten (row 9)
        res = self.req(t, "code-review")
        target = next(x for x in self.choice(res, "reopen")["reopenTargets"] if x["node"] == "coding")
        self.assertTrue(target["needsUser"])  # coding was already reopened once: round 2 is the user's (D5)
        code, inv = run("invalidate", "--ticket", t, "--node", "coding", "--reason", "edge case missed", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("G4", inv["error"])
        code, inv = run("invalidate", "--ticket", t, "--node", "coding", "--reason", "edge case missed",
                        "--actor", "user", *self.p)
        self.assertEqual(code, 0, inv)
        self.assertEqual(inv["stale"], target["staleNodes"])
        code, pk = run("packet", "--ticket", t, "--node", "coding", "--unit", "worker", *self.p)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("ok")
        run("record", "--ticket", t, "--node", "coding", "--unit", "worker", "--result", pk["resultPath"], "--model", "sonnet", *self.p)
        res = self.req(t, "coding")
        text = res["readiness"]["problems"][0]
        self.assertTrue(text.startswith("G7: coding artifacts not rewritten since a rerun superseded them"), text)
        self.assertFalse([p for p in res["readiness"]["problems"] if "is missing" in p])
        self.agree(t, "coding", text)
        self.complete(t, "coding")
        self.complete(t, "code-review")

    def final_review(self, t: str) -> None:
        self.write_artifacts(t, "final-review")
        text_before = self.req(t, "final-review")["readiness"]["problems"][0]
        self.assertTrue(text_before.startswith("G7: final-review has no ok record for units"), text_before)
        # a refused gate on a receipt node runs no git at all (the walk is lazy)
        with mock.patch("harness.state._git", side_effect=AssertionError("git ran")), \
                mock.patch("harness.gitid.foreign_commits", side_effect=AssertionError("git ran")), \
                mock.patch("harness.gitid.commits_since", side_effect=AssertionError("git ran")):
            self.agree(t, "final-review", text_before, actor="controller")
        res = self.req(t, "final-review")
        self.assertEqual(self.choice(res, "approve")["argv"][-2:], ["--actor", "user"])
        self.record_all(t, "final-review")
        self.assertTrue(self.req(t, "final-review")["readiness"]["ok"])
        code, g = self.gate(t, "final-review", "approved")
        self.assertEqual(code, 0, g)
        self.assertIn("app", g["receipts"])

    def triage_round(self, t: str) -> None:
        review = self.tmp / "review.md"
        review.write_text("RC-1: rename variable\n")
        code, res = run("handback", "--ticket", t, "--review-file", str(review), *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "read-review")
        self.write_artifacts(t, "triage-review")  # says `Reopen: none`
        self.record_all(t, "triage-review")
        # row 14: --reopen is required; one approve view per target
        res = self.req(t, "triage-review")
        text = res["readiness"]["problems"][0]
        self.assertTrue(text.startswith("triage-review: pass --reopen"), text)
        self.agree(t, "triage-review", text)
        approve = self.choice(res, "approve")
        targets = approve["reopenTargets"]
        settled = [n for n in self.session(t).order[:self.session(t).order.index("triage-review")]
                   if self.nodes_state(t)[n]["status"] == "done"]
        self.assertEqual([x["node"] for x in targets], settled + ["none"])
        self.assertEqual(targets[-1]["staleNodes"], [])
        self.assertTrue(approve["allowed"])  # some target (none) is allowed
        self.assertNotIn("whyNot", approve)
        for x in targets:
            self.assertEqual(x["argv"], approve["argv"] + ["--reopen", x["node"]])
            self.assertEqual(x["allowed"], x["node"] == "none", x)
            if not x["allowed"]:
                self.assertIn(f"--reopen {x['node']}", x["whyNot"])
                self.agree(t, "triage-review", x["whyNot"], reopen=x["node"])
        self.assertEqual({c["id"] for c in res["choices"] if c["id"] != "approve"}, {"reject", "stop", "reopen"})
        self.assertEqual([c["reopenTargets"] for c in res["choices"] if c["id"] in ("reject", "stop")], [[], []])

        # the artifact names the target: that target opens, `none` closes, stale list is what the gate returns
        self.write_artifacts(t, "triage-review", reopen="coding")
        res = self.req(t, "triage-review")
        targets = {x["node"]: x for x in self.choice(res, "approve")["reopenTargets"]}
        self.assertTrue(targets["coding"]["allowed"])
        self.assertFalse(targets["none"]["allowed"])
        code, g = self.gate(t, "triage-review", "approved", "--reopen", "coding", actor="user")
        self.assertEqual(code, 0, g)
        self.assertEqual(g["stale"], targets["coding"]["staleNodes"])


class RuleRows(GateRequestBase):
    def test_reviewer_rows(self) -> None:
        t = "GR-F"
        self.init(t, kind="feature")
        self.complete(t, "prepare")
        self.write_artifacts(t, "requirement-analysis")
        # units lacking and no reviewer record: no IndexError, no reviewer problem (guard)
        res = self.req(t, "requirement-analysis")
        problems = res["readiness"]["problems"]
        self.assertTrue(problems[0].startswith("G7: requirement-analysis has no ok record for units ['worker', 'reviewer']"))
        self.assertFalse([p for p in problems if "reviewer verdict" in p])
        self.agree(t, "requirement-analysis", problems[0])
        self.assertIsNone(res["reviewer"])
        # row 7: reviewer FAIL
        self.record_all(t, "requirement-analysis")
        code, pk = run("packet", "--ticket", t, "--node", "requirement-analysis", "--unit", "reviewer", *self.p)
        Path(pk["resultPath"]).write_text("status: ok\n")
        code, rec = run("record", "--ticket", t, "--node", "requirement-analysis", "--unit", "reviewer", "--result",
                        pk["resultPath"], "--model", pk["model"], "--verdict", "FAIL", "--failure-class", "artifact", *self.p)
        self.assertEqual(code, 0, rec)
        res = self.req(t, "requirement-analysis")
        text = res["readiness"]["problems"][0]
        self.assertEqual(text, "requirement-analysis: last reviewer verdict is FAIL (artifact); fix and re-review")
        self.assertEqual(res["waitingReason"], "reviewer-fail")
        self.assertEqual((res["reviewer"]["verdict"], res["reviewer"]["failureClass"]), ("FAIL", "artifact"))
        self.assertIn("result", {e["source"] for e in res["evidence"]})
        self.agree(t, "requirement-analysis", text)
        # feature-plan: not ready until its producers are settled
        res = self.req(t, "feature-plan")
        self.assertTrue(res["readiness"]["problems"][0].startswith("G7: feature-plan is not ready"))

    def test_commit_rule_row(self) -> None:
        """Row 15: a commit made during the flow, whoever made it, blocks a node with a subject."""
        t = "GR-B"
        self.init(t)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        self.write_artifacts(t, "coding")
        self.record_all(t, "coding")
        self.assertTrue(self.req(t, "coding")["readiness"]["ok"])
        (self.repo / "main.py").write_text("print('rogue')\n")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Someone Else", "-c", "user.email=else@corp.test",
                        "commit", "-qam", "rogue"], check=True)
        res = self.req(t, "coding")
        text = res["readiness"]["problems"][0]
        self.assertTrue(text.startswith("G7: commit rule broken"), text)
        self.assertIn("else@corp.test", text)
        self.assertEqual(res["controllerReadiness"]["problems"], res["readiness"]["problems"])
        self.agree(t, "coding", text)

    def test_errors_and_empty_answers(self) -> None:
        t = "GR-E"
        self.init(t, lane="lite")
        code, res = run("gate-request", "--ticket", t, "--node", "nope", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("not in this ticket's trace", res["error"])
        code, res = run("gate-request", "--ticket", "NO-SUCH", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("error", res)
        log = self.tdir(t) / "decisions.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"at": "', '"at": "edited ', 1)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, res = run("gate-request", "--ticket", t, *self.p)
        self.assertEqual(code, 2, res)

    def test_nothing_pending(self) -> None:
        t = "GR-N"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        res = self.req(t, "scope")  # a closed node asked for by name
        self.assertTrue(res["readiness"]["problems"][0].startswith("scope already closed"))
        self.assertEqual(res["waitingReason"], "done")
        self.complete(t, "change")
        self.complete(t, "check")
        self.complete(t, "delivery-drafts")
        self.complete(t, "deliverable-ready", actor="user")
        res = self.req(t)
        self.assertEqual(res, {"contractVersion": 1, "ticket": t, "node": None})


class LiteReceiptRows(GateRequestBase):
    def test_lite_ticket_with_receipts_and_language(self) -> None:
        (self.repo / "new_module.py").write_text("VALUE = 1\n")
        self.edit_profile(locale={"delivery": "ja"})
        t = "GR-L"
        self.init(t, lane="lite")
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        # a lite `check` issues receipts: a node with a subject, shaped like any other
        self.write_artifacts(t, "check")
        res = self.req(t, "check")
        self.assertEqual([c["id"] for c in res["choices"]], ["approve", "needs-changes", "reject", "stop", "reopen"])
        self.complete(t, "check")

        # row 12: G5 binds the controller, not the user
        self.write_artifacts(t, "delivery-drafts")
        (self.tdir(t) / "drafts" / "commit-msg.md").write_text(LONG_ENGLISH, encoding="utf-8")
        res = self.req(t, "delivery-drafts")
        self.assertTrue(res["readiness"]["ok"], res["readiness"])
        g5 = res["controllerReadiness"]["problems"][0]
        self.assertTrue(g5.startswith("G5: delivery-drafts: output language does not match"), g5)
        approve = self.choice(res, "approve")
        self.assertEqual((approve["allowed"], approve["controllerAllowed"], approve["controllerWhyNot"]), (True, False, g5))
        self.agree(t, "delivery-drafts", g5, actor="controller")
        code, g = self.gate(t, "delivery-drafts", "approved", actor="user")
        self.assertEqual(code, 0, g)

        # row 16: receipts. A fresh receipt passes; a changed checkout does not. The check runs once per request.
        res = self.req(t, "deliverable-ready")
        self.assertTrue(res["readiness"]["ok"], res["readiness"])
        self.assertEqual(res["controllerReadiness"]["problems"][0][:3], "G0:")
        import harness.cli as cli
        (self.repo / "new_module.py").write_text("VALUE = 2\n")  # same name, new content
        with mock.patch("harness.cli.check_receipts", wraps=cli.check_receipts) as receipts, \
                mock.patch("harness.cli._commit_rule_problem", wraps=cli._commit_rule_problem) as rule:
            res = self.req(t, "deliverable-ready")
        self.assertEqual((receipts.call_count, rule.call_count), (1, 1))
        text = res["readiness"]["problems"][0]
        self.assertIn("checkout changed", text)
        self.agree(t, "deliverable-ready", text)

        # a git failure is a problem string in the request (rc 0) and the same error from `sdlc gate`
        (self.repo / ".git").rename(self.repo / ".git-off")  # the checkout is no longer a git repository
        res = self.req(t, "deliverable-ready")
        problems = res["readiness"]["problems"]
        self.assertTrue(problems[-1].startswith("G7: git failed in"), problems)
        self.assertEqual(res["controllerReadiness"]["problems"][-1], problems[-1])
        self.agree(t, "deliverable-ready", problems[0])


class SharedHelpers(GateRequestBase):
    def test_stale_and_superseded_helpers(self) -> None:
        t = "GR-S"
        self.init(t)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        s = self.session(t)
        stale = stale_preview(s, "investigate")
        self.assertEqual(stale, G.dependents(s.graph, s.order, "investigate"))
        # nothing after `investigate` has run: every dependent but the node itself never ran
        self.assertEqual(superseded_preview(s, "investigate"), ["investigate"])
        self.assertGreater(len(stale_preview(s, "prepare")), 2)
        self.assertEqual(superseded_preview(s, "prepare"), ["prepare", "investigate"])
        before = json.dumps(s.state, sort_keys=True)
        views = readiness_views(s, "coding", "approved", None)
        self.assertEqual(views["user"], views["controller"])
        self.assertEqual(json.dumps(s.state, sort_keys=True), before)  # pure: nothing marked, nothing written

    def test_graph_assumption_for_the_reordered_receipts_check(self) -> None:
        """`gate` used to run `repo_subject` between the commit rule and the receipts check; the shared walk runs the
        receipts check first. Equal while no node combines them; this fails the day a graph does."""
        graph = G.load_graph(HOME, load_profile(self.profile), self.tmp)
        for node, spec in graph["nodes"].items():
            self.assertFalse(spec.get("checksReceipts") and (spec.get("issuesReceipts") or spec.get("subjectNode")), node)
        profile = load_profile(self.profile)
        self.assertTrue(G.trace(graph, build_context(profile, "T", "bug", None, None, None)))

    def test_contract_lists_the_command(self) -> None:
        code, res = run("contract")
        self.assertEqual(code, 0)
        self.assertIn("gate-request", res["commands"])


if __name__ == "__main__":
    unittest.main()
