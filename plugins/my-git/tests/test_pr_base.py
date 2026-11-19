"""pr_base.py on a real throwaway repository: the parent work branch is found, staging is the fallback."""
import os
import subprocess
import tempfile
import unittest

from helpers import contracts
import pr_base

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=ENV).stdout


class PrBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.repo = self.tmp.name
        git(r, "init", "-q", "-b", "develop/base")
        self.commit("base")
        git(r, "switch", "-q", "-c", "develop/feat/kaneki-harness")
        self.commit("parent work")
        git(r, "switch", "-q", "-c", "develop/feat/kh-P1-T07")
        self.commit("child work")
        git(r, "switch", "-q", "-c", "develop/fix/other", "develop/base")
        self.commit("unrelated")
        # pretend everything is on origin
        for b in ("develop/base", "develop/feat/kaneki-harness", "develop/feat/kh-P1-T07", "develop/fix/other"):
            git(r, "update-ref", f"refs/remotes/origin/{b}", b)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, msg):
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        git(self.repo, "add", "f.txt")
        git(self.repo, "commit", "-q", "-m", msg)

    def run_git(self, repo, *args):
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=ENV)
        return p.returncode, p.stdout

    def test_child_gets_parent(self):
        rows = pr_base.ranked(self.repo, "develop/feat/kh-P1-T07", contracts().branch, git=self.run_git)
        self.assertEqual(rows[0], ("develop/feat/kaneki-harness", 1))
        self.assertIn(("develop/base", 2), rows)

    def test_parent_and_unrelated_get_staging(self):
        c = contracts().branch
        self.assertEqual(pr_base.ranked(self.repo, "develop/feat/kaneki-harness", c, git=self.run_git)[0][0],
                         "develop/base")
        self.assertEqual(pr_base.ranked(self.repo, "develop/fix/other", c, git=self.run_git)[0][0], "develop/base")

    def test_strict_mode_only_staging(self):
        def strict(raw):
            raw["branching"]["settings"]["nested_work_branches"] = False
        rows = pr_base.ranked(self.repo, "develop/feat/kh-P1-T07", contracts(strict).branch, git=self.run_git)
        self.assertEqual([n for n, _ in rows], ["develop/base"])


if __name__ == "__main__":
    unittest.main()
