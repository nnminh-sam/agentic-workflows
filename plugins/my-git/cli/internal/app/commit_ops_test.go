package app

import (
	"reflect"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
)

func TestCommitParseStatus(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want CommitStatus
	}{
		{"clean", "", CommitStatus{}},
		{"staged", "1 M. N... 100644 100644 100644 a b f.go\n2 R. N... 100644 100644 100644 a b R100 new.go\told.go\n", CommitStatus{Total: 2, Staged: 2}},
		{"unstaged and untracked", "1 .M N... 100644 100644 100644 a a f.go\n? x.txt\n", CommitStatus{Total: 2}},
		{"mixed", "1 MM N... 100644 100644 100644 a b f.go\n? x.txt\n! ignored\n# branch.head main\n", CommitStatus{Total: 2, Staged: 1}},
		{"unmerged", "u UU N... 1 1 1 1 a b c f.go\n", CommitStatus{Total: 1, Unmerged: 1}},
		{"crlf", "1 A. N... 0 1 1 a b f.go\r\n", CommitStatus{Total: 1, Staged: 1}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := CommitParseStatus(tt.in); got != tt.want {
				t.Errorf("got %+v, want %+v", got, tt.want)
			}
		})
	}
}

func TestCommitGitArgs(t *testing.T) {
	prof := func(format, key, program string) *config.Resolved {
		return &config.Resolved{Profile: profile.Profile{Git: profile.Git{
			Name: "Octo Cat", Email: profile.StringList{"o@x.example", "p@x.example"},
			Signing: profile.Signing{Format: format, Key: key, Program: program},
		}}}
	}
	e := &Env{Deps: Deps{Home: "/home/u"}}
	id := []string{"-c", "user.name=Octo Cat", "-c", "user.email=o@x.example"}
	tests := []struct {
		name       string
		c          *config.Resolved
		want       []string
		wantSigned bool
	}{
		{"ssh", prof("ssh", "~/.ssh/k.pub", ""),
			append(append([]string{}, id...), "-c", "gpg.format=ssh", "-c", "user.signingkey=/home/u/.ssh/k.pub"), true},
		{"openpgp with program", prof("openpgp", "ABC123", "/usr/bin/gpg2"),
			append(append([]string{}, id...), "-c", "gpg.format=openpgp", "-c", "user.signingkey=ABC123", "-c", "gpg.program=/usr/bin/gpg2"), true},
		{"x509 program", prof("x509", "", "smimesign"),
			append(append([]string{}, id...), "-c", "gpg.format=x509", "-c", "gpg.x509.program=smimesign"), true},
		{"default", prof("default", "", ""), id, true},
		{"none", prof("none", "", ""), append(append([]string{}, id...), "-c", "commit.gpgsign=false"), false},
		{"unset", prof("", "", ""), id, false},
		{"no profile", &config.Resolved{}, nil, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, signed := CommitGitArgs(e, tt.c)
			if !reflect.DeepEqual(got, tt.want) || signed != tt.wantSigned {
				t.Errorf("got %q signed=%v\nwant %q signed=%v", got, signed, tt.want, tt.wantSigned)
			}
		})
	}
}

func TestCommitStepArgs(t *testing.T) {
	got := CommitStepArgs([]string{"-c", "user.name=A"}, true, "/t/m")
	want := []string{"-c", "user.name=A", "commit", "-S", "-F", "/t/m"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
	if got := CommitStepArgs(nil, false, "/t/m"); !reflect.DeepEqual(got, []string{"commit", "-F", "/t/m"}) {
		t.Errorf("unsigned = %q", got)
	}
}

func TestCommitSigStatus(t *testing.T) {
	for code, want := range map[string]string{"G": "Good", "U": "Good, untrusted", "B": "Bad", "N": "", "": "", "E": "Cannot check"} {
		if got := CommitSigStatus(code); got != want {
			t.Errorf("%q = %q, want %q", code, got, want)
		}
	}
}
