"""workspace_graph.py on a throwaway workspace: profile repos (inside and outside the folder), worktrees, ahead/behind
against the remote and the base branch, nested parents, and the text / JSON output."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import helpers  # noqa: F401  (puts scripts/ on sys.path)
import workspace_graph as wg

ENV_KEYS = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo, msg):
    with open(Path(repo) / "f.txt", "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-q", "-m", msg)


class Fixture:
    """ws/ with sdlc.profile.json; ws/app (remote: a bare repo) with a nested child worktree under ws/worktrees and a
    develop/base worktree outside ws; a second repo outside ws listed in the profile."""

    def __init__(self):
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ.update(ENV_KEYS)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        ws = self.ws = root / "ws"
        ws.mkdir()
        remote = root / "remote.git"
        git(root, "init", "-q", "--bare", "-b", "main", str(remote))

        app = self.app = ws / "app"
        git(root, "init", "-q", "-b", "main", str(app))
        git(app, "remote", "add", "origin", str(remote))
        commit(app, "init")
        git(app, "push", "-q", "origin", "main")
        git(app, "switch", "-q", "-c", "develop/base")
        commit(app, "staging work")
        git(app, "push", "-q", "-u", "origin", "develop/base")
        # parent feature branch (pushed, not merged) and a nested child in a worktree
        git(app, "switch", "-q", "-c", "develop/feat/parent")
        commit(app, "parent 1")
        git(app, "push", "-q", "-u", "origin", "develop/feat/parent")
        child = ws / "worktrees" / "child"
        git(app, "worktree", "add", "-q", "-b", "develop/feat/child", str(child))
        commit(child, "child 1")
        commit(child, "child 2")
        # a branch one commit ahead of its remote, plus an untracked file
        git(app, "switch", "-q", "-c", "develop/fix/bug", "develop/base")
        commit(app, "fix 1")
        git(app, "push", "-q", "-u", "origin", "develop/fix/bug")
        commit(app, "fix 2")
        (app / "dirty.txt").write_text("x", encoding="utf-8")
        # a worktree outside the workspace, with local staging one commit behind its remote
        self.ext = root / "elsewhere"
        git(app, "worktree", "add", "-q", str(self.ext), "develop/base")
        git(self.ext, "reset", "-q", "--hard", "HEAD~1")

        other = self.other = root / "outside-repo"
        git(root, "init", "-q", "-b", "main", str(other))
        commit(other, "other")
        (ws / "sdlc.profile.json").write_text(json.dumps({
            "paths": {"worktreeRoot": "worktrees"},
            "repositories": [{"id": "app", "path": "app"}, {"id": "other", "path": "../outside-repo"}]}),
            encoding="utf-8")

    def data(self, start=None):
        return wg.collect(start or self.ws, None, False, 40, False)

    def close(self):
        self.tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class WorkspaceGraph(unittest.TestCase):
    """Read-only checks share one fixture."""

    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.f.close()

    def branches(self):
        return {x.name: x for x in self.f.data()["repos"][0].branches}

    def test_finds_repos_inside_and_outside_the_workspace(self):
        d = self.f.data()
        self.assertEqual([r.name for r in d["repos"]], ["app", "other"])
        self.assertEqual(d["repos"][1].rel, self.f.other.resolve().as_posix())

    def test_found_from_inside_a_worktree(self):
        d = self.f.data(self.f.ws / "worktrees" / "child")
        self.assertEqual(Path(d["workspace"]), self.f.ws.resolve())

    def test_checkouts_and_their_kinds(self):
        app = self.f.data()["repos"][0]
        kinds = {c.rel: (c.kind, c.branch) for c in app.checkouts}
        self.assertEqual(kinds["app"], ("base", "develop/fix/bug"))
        self.assertEqual(kinds["worktrees/child"], ("worktree", "develop/feat/child"))
        self.assertEqual(kinds[self.f.ext.resolve().as_posix()], ("external", "develop/base"))
        base = next(c for c in app.checkouts if c.kind == "base")
        self.assertEqual((base.dirty, base.untracked), (0, 1))

    def test_ahead_behind_remote_and_base(self):
        b = self.branches()
        self.assertEqual(b["develop/fix/bug"].vs_remote, (1, 0))
        self.assertEqual(b["develop/fix/bug"].base, "origin/develop/base")
        self.assertEqual(b["develop/fix/bug"].vs_base, (2, 0))
        self.assertEqual(b["develop/base"].vs_remote, (0, 1))
        self.assertEqual(b["main"].role, "production")
        self.assertEqual(b["main"].vs_base, (0, 1))  # one staging commit not released yet

    def test_nested_child_measured_against_its_parent(self):
        b = self.branches()
        self.assertEqual(b["develop/feat/child"].base, "origin/develop/feat/parent")
        self.assertEqual(b["develop/feat/child"].vs_base, (2, 0))
        self.assertEqual(b["develop/feat/parent"].base, "origin/develop/base")

    def test_checked_out_branch_knows_its_checkout(self):
        self.assertEqual(self.branches()["develop/feat/child"].checkout, "worktrees/child")

    def test_text_and_json_render(self):
        out = io.StringIO()
        with redirect_stdout(out):
            wg.main([str(self.f.ws), "--color", "never", "--width", "120"])
        text = out.getvalue()
        for needle in ("CHECKOUTS", "BRANCHES", "GRAPH", "worktrees/child", "develop/feat/child",
                       "1 unreleased on develop/base", "pull (ff)", "push"):
            self.assertIn(needle, text)
        out = io.StringIO()
        with redirect_stdout(out):
            wg.main([str(self.f.ws), "--json"])
        self.assertEqual(json.loads(out.getvalue())["repos"][0]["name"], "app")

    def test_html_view_embeds_the_data(self):
        out = Path(self.f.tmp.name) / "page" / "graph.html"
        with redirect_stdout(io.StringIO()):
            wg.main([str(self.f.ws), "--html", str(out)])
        page = out.read_text(encoding="utf-8")
        self.assertNotIn(wg.DATA_MARK, page)
        blob = page.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        data = json.loads(blob)
        app = data["repos"][0]
        self.assertTrue(app["commits"] and all("parents" in c for c in app["commits"]))
        child = next(b for b in app["branches"] if b["name"] == "develop/feat/child")
        self.assertEqual([c["subject"] for c in child["ahead_base"]], ["child 2", "child 1"])
        bug = next(b for b in app["branches"] if b["name"] == "develop/fix/bug")
        self.assertEqual([c["subject"] for c in bug["ahead_remote"]], ["fix 2"])
        self.assertIn({"kind": "good", "text": "push"}, bug["notes"])
        self.assertIn("--html", data["command"])

    def test_html_escapes_script_close(self):
        payload = {"x": "</script><b>"}
        data = {"repos": [], **payload}
        out = Path(self.f.tmp.name) / "esc.html"
        wg.write_html(data, out, [])
        self.assertNotIn("</script><b>", out.read_text(encoding="utf-8"))

    def test_ascii_output_is_ascii(self):
        out = io.StringIO()
        with redirect_stdout(out):
            wg.main([str(self.f.ws), "--ascii", "--color", "never"])
        self.assertTrue(out.getvalue().isascii())


class Mutating(unittest.TestCase):
    """Checks that change the fixture build their own."""

    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_merged_parent_is_not_a_parent(self):
        ext = self.f.ext  # develop/base is checked out there
        git(ext, "merge", "-q", "--ff-only", "origin/develop/base")
        git(ext, "merge", "-q", "--no-ff", "-m", "merge parent", "develop/feat/parent")
        git(ext, "push", "-q", "origin", "develop/base")
        b = {x.name: x for x in self.f.data()["repos"][0].branches}
        self.assertEqual(b["develop/feat/child"].base, "origin/develop/base")
        self.assertEqual(b["develop/feat/parent"].vs_base, (0, 1))

    def test_no_profile_scans_the_folder(self):
        (self.f.ws / "sdlc.profile.json").unlink()
        d = self.f.data()
        self.assertIsNone(d["profile"])
        self.assertEqual([r.name for r in d["repos"]], ["app"])


if __name__ == "__main__":
    unittest.main()
