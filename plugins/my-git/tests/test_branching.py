import unittest

from helpers import FakeRunner, evaluate, ids

GOOD_BODY = ("## Summary\nAdd x.\n\n## Detailed changes\n- add x\n\n## Explanation\nNeeded.\n\n"
             "## Test results\n### Unit\n`npm test`: 3 passed\n### Lint\n`npm run lint`: clean\n")
FILES = {"pr.md": GOOD_BODY}


def run(cmd, **kw):
    files = kw.pop("files", FILES)
    return evaluate(cmd, runner=FakeRunner(**kw), files=files)


class ProtectedCommit(unittest.TestCase):
    def test_commit_on_protected_denied(self):
        for b in ("main", "develop/base"):
            v = run("git commit -m 'feat: add x'", branch=b)
            self.assertEqual((v.decision, ids(v)), ("deny", ["BR001"]), b)

    def test_commit_on_work_branch_allowed(self):
        self.assertEqual(run("git commit -m 'feat: add x'").findings, [])

    def test_merge_and_cherry_pick_on_protected(self):
        self.assertIn("BR001", ids(run("git merge develop/feat/x", branch="develop/base")))
        self.assertIn("BR001", ids(run("git cherry-pick abc", branch="main")))
        self.assertEqual(run("git merge --ff-only origin/develop/base", branch="develop/base").findings, [])
        self.assertEqual(run("git rebase --abort", branch="main").findings, [])

    def test_initial_commit_asks(self):
        v = run("git commit -m 'chore: initialize repository'", branch="main", has_commits=False)
        self.assertEqual((v.decision, ids(v)), ("ask", ["BR001"]))


class ProtectedPush(unittest.TestCase):
    def test_push_protected_denied(self):
        for cmd, branch in [("git push origin develop/base", "develop/feat/x"), ("git push", "main"),
                            ("git push origin HEAD", "develop/base"), ("git push origin x:main", "x"),
                            ("git push origin HEAD:refs/heads/main", "develop/feat/x"),
                            ("git push --force origin +develop/base", "develop/feat/x")]:
            v = run(cmd, branch=branch)
            self.assertEqual(v.decision, "deny", cmd)
            self.assertIn("BR002", ids(v), cmd)

    def test_delete_and_all_denied(self):
        self.assertIn("BR002", ids(run("git push origin --delete main")))
        self.assertIn("BR002", ids(run("git push origin :develop/base")))
        self.assertIn("BR002", ids(run("git push --all origin")))

    def test_work_branch_push_allowed(self):
        self.assertEqual(run("git push -u origin develop/feat/test").findings, [])
        self.assertEqual(run("git push").findings, [])

    def test_first_push_of_protected_asks(self):
        v = run("git push -u origin develop/base", branch="develop/base", remote_branches=())
        self.assertEqual((v.decision, ids(v)), ("ask", ["BR002"]))


class NewBranch(unittest.TestCase):
    def test_good_branch(self):
        for cmd in ("git switch -c develop/feat/login develop/base", "git checkout -b develop/fix/x origin/develop/base",
                    "git branch develop/chore/deps origin/develop/base",
                    "git switch -c develop/feat/login --track origin/develop/base"):
            self.assertEqual(run(cmd).findings, [], cmd)
        self.assertEqual(run("git switch -c develop/feat/login", branch="develop/base").findings, [])

    def test_bad_name(self):
        for name in ("login", "feat/login", "develop/feat/Login", "develop/feature/x"):
            v = run(f"git switch -c {name} develop/base")
            self.assertEqual(ids(v), ["BR003"], name)

    def test_bad_base(self):
        self.assertEqual(ids(run("git switch -c develop/feat/x main")), ["BR004"])
        self.assertEqual(ids(run("git checkout -b develop/feat/x")), ["BR004"])  # current branch is a work branch

    def test_protected_names_and_listing_ok(self):
        self.assertEqual(run("git switch -c develop/base origin/develop/base", branch="main").findings, [])
        for cmd in ("git branch", "git branch -a", "git branch -d develop/feat/old", "git switch develop/base"):
            self.assertEqual(run(cmd).findings, [], cmd)


class PullRequest(unittest.TestCase):
    def test_default_target(self):
        self.assertEqual(run("gh pr create --base develop/base --title 'feat: add x' -F pr.md").findings, [])

    def test_base_required(self):
        self.assertIn("BR005", ids(run("gh pr create --title 'feat: add x' -F pr.md")))

    def test_release_only_from_staging(self):
        self.assertIn("BR005", ids(run("gh pr create --base main --title 'feat: add x' -F pr.md")))
        ok = "gh pr create --base main --head develop/base --title 'chore(release): v1.0.0' -F pr.md"
        self.assertEqual(run(ok).findings, [])
        self.assertEqual(run("gh pr create -B main --title 'chore(release): v1.0.0' -F pr.md", branch="develop/base").findings, [])

    def test_other_bases_denied(self):
        self.assertIn("BR005", ids(run("gh pr create --base develop/feat/y --title 'feat: add x' -F pr.md")))
        self.assertIn("BR005", ids(run("gh pr edit 3 --base main")))

    def test_body_sections(self):
        bad = {"pr.md": "## Summary\nAdd x.\n\n## Explanation\n\n## Test results\nUnit: ok\n"}
        v = run("gh pr create --base develop/base --title 'feat: add x' -F pr.md", files=bad)
        msgs = " ".join(f.message for f in v.findings)
        self.assertEqual(ids(v), ["GH008"])
        self.assertIn("Detailed changes", msgs)
        self.assertIn("'Explanation' is empty", msgs)
        self.assertIn("'lint'", msgs)

    def test_placeholders_and_comments_are_empty(self):
        body = GOOD_BODY.replace("Needed.", "<!-- why? -->\nTODO")
        v = run("gh pr create --base develop/base --title 'feat: add x' -F pr.md", files={"pr.md": body})
        self.assertEqual(ids(v), ["GH008"])

    def test_body_ai_attribution(self):
        body = GOOD_BODY + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
        v = run("gh pr create --base develop/base --title 'feat: add x' -F pr.md", files={"pr.md": body})
        self.assertEqual(ids(v), ["GH009"])

    def test_edit_without_body_skips_sections(self):
        self.assertEqual(run("gh pr edit 3 --title 'feat: add y'").findings, [])


if __name__ == "__main__":
    unittest.main()
