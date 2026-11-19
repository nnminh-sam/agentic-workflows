"""pr_via = browser: GH010 denies every gh command, check_pr builds the prefilled compare link."""
import unittest
from urllib.parse import parse_qs, urlsplit

from helpers import PROFILE, CWD, FakeRunner, contracts, evaluate, ids
import check_pr
import session_start

BROWSER = contracts(profile={**PROFILE, "pr_via": "browser"})
ENV = {"GH_CONFIG_DIR": CWD + "/no-gh"}


class GH010(unittest.TestCase):
    def test_every_gh_command_denied_in_browser_mode(self):
        for cmd in ["gh pr list", "gh pr create --title 'feat: x' --body-file b.md --base develop/base",
                    "gh auth status", "gh api repos/octocat/x/pulls"]:
            v = evaluate(cmd, contracts_obj=BROWSER)
            self.assertEqual(v.decision, "deny", cmd)
            self.assertIn("GH010", ids(v))

    def test_powershell_gh_denied_too(self):
        self.assertIn("GH010", ids(evaluate("gh.exe pr view 3", dialect="powershell", contracts_obj=BROWSER)))

    def test_gh_mode_and_unset_unaffected(self):
        for prof in ({**PROFILE, "pr_via": "gh"}, PROFILE):
            self.assertNotIn("GH010", ids(evaluate("gh pr list", contracts_obj=contracts(profile=prof))))

    def test_git_unaffected(self):
        self.assertNotIn("GH010", ids(evaluate("git push origin develop/feat/test", contracts_obj=BROWSER)))

    def test_session_start_skips_gh_login_and_says_why(self):
        notes = session_start.summary(CWD, BROWSER, run=FakeRunner(gh_login="someone-else"), env=ENV)
        self.assertFalse(any("ID005" in n for n in notes))
        self.assertNotIn("pr_via", session_start.unset_profile(contracts()))


class CompareUrl(unittest.TestCase):
    def test_ssh_and_https_remotes(self):
        for remote in ["git@github.com:octocat/repo.git", "ssh://git@github.com/octocat/repo.git",
                       "https://github.com/octocat/repo.git", "https://github.com/octocat/repo"]:
            self.assertEqual(check_pr.repo_slug(remote), ("github.com", "octocat/repo"), remote)
        self.assertIsNone(check_pr.repo_slug("/local/path"))

    def test_link_is_prefilled(self):
        url, with_body = check_pr.compare_url("git@github.com:octocat/repo.git", "develop/base",
                                              "develop/docs/app", "docs(app): add plan & specs", "## Summary\nA + b")
        self.assertTrue(with_body)
        parts = urlsplit(url)
        self.assertEqual(parts.path, "/octocat/repo/compare/develop/base...develop/docs/app")
        q = parse_qs(parts.query)
        self.assertEqual((q["expand"], q["title"], q["body"]),
                         (["1"], ["docs(app): add plan & specs"], ["## Summary\nA + b"]))

    def test_long_body_left_out(self):
        url, with_body = check_pr.compare_url("git@github.com:octocat/repo.git", "develop/base", "develop/feat/x",
                                              "feat: x", "y" * (check_pr.MAX_URL + 1))
        self.assertFalse(with_body)
        self.assertNotIn("body=", url)
        self.assertLessEqual(len(url), check_pr.MAX_URL)

    def test_bad_remote_raises(self):
        with self.assertRaises(ValueError):
            check_pr.compare_url("not a url", "develop/base", "develop/feat/x", "feat: x", "")


if __name__ == "__main__":
    unittest.main()
