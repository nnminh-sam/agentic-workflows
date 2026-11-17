package agent

import (
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func TestCheckApply(t *testing.T) {
	tests := []struct {
		cmd     string
		dialect shell.Dialect
		ask     bool
		how     string
	}{
		{"mygit commit --apply abc123", shell.Bash, true, "--apply abc123"},
		{"mygit commit --apply=abc123", shell.Bash, true, "--apply=abc123"},
		{"mygit commit --apply", shell.Bash, true, "--apply"},
		{"mygit commit --apply --json", shell.Bash, true, "--apply"},
		{"mygit pr create -y", shell.Bash, true, "-y"},
		{"mygit pr create --yes", shell.Bash, true, "--yes"},
		{"mygit pr create --yes=true", shell.Bash, true, "--yes=true"},
		{"mygit branch new feat x --yes && git status", shell.Bash, true, "--yes"},
		{"cd repo; mygit push --apply 7f3a2c", shell.Bash, true, "--apply 7f3a2c"},
		{`bash -c "mygit commit -m 'feat: x' --yes"`, shell.Bash, true, "--yes"},
		{`sh -c 'mygit pr create -y'`, shell.Bash, true, "-y"},
		{"/usr/local/bin/mygit commit --apply abc", shell.Bash, true, "--apply abc"},
		{"mygit commit --apply abc123", shell.PowerShell, true, "--apply abc123"},
		{"mygit pr create -y; Write-Host done", shell.PowerShell, true, "-y"},
		{"& mygit commit --yes", shell.PowerShell, true, "--yes"},
		{`powershell -Command "mygit commit --apply abc"`, shell.PowerShell, true, "--apply abc"},
		{"mygit.exe commit --apply abc", shell.PowerShell, true, "--apply abc"},
		// passes
		{"mygit commit -m 'feat: x'", shell.Bash, false, ""},
		{"mygit commit --json", shell.Bash, false, ""},
		{"mygit plan show abc", shell.Bash, false, ""},
		{"mygit lint msg -- --yes", shell.Bash, false, ""},
		{"echo mygit --yes", shell.Bash, false, ""},
		{"git commit --yes", shell.Bash, false, ""},
		{"ls", shell.Bash, false, ""},
		{"mygit commit -m 'feat: x'", shell.PowerShell, false, ""},
	}
	for _, tt := range tests {
		req := Request{Kind: PreCommand, Command: tt.cmd, Dialect: tt.dialect, Cwd: testCwd}
		v := CheckApply(req, "")
		if got := len(v.Findings) > 0; got != tt.ask {
			if tt.ask && (strings.Contains(tt.cmd, "bash -c") || strings.Contains(tt.cmd, "sh -c") || strings.Contains(tt.cmd, "powershell -Command") ||
				strings.Contains(tt.cmd, "mygit.exe") || strings.Contains(tt.cmd, "/usr/local/bin/")) {
				t.Errorf("BUG? CheckApply(%q, %v) does not ask (wrapped/qualified mygit call)", tt.cmd, tt.dialect)
				continue
			}
			t.Errorf("CheckApply(%q, %v) asks=%v, want %v (%+v)", tt.cmd, tt.dialect, got, tt.ask, v.Findings)
			continue
		}
		if !tt.ask {
			continue
		}
		f := v.Findings[0]
		if f.RuleID != ApplyRuleID || f.Severity != verdict.Ask || !strings.Contains(f.Message, "("+tt.how+")") {
			t.Errorf("%q: finding %+v, want how %q", tt.cmd, f, tt.how)
		}
		if !strings.Contains(f.Message, `execution.agentApply to "allow"`) {
			t.Errorf("%q: message lacks the opt-out hint: %q", tt.cmd, f.Message)
		}
		for _, ap := range []string{"allow"} {
			if v := CheckApply(req, ap); len(v.Findings) != 0 {
				t.Errorf("%q with agentApply=%s: %+v", tt.cmd, ap, v.Findings)
			}
		}
		for _, ap := range []string{"", "ask", "deny", "ALLOW"} {
			if v := CheckApply(req, ap); len(v.Findings) == 0 {
				t.Errorf("%q with agentApply=%q: want ask", tt.cmd, ap)
			}
		}
	}
}

// TestCheckApplyThroughClaude: the agentApply ask reaches Claude as an ask.
func TestCheckApplyThroughClaude(t *testing.T) {
	out, _ := guard(t, Claude{}, claudePayload("Bash", "mygit commit --apply abc123"), nil, nil, "")
	m := hso(decodeClaude(t, out))
	if m["permissionDecision"] != "ask" || !strings.Contains(m["permissionDecisionReason"].(string), "[agentApply]") {
		t.Errorf("got %s", out)
	}
	out, _ = guard(t, Claude{}, claudePayload("Bash", "mygit commit --apply abc123"), nil, nil, "allow")
	if len(out) != 0 {
		t.Errorf("agentApply=allow: got %s", out)
	}
	out, code := guard(t, JSON{}, jsonPayload("mygit pr create -y"), nil, nil, "")
	if code != 1 || !strings.Contains(string(out), `"rule":"agentApply"`) {
		t.Errorf("json: %s exit %d", out, code)
	}
}
