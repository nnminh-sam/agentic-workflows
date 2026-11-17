package engine

// Port of tests/test_gh_policy.py. Subtest names are the Python test names.

import (
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const ghBody = "--base develop/base -F body.md"

var ghFiles = map[string]string{"body.md": "## Summary\nAdd x.\n\n## Detailed changes\n- add x\n\n## Explanation\nNeeded.\n\n" +
	"## Test results\n- Unit tests: 3 passed\n- Lint: clean\n"}

// decision is Policy.assertDecision.
func decision(t *testing.T, cmd string, d verdict.Decision, rule string, dialect shell.Dialect) {
	t.Helper()
	v := run(t, cmd, opts{files: ghFiles, dialect: dialect})
	if v.Decision() != d {
		t.Errorf("%q: got %s %v, want %s\n%s", cmd, v.Decision(), ids(v), d, v.Reason())
	}
	if rule != "" && !contains(ids(v), rule) {
		t.Errorf("%q: want %s in %v", cmd, rule, ids(v))
	}
}

func TestGhPolicy(t *testing.T) {
	b := shell.Bash
	t.Run("test_read_only_commands_allowed", func(t *testing.T) {
		for _, cmd := range []string{"gh pr list", "gh issue view 12", "gh repo view octocat/x", "gh api repos/octocat/x/pulls",
			"gh run list", "gh pr checks 3"} {
			decision(t, cmd, verdict.Allow, "", b)
		}
	})
	t.Run("test_command_policy", func(t *testing.T) {
		decision(t, "gh repo delete octocat/x --yes", verdict.DecDeny, "GH002", b)
		decision(t, "gh auth token", verdict.DecDeny, "GH002", b)
		decision(t, "gh pr merge 3 --squash", verdict.DecAsk, "GH002", b)
		decision(t, "gh secret set FOO --body bar", verdict.DecAsk, "GH002", b)
		decision(t, "gh release delete v1 --yes", verdict.DecDeny, "GH002", b)
	})
	t.Run("test_api_mutations", func(t *testing.T) {
		decision(t, "gh api -X DELETE repos/octocat/x/git/refs/heads/y", verdict.DecDeny, "GH003", b)
		decision(t, "gh api --method=PATCH repos/octocat/x -f name=y", verdict.DecAsk, "GH003", b)
		decision(t, "gh api repos/octocat/x/issues -f title=t", verdict.DecAsk, "GH003", b)
		decision(t, "gh api -XPOST repos/octocat/x/forks", verdict.DecAsk, "GH003", b)
		decision(t, "gh api graphql -f query='mutation { x }'", verdict.DecAsk, "GH003", b)
		decision(t, "gh api graphql -f query='query { viewer { login } }'", verdict.Allow, "", b)
	})
	t.Run("test_pr_create_must_be_noninteractive", func(t *testing.T) {
		decision(t, "gh pr create", verdict.DecDeny, "GH004", b)
		decision(t, "gh pr create --title 'feat: add x'", verdict.DecDeny, "GH004", b)
		decision(t, "gh pr create --fill --base develop/base", verdict.DecAsk, "GH008", b)
		decision(t, "gh pr create --title 'feat: add x' --body 'why' --base develop/base", verdict.DecDeny, "GH008", b)
		decision(t, "gh pr create -t 'feat: add x' -F body.md --base develop/base", verdict.Allow, "", b)
	})
	t.Run("test_pr_title_convention", func(t *testing.T) {
		decision(t, "gh pr create --title 'Fix stuff' "+ghBody, verdict.DecDeny, "GH005", b)
		decision(t, "gh pr edit 4 --title 'update things'", verdict.DecDeny, "GH005", b)
		decision(t, `gh pr create --title "$TITLE" `+ghBody, verdict.DecAsk, "GH005", b)
		decision(t, "gh pr create --title 'feat: Add x' "+ghBody, verdict.DecWarn, "", b)
	})
	t.Run("test_owner_scope", func(t *testing.T) {
		decision(t, "gh issue create -R someone/x --title t --body b", verdict.DecAsk, "GH006", b)
		decision(t, "gh repo clone someone/x", verdict.DecAsk, "GH006", b)
		decision(t, "gh api repos/someone/x/issues", verdict.DecAsk, "GH006", b)
		decision(t, "gh issue list --repo=octocat/x", verdict.Allow, "", b)
		decision(t, "gh repo view https://github.com/someone/x", verdict.DecAsk, "GH006", b)
	})
	t.Run("test_repo_create_visibility", func(t *testing.T) {
		decision(t, "gh repo create octocat/x", verdict.DecDeny, "GH007", b)
		decision(t, "gh repo create octocat/x --private", verdict.Allow, "", b)
		decision(t, "gh repo create octocat/x --public", verdict.DecAsk, "GH007", b)
	})
	t.Run("test_direct_api_calls_blocked", func(t *testing.T) {
		decision(t, "curl -s https://api.github.com/user", verdict.DecDeny, "GH001", b)
		decision(t, "Invoke-RestMethod -Uri https://api.github.com/repos/a/b", verdict.DecDeny, "GH001", shell.PowerShell)
		decision(t, "curl https://example.com/api.github.com.html", verdict.Allow, "", b)
	})
	t.Run("test_mixed_chain_takes_strictest", func(t *testing.T) {
		expect(t, "gh pr list && gh pr merge 1 && gh repo delete a/b --yes", opts{}, verdict.DecDeny, []string{"GH002", "GH006"})
	})
}
