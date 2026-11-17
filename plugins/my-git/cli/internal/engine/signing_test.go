package engine

// ID006 (Go-only rule, no Python counterpart): commits must be signed when
// the profile's signing_format is set and not "none".

import (
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func TestSigningID006(t *testing.T) {
	signing := func(format any) *policy.Policy {
		vars := testProfile()
		if format != nil {
			vars["signing_format"] = format
		}
		return testPolicy(t, vars, nil)
	}
	ssh := signing("ssh")
	cases := []struct {
		name, cmd string
		pol       *policy.Policy
		dialect   shell.Dialect
		finding   bool
	}{
		{"unsigned", commitX, ssh, shell.Bash, true},
		{"S", "git commit -S -m 'feat: add x'", ssh, shell.Bash, false},
		{"aS_cluster", "git commit -aS -m 'feat: add x'", ssh, shell.Bash, false},
		{"Sm_cluster", "git commit -Sm 'feat: add x'", ssh, shell.Bash, false},
		{"S_with_keyid", "git commit -SABCDEF -m 'feat: add x'", ssh, shell.Bash, false},
		{"gpg_sign", "git commit --gpg-sign -m 'feat: add x'", ssh, shell.Bash, false},
		{"gpg_sign_eq", "git commit --gpg-sign=ABC -m 'feat: add x'", signing("openpgp"), shell.Bash, false},
		{"config_true", "git -c commit.gpgsign=true commit -m 'feat: add x'", ssh, shell.Bash, false},
		{"config_TRUE_case", "git -c Commit.GpgSign=TRUE commit -m 'feat: add x'", ssh, shell.Bash, false},
		{"no_gpg_sign", "git commit --no-gpg-sign -m 'feat: add x'", ssh, shell.Bash, true},
		{"S_then_no_gpg_sign", "git commit -S --no-gpg-sign -m 'feat: add x'", ssh, shell.Bash, true},
		{"config_false", "git -c commit.gpgsign=false commit -m 'feat: add x'", ssh, shell.Bash, true},
		{"config_false_with_S", "git -c commit.gpgsign=false commit -S -m 'feat: add x'", ssh, shell.Bash, true},
		{"m_value_is_not_a_flag", "git commit -mS", ssh, shell.Bash, true},                   // message "S", not -S
		{"m_separate_value_dash_S", "git commit -m '-S fix' -m body", ssh, shell.Bash, true}, // -m's value is not a flag
		{"u_then_S", "git commit -u -S -m 'feat: add x'", ssh, shell.Bash, false},
		{"untracked_files_then_S", "git commit --untracked-files -S -m 'feat: add x'", ssh, shell.Bash, false},
		{"S_keyid_with_m_then_m", "git commit -Sjim -m 'feat: add x'", ssh, shell.Bash, false},
		{"u_mode_with_S_is_not_S", "git commit -uno -m 'feat: add x'", ssh, shell.Bash, true},
		{"after_double_dash", "git commit -m 'feat: add x' -- -S", ssh, shell.Bash, true},
		{"x509", commitX, signing("x509"), shell.Bash, true},
		{"format_none", commitX, signing("none"), shell.Bash, false},
		{"format_unset", commitX, signing(nil), shell.Bash, false},
		{"format_empty", commitX, signing(""), shell.Bash, false},
		{"not_a_commit", "git push origin develop/feat/test", ssh, shell.Bash, false},
		{"powershell_S", "git commit -S -m 'feat: add x'", ssh, shell.PowerShell, false},
		{"powershell_unsigned", "git commit -m 'feat: add x'", ssh, shell.PowerShell, true},
		{"chain_second_unsigned", "git commit -S -m 'feat: a' && git commit --amend --no-edit", ssh, shell.Bash, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			v := run(t, c.cmd, opts{pol: c.pol, dialect: c.dialect})
			if got := contains(ids(v), "ID006"); got != c.finding {
				t.Errorf("%q: ID006=%v want %v\n%s", c.cmd, got, c.finding, v.Reason())
			}
			if c.finding {
				for _, f := range v.Findings {
					if f.RuleID == "ID006" && f.Severity != verdict.Warn {
						t.Errorf("ID006 severity %s, want warn (default)", f.Severity)
					}
				}
			}
		})
	}
	t.Run("strict_severity_deny", func(t *testing.T) {
		vars := testProfile()
		vars["signing_format"] = "ssh"
		pol := testPolicy(t, vars, setSeverity("ID006", verdict.Deny))
		expect(t, commitX, opts{pol: pol}, verdict.DecDeny, []string{"ID006"})
	})
	t.Run("off", func(t *testing.T) {
		vars := testProfile()
		vars["signing_format"] = "ssh"
		clean(t, commitX, opts{pol: testPolicy(t, vars, setSeverity("ID006", verdict.Off))})
	})
	t.Run("direct_non_commit_call", func(t *testing.T) {
		call, _ := shell.AsGit(shell.ParseWith("git push", shell.Bash, cwd, shell.Options{})[0])
		if v := rules.CheckSigning(call, ssh.Contract(policy.Identity), ssh.Vars); len(v.Findings) != 0 {
			t.Error(v.Reason())
		}
	})
}
