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

    def test_accept_current_state_waits_for_the_ticket_lock(self) -> None:
        run("init", "--ticket", "I-5", "--kind", "bug", *self.p)
        tdir = self.tdir("I-5")
        state_path, log_path, lock = tdir / "state.json", tdir / "decisions.jsonl", tdir / ".lock"
        st = json.loads(state_path.read_text(encoding="utf-8"))
        st["nodes"]["investigate"]["status"] = "done"  # edited outside the driver
        state_path.write_text(json.dumps(st), encoding="utf-8")
        lock.mkdir()
        owner = json.dumps({"pid": 1, "command": "gate", "since": time.time()})
        (lock / "owner.json").write_text(owner)
        state_before, log_before = state_path.read_bytes(), log_path.read_bytes()
        accept = ("status", "--ticket", "I-5", "--accept-current-state", "--reason", "test: deliberate edit", *self.p)
        code, res = run(*accept)  # a live foreign lock refuses the accept and writes nothing
        self.assertEqual(code, 2, res)
        self.assertIn("G7", res["error"])
        self.assertIn("locked by another session", res["error"])
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(log_path.read_bytes(), log_before)
        self.assertEqual((lock / "owner.json").read_text(), owner)
        code, res = run("status", "--ticket", "I-5", "--accept-current-state", *self.p)  # no reason: still refused, still no write
        self.assertEqual(code, 2, res)
        self.assertEqual(log_path.read_bytes(), log_before)
        # plain status stays lock-free: it is refused for the edit, not for the lock, and writes nothing
        code, res = run("status", "--ticket", "I-5", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("changed outside the driver", res["error"])
        self.assertEqual(log_path.read_bytes(), log_before)
        (lock / "owner.json").unlink()
        lock.rmdir()
        code, res = run(*accept)
        self.assertEqual(code, 0, res)
        added = log_path.read_bytes()[len(log_before):].decode("utf-8").splitlines()
        self.assertEqual(len(added), 1)
        self.assertEqual(json.loads(added[0])["event"], "state-write")
        self.assertIn('"acceptedByUser": true', added[0])
        self.assertFalse(lock.exists())
        code, res = run("status", "--ticket", "I-5", *self.p)
        self.assertEqual(code, 0, res)

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
        for unit, model in (("technical-review", "sonnet"), ("semantic-review", "opus")):
            code, pk = run("packet", "--ticket", "C-2", "--node", "code-review", "--unit", unit, *self.p)
            Path(pk["resultPath"]).write_text("ok")
            run("record", "--ticket", "C-2", "--node", "code-review", "--unit", unit, "--result", pk["resultPath"],
                "--model", model, "--verdict", "PASS", *self.p)
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


GUARD = HOME / "skills" / "kaneki-flow" / "scripts" / "guard_git.py"


class GuardHookTests(unittest.TestCase):
    def hook(self, command: str, project: Path | None = None, cwd: Path | None = None) -> int:
        env = {k: v for k, v in os.environ.items() if k not in ("SDLC_HOME", "CLAUDE_PROJECT_DIR")}
        if project:
            env["CLAUDE_PROJECT_DIR"] = str(project)
        payload = {"tool_input": {"command": command}, **({"cwd": str(cwd)} if cwd else {})}
        res = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True, text=True, env=env)
        return res.returncode

    def test_guard_policy_needs_no_harness(self) -> None:
        """Node rules only (identity/remote rules are my-git's), and no SDLC_HOME or .harness-home is needed."""
        blocked = ["git commit -m x", "cd app && git push origin HEAD", "git -C app reset --hard",
                   "echo ok; git rebase main", "/usr/bin/git merge x", "git branch -D old", "git clean -fd"]
        allowed = ["git status", "git diff HEAD", "git log -5", "npm test", "git add src/a.ts", "git fetch origin",
                   'sh "/x/.claude/skills/kaneki-flow/scripts/sdlc" --profile "p.json" git --ticket T-1 --repo app -- fetch',
                   "git worktree add ../app-T1 -b ticket/T1 main", "git remote -v", "cat plugins/my-git/README.md"]
        for cmd in blocked:
            self.assertEqual(self.hook(cmd), 2, cmd)
        for cmd in allowed:
            self.assertEqual(self.hook(cmd), 0, cmd)

    def test_ship_approval_lets_commit_and_push_through_in_that_checkout_only(self) -> None:
        from harness import mygit
        project = Path(tempfile.mkdtemp(prefix="sdlc-guard-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(project, ignore_errors=True))
        self.assertEqual(mygit.approvals_file(project), project / ".sdlc" / "ship-approvals.json")
        wt, other = project / "wt", project / "other"
        wt.mkdir()
        other.mkdir()
        commit = f'git -C "{wt.as_posix()}" -c user.name="A" commit -S -F msg.txt'
        self.assertEqual(self.hook(commit, project), 2)
        mygit.grant(project, "T-1", "app", str(wt), "develop/feat/x", "d" * 64)
        self.assertTrue((project / ".sdlc" / "ship-approvals.json").is_file())
        self.assertEqual(self.hook(commit, project), 0)
        self.assertEqual(self.hook(f'git -C "{wt}" push -u origin develop/feat/x', project), 0)
        self.assertEqual(self.hook("git push origin HEAD", project, cwd=wt), 0)
        self.assertEqual(self.hook(f'git -C "{other}" commit -m x', project), 2)  # another checkout
        self.assertEqual(self.hook(f'git -C "{wt}" rebase main', project), 2)  # only commit/push
        self.assertEqual(self.hook(f'git -C "{wt}" reset --hard', project), 2)
        mygit.revoke(project, "T-1", "app")
        self.assertEqual(self.hook(commit, project), 2)
        self.assertFalse(mygit.approvals_file(project).exists())

    def test_legacy_claude_approval_file_still_honored(self) -> None:
        project = Path(tempfile.mkdtemp(prefix="sdlc-guard-leg-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(project, ignore_errors=True))
        wt = project / "wt"
        wt.mkdir()
        legacy = project / ".claude" / "kaneki-ship-approvals.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"schemaVersion": 1, "approvals": [
            {"ticket": "T-1", "repo": "app", "path": str(wt.resolve()), "branch": "ticket/T-1",
             "requestDigest": "d" * 64, "allow": ["commit", "push"], "expiresAt": int(time.time()) + 600}
        ]}), encoding="utf-8")
        self.assertEqual(self.hook(f'git -C "{wt.as_posix()}" commit -F msg.txt', project), 0)
        self.assertEqual(self.hook(f'git -C "{wt.as_posix()}" push -u origin HEAD', project), 0)

    def test_guard_rules_match_the_wrapper_policy(self) -> None:
        import importlib.util
        from harness import gitpolicy
        spec = importlib.util.spec_from_file_location("kaneki_guard", GUARD)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)  # type: ignore[union-attr]
        self.assertEqual(guard.HISTORY, gitpolicy.HISTORY)
        samples = [("reset", ["--hard"]), ("reset", ["--soft", "HEAD~1"]), ("clean", ["-fd"]), ("clean", ["-n"]),
                   ("checkout", ["--", "a"]), ("checkout", ["-b", "x"]), ("restore", ["a"]), ("restore", ["--staged", "a"]),
                   ("branch", ["-D", "x"]), ("branch", ["x"]), ("stash", ["drop"]), ("stash", ["push"]),
                   ("worktree", ["remove", "--force", "x"]), ("worktree", ["remove", "x"]), ("update-ref", ["-d", "r"]),
                   ("gc", ["--prune=now"]), ("gc", [])]
        for sub, rest in samples:
            self.assertEqual(guard.destructive(sub, rest), gitpolicy.destructive(sub, rest), (sub, rest))


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

    MESSAGE = "fix(app): print the fixed value\n\nThe old value was wrong. Refs SH-1.\n"

    def my_git_commit(self, message_file: str, name: str = "Kaneki Dev") -> None:
        """What /my-git:commit runs: commit exactly what is staged, identity pinned on the command."""
        git(self.repo, "-c", f"user.name={name}", "-c", "user.email=dev@kaneki.test", "commit", "-q", "-F", message_file)

    def ready(self, ticket: str, backend: str | None = None) -> dict:
        (self.repo / "main.py").write_text("print('fixed')\n")
        (self.repo / "added.py").write_text("NEW = True\n")
        run("init", "--ticket", ticket, "--kind", "bug", *self.p)
        self.to(ticket, "deliverable-ready")
        (self.tdir(ticket) / "drafts" / "commit-msg.md").write_text(self.MESSAGE, encoding="utf-8")
        extra = ("--backend", backend) if backend else ()
        code, req = run("ship-request", "--ticket", ticket, "--repo", "app", *extra, *self.p)
        self.assertEqual(code, 0, req)
        return req

    def test_ship_stages_for_my_git_then_records_the_approved_commit(self) -> None:
        req = self.ready("SH-1", backend="my-git")
        self.assertEqual(req["files"], ["added.py", "main.py"])
        self.assertEqual((req["via"], req["pr"]), ("my-git", "my-git"))
        code, res = run("ship", "--ticket", "SH-1", "--repo", "app", "--approve", "0" * 64, "--backend", "my-git", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("changed since it was approved", res["error"])
        code, res = run("ship", "--ticket", "SH-1", "--repo", "app", "--approve", req["requestDigest"],
                        "--backend", "my-git", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(sorted(res["staged"]), ["added.py", "main.py"])
        self.assertTrue(res["next"][0].startswith("/my-git:commit"))
        self.assertTrue(any(step.startswith("/my-git:push") for step in res["next"]))
        self.assertTrue(any(step.startswith("/my-git:pr") for step in res["next"]))
        self.assertTrue((self.tmp / ".sdlc" / "ship-approvals.json").is_file())
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").strip(), "init")  # ship itself commits nothing

        code, res = run("ship-record", "--ticket", "SH-1", "--repo", "app", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("no new commit", res["error"])
        self.my_git_commit(str(self.tdir("SH-1") / "ship" / "app-commit-msg.txt"))
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        code, res = run("ship-record", "--ticket", "SH-1", "--repo", "app", "--pr",
                        "https://github.com/acme/app/pull/7", *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["pushed"])
        self.assertEqual(res["pr"], "https://github.com/acme/app/pull/7")
        self.assertEqual(git(self.remote, "rev-parse", f"refs/heads/{branch}").strip(), res["commit"])
        self.assertFalse((self.tmp / ".sdlc" / "ship-approvals.json").exists())

        # review round: fetch the review, reopen coding; the shipped commit is not a "node commit"
        git(self.repo, "remote", "set-url", "origin", "git@github.com:acme/app.git")
        code, res = run("review-fetch", "--ticket", "SH-1", "--repo", "app", *self.p)
        self.assertEqual(code, 0, res)
        self.assertIn("Please rename x.", Path(res["fetched"]).read_text(encoding="utf-8"))
        self.complete("SH-1", "read-review")
        self.complete("SH-1", "triage-review", actor="user", extra=("--reopen", "coding"))
        (self.repo / "main.py").write_text("print('fixed and renamed')\n")
        self.complete("SH-1", "coding")

    def test_ship_record_refuses_a_commit_that_is_not_the_approved_one(self) -> None:
        req = self.ready("SH-2", backend="my-git")
        code, res = run("ship", "--ticket", "SH-2", "--repo", "app", "--approve", req["requestDigest"],
                        "--backend", "my-git", *self.p)
        self.assertEqual(code, 0, res)
        other = self.tmp / "other-msg.txt"
        other.write_text("fix(app): something else\n", encoding="utf-8")
        self.my_git_commit(str(other))
        code, res = run("ship-record", "--ticket", "SH-2", "--repo", "app", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("commit message differs", res["error"])

    def test_ship_request_refuses_a_message_my_git_rejects(self) -> None:
        (self.repo / "main.py").write_text("print('fixed')\n")
        run("init", "--ticket", "SH-3", "--kind", "bug", *self.p)
        self.to("SH-3", "deliverable-ready")
        (self.tdir("SH-3") / "drafts" / "commit-msg.md").write_text(
            "fix(app): x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", encoding="utf-8")
        code, res = run("ship-request", "--ticket", "SH-3", "--repo", "app", "--backend", "my-git", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("my-git", res["error"])

    def test_native_ship_commits_pushes_opens_pr_without_my_git(self) -> None:
        os.environ["SDLC_MYGIT"] = str(self.tmp / "no-my-git")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        req = self.ready("SH-NAT-1")
        self.assertEqual((req["via"], req["pr"]), ("native-git", "gh"))
        code, res = run("ship", "--ticket", "SH-NAT-1", "--repo", "app", "--approve", "0" * 64, *self.p)
        self.assertEqual(code, 2)
        self.assertIn("changed since it was approved", res["error"])

        code, res = run("ship", "--ticket", "SH-NAT-1", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["via"], "native-git")
        self.assertTrue(res["pushed"])
        self.assertEqual(res["pr"], "https://github.com/acme/app/pull/7")
        self.assertEqual(res["next"], [])
        self.assertFalse((self.tmp / ".sdlc" / "ship-approvals.json").exists())
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.assertEqual(git(self.remote, "rev-parse", f"refs/heads/{branch}").strip(), res["commit"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an <%ae>").strip(), "Kaneki Dev <dev@kaneki.test>")

        # Re-ship after review round: updates branch and preserves PR URL without recreating PR
        old_gh = os.environ["SDLC_GH"]
        no_create_gh = self.tmp / "no_create_gh.py"
        no_create_gh.write_text(textwrap.dedent('''
            import json, sys
            a = sys.argv[1:]
            if a[:2] == ["pr", "view"]:
                print(json.dumps({"url": "https://github.com/acme/app/pull/7", "headRefOid": "abc",
                                  "reviews": [{"author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "body": "Fix y."}],
                                  "comments": []}))
            elif a[:1] == ["api"]:
                print("[]")
            else:
                sys.exit(4)  # pr create must NOT be called on re-ship
        '''), encoding="utf-8")
        os.environ["SDLC_GH"] = f"{Path(sys.executable).as_posix()} {no_create_gh.as_posix()}"
        git(self.repo, "remote", "set-url", "origin", "git@github.com:acme/app.git")
        code, res = run("review-fetch", "--ticket", "SH-NAT-1", "--repo", "app", *self.p)
        self.assertEqual(code, 0, res)
        git(self.repo, "remote", "set-url", "origin", str(self.remote))
        self.complete("SH-NAT-1", "read-review")
        self.complete("SH-NAT-1", "triage-review", actor="user", extra=("--reopen", "coding"))
        (self.repo / "main.py").write_text("print('fixed round 2')\n")
        for node in ("coding", "code-review", "execute-and-evidence",
                     "final-review", "delivery-drafts", "deliverable-ready"):
            self.complete("SH-NAT-1", node, actor="user" if node == "deliverable-ready" else "controller")
        (self.tdir("SH-NAT-1") / "drafts" / "commit-msg.md").write_text(
            "fix(app): apply round 2 review feedback\n\nRefs SH-NAT-1.\n", encoding="utf-8")
        code, req2 = run("ship-request", "--ticket", "SH-NAT-1", "--repo", "app", *self.p)
        self.assertEqual(code, 0, req2)
        code, res2 = run("ship", "--ticket", "SH-NAT-1", "--repo", "app", "--approve", req2["requestDigest"], *self.p)
        self.assertEqual(code, 0, res2)
        self.assertEqual(res2["pr"], "https://github.com/acme/app/pull/7")
        self.assertEqual(git(self.remote, "rev-parse", f"refs/heads/{branch}").strip(), res2["commit"])
        os.environ["SDLC_GH"] = old_gh

    def test_native_ship_manual_adapter_commits_without_push(self) -> None:
        os.environ["SDLC_MYGIT"] = str(self.tmp / "no-my-git")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        prof = json.loads(self.profile.read_text())
        prof["delivery"] = {"adapter": "manual", "drafts": ["commit-msg", "pr-body"]}
        self.profile.write_text(json.dumps(prof))
        req = self.ready("SH-NAT-2")
        self.assertEqual((req["via"], req["push"], req["pr"]), ("native-git", False, None))
        code, res = run("ship", "--ticket", "SH-NAT-2", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 0, res)
        self.assertFalse(res["pushed"])
        self.assertIsNone(res["pr"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").strip(), "fix(app): print the fixed value")

    def test_native_ship_rejects_bad_commit_messages(self) -> None:
        os.environ["SDLC_MYGIT"] = str(self.tmp / "no-my-git")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        (self.repo / "main.py").write_text("print('fixed')\n")
        run("init", "--ticket", "SH-NAT-3", "--kind", "bug", *self.p)
        self.to("SH-NAT-3", "deliverable-ready")
        bad_cases = [
            ("fix(app): ok\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", "CM016"),
            ("fix(app): ok\n\n🤖 Generated with Codex\n", "CM016"),
            ("Fixed the bug", "CM003"),
            ("fix(app): " + "a" * 65, "CM007"),
            ("fix(app): bad trailing period.", "CM008"),
            ("fix(app): WIP temporary", "CM014"),
            ("fix(app): valid header\nmissing blank line\n", "CM011"),
            ("fix(app): ok\n\nbreaking change: lowercase\n", "CM013"),
        ]
        for msg, rule_id in bad_cases:
            (self.tdir("SH-NAT-3") / "drafts" / "commit-msg.md").write_text(msg, encoding="utf-8")
            code, res = run("ship-request", "--ticket", "SH-NAT-3", "--repo", "app", *self.p)
            self.assertEqual(code, 2, (rule_id, msg))
            self.assertIn(rule_id, res["error"], (rule_id, msg))

    def test_native_ship_push_failure_records_commit_and_revokes_approval(self) -> None:
        os.environ["SDLC_MYGIT"] = str(self.tmp / "no-my-git")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "missing-remote.git"))
        req = self.ready("SH-NAT-4")
        code, res = run("ship", "--ticket", "SH-NAT-4", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 2)
        self.assertIn("commit", res["error"])
        self.assertIn("push failed", res["error"])
        self.assertFalse((self.tmp / ".sdlc" / "ship-approvals.json").exists())
        code, st = run("status", "--ticket", "SH-NAT-4", *self.p)
        self.assertEqual(code, 0, st)
        state = json.loads((self.tdir("SH-NAT-4") / "state.json").read_text(encoding="utf-8"))
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertIn(sha, state["shipped"]["app"]["commits"])

        git(self.repo, "remote", "set-url", "origin", str(self.remote))
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        code, rec = run("ship-record", "--ticket", "SH-NAT-4", "--repo", "app", "--pr",
                        "https://github.com/acme/app/pull/99", *self.p)
        self.assertEqual(code, 0, rec)
        self.assertTrue(rec["pushed"])
        self.assertEqual(rec["pr"], "https://github.com/acme/app/pull/99")


class InstallTests(unittest.TestCase):
    def test_install_and_keep_user_edits(self) -> None:
        target = Path(tempfile.mkdtemp(prefix="sdlc-install-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        code, res = run("install", "--target", str(target), "--host", "claude")
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
        code, res = run("install", "--target", str(target), "--host", "claude")
        self.assertIn("skills/kaneki-flow/SKILL.md", res["keptYourEdits"])
        self.assertIn("my note", card.read_text(encoding="utf-8"))

    def test_install_claude_without_my_git_succeeds(self) -> None:
        target = Path(tempfile.mkdtemp(prefix="sdlc-install-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        os.environ["SDLC_MYGIT"] = str(target / "missing")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        code, res = run("install", "--target", str(target), "--host", "claude")
        self.assertEqual(code, 0, res)
        self.assertNotIn("myGit", res)
        self.assertTrue((target / ".claude" / "skills/kaneki-flow/SKILL.md").is_file())
        self.assertTrue((target / ".sdlc" / "bin" / "sdlc").is_file())
        self.assertTrue((target / ".sdlc" / "host.local.json").is_file())

    def test_install_registers_the_my_git_of_the_same_checkout(self) -> None:
        from harness import mygit
        root = mygit.find_root(HOME)
        self.assertIsNotNone(root, "plugins/my-git next to the harness")
        target = Path(tempfile.mkdtemp(prefix="sdlc-install-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        (target / ".claude").mkdir()
        stale = {"extraKnownMarketplaces": {"my-git-local": {"source": {"source": "directory", "path": "/old/my-git"}}},
                 "enabledPlugins": {"my-git@my-git-local": True}}
        (target / ".claude" / "settings.local.json").write_text(json.dumps(stale), encoding="utf-8")
        code, res = run("install", "--target", str(target), "--host", "claude", "--with-my-git")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["myGit"]["version"], mygit.version(root))
        for name in ("settings.json", "settings.local.json"):
            s = json.loads((target / ".claude" / name).read_text(encoding="utf-8"))
            self.assertEqual(Path(s["extraKnownMarketplaces"]["my-git-local"]["source"]["path"]).resolve(), root.resolve(), name)
            self.assertTrue(s["enabledPlugins"]["my-git@my-git-local"], name)
        manifest = json.loads((target / ".claude" / "kaneki-harness.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["myGit"]["version"], mygit.version(root))

    def test_install_refuses_without_my_git(self) -> None:
        target = Path(tempfile.mkdtemp(prefix="sdlc-install-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        os.environ["SDLC_MYGIT"] = str(target / "missing")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        code, res = run("install", "--target", str(target), "--host", "claude", "--with-my-git")
        self.assertEqual(code, 2)
        self.assertIn("my-git", res["error"])
        self.assertFalse((target / ".claude" / "skills").exists())


class CommitMessageCheckTests(unittest.TestCase):
    def test_valid_and_exempt_messages(self) -> None:
        from harness import checks
        self.assertEqual(checks.commit_message("feat(api): add retry logic"), [])
        self.assertEqual(checks.commit_message("feat!: drop legacy endpoint\n\nBREAKING CHANGE: v1 removed"), [])
        self.assertEqual(checks.commit_message("Merge branch 'develop/base'"), [])
        self.assertEqual(checks.commit_message('Revert "feat: old"'), [])
        self.assertEqual(checks.commit_message("fixup! feat: something"), [])

    def test_rejects_every_rule(self) -> None:
        from harness import checks
        cases = [
            ("", "CM003"),
            ("Not conventional", "CM003"),
            ("feature: wrong type", "CM004"),
            ("feat(BAD_SCOPE): bad scope", "CM005"),
            ("feat(): empty scope", "CM005"),
            ("fix: x", "CM006"),
            ("feat: " + "a" * 70, "CM007"),
            ("docs: trailing dot.", "CM008"),
            ("feat: one\nno blank line", "CM011"),
            ("feat: one\n\nbreaking change: lower", "CM013"),
            ("feat: WIP work", "CM014"),
            ("feat: ok\n\nCo-Authored-By: Gemini <gemini@google.com>", "CM016"),
            ("feat: ok\n\nGenerated with Claude Code", "CM016"),
        ]
        for msg, expected_code in cases:
            errs = checks.commit_message(msg)
            self.assertTrue(any(e.startswith(expected_code) for e in errs), (expected_code, msg, errs))


class WorktreeHookTests(Flow):
    def setUp(self) -> None:
        super().setUp()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prof = json.loads(self.profile.read_text())
        prof["delivery"] = {"adapter": "github-pr", "drafts": ["commit-msg", "pr-body", "tracker-reply"]}
        self.profile.write_text(json.dumps(prof))
        fake = self.tmp / "fake_gh.py"
        fake.write_text('import sys\nprint("https://github.com/acme/app/pull/12")\n', encoding="utf-8")
        os.environ["SDLC_GH"] = f"{Path(sys.executable).as_posix()} {fake.as_posix()}"
        self.addCleanup(os.environ.pop, "SDLC_GH", None)

    def _raw_git(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")

    def _bind_worktree(self, ticket: str) -> Path:
        wt = self.tmp / "worktrees" / f"app-{ticket}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", str(wt), "-b", f"ticket/{ticket}"], check=True)
        code, res = run("worktree", "--ticket", ticket, "--repo", "app", "--path", str(wt), *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["hooks"]["installed"], res)
        return wt

    def test_worktree_bind_installs_hooks_that_block_commit_and_push_until_approved(self) -> None:
        from harness import approvals
        run("init", "--ticket", "WH-1", "--kind", "bug", *self.p)
        wt = self._bind_worktree("WH-1")
        (wt / "main.py").write_text("print('edited in wt')\n")
        git(wt, "add", "main.py")

        res = self._raw_git(wt, "-c", "user.name=Dev", "-c", "user.email=dev@kaneki.test", "commit", "-m", "sneaky")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Blocked by kaneki worktree hook", res.stderr)

        # Main checkout remains unaffected (per-worktree isolation)
        (self.repo / "main_only.txt").write_text("ok\n")
        git(self.repo, "add", "main_only.txt")
        main_res = self._raw_git(self.repo, "-c", "user.name=Dev", "-c", "user.email=dev@kaneki.test",
                                 "commit", "-m", "main repo commit")
        self.assertEqual(main_res.returncode, 0, main_res.stderr)

        # Grant approval -> commit and push succeed in worktree
        approvals.grant(self.tmp, "WH-1", "app", str(wt), "ticket/WH-1", "d" * 64)
        ok_commit = self._raw_git(wt, "-c", "user.name=Dev", "-c", "user.email=dev@kaneki.test",
                                  "commit", "-m", "fix: approved commit")
        self.assertEqual(ok_commit.returncode, 0, ok_commit.stderr)
        ok_push = self._raw_git(wt, "push", "-u", "origin", "HEAD:refs/heads/ticket/WH-1")
        self.assertEqual(ok_push.returncode, 0, ok_push.stderr)

        # Revoke -> blocked again
        approvals.revoke(self.tmp, "WH-1", "app")
        (wt / "main.py").write_text("print('second edit')\n")
        git(wt, "add", "main.py")
        blocked_again = self._raw_git(wt, "-c", "user.name=Dev", "-c", "user.email=dev@kaneki.test",
                                      "commit", "-m", "after revoke")
        self.assertNotEqual(blocked_again.returncode, 0)
        self.assertIn("Blocked by kaneki worktree hook", blocked_again.stderr)

    def test_worktree_hooks_allow_native_ship_end_to_end(self) -> None:
        os.environ["SDLC_MYGIT"] = str(self.tmp / "no-my-git")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        run("init", "--ticket", "WH-2", "--kind", "bug", *self.p)
        wt = self._bind_worktree("WH-2")
        (wt / "main.py").write_text("print('wt fix')\n")
        self.to("WH-2", "deliverable-ready")
        (self.tdir("WH-2") / "drafts" / "commit-msg.md").write_text("fix(app): fix inside worktree\n", encoding="utf-8")
        code, req = run("ship-request", "--ticket", "WH-2", "--repo", "app", *self.p)
        self.assertEqual(code, 0, req)
        code, res = run("ship", "--ticket", "WH-2", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["pushed"])
        self.assertEqual(res["pr"], "https://github.com/acme/app/pull/12")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/ticket/WH-2").strip(), res["commit"])
        self.assertFalse((self.tmp / ".sdlc" / "ship-approvals.json").exists())

        # Post-ship rogue commit inside worktree is blocked again
        (wt / "main.py").write_text("print('rogue')\n")
        git(wt, "add", "main.py")
        rogue = self._raw_git(wt, "-c", "user.name=Dev", "-c", "user.email=dev@kaneki.test", "commit", "-m", "rogue")
        self.assertNotEqual(rogue.returncode, 0)
        self.assertIn("Blocked by kaneki worktree hook", rogue.stderr)

    def test_worktree_hook_chains_existing_repo_hooks_and_is_idempotent(self) -> None:
        import shlex
        from harness import approvals
        repo_hooks = self.tmp / "repo-hooks"
        repo_hooks.mkdir()
        pc_marker = self.tmp / "pc-ran.txt"
        pp_marker = self.tmp / "pp-stdin.txt"
        pc_hook = repo_hooks / "pre-commit"
        pp_hook = repo_hooks / "pre-push"
        pc_hook.write_bytes(f"#!/bin/sh\necho ran > {shlex.quote(pc_marker.as_posix())}\n".encode("utf-8"))
        pp_hook.write_bytes(f"#!/bin/sh\ncat > {shlex.quote(pp_marker.as_posix())}\n".encode("utf-8"))
        os.chmod(pc_hook, 0o755)
        os.chmod(pp_hook, 0o755)
        git(self.repo, "config", "core.hooksPath", str(repo_hooks))

        run("init", "--ticket", "WH-3", "--kind", "bug", *self.p)
        wt = self._bind_worktree("WH-3")
        # Bind a second time to verify idempotency (must not self-chain into infinite loop)
        code, res2 = run("worktree", "--ticket", "WH-3", "--repo", "app", "--path", str(wt), *self.p)
        self.assertEqual(code, 0, res2)
        self.assertEqual(Path(res2["hooks"]["chained"]).resolve(), repo_hooks.resolve())

        (wt / "main.py").write_text("print('chained')\n")
        git(wt, "add", "main.py")
        approvals.grant(self.tmp, "WH-3", "app", str(wt), "ticket/WH-3", "e" * 64)
        ok_commit = self._raw_git(wt, "-c", "user.name=Dev", "-c", "user.email=dev@kaneki.test",
                                  "commit", "-m", "fix: chained commit")
        self.assertEqual(ok_commit.returncode, 0, ok_commit.stderr)
        self.assertTrue(pc_marker.is_file())
        ok_push = self._raw_git(wt, "push", "-u", "origin", "HEAD:refs/heads/ticket/WH-3")
        self.assertEqual(ok_push.returncode, 0, ok_push.stderr)
        self.assertTrue(pp_marker.is_file())
        self.assertIn("refs/heads/ticket/WH-3", pp_marker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
