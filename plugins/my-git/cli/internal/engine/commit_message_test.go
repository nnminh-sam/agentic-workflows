package engine

// Port of tests/test_commit_message.py (Convention, Extraction,
// CommitThroughEngine). Subtest names are the Python test names.

import (
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// check is test_commit_message.check.
func check(t *testing.T, msg string, headerOnly bool, mutate func(map[string]*policy.Contract)) verdict.Verdict {
	t.Helper()
	c := testPolicy(t, testProfile(), mutate).Contract(policy.Commit)
	if headerOnly {
		return rules.CheckHeader(msg, c, verdict.Off)
	}
	return rules.CheckMessage(msg, c)
}

func setParam(id, key string, val any) func(map[string]*policy.Contract) {
	return func(cs map[string]*policy.Contract) {
		for _, c := range cs {
			for i := range c.Rules {
				if c.Rules[i].ID == id {
					c.Rules[i].Params[key] = val
				}
			}
		}
	}
}

func TestConvention(t *testing.T) {
	t.Run("test_valid_messages", func(t *testing.T) {
		for _, m := range []string{"feat: add login", "fix(api): handle 404 from upstream", "refactor(ui/button)!: drop size prop",
			"docs: update README for API v2", "feat: add retry\n\nLonger body.\n\nRefs: #12",
			"feat!: new config\n\nBREAKING CHANGE: config moved to .env"} {
			if v := check(t, m, false, nil); len(v.Findings) != 0 {
				t.Errorf("%q: %s", m, v.Reason())
			}
		}
	})
	t.Run("test_exempt_headers", func(t *testing.T) {
		for _, m := range []string{"Merge branch 'main' into x", `Revert "feat: add login"`, "fixup! feat: add login"} {
			if v := check(t, m, false, nil); len(v.Findings) != 0 {
				t.Errorf("%q: %s", m, v.Reason())
			}
		}
	})
	t.Run("test_each_rule_fires", func(t *testing.T) {
		cases := []struct{ msg, id string }{
			{"Added login", "CM003"}, {"feat:add login", "CM003"}, {"feat (api): x", "CM003"}, {"", "CM003"},
			{"feature: add login", "CM004"}, {"Fix: typo", "CM004"},
			{"feat(API): add", "CM005"}, {"feat(): add", "CM005"},
			{"fix: x", "CM006"},
			{"feat: " + strings.Repeat("a", 70), "CM007"},
			{"docs: fix typo.", "CM008"},
			{"feat: Add login", "CM009"},
			{"fix: fixed empty list", "CM010"},
			{"feat: x y z\nbody right away", "CM011"},
			{"feat: add x\n\n" + strings.Repeat("w", 120), "CM012"},
			{"feat: add x\n\nbreaking change: moved", "CM013"},
			{"feat: WIP login", "CM014"},
		}
		for _, c := range cases {
			if got := ids(check(t, c.msg, false, nil)); !contains(got, c.id) {
				t.Errorf("%q: want %s in %v", c.msg, c.id, got)
			}
		}
	})
	t.Run("test_severities", func(t *testing.T) {
		if d := check(t, "feat: Add login", false, nil).Decision(); d != verdict.DecWarn {
			t.Errorf("CM009 decision %s", d)
		}
		if d := check(t, "Added login", false, nil).Decision(); d != verdict.DecDeny {
			t.Errorf("CM003 decision %s", d)
		}
	})
	t.Run("test_long_url_lines_are_ignored", func(t *testing.T) {
		if v := check(t, "docs: add link\n\nhttps://example.com/"+strings.Repeat("x", 120), false, nil); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
	})
	t.Run("test_hint_for_missing_space", func(t *testing.T) {
		v := check(t, "feat:add", false, nil)
		if len(v.Findings) == 0 || !strings.Contains(v.Findings[0].Message, "one space after the colon") {
			t.Error(v.Reason())
		}
	})
	t.Run("test_allowed_scopes", func(t *testing.T) {
		m := setParam("CM005", "allowed_scopes", []any{"api"})
		if v := check(t, "feat(api): add x", false, m); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
		if !contains(ids(check(t, "feat(web): add x", false, m)), "CM005") {
			t.Error("feat(web) should fire CM005")
		}
	})
	t.Run("test_required_trailers_when_enabled", func(t *testing.T) {
		m := func(cs map[string]*policy.Contract) {
			setSeverity("CM015", verdict.Deny)(cs)
			setParam("CM015", "trailers", []any{"Signed-off-by"})(cs)
		}
		if !contains(ids(check(t, "feat: add x", false, m)), "CM015") {
			t.Error("CM015 should fire")
		}
		if v := check(t, "feat: add x\n\nSigned-off-by: A <a@b.c>", false, m); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
	})
	t.Run("test_off_rule_is_silent", func(t *testing.T) {
		if v := check(t, "feat: Add login", false, setSeverity("CM009", verdict.Off)); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
	})
	t.Run("test_header_only_skips_body_rules", func(t *testing.T) {
		if v := check(t, "feat: add x\nbody", true, nil); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
	})
	t.Run("test_cleanup_trims_whitespace", func(t *testing.T) {
		if v := check(t, "\n\nfeat: add x   \n\n\n\nbody\n\n", false, nil); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
	})
}

// extract is test_commit_message.extract.
func extract(t *testing.T, cmd string, d shell.Dialect, files map[string]string) rules.Message {
	t.Helper()
	invs := shell.ParseWith(cmd, d, cwd, shell.Options{})
	if len(invs) == 0 {
		t.Fatalf("%q: no invocation", cmd)
	}
	call, ok := shell.AsGit(invs[0])
	if !ok {
		t.Fatalf("%q: not a git call", cmd)
	}
	return rules.ExtractMessage(call, readFiles(files))
}

func TestExtraction(t *testing.T) {
	text := func(t *testing.T, cmd string, d shell.Dialect, files map[string]string, want string) {
		t.Helper()
		if m := extract(t, cmd, d, files); m.Kind != rules.MsgText || m.Text != want {
			t.Errorf("%q: got %s %q, want %q", cmd, m.Kind, m.Text, want)
		}
	}
	kind := func(t *testing.T, cmd string, d shell.Dialect, want rules.MessageKind) {
		t.Helper()
		if m := extract(t, cmd, d, nil); m.Kind != want {
			t.Errorf("%q: kind %s, want %s", cmd, m.Kind, want)
		}
	}
	t.Run("test_forms", func(t *testing.T) {
		text(t, "git commit -m 'feat: a'", shell.Bash, nil, "feat: a")
		text(t, "git commit -am 'feat: a'", shell.Bash, nil, "feat: a")
		text(t, "git commit -m'feat: a'", shell.Bash, nil, "feat: a")
		text(t, "git commit --message='feat: a'", shell.Bash, nil, "feat: a")
		text(t, "git commit --message 'feat: a' -m body", shell.Bash, nil, "feat: a\n\nbody")
	})
	t.Run("test_file_and_stdin", func(t *testing.T) {
		f := map[string]string{"msg.txt": "fix: b"}
		text(t, "git commit -F msg.txt", shell.Bash, f, "fix: b")
		text(t, "git commit --file=msg.txt", shell.Bash, f, "fix: b")
		text(t, "git commit -F - <<'EOF'\nfix: c\nEOF", shell.Bash, nil, "fix: c\n")
		kind(t, "git commit -F missing.txt", shell.Bash, rules.MsgUnreadable)
	})
	t.Run("test_reuse_and_none", func(t *testing.T) {
		kind(t, "git commit", shell.Bash, rules.MsgNone)
		kind(t, "git commit --amend", shell.Bash, rules.MsgNone)
		kind(t, "git commit --amend --no-edit", shell.Bash, rules.MsgReuse)
		kind(t, "git commit --fixup HEAD~1", shell.Bash, rules.MsgReuse)
		kind(t, "git commit -C HEAD", shell.Bash, rules.MsgReuse)
	})
	t.Run("test_dynamic", func(t *testing.T) {
		kind(t, `git commit -m "$MSG"`, shell.Bash, rules.MsgDynamic)
		kind(t, `git commit -m "$msg"`, shell.PowerShell, rules.MsgDynamic)
	})
	t.Run("test_powershell_here_string", func(t *testing.T) {
		text(t, "git commit -m @'\nfeat: a\n\nbody\n'@", shell.PowerShell, nil, "feat: a\n\nbody")
	})
}

func TestCommitThroughEngine(t *testing.T) {
	t.Run("test_good_commit_passes", func(t *testing.T) { clean(t, "git add -A && git commit -m 'feat: add login'", opts{}) })
	t.Run("test_bad_commit_denied", func(t *testing.T) {
		v := has(t, "git commit -m 'update stuff'", opts{}, "CM003")
		if v.Decision() != verdict.DecDeny {
			t.Errorf("decision %s", v.Decision())
		}
	})
	t.Run("test_no_message_denied", func(t *testing.T) { has(t, "git commit", opts{}, "CM001") })
	t.Run("test_dynamic_asks", func(t *testing.T) {
		expect(t, `git commit -m "$MSG"`, opts{}, verdict.DecAsk, []string{"CM002"})
	})
	t.Run("test_claude_style_heredoc", func(t *testing.T) {
		clean(t, "git commit -m \"$(cat <<'EOF'\nfeat(hooks): add git-guard\n\nChecks identity and messages.\nEOF\n)\"", opts{})
	})
	t.Run("test_ai_coauthor_denied", func(t *testing.T) {
		expect(t, "git commit -m \"$(cat <<'EOF'\nfeat(hooks): add git-guard\n\nChecks identity and messages.\n\n"+
			"Co-Authored-By: Claude <noreply@anthropic.com>\nEOF\n)\"", opts{}, verdict.DecDeny, []string{"CM016"})
	})
}

// TestBlockExample is the spec's "What a block looks like" example. The spec
// shows CM003 and CM008; the Python engine (and this port) report only CM003
// because subject rules do not run on a header that fails the convention.
func TestBlockExample(t *testing.T) {
	v := expect(t, `git commit -m "Added stuff."`, opts{}, verdict.DecDeny, []string{"CM003"})
	if v.Decision().ExitCode() != 2 {
		t.Errorf("exit code %d, want 2", v.Decision().ExitCode())
	}
	// The suggested fix passes; a conventional header with a trailing period gets CM008.
	clean(t, `git commit -m "feat: add stuff"`, opts{})
	expect(t, `git commit -m "feat: add stuff."`, opts{}, verdict.DecDeny, []string{"CM008"})
	for d, code := range map[verdict.Decision]int{verdict.Allow: 0, verdict.DecWarn: 0, verdict.DecAsk: 1, verdict.DecDeny: 2} {
		if d.ExitCode() != code {
			t.Errorf("%s exit %d want %d", d, d.ExitCode(), code)
		}
	}
}
