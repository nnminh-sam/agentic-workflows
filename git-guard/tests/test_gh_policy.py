import unittest

from helpers import evaluate, ids


class Policy(unittest.TestCase):
    def assertDecision(self, cmd, decision, rule=None, dialect="bash"):
        v = evaluate(cmd, dialect=dialect)
        self.assertEqual(v.decision, decision, f"{cmd}: {[f.render() for f in v.findings]}")
        if rule:
            self.assertIn(rule, ids(v))

    def test_read_only_commands_allowed(self):
        for cmd in ["gh pr list", "gh issue view 12", "gh repo view octocat/x", "gh api repos/octocat/x/pulls",
                    "gh run list", "gh pr checks 3"]:
            self.assertDecision(cmd, "allow")

    def test_command_policy(self):
        self.assertDecision("gh repo delete octocat/x --yes", "deny", "GH002")
        self.assertDecision("gh auth token", "deny", "GH002")
        self.assertDecision("gh pr merge 3 --squash", "ask", "GH002")
        self.assertDecision("gh secret set FOO --body bar", "ask", "GH002")
        self.assertDecision("gh release delete v1 --yes", "deny", "GH002")

    def test_api_mutations(self):
        self.assertDecision("gh api -X DELETE repos/octocat/x/git/refs/heads/y", "deny", "GH003")
        self.assertDecision("gh api --method=PATCH repos/octocat/x -f name=y", "ask", "GH003")
        self.assertDecision("gh api repos/octocat/x/issues -f title=t", "ask", "GH003")
        self.assertDecision("gh api -XPOST repos/octocat/x/forks", "ask", "GH003")
        self.assertDecision("gh api graphql -f query='mutation { x }'", "ask", "GH003")
        self.assertDecision("gh api graphql -f query='query { viewer { login } }'", "allow")

    def test_pr_create_must_be_noninteractive(self):
        self.assertDecision("gh pr create", "deny", "GH004")
        self.assertDecision("gh pr create --title 'feat: add x'", "deny", "GH004")
        self.assertDecision("gh pr create --fill", "allow")
        self.assertDecision("gh pr create --title 'feat: add x' --body 'why'", "allow")
        self.assertDecision("gh pr create -t 'feat: add x' -F body.md", "allow")

    def test_pr_title_convention(self):
        self.assertDecision("gh pr create --title 'Fix stuff' --body b", "deny", "GH005")
        self.assertDecision("gh pr edit 4 --title 'update things'", "deny", "GH005")
        self.assertDecision('gh pr create --title "$TITLE" --body b', "ask", "GH005")
        v = evaluate("gh pr create --title 'feat: Add x' --body b")
        self.assertEqual(v.decision, "warn")  # CM009 is only a warning

    def test_owner_scope(self):
        self.assertDecision("gh issue create -R someone/x --title t --body b", "ask", "GH006")
        self.assertDecision("gh repo clone someone/x", "ask", "GH006")
        self.assertDecision("gh api repos/someone/x/issues", "ask", "GH006")
        self.assertDecision("gh issue list --repo=octocat/x", "allow")
        self.assertDecision("gh repo view https://github.com/someone/x", "ask", "GH006")

    def test_repo_create_visibility(self):
        self.assertDecision("gh repo create octocat/x", "deny", "GH007")
        self.assertDecision("gh repo create octocat/x --private", "allow")
        self.assertDecision("gh repo create octocat/x --public", "ask", "GH007")

    def test_direct_api_calls_blocked(self):
        self.assertDecision("curl -s https://api.github.com/user", "deny", "GH001")
        self.assertDecision("Invoke-RestMethod -Uri https://api.github.com/repos/a/b", "deny", "GH001", dialect="powershell")
        self.assertDecision("curl https://example.com/api.github.com.html", "allow")

    def test_mixed_chain_takes_strictest(self):
        v = evaluate("gh pr list && gh pr merge 1 && gh repo delete a/b --yes")
        self.assertEqual(v.decision, "deny")
        self.assertEqual(ids(v), ["GH002", "GH006"])


if __name__ == "__main__":
    unittest.main()
