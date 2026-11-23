"""Git safety for every agent: the reference-transaction / pre-push / pre-commit hooks in ticket checkouts.

Real temporary repositories and a local bare remote. The owner's rule: branches are never deleted, nothing is
force-pushed, history is never rewritten outside an approved ship.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_hardening import Flow, git  # noqa: E402
from test_lifecycle import run  # noqa: E402

from harness import approvals  # noqa: E402

ID = ("-c", "user.name=Kaneki Dev", "-c", "user.email=dev@kaneki.test")
BLOCKED = "Blocked by kaneki worktree hook"


class GitSafetyBase(Flow):
    def setUp(self) -> None:
        super().setUp()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prof = json.loads(self.profile.read_text())
        prof["delivery"] = {"adapter": "github-pr", "drafts": ["commit-msg", "pr-body", "tracker-reply"]}
        self.profile.write_text(json.dumps(prof))
        fake = self.tmp / "fake_gh.py"
        fake.write_text('import sys\nprint("https://github.com/acme/app/pull/7")\n', encoding="utf-8")
        os.environ["SDLC_GH"] = f"{Path(sys.executable).as_posix()} {fake.as_posix()}"
        self.addCleanup(os.environ.pop, "SDLC_GH", None)
        os.environ["SDLC_MYGIT"] = str(self.tmp / "no-my-git")
        self.addCleanup(os.environ.pop, "SDLC_MYGIT", None)
        self.base = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        # a second commit on the base, and a branch nobody may delete
        (self.repo / "two.txt").write_text("two\n")
        git(self.repo, "add", "two.txt")
        git(self.repo, *ID, "commit", "-qm", "two")
        git(self.repo, "branch", "scratch")

    def raw(self, where: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, encoding="utf-8")

    def rev(self, where: Path, ref: str) -> str:
        return git(where, "rev-parse", ref).strip()

    def commit_file(self, where: Path, name: str, text: str, *extra: str) -> subprocess.CompletedProcess[str]:
        (where / name).write_text(text)
        git(where, "add", name)
        return self.raw(where, *ID, "commit", "-qm", f"fix: {name}", *extra)

    def assert_refused(self, res: subprocess.CompletedProcess[str], rule: str) -> None:
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(BLOCKED, res.stderr)
        self.assertIn(rule, res.stderr)


class LinkedWorktreeSafetyTests(GitSafetyBase):
    def bind(self, ticket: str) -> Path:
        run("init", "--ticket", ticket, "--kind", "bug", *self.p)
        wt = self.tmp / "worktrees" / f"app-{ticket}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", str(wt), "-b", f"ticket/{ticket}")
        code, res = run("worktree", "--ticket", ticket, "--repo", "app", "--path", str(wt), *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual((res["hooks"]["installed"], res["hooks"]["mode"]), (True, "worktree"), res)
        return wt

    def test_branch_delete_is_refused_in_every_spelling(self) -> None:
        wt = self.bind("GS-1")
        for args in (("branch", "-D", "scratch"), ("branch", "-d", "scratch"), ("branch", "--delete", "scratch"),
                     ("branch", "-m", "scratch", "renamed"), ("update-ref", "-d", "refs/heads/scratch")):
            self.assert_refused(self.raw(wt, *args), "branches are never deleted")
        self.assertTrue(self.rev(self.repo, "refs/heads/scratch"))

    def test_history_rewrites_on_the_ticket_branch_are_refused(self) -> None:
        wt = self.bind("GS-2")
        approvals.grant(self.tmp, "GS-2", "app", str(wt), "ticket/GS-2", "a" * 64)
        self.assertEqual(self.commit_file(wt, "fix.txt", "fix\n").returncode, 0)
        approvals.revoke(self.tmp, "GS-2", "app")
        head = self.rev(wt, "HEAD")

        self.assert_refused(self.raw(wt, "reset", "--hard", "HEAD~1"), "history is never rewritten")
        self.assertEqual(self.rev(wt, "refs/heads/ticket/GS-2"), head)
        git(wt, "checkout", "-q", "--", ".")  # reset rewrote the files before its ref update was refused
        # --no-verify skips pre-commit, never reference-transaction
        self.assert_refused(self.commit_file(wt, "fix.txt", "amended\n", "--amend", "--no-verify"),
                            "history is never rewritten")
        git(wt, "reset", "-q")
        git(wt, "checkout", "-q", "--", ".")
        self.assert_refused(self.raw(wt, "branch", "-f", "scratch", "scratch~1"), "history is never rewritten")

        git(self.repo, *ID, "commit", "-q", "--allow-empty", "-m", "base moves on")  # main checkout is not guarded
        res = self.raw(wt, *ID, "rebase", self.base)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("history is never rewritten", res.stdout + res.stderr)
        self.raw(wt, "rebase", "--abort")
        self.assertEqual(self.rev(self.repo, "refs/heads/ticket/GS-2"), head)

    def test_protected_branches_are_never_updated(self) -> None:
        wt = self.bind("GS-3")
        for branch in ("main", "develop/base"):
            self.assert_refused(self.raw(wt, "branch", branch, "HEAD"), "is protected")
        approvals.grant(self.tmp, "GS-3", "app", str(wt), "ticket/GS-3", "b" * 64)
        self.assert_refused(self.raw(wt, "push", "origin", "HEAD:refs/heads/main"), "is protected")

    def test_approved_ship_commits_and_pushes_but_never_forces_or_deletes(self) -> None:
        wt = self.bind("GS-4")
        (wt / "main.py").write_text("print('shipped')\n")
        self.to("GS-4", "deliverable-ready")
        (self.tdir("GS-4") / "drafts" / "commit-msg.md").write_text("fix(app): ship through the hooks\n", encoding="utf-8")
        code, req = run("ship-request", "--ticket", "GS-4", "--repo", "app", *self.p)
        self.assertEqual(code, 0, req)
        code, res = run("ship", "--ticket", "GS-4", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["pushed"])
        self.assertEqual(self.rev(self.remote, "refs/heads/ticket/GS-4"), res["commit"])

        # even inside an approval: amend passes locally (the ship's fix-up path), but forcing it out does not
        approvals.grant(self.tmp, "GS-4", "app", str(wt), "ticket/GS-4", "c" * 64)
        self.assertEqual(self.raw(wt, *ID, "commit", "-q", "--amend", "-m", "fix(app): reworded").returncode, 0)
        self.assert_refused(self.raw(wt, "push", "--force", "origin", "HEAD:refs/heads/ticket/GS-4"),
                            "nothing is force-pushed")
        self.assert_refused(self.raw(wt, "push", "origin", "--delete", "ticket/GS-4"), "branches are never deleted")
        self.assertEqual(self.rev(self.remote, "refs/heads/ticket/GS-4"), res["commit"])

    def test_main_checkout_is_untouched_by_linked_worktree_hooks(self) -> None:
        self.bind("GS-5")
        self.assertEqual(self.raw(self.repo, "branch", "-D", "scratch").returncode, 0)
        self.assertEqual(self.raw(self.repo, "reset", "-q", "--hard", "HEAD~1").returncode, 0)


class InPlaceLaneSafetyTests(GitSafetyBase):
    def init_lite(self, ticket: str) -> dict:
        code, res = run("init", "--ticket", ticket, "--kind", "bug", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["hooks"]["app"]["mode"], "in-place", res)
        return res

    def worktree_hooks_path(self) -> subprocess.CompletedProcess[str]:
        return self.raw(self.repo, "config", "--worktree", "--get", "core.hooksPath")

    def test_main_checkout_without_an_active_ticket_is_unaffected(self) -> None:
        self.assertEqual(self.raw(self.repo, "branch", "scratch2").returncode, 0)
        self.assertEqual(self.raw(self.repo, "branch", "-D", "scratch2").returncode, 0)
        self.init_lite("LP-0")
        code, res = run("worktree", "--ticket", "LP-0", "--repo", "app", "--release", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("--actor user", res["error"])
        code, res = run("worktree", "--ticket", "LP-0", "--repo", "app", "--release", "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["released"] and res["hooksPathRestored"], res)
        self.assertNotEqual(self.worktree_hooks_path().returncode, 0)  # nothing left in the checkout's config
        self.assertEqual(self.raw(self.repo, "branch", "-D", "scratch").returncode, 0)
        self.assertEqual(self.raw(self.repo, "reset", "-q", "--hard", "HEAD~1").returncode, 0)
        self.assertEqual(self.commit_file(self.repo, "own.txt", "owner work\n").returncode, 0)

    def test_deleted_ticket_stops_enforcing_even_before_release(self) -> None:
        self.init_lite("LP-1")
        self.assert_refused(self.raw(self.repo, "branch", "-D", "scratch"), "branches are never deleted")
        shutil.rmtree(self.tdir("LP-1"))
        self.assertEqual(self.raw(self.repo, "branch", "-D", "scratch").returncode, 0)

    def test_active_in_place_ticket_is_protected_and_ships(self) -> None:
        hooks = self.tmp / "owner-hooks"
        hooks.mkdir()
        seen = self.tmp / "owner-rt.txt"
        (hooks / "reference-transaction").write_bytes(
            f"#!/bin/sh\ncat >> {shlex.quote(seen.as_posix())}\n".encode("utf-8"))
        os.chmod(hooks / "reference-transaction", 0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))

        self.init_lite("LP-2")
        git(self.repo, "switch", "-q", "-c", "ticket/LP-2")  # creating the ticket branch is allowed
        self.assertIn("refs/heads/ticket/LP-2", seen.read_text(encoding="utf-8"))  # owner's hook still chained
        for args in (("branch", "-D", "scratch"), ("branch", "--delete", "scratch")):
            self.assert_refused(self.raw(self.repo, *args), "branches are never deleted")
        self.assert_refused(self.raw(self.repo, "reset", "--hard", "HEAD~1"), "history is never rewritten")
        git(self.repo, "checkout", "-q", "--", ".")
        self.assert_refused(self.raw(self.repo, "branch", "-f", self.base, "HEAD~1"), "")
        self.assert_refused(self.commit_file(self.repo, "x.txt", "x\n"), "nodes never commit")
        self.assert_refused(self.raw(self.repo, *ID, "commit", "-q", "--amend", "--no-verify", "-m", "x"),
                            "history is never rewritten")
        git(self.repo, "reset", "-q")
        (self.repo / "x.txt").unlink()

        # the approved ship still commits and pushes from the checkout itself
        (self.repo / "main.py").write_text("print('lite fix')\n")
        for node, actor in (("scope", "user"), ("change", "controller"), ("check", "controller"),
                            ("delivery-drafts", "controller"), ("deliverable-ready", "user")):
            self.complete("LP-2", node, actor=actor)
        (self.tdir("LP-2") / "drafts" / "commit-msg.md").write_text("fix(app): lite fix in place\n", encoding="utf-8")
        code, req = run("ship-request", "--ticket", "LP-2", "--repo", "app", *self.p)
        self.assertEqual(code, 0, req)
        code, res = run("ship", "--ticket", "LP-2", "--repo", "app", "--approve", req["requestDigest"], *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["pushed"], res)
        self.assertEqual(self.rev(self.remote, "refs/heads/ticket/LP-2"), res["commit"])
        self.assert_refused(self.raw(self.repo, "branch", "-D", "scratch"), "branches are never deleted")

        code, res = run("worktree", "--ticket", "LP-2", "--repo", "app", "--release", "--actor", "user", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(Path(git(self.repo, "config", "--get", "core.hooksPath").strip()).resolve(), hooks.resolve())
        self.assertEqual(self.raw(self.repo, "branch", "-D", "scratch").returncode, 0)

    def test_moving_the_ticket_to_a_linked_worktree_releases_the_checkout(self) -> None:
        self.init_lite("LP-3")
        code, res = run("lane", "--ticket", "LP-3", "--set", "full", "--reason", "bigger than it looked", *self.p)
        self.assertEqual(code, 0, res)
        wt = self.tmp / "worktrees" / "app-LP-3"
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", str(wt), "-b", "ticket/LP-3")
        code, res = run("worktree", "--ticket", "LP-3", "--repo", "app", "--path", str(wt), *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["hooks"]["releasedInPlace"]["hooksPathRestored"], res)
        self.assertEqual(self.raw(self.repo, "branch", "-D", "scratch").returncode, 0)
        git(self.repo, "branch", "scratch")
        self.assert_refused(self.raw(wt, "branch", "-D", "scratch"), "branches are never deleted")
