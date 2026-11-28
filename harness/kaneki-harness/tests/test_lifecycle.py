"""End-to-end driver tests on a throwaway git repo. Run: python -m unittest discover -s tests"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME))

from harness import graph as G  # noqa: E402
from harness.cli import main  # noqa: E402
from harness.profile import build_context, load_profile  # noqa: E402


def run(*argv: str) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(argv))
    return code, json.loads(buf.getvalue().strip().splitlines()[-1])


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sdlc-test-"))
        # The user-level dirs (agent logs read by sessions/cost, the cost cache and price file written by cost) live
        # outside self.tmp so read-only snapshots of self.tmp stay exact, and never in the real home folder.
        self.user_home = Path(tempfile.mkdtemp(prefix="sdlc-home-"))
        self.addCleanup(shutil.rmtree, self.user_home, True)
        env = mock.patch.dict(os.environ, {"KANEKI_HOME": str(self.user_home / "kaneki"),
                                           "CLAUDE_CONFIG_DIR": str(self.user_home / "claude"),
                                           "CODEX_HOME": str(self.user_home / "codex"),
                                           "ANTIGRAVITY_CLI_ROOT": str(self.user_home / "agy")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KANEKI_PRICES", None)
        self.repo = self.tmp / "app"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "main.py").write_text("print('v1')\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
        profile = {
            "schemaVersion": 1,
            "project": {"id": "demo", "name": "Demo"},
            "git": {"user": {"name": "Kaneki Dev", "email": "dev@kaneki.test"}},
            "paths": {"ticketRoot": "tickets"},
            "models": {"routine": "sonnet", "deep": "opus"},
            "tracker": {"adapter": "local-file"},
            "repositories": [{"id": "app", "path": str(self.repo), "stack": "python", "surfaces": ["cli"]}],
            "rulepacks": ["core"],
            "testcase": {"format": "markdown"},
            "evidence": {"surfaces": {"cli": {"adapter": "cli"}}},
            "delivery": {"adapter": "manual", "drafts": ["commit-msg", "pr-body"]},
        }
        self.profile = self.tmp / "sdlc.profile.json"
        self.profile.write_text(json.dumps(profile))
        self.p = ["--profile", str(self.profile)]

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tdir(self, ticket: str) -> Path:
        return self.tmp / "tickets" / ticket

    def write_artifacts(self, ticket: str, node: str, verdict: str = "APPROVED", reopen: str | None = None) -> dict:
        """Write artifacts that satisfy the node's declared checks, using only what `plan` exposes
        (artifact names, checks, rule ids), as a unit would from its packet."""
        code, plan = run("plan", "--ticket", ticket, "--node", node, *self.p)
        self.assertEqual(code, 0, plan)
        rules = plan.get("ruleIds") or []
        for art in plan["artifacts"]:
            check = plan.get("checks", {}).get(art, {})
            lines = [f"# {node}", "", f"Work for {node}."]
            for sec in check.get("sections", []):
                lines += ["", sec, "Content."]
                if "Condition" in sec:
                    lines += ["| condition | positive | negative |", "|---|---|---|", "| a > 0 | 1 | 0 |"]
                if sec in ("## Design rules", "## Rule self-check"):
                    lines += [f"- {rid}: PASS" for rid in rules]
            parsers = check.get("parsers", [])
            if "confidence" in parsers:
                lines.append("Confidence: High")
            if "rule-verdicts" in parsers:
                lines += [f"- {rid}: PASS" for rid in rules]
            if "verdict" in parsers:
                lines.append(f"Verdict: {verdict}")
            if "reopen" in parsers:
                lines.append(f"Reopen: {reopen or 'none'}")
            if "testcases" in parsers:  # testcases.json (C1): one criterion, one critical repo-test case
                lines = [json.dumps({"schemaVersion": 1, "ticket": ticket,
                                     "criteria": [{"id": "AC-1", "text": "the change works"}],
                                     "cases": [{"id": "TC-1", "title": "the change", "surface": "cli", "risk": "critical",
                                                "criteria": ["AC-1"], "checks": [{"id": "TC-1.a1", "expect": "exit 0"}],
                                                "inScope": True, "reason": None,
                                                "run": {"kind": "repo-test", "command": "python -c 0"}}]})]
            if "evidence" in parsers:  # evidence.json (C1): every check of testcases.json observed
                tcs = json.loads((self.tdir(ticket) / "testcases.json").read_text(encoding="utf-8"))
                lines = [json.dumps({"schemaVersion": 1, "ticket": ticket, "at": "2026-11-27T00:00:00+00:00",
                                     "checks": {k["id"]: {"case": c["id"], "status": "PASS", "observed": 0}
                                                for c in tcs["cases"] for k in c["checks"]}})]
            if "proposals" in parsers:  # reflect: a valid, empty proposals file
                lines = [json.dumps({"schemaVersion": 1, "ticket": ticket, "proposals": []})]
            if art == "baseline.json":  # written by `sdlc baseline` (B3)
                lines = [json.dumps({"ticket": ticket, "ok": True, "repos": [], "summary": {}})]
            if art == "drafts/commit-msg.md":  # drafts follow the ship rules (B5: the delivery-drafts gate checks them)
                lines = ["fix(app): correct the edge case", "", f"Work for {ticket}."]
            if art == "drafts/pr-body.md":
                lines = ["## Summary", "x", "## Detailed changes", "x", "## Explanation", "x",
                         "## Test results (local)", "- unit: pass", "- lint: pass"]
            if node in ("final-review", "check") and art.endswith(".md"):  # a receipt review names every new file (B5)
                status = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain", "--untracked-files=all"],
                                        capture_output=True, text=True).stdout.splitlines()
                lines += ["", "Files in the change:"] + [f"- {ln[3:]}" for ln in status if ln.startswith("??")]
            path = self.tdir(ticket) / art
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if node == "execute-and-evidence" and verdict == "APPROVED":
            self.write_judgements(ticket)
        return plan

    def write_judgements(self, ticket: str) -> None:
        """What the evidence run and the two judges leave (J1-J5): run.json, a mutation exemption, two judgements
        citing testcases.json / evidence.json verbatim, a reason for every changed file no case observes."""
        tdir = self.tdir(ticket)
        tcs = json.loads((tdir / "testcases.json").read_text(encoding="utf-8"))
        (tdir / "evidence").mkdir(exist_ok=True)
        (tdir / "evidence" / "run.json").write_text(json.dumps(
            {"cases": {c["id"]: {"status": "PASS", "log": ""} for c in tcs["cases"]}}), encoding="utf-8")
        ev = json.loads((tdir / "evidence.json").read_text(encoding="utf-8"))
        for row in ev["checks"].values():
            row["source"] = {"kind": "test", "ref": "testcases.json:1"}
        (tdir / "evidence.json").write_text(json.dumps(ev), encoding="utf-8")
        (tdir / "mutations.json").write_text(json.dumps(
            {k["id"]: {"none": "test fixture"} for c in tcs["cases"] for k in c["checks"]}), encoding="utf-8")
        code, fx = run("judge-facts", "--ticket", ticket, *self.p)
        self.assertEqual(code, 0, fx)
        verdict = {"verdict": "PASS", "confidence": "High", "class": "judgeable", "reason": "observed as required",
                   "requirement": {"ref": "testcases.json:1", "quote": tcs["criteria"][0]["text"]},
                   "evidence": {"ref": "evidence.json:1", "quote": '"status": "PASS"'}}
        checks = [k["id"] for c in tcs["cases"] for k in c["checks"]]
        for judge, name in (("a", "evidence-judgement.json"), ("b", "evidence-judgement-2.json")):
            (tdir / name).write_text(json.dumps({
                "schemaVersion": 1, "ticket": ticket, "judge": judge, "verdicts": {c: verdict for c in checks},
                **({"uncoveredFiles": {f: "fixture change outside the cases" for f in fx["uncoveredFiles"]}}
                   if judge == "a" else {})}), encoding="utf-8")

    def complete(self, ticket: str, node: str, *, actor: str = "controller", extra: tuple[str, ...] = ()) -> dict:
        """Simulate the controller: write artifacts, record every planned unit, close the node."""
        reopen = extra[extra.index("--reopen") + 1] if "--reopen" in extra else None
        plan = self.write_artifacts(ticket, node, reopen=reopen)
        for unit in plan["units"]:
            code, pk = run("packet", "--ticket", ticket, "--node", node, "--unit", unit["id"], *self.p)
            self.assertEqual(code, 0, pk)
            Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
            Path(pk["resultPath"]).write_text("status: ok\n")
            verdict = ("--verdict", "PASS") if unit["role"] == "reviewer" or plan["pattern"].startswith("staged") else ()
            code, rec = run("record", "--ticket", ticket, "--node", node, "--unit", unit["id"],
                            "--result", pk["resultPath"], "--model", unit["model"], "--tokens", "1000", *verdict, *self.p)
            self.assertEqual(code, 0, rec)
        code, res = run("gate", "--ticket", ticket, "--node", node, "--status", "approved", "--actor", actor, *extra, *self.p)
        self.assertEqual(code, 0, res)
        return res


class TraceTests(Base):
    def test_bug_and_feature_traces(self) -> None:
        profile = load_profile(self.profile)
        graph = G.load_graph(HOME, profile, self.tmp)
        bug = G.trace(graph, build_context(profile, "T", "bug", None, None, None))
        feat = G.trace(graph, build_context(profile, "T", "feature", None, None, None))
        self.assertEqual(bug[:6], ["prepare", "investigate", "fix-plan", "testcase", "testcase-review", "coding"])
        self.assertEqual(feat[:7], ["prepare", "requirement-analysis", "impact-analysis", "feature-plan", "testcase",
                                    "testcase-review", "coding"])
        self.assertNotIn("read-review", bug)  # after-PR nodes appear only on handback

    def test_invalidate_coding_keeps_testcases(self) -> None:
        profile = load_profile(self.profile)
        graph = G.load_graph(HOME, profile, self.tmp)
        order = G.trace(graph, build_context(profile, "T", "bug", None, None, None))
        stale = G.dependents(graph, order, "coding")
        self.assertIn("code-review", stale)
        self.assertIn("final-review", stale)
        self.assertNotIn("testcase", stale)  # test cases derive from the plan, not the code
        self.assertNotIn("testcase-review", stale)

    def test_wms_example_family_extension(self) -> None:
        wms = HOME / "examples" / "wms" / "sdlc.profile.json"
        profile = load_profile(wms)
        graph = G.load_graph(HOME, profile, wms.parent)
        with_if = G.trace(graph, build_context(profile, "T", "feature", None, ["if"], None))
        without = G.trace(graph, build_context(profile, "T", "feature", None, None, None))
        i = with_if.index("if-registration")
        self.assertEqual(with_if[i - 1], "code-review")
        self.assertNotIn("if-registration", without)
        self.assertIn("if-proof", graph["nodes"]["final-review"]["requires"])


class DriverTests(Base):
    def test_validate_examples(self) -> None:
        code, res = run("validate", *self.p)
        self.assertEqual(code, 0, res)
        code, res = run("validate", "--profile", str(HOME / "examples" / "generic-web" / "sdlc.profile.json"))
        self.assertEqual(code, 0, res)

    def test_full_bug_cycle_with_review_loop_and_handback(self) -> None:
        t = "BUG-1"
        code, res = run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)

        self.complete(t, "prepare")
        # out of order: coding before the plan is refused
        code, res = run("gate", "--ticket", t, "--node", "coding", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("not ready", res["error"])

        self.complete(t, "investigate")
        # plan approval needs the user
        code, res = run("plan", "--ticket", t, "--node", "fix-plan", *self.p)
        self.assertEqual(res["boundary"], "user")
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review"):  # D1: test cases before coding
            self.complete(t, node)

        (self.repo / "main.py").write_text("print('v2')\n")  # the "fix"
        self.complete(t, "coding")

        # code-review NEEDS CHANGES -> coding reopened, testcases kept
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "code-review", "--status", "needs-changes", "--note", "edge case", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["reopened"], "coding")
        self.assertEqual(res["nextPending"], "coding")
        self.complete(t, "coding")
        self.complete(t, "code-review")
        for node in ("execute-and-evidence",):
            self.complete(t, node)
        res = self.complete(t, "final-review")
        self.assertIn("app", res["receipts"])
        self.complete(t, "delivery-drafts")
        self.complete(t, "deliverable-ready", actor="user")
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual(st["nextPending"], "reflect")  # the code-review loop-back is a signal: reflect has a unit
        self.complete(t, "reflect")
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertIsNone(st["nextPending"])

        # after PR: a review arrives, triage reopens coding, only the stale part reruns
        review = self.tmp / "review.md"
        review.write_text("RC-1: rename variable\n")
        code, res = run("handback", "--ticket", t, "--review-file", str(review), *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["nextPending"], "read-review")
        self.complete(t, "read-review")
        res = self.complete(t, "triage-review", actor="user", extra=("--reopen", "coding"))
        self.assertIn("coding", res["stale"])
        self.assertNotIn("testcase", res["stale"])
        self.assertEqual(res["nextPending"], "coding")
        (self.repo / "main.py").write_text("print('v3')\n")
        for node in ("coding", "code-review", "execute-and-evidence", "final-review", "delivery-drafts"):
            self.complete(t, node)
        self.complete(t, "deliverable-ready", actor="user")
        self.complete(t, "respond-review", actor="user")
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual(st["nextPending"], "reflect")  # restarted by the handback; the reopen is a new signal
        self.complete(t, "reflect")
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertIsNone(st["nextPending"])

    def test_receipt_breaks_when_checkout_changes(self) -> None:
        t = "BUG-2"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review", "coding", "code-review", "execute-and-evidence", "final-review", "delivery-drafts"):
            self.complete(t, node)
        (self.repo / "main.py").write_text("print('sneaky')\n")
        code, res = run("gate", "--ticket", t, "--node", "deliverable-ready", "--status", "approved", "--actor", "user", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("checkout changed", res["error"])

    def test_wrong_model_refused(self) -> None:
        t = "BUG-3"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.complete(t, "prepare")
        result = self.tmp / "r.md"
        result.write_text("x")
        code, res = run("record", "--ticket", t, "--node", "investigate", "--unit", "worker",
                        "--result", str(result), "--model", "sonnet", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("refused", res["error"])

    def test_loop_cap_raises_g4(self) -> None:
        t = "BUG-4"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review"):  # D1: test cases before coding
            self.complete(t, node)
        self.complete(t, "coding")
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        code, _ = run("gate", "--ticket", t, "--node", "code-review", "--status", "needs-changes", *self.p)
        self.assertEqual(code, 0)
        self.complete(t, "coding")
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "code-review", "--status", "needs-changes", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("G4", res["error"])

    def test_final_review_names_every_untracked_file(self) -> None:
        """B5: a file a tool generated in the worktree is caught at final-review, not after the receipts."""
        t = "BUG-6"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review", "coding", "code-review", "execute-and-evidence"):
            self.complete(t, node)
        self.write_artifacts(t, "final-review")  # the review lists the change's files ...
        (self.repo / "AGENTS.md").write_text("generated by a dev server\n")  # ... then a tool writes one more
        code, res = run("check", "--ticket", t, "--node", "final-review", *self.p)
        self.assertEqual(code, 1, res)
        self.assertTrue(any("untracked files ship with the change" in p and "AGENTS.md" in p for p in res["problems"]),
                        res["problems"])
        (self.repo / "AGENTS.md").unlink()
        self.complete(t, "final-review")

    def test_loop_cap_counts_manual_reopens(self) -> None:
        """D5, the feature-flags pattern: coding reopened by hand between review loop-backs. Manual reopens count, and
        reopening a node does not reset the count of the review that loops back to it."""
        t = "BUG-5"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        for node in ("prepare", "investigate"):
            self.complete(t, node)
        self.complete(t, "fix-plan", actor="user")
        for node in ("testcase", "testcase-review"):  # D1: test cases before coding
            self.complete(t, node)
        self.complete(t, "coding")
        self.complete(t, "code-review")
        code, res = run("invalidate", "--ticket", t, "--node", "coding", "--reason", "fix docs", *self.p)
        self.assertEqual(code, 0, res)                      # round 1 of 2: the controller may reopen
        self.complete(t, "coding")
        code, res = run("invalidate", "--ticket", t, "--node", "coding", "--reason", "again", *self.p)
        self.assertEqual(code, 2)                           # round 2 reaches the cap: the user decides
        self.assertIn("G4", res["error"])
        code, res = run("invalidate", "--ticket", t, "--node", "coding", "--reason", "again", "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "coding")
        self.write_artifacts(t, "code-review", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "code-review", "--status", "needs-changes", *self.p)
        self.assertEqual(code, 2)                           # the first review loop-back already needs the user
        self.assertIn("G4", res["error"])
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["nodes"]["coding"]["reopens"], 2)


if __name__ == "__main__":
    unittest.main()
