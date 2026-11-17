package rules_test

import (
	"reflect"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const prBody = "## Summary\nAdd x.\n\n## Detailed changes\n- add x\n\n## Explanation\nNeeded.\n\n" +
	"## Test results\n### Unit\n`npm test`: 3 passed\n### Lint\n`npm run lint`: clean\n"

func TestCheckPR(t *testing.T) {
	p := testPolicy(t, testProfile())
	gh, commit := p.Contract(policy.GitHubCLI), p.Contract(policy.Commit)
	cases := []struct {
		name, title, body string
		d                 verdict.Decision
		ids               []string
	}{
		{"good", "feat: add x", prBody, verdict.Allow, []string{}},
		{"title_warn_only", "feat: Add x", prBody, verdict.DecWarn, []string{"GH005"}},
		{"bad_title_and_body", "update stuff", "nothing", verdict.DecDeny, []string{"GH005", "GH008"}},
		{"ai_attribution", "feat: add x", prBody + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n", verdict.DecDeny, []string{"GH009"}},
		{"period", "fix: typo.", prBody, verdict.DecDeny, []string{"GH005"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			v := rules.CheckPR(c.title, c.body, gh, commit)
			if v.Decision() != c.d || !reflect.DeepEqual(ruleIDs(v), c.ids) {
				t.Errorf("got %s %v want %s %v\n%s", v.Decision(), ruleIDs(v), c.d, c.ids, v.Reason())
			}
		})
	}
}

func TestCheckMessageDirect(t *testing.T) {
	p := testPolicy(t, testProfile())
	commit := p.Contract(policy.Commit)
	// spec "What a block looks like": Python and Go both report CM003 only.
	if got := ruleIDs(rules.CheckMessage("Added stuff.", commit)); !reflect.DeepEqual(got, []string{"CM003"}) {
		t.Errorf("Added stuff.: %v", got)
	}
	if v := rules.CheckMessage("feat: add x", nil); len(v.Findings) != 0 {
		t.Errorf("nil contract should be silent: %v", v.Reason())
	}
	ai := rules.AIAttribution([]string{"Co-Authored-By: Claude <noreply@anthropic.com>", "plain"}, commit.Rule("CM016").Params)
	if len(ai) != 1 {
		t.Errorf("AIAttribution: %q", ai)
	}
}

func extractMsg(t *testing.T, cmd string) rules.Message {
	t.Helper()
	call, ok := shell.AsGit(shell.ParseWith(cmd, shell.Bash, "/repo", shell.Options{})[0])
	if !ok {
		t.Fatalf("%q", cmd)
	}
	return rules.ExtractMessage(call, func(p string) ([]byte, error) {
		if strings.HasSuffix(p, "m.txt") {
			return []byte("fix: from file\n"), nil
		}
		return nil, errInvalid
	})
}

var errInvalid = &invalidErr{}

type invalidErr struct{}

func (*invalidErr) Error() string { return "invalid" }

func TestExtractMessageForms(t *testing.T) {
	cases := []struct {
		cmd  string
		kind rules.MessageKind
		text string
	}{
		{"git commit -am'feat: a'", rules.MsgText, "feat: a"},
		{"git commit -F m.txt -m extra", rules.MsgText, ""}, // git rejects -m with -F; only kind checked
		// Python parity: _short_opt_value finds the first 'm' in the cluster, so -Fm.txt
		// reads as -m ".txt". Real git reads file "m.txt". Shared Python/Go bug, kept as-is.
		{"git commit -Fm.txt", rules.MsgText, ".txt"},
		{"git commit --file m.txt", rules.MsgText, "fix: from file\n"},
		{"git commit -F -", rules.MsgDynamic, ""},
		{"git commit -c HEAD", rules.MsgReuse, ""},
		{"git commit --reuse-message=HEAD", rules.MsgReuse, ""},
		{"git commit --squash=HEAD~2", rules.MsgReuse, ""},
		{"git commit -m 'feat: a' -- file.go", rules.MsgText, "feat: a"},
		{"git commit -- -m", rules.MsgNone, ""},
	}
	for _, c := range cases {
		m := extractMsg(t, c.cmd)
		if m.Kind != c.kind || (c.text != "" && m.Text != c.text) {
			t.Errorf("%q: got %s %q want %s %q", c.cmd, m.Kind, m.Text, c.kind, c.text)
		}
	}
}

func TestCheckHTTPClient(t *testing.T) {
	gh := testPolicy(t, testProfile()).Contract(policy.GitHubCLI)
	for cmd, deny := range map[string]bool{
		"curl -s https://api.github.com/user":                 true,
		"curl https://API.GITHUB.COM/user":                    true,
		"wget https://example.com":                            false,
		"curl https://example.com/api.github.com.html":        false,
		"curl -H 'Host: api.github.com' https://1.2.3.4/user": false,
	} {
		inv := shell.ParseWith(cmd, shell.Bash, "/repo", shell.Options{})[0]
		v := rules.CheckHTTPClient(inv, gh)
		if (v.Decision() == verdict.DecDeny) != deny {
			t.Errorf("%q: %s", cmd, v.Decision())
		}
	}
}
