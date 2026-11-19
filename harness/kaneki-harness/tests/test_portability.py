"""OS-agnostic behaviour: moved projects, the sh launcher, the bash used for verification."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import HOME, Base, run  # noqa: E402

from harness.onboard import find_bash, find_file  # noqa: E402


class MovedProjectTests(Base):
    def test_ticket_survives_moving_the_project(self) -> None:
        """Profile, repo and tickets move together (another machine, another OS); state paths re-derive."""
        t = "MOVE-1"
        # make the repo path relative to the profile, as onboarding writes it
        prof = json.loads(self.profile.read_text())
        prof["repositories"][0]["path"] = "app"
        self.profile.write_text(json.dumps(prof))
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.complete(t, "prepare")
        code, res = run("worktree", "--ticket", t, "--repo", "app", "--path", str(self.repo), *self.p)
        self.assertEqual(code, 0, res)
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["profilePath"], "../../sdlc.profile.json")
        self.assertEqual(state["context"]["repositories"][0]["worktreeRel"], "app")

        # copy, not move: on Windows a rename of a folder holding read-only git objects can be refused (AV handles)
        moved = self.tmp.parent / (self.tmp.name + "-moved")
        shutil.copytree(self.tmp, moved)
        original = self.tmp
        self.addCleanup(shutil.rmtree, original, True)
        self.tmp = moved  # tearDown cleans the new place
        self.repo = moved / "app"
        self.profile = moved / "sdlc.profile.json"
        self.p = ["--profile", str(self.profile)]

        self.complete(t, "investigate")
        self.complete(t, "fix-plan", actor="user")
        res = self.complete(t, "coding")  # subject digest needs the repo at its new place
        code, st = run("status", "--ticket", t, "--compact", *self.p)
        self.assertEqual(st["nextPending"], "code-review")
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["context"]["repositories"][0]["worktree"].startswith(str(moved.resolve())))


class ShellTests(unittest.TestCase):
    def test_bash_is_never_the_wsl_launcher(self) -> None:
        bash = find_bash()
        if os.name == "nt" and bash:
            self.assertNotIn("system32", bash[0].lower())
        if os.name != "nt":
            self.assertIsNotNone(bash)

    def test_find_file_is_case_insensitive(self) -> None:
        tmp = Path(__file__).resolve().parent
        self.assertIsNotNone(find_file(tmp.parent, "readme.MD"))

    def test_sh_launcher_runs_the_driver(self) -> None:
        sh = find_bash()
        if not sh:
            self.skipTest("no POSIX shell on this machine")
        launcher = HOME / "skills" / "kaneki-flow" / "scripts" / "sdlc"
        target = HOME / "graph" / "lifecycle.json"
        env = dict(os.environ, SDLC_PYTHON=Path(sys.executable).as_posix())
        res = subprocess.run([*sh, str(launcher).replace("\\", "/"), "digest", "--path", str(target)],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout.strip().splitlines()[-1])["bytes"], target.stat().st_size)


if __name__ == "__main__":
    unittest.main()
