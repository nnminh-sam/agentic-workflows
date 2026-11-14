"""Hardening: state integrity, lock, SUPERSEDED, subject snapshot, content checks, citations, language, inputs,
schemas, git guard hook, kaneki-ship, review-fetch, installer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import HOME, Base, run  # noqa: E402

from harness import schema  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


class Flow(Base):
    def to(self, t: str, last: str) -> None:
        """Run a bug ticket through every node up to and including `last`."""
        order = ["prepare", "investigate", "fix-plan", "coding", "code-review", "testcase", "testcase-review",
                 "execute-and-evidence", "final-review", "delivery-drafts", "deliverable-ready"]
        for node in order[: order.index(last) + 1]:
            self.complete(t, node, actor="user" if node in ("fix-plan", "deliverable-ready") else "controller")


class IntegrityTests(Flow):
    def test_state_edited_outside_the_driver_is_refused(self) -> None:
        run("init", "--ticket", "I-1", "--kind", "bug", *self.p)
        state_path = self.tdir("I-1") / "state.json"
        st = json.loads(state_path.read_text(encoding="utf-8"))
        st["nodes"]["investigate"]["status"] = "done"  # skip a node by hand
        state_path.write_text(json.dumps(st), encoding="utf-8")
        code, res = run("status", "--ticket", "I-1", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("changed outside the driver", res["error"])
        code, res = run("status", "--ticket", "I-1", "--accept-current-state", "--reason", "test: deliberate edit", *self.p)
        self.assertEqual(code, 0, res)

    def test_edited_log_line_breaks_the_chain(self) -> None:
        run("init", "--ticket", "I-2", "--kind", "bug", *self.p)
        self.complete("I-2", "prepare")
        log = self.tdir("I-2") / "decisions.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"event"', '"event" ', 1)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, res = run("status", "--ticket", "I-2", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("hash chain", res["error"])

    def test_lock_blocks_a_second_writer_and_breaks_when_stale(self) -> None:
        run("init", "--ticket", "I-3", "--kind", "bug", *self.p)
        lock = self.tdir("I-3") / ".lock"
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({"pid": 1, "command": "gate", "since": time.time()}))
        code, res = run("gate", "--ticket", "I-3", "--node", "prepare", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("locked by another session", res["error"])
        (lock / "owner.json").write_text(json.dumps({"pid": 1, "command": "gate", "since": time.time() - 3600}))
        self.complete("I-3", "prepare")  # stale lock is broken and logged
        self.assertFalse(lock.exists())
        self.assertIn("lock-broken", (self.tdir("I-3") / "decisions.jsonl").read_text(encoding="utf-8"))

    def test_status_and_plan_do_not_write(self) -> None:
        run("init", "--ticket", "I-4", "--kind", "bug", *self.p)
        before = (self.tdir("I-4") / "decisions.jsonl").read_text(encoding="utf-8")
        run("status", "--ticket", "I-4", *self.p)
        run("plan", "--ticket", "I-4", *self.p)
        self.assertEqual(before, (self.tdir("I-4") / "decisions.jsonl").read_text(encoding="utf-8"))


class SupersedeAndSubjectTests(Flow):
    def test_reopened_artifact_must_be_rewritten(self) -> None:
        run("init", "--ticket", "S-1", "--kind", "bug", *self.p)
        self.to("S-1", "code-review")
        run("invalidate", "--ticket", "S-1", "--node", "coding", "--reason", "edge case missed", *self.p)
        impl = self.tdir("S-1") / "implementation.md"
        self.assertIn("## SUPERSEDED:", impl.read_text(encoding="utf-8"))
        code, pk = run("packet", "--ticket", "S-1", "--node", "coding", "--unit", "worker", *self.p)
        Path(pk["resultPath"]).write_text("ok")
        run("record", "--ticket", "S-1", "--node", "coding", "--unit", "worker", "--result", pk["resultPath"], "--model", "sonnet", *self.p)
        code, res = run("gate", "--ticket", "S-1", "--node", "coding", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("not rewritten since a rerun superseded them", res["error"])
        self.complete("S-1", "coding")  # a real rewrite passes

    def test_editing_a_new_untracked_file_breaks_the_receipt(self) -> None:
        (self.repo / "new_module.py").write_text("VALUE = 1\n")
        run("init", "--ticket", "S-2", "--kind", "bug", *self.p)
        self.to("S-2", "delivery-drafts")
        (self.repo / "new_module.py").write_text("VALUE = 2\n")  # same name, new content
        code, res = run("gate", "--ticket", "S-2", "--node", "deliverable-ready", "--status", "approved", "--actor", "user", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("checkout changed", res["error"])

    def test_ticket_folder_inside_the_repo_does_not_break_receipts(self) -> None:
        prof = json.loads(self.profile.read_text())
        prof["repositories"][0]["path"] = "app"
        prof["paths"]["ticketRoot"] = "app/.sdlc/tickets"  # single-repo project: tickets live inside the repo
        self.profile.write_text(json.dumps(prof))
        self.tdir = lambda t: self.repo / ".sdlc" / "tickets" / t  # type: ignore[method-assign]
        run("init", "--ticket", "S-3", "--kind", "bug", *self.p)
        self.to("S-3", "deliverable-ready")  # every log write after final-review would otherwise change the subject


class ContentCheckTests(Flow):
    def test_missing_section_and_rule_verdicts_fail_the_gate(self) -> None:
        run("init", "--ticket", "C-1", "--kind", "bug", *self.p)
        self.to("C-1", "investigate")
        self.write_artifacts("C-1", "fix-plan")
        plan = self.tdir("C-1") / "plan.md"
        plan.write_text(plan.read_text(encoding="utf-8").replace("## Test intent", "## Notes"), encoding="utf-8")
        code, pk = run("packet", "--ticket", "C-1", "--node", "fix-plan", "--unit", "worker", *self.p)
        Path(pk["resultPath"]).write_text("ok")
        run("record", "--ticket", "C-1", "--node", "fix-plan", "--unit", "worker", "--result", pk["resultPath"], "--model", "opus", *self.p)
        code, res = run("gate", "--ticket", "C-1", "--node", "fix-plan", "--status", "approved", "--actor", "user", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("missing section `## Test intent`", res["error"])
        text = plan.read_text(encoding="utf-8").replace("## Notes", "## Test intent").replace("- DES-01: PASS", "")
        plan.write_text(text, encoding="utf-8")
        code, res = run("gate", "--ticket", "C-1", "--node", "fix-plan", "--status", "approved", "--actor", "user", *self.p)
        self.assertIn("DES-01", res["error"])

    def test_review_verdict_must_match_the_gate(self) -> None:
        run("init", "--ticket", "C-2", "--kind", "bug", *self.p)
        self.to("C-2", "coding")
        self.write_artifacts("C-2", "code-review", verdict="NEEDS CHANGES")
        for unit in ("mechanical-review", "semantic-review"):
            code, pk = run("packet", "--ticket", "C-2", "--node", "code-review", "--unit", unit, *self.p)
            Path(pk["resultPath"]).write_text("ok")
            run("record", "--ticket", "C-2", "--node", "code-review", "--unit", unit, "--result", pk["resultPath"], "--model", "opus", *self.p)
        code, res = run("gate", "--ticket", "C-2", "--node", "code-review", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("verdict is NEEDS CHANGES", res["error"])

    def test_broken_citation_fails_a_receipt_node(self) -> None:
        run("init", "--ticket", "C-3", "--kind", "bug", *self.p)
        self.to("C-3", "fix-plan")
        self.write_artifacts("C-3", "coding")
        impl = self.tdir("C-3") / "implementation.md"
        impl.write_text(impl.read_text(encoding="utf-8") + "\nChanged `main.py:1` and `main.py:99`.\n", encoding="utf-8")
        code, pk = run("packet", "--ticket", "C-3", "--node", "coding", "--unit", "worker", *self.p)
        Path(pk["resultPath"]).write_text("ok")
        run("record", "--ticket", "C-3", "--node", "coding", "--unit", "worker", "--result", pk["resultPath"], "--model", "sonnet", *self.p)
        code, res = run("gate", "--ticket", "C-3", "--node", "coding", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("main.py:99", res["error"])
        self.assertNotIn("main.py:1`", res["error"])

    def test_draft_in_the_wrong_language_needs_the_user(self) -> None:
        prof = json.loads(self.profile.read_text())
        prof["locale"] = {"delivery": "ja"}
        self.profile.write_text(json.dumps(prof))
        run("init", "--ticket", "C-4", "--kind", "bug", *self.p)
        self.to("C-4", "final-review")
        plan = self.write_artifacts("C-4", "delivery-drafts")
        (self.tdir("C-4") / "drafts" / "commit-msg.md").write_text(
            "fix: handle empty orders\n\nThe order total was computed before the empty check, so empty orders crashed.\n", encoding="utf-8")
        code, pk = run("packet", "--ticket", "C-4", "--node", "delivery-drafts", "--unit", "worker", *self.p)
        Path(pk["resultPath"]).write_text("ok")
        run("record", "--ticket", "C-4", "--node", "delivery-drafts", "--unit", "worker", "--result", pk["resultPath"], "--model", "sonnet", *self.p)
        code, res = run("gate", "--ticket", "C-4", "--node", "delivery-drafts", "--status", "approved", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("G5", res["error"])
        self.assertIn("expected `ja`", res["error"])
        code, res = run("gate", "--ticket", "C-4", "--node", "delivery-drafts", "--status", "approved", "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)

    def test_packet_lists_inputs_from_earlier_nodes(self) -> None:
        run("init", "--ticket", "C-5", "--kind", "bug", *self.p)
        self.to("C-5", "fix-plan")
        code, pk = run("packet", "--ticket", "C-5", "--node", "coding", "--unit", "worker", *self.p)
        text = Path(pk["path"]).read_text(encoding="utf-8")
        self.assertIn("plan.md", text)
        self.assertIn("from `fix-plan`", text)
        self.assertIn("`## Rule self-check`", text)


class SchemaTests(Flow):
    def test_graph_typo_is_refused(self) -> None:
        graph = json.loads((HOME / "graph" / "lifecycle.json").read_text(encoding="utf-8"))
        self.assertEqual(schema.errors(graph, "graph.schema.json"), [])
        graph["nodes"]["coding"]["requries"] = ["approved-plan"]  # typo
        self.assertTrue(any("requries" in e for e in schema.errors(graph, "graph.schema.json")))

    def test_facts_with_unknown_key_are_refused(self) -> None:
        run("init", "--ticket", "D-1", "--kind", "bug", *self.p)
        facts = self.tmp / "facts.json"
        facts.write_text(json.dumps({"weakestClaim": "x", "obsereved": ["typo"]}))
        code, res = run("packet", "--ticket", "D-1", "--node", "prepare", "--unit", "worker", "--facts", str(facts), *self.p)
        self.assertEqual(code, 2)
        self.assertIn("obsereved", res["error"])


class GuardHookTests(unittest.TestCase):
    def hook(self, command: str) -> int:
        res = subprocess.run([sys.executable, str(HOME / "skills" / "kaneki-flow" / "scripts" / "guard_git.py")],
                             input=json.dumps({"tool_input": {"command": command}}), capture_output=True, text=True,
                             env=dict(os.environ, SDLC_HOME=str(HOME)))
        return res.returncode

    def test_guard_policy(self) -> None:
        blocked = ["git commit -m x", "cd app && git push origin HEAD", "git -C app reset --hard", "git fetch origin",
                   "git config user.email a@b.c", "echo ok; git rebase main", "/usr/bin/git merge x"]
        allowed = ["git status", "git diff HEAD", "git log -5", "npm test", "git add src/a.ts",
                   'sh "/x/.claude/skills/kaneki-flow/scripts/sdlc" --profile "p.json" git --ticket T-1 --repo app -- fetch',
                   "git worktree add ../app-T1 -b ticket/T1 main", "git remote -v"]
        for cmd in blocked:
            self.assertEqual(self.hook(cmd), 2, cmd)
        for cmd in allowed:
            self.assertEqual(self.hook(cmd), 0, cmd)


class ShipTests(Flow):
    def setUp(self) -> None:
        super().setUp()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prof = json.loads(self.profile.read_text())
        prof["delivery"] = {"adapter": "github-pr", "drafts": ["commit-msg", "pr-body", "tracker-reply"]}
        self.profile.write_text(json.dumps(prof))
        fake = self.tmp / "fake_gh.py"
        fake.write_text(textwrap.dedent('''
            import json, sys
            a = sys.argv[1:]
            if a[:2] == ["pr", "create"]:
                print("https://github.com/acme/app/pull/7")
            elif a[:2] == ["pr", "view"]:
                print(json.dumps({"url": "https://github.com/acme/app/pull/7", "headRefOid": "abc",
                                  "reviews": [{"author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "body": "Please rename x."}],
                                  "comments": []}))
            elif a[:1] == ["api"]:
                print(json.dumps([{"path": "main.py", "line": 1, "user": {"login": "rev"}, "commit_id": "abc", "body": "typo"}]))
            else:
                sys.exit(3)
        '''), encoding="utf-8")
        os.environ["SDLC_GH"] = f"{Path(sys.executable).as_posix()} {fake.as_posix()}"
        self.addCleanup(os.environ.pop, "SDLC_GH", None)

    def test_ship_commits_as_identity_pushes_and_opens_pr(self) -> None:
        (self.repo / "main.py").write_text("print('fixed')\n")
        (self.repo / "added.py").write_text("NEW = True\n")
        run("init", "--ticket", "SH-1", "--kind", "bug", *self.p)
        self.to("SH-1", "deliverable-ready")
        code, req = run("ship-request", "--ticket", "SH-1", "--repo", "app", *self.p)
        self.assertEqual(code, 0, req)
        self.assertEqual(req["files"], ["added.py", "main.py"])
        code, res = run("ship", "--ticket", "SH-1", "--repo", "app", "--approve", "0" * 64, *self.p)
        self.assertEqual(code, 2)
        self.assertIn("changed since it was approved", res["error"])
        code, res = run("ship", "--ticket", "SH-1", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["pushed"])
        self.assertEqual(res["pr"], "https://github.com/acme/app/pull/7")
        author = git(self.repo, "log", "-1", "--format=%an <%ae> / %cn <%ce>").strip()
        self.assertEqual(author, "Kaneki Dev <dev@kaneki.test> / Kaneki Dev <dev@kaneki.test>")
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.assertEqual(git(self.remote, "rev-parse", f"refs/heads/{branch}").strip(), res["commit"])

        # review round: fetch the review, reopen coding; the shipped commit is not a "node commit"
        git(self.repo, "remote", "set-url", "origin", "git@github.com:acme/app.git")
        code, res = run("review-fetch", "--ticket", "SH-1", "--repo", "app", *self.p)
        self.assertEqual(code, 0, res)
        self.assertIn("Please rename x.", Path(res["fetched"]).read_text(encoding="utf-8"))
        self.complete("SH-1", "read-review")
        self.complete("SH-1", "triage-review", actor="user", extra=("--reopen", "coding"))
        (self.repo / "main.py").write_text("print('fixed and renamed')\n")
        self.complete("SH-1", "coding")


class InstallTests(unittest.TestCase):
    def test_install_and_keep_user_edits(self) -> None:
        target = Path(tempfile.mkdtemp(prefix="sdlc-install-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        code, res = run("install", "--target", str(target))
        self.assertEqual(code, 0, res)
        claude = target / ".claude"
        for f in ("skills/kaneki-flow/SKILL.md", "skills/kaneki-ship/SKILL.md", "skills/kaneki-onboarding/SKILL.md",
                  "agents/kaneki-worker.md", "agents/kaneki-deep.md", "skills/kaneki-flow/scripts/guard-git"):
            self.assertTrue((claude / f).is_file(), f)
        self.assertEqual((claude / "skills/kaneki-flow/scripts/.harness-home").read_text().strip(), str(HOME.resolve()))
        res = subprocess.run([sys.executable, str(claude / "skills/kaneki-flow/scripts/sdlc.py"), "digest",
                              "--path", str(HOME / "graph" / "lifecycle.json")], capture_output=True, text=True,
                             env={k: v for k, v in os.environ.items() if k != "SDLC_HOME"})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        card = claude / "skills/kaneki-flow/SKILL.md"
        card.write_text(card.read_text(encoding="utf-8") + "\n<!-- my note -->\n", encoding="utf-8")
        code, res = run("install", "--target", str(target))
        self.assertIn("skills/kaneki-flow/SKILL.md", res["keptYourEdits"])
        self.assertIn("my note", card.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
