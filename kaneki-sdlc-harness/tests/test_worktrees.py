"""Ticket worktrees live together under paths.worktreeRoot, never loose next to the repositories."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402


def add_worktree(repo: Path, dest: Path, branch: str) -> None:
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(dest), "-b", branch], check=True)


class WorktreeRootTests(Base):
    def test_validate_reports_default_root_next_to_profile(self) -> None:
        code, res = run("validate", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(Path(res["worktreeRoot"]), (self.tmp / "worktrees").resolve())

    def test_prepare_packet_names_the_exact_worktree(self) -> None:
        t = "WT-1"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        code, plan = run("plan", "--ticket", t, "--node", "prepare", *self.p)
        self.assertEqual(code, 0, plan)
        code, pk = run("packet", "--ticket", t, "--node", "prepare", "--unit", plan["units"][0]["id"], *self.p)
        self.assertEqual(code, 0, pk)
        text = Path(pk["path"]).read_text(encoding="utf-8")
        self.assertIn(f"`{(self.tmp / 'worktrees' / 'app-WT-1').resolve().as_posix()}`", text)
        self.assertIn("branch `ticket/WT-1`", text)

    def test_bind_accepts_the_slot_and_refuses_a_loose_folder(self) -> None:
        t = "WT-2"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        loose = self.tmp / "app-WT-2"
        add_worktree(self.repo, loose, "loose-WT-2")
        code, res = run("worktree", "--ticket", t, "--repo", "app", "--path", str(loose), *self.p)
        self.assertNotEqual(code, 0)
        self.assertIn("worktrees/app-WT-2", res["error"])

        slot = self.tmp / "worktrees" / "app-WT-2"
        slot.parent.mkdir()
        add_worktree(self.repo, slot, "ticket/WT-2")
        code, res = run("worktree", "--ticket", t, "--repo", "app", "--path", str(slot), *self.p)
        self.assertEqual(code, 0, res)
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["context"]["repositories"][0]["worktreeRel"], "worktrees/app-WT-2")

    def test_custom_root_is_honoured(self) -> None:
        prof = json.loads(self.profile.read_text())
        prof["paths"]["worktreeRoot"] = "trees"
        self.profile.write_text(json.dumps(prof))
        code, res = run("validate", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(Path(res["worktreeRoot"]), (self.tmp / "trees").resolve())

    def test_root_inside_a_repository_is_refused(self) -> None:
        prof = json.loads(self.profile.read_text())
        prof["paths"]["worktreeRoot"] = "app/worktrees"
        self.profile.write_text(json.dumps(prof))
        code, res = run("validate", *self.p)
        self.assertNotEqual(code, 0)
        self.assertIn("inside repository app", res["error"])
        code, res = run("init", "--ticket", "WT-3", "--kind", "bug", *self.p)
        self.assertNotEqual(code, 0)
        self.assertIn("worktreeRoot", res["error"])


class OnboardingWorktreeRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sdlc-wt-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self, root: Path) -> dict:
        out = self.tmp / "cand.json"
        code, res = run("onboard-scan", "--root", str(root), "--out", str(out))
        self.assertEqual(code, 0, res)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_workspace_folder_gets_a_worktrees_subfolder(self) -> None:
        repo = self.tmp / "ws" / "shop"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        cand = self.scan(self.tmp / "ws")
        self.assertEqual(cand["proposal"]["paths.worktreeRoot"], "worktrees")

    def test_repository_as_root_keeps_worktrees_outside_it(self) -> None:
        repo = self.tmp / "shop"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        cand = self.scan(repo)
        self.assertEqual(cand["proposal"]["paths.worktreeRoot"], "../shop-worktrees")
        self.assertTrue(any("shop-worktrees" in g for g in cand["gaps"]))


if __name__ == "__main__":
    unittest.main()
