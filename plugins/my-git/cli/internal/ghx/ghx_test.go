package ghx

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

var ctx = context.Background()

func TestParseVersion(t *testing.T) {
	tests := map[string]string{
		"gh version 2.40.1 (2023-12-13)\nhttps://github.com/cli/cli/releases/tag/v2.40.1\n": "2.40.1",
		"gh version 2.63.0 (2024-11-27)": "2.63.0",
		"something else\nmore":           "something else",
	}
	for in, want := range tests {
		if got := ParseVersion(in); got != want {
			t.Errorf("ParseVersion(%q) = %q, want %q", in, got, want)
		}
	}
	f := execx.NewFake().OnOut("gh --version", "gh version 2.40.1 (2023-12-13)\n")
	if v, err := New(f, "").Version(ctx); v != "2.40.1" || err != nil {
		t.Errorf("Version = %q %v", v, err)
	}
	if _, err := New(execx.NewFake(), "").Version(ctx); err == nil {
		t.Error("Version failure should error")
	}
}

func TestExecRunErrors(t *testing.T) {
	f := execx.NewFake().On("gh x", execx.Result{ExitCode: 4, Stderr: "  auth required \n"}).
		On("gh y", execx.Result{ExitCode: 1, Stdout: "out only"})
	f.Errors["gh z"] = execx.ErrNotFound
	var traced int
	g := New(f, "/r")
	g.Trace = func(execx.Cmd) { traced++ }
	_, err := g.Run(ctx, "x")
	var ee *ExitError
	if !errors.As(err, &ee) || err.Error() != "gh x: exit 4: auth required" {
		t.Errorf("Run err = %v", err)
	}
	if _, err := g.Run(ctx, "y"); err == nil || !strings.HasSuffix(err.Error(), "out only") {
		t.Errorf("stdout fallback = %v", err)
	}
	if _, err := g.Query(ctx, "z"); !errors.Is(err, execx.ErrNotFound) {
		t.Errorf("start error = %v", err)
	}
	if traced != 3 || f.Calls[0].Dir != "/r" || f.Calls[0].Name != "gh" {
		t.Errorf("traced=%d calls=%+v", traced, f.Calls)
	}
}

const newFormat = `github.com
  ✓ Logged in to github.com account octo-acme (keyring)
  - Active account: true
  - Git operations protocol: ssh
  - Token: gho_************************************
  - Token scopes: 'admin:public_key', 'admin:ssh_signing_key', 'gist', 'read:org', 'repo'

  ✓ Logged in to github.com account octo (keyring)
  - Active account: false
  - Git operations protocol: https
  - Token: gho_************************************
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'

ghe.acme.example
  X Failed to log in to ghe.acme.example account octo-ent (default)
  - Active account: true
  - The token in default is invalid.
  - To re-authenticate, run: gh auth login -h ghe.acme.example
`

const oldFormat = `github.com
  ✓ Logged in to github.com as octo (/Users/octo/.config/gh/hosts.yml)
  ✓ Git operations for github.com configured to use https protocol.
  ✓ Token: *******************
  ✓ Token scopes: gist, read:org, repo, workflow
`

func TestParseAuthStatus(t *testing.T) {
	got := ParseAuthStatus(newFormat)
	want := []Account{
		{Host: "github.com", Login: "octo-acme", Active: true, OK: true, Protocol: "ssh",
			Scopes: []string{"admin:public_key", "admin:ssh_signing_key", "gist", "read:org", "repo"}},
		{Host: "github.com", Login: "octo", Active: false, OK: true, Protocol: "https",
			Scopes: []string{"gist", "read:org", "repo", "workflow"}},
		{Host: "ghe.acme.example", Login: "octo-ent", Active: true, OK: false},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("new format =\n%+v\nwant\n%+v", got, want)
	}

	got = ParseAuthStatus(oldFormat)
	if len(got) != 1 {
		t.Fatalf("old format = %+v", got)
	}
	a := got[0]
	if a.Host != "github.com" || a.Login != "octo" || !a.Active || !a.OK ||
		!reflect.DeepEqual(a.Scopes, []string{"gist", "read:org", "repo", "workflow"}) {
		t.Errorf("old format = %+v", a)
	}

	if got := ParseAuthStatus("You are not logged into any GitHub hosts. To log in, run: gh auth login\n"); got != nil {
		t.Errorf("not logged in = %+v", got)
	}
	// scopes "none" and empty
	got = ParseAuthStatus("✓ Logged in to github.com account a (GH_TOKEN)\n- Token scopes: none\n")
	if len(got) != 1 || got[0].Scopes != nil || !got[0].Active {
		t.Errorf("no scopes = %+v", got)
	}
	// CRLF output (Windows)
	got = ParseAuthStatus(strings.ReplaceAll(newFormat, "\n", "\r\n"))
	if len(got) != 3 || got[0].Protocol != "ssh" || got[1].Active {
		t.Errorf("crlf = %+v", got)
	}
}

// The pre-2.40 single-account format states the protocol as
// "Git operations for github.com configured to use https protocol.".
func TestParseAuthStatusOldProtocol(t *testing.T) {
	got := ParseAuthStatus(oldFormat)
	if len(got) != 1 || got[0].Protocol != "https" {
		t.Errorf("old gh format: Protocol = %+v, want https", got)
	}
}

func TestAuthStatus(t *testing.T) {
	// gh writes auth status to stderr and exits 1 when any account fails.
	f := execx.NewFake().On("gh auth status", execx.Result{ExitCode: 1, Stderr: newFormat})
	accts, err := New(f, "").AuthStatus(ctx)
	if err != nil || len(accts) != 3 {
		t.Errorf("AuthStatus = %+v %v", accts, err)
	}
	f = execx.NewFake().On("gh auth status", execx.Result{ExitCode: 1, Stderr: "You are not logged into any GitHub hosts."})
	if _, err := New(f, "").AuthStatus(ctx); err == nil {
		t.Error("nothing parsed + exit 1 should error")
	}
	f = execx.NewFake().On("gh auth status", execx.Result{Stdout: oldFormat})
	if accts, err := New(f, "").AuthStatus(ctx); err != nil || len(accts) != 1 {
		t.Errorf("stdout = %+v %v", accts, err)
	}
	f = execx.NewFake()
	f.Errors["gh auth status"] = execx.ErrNotFound
	if _, err := New(f, "").AuthStatus(ctx); !errors.Is(err, execx.ErrNotFound) {
		t.Errorf("missing gh = %v", err)
	}
}

func TestActiveLogin(t *testing.T) {
	tests := []struct {
		name  string
		fake  *execx.Fake
		host  string
		want  string
		error bool
	}{
		{"api", execx.NewFake().OnOut("gh api user --jq .login", "octo-acme\n"), "", "octo-acme", false},
		{"api github.com", execx.NewFake().OnOut("gh api user --jq .login", "octo\n"), "github.com", "octo", false},
		{"api enterprise", execx.NewFake().OnOut("gh api user --jq .login --hostname ghe.acme.example", "ent\n"), "ghe.acme.example", "ent", false},
		{"fallback to status", execx.NewFake().
			On("gh api user --jq .login", execx.Result{ExitCode: 1}).
			On("gh auth status", execx.Result{Stderr: newFormat}), "", "octo-acme", false},
		{"fallback enterprise, host case-insensitive, failed token is not logged in", execx.NewFake().
			On("gh auth status", execx.Result{ExitCode: 1, Stderr: newFormat}), "GHE.acme.example", "", true},
		{"no account for host", execx.NewFake().
			On("gh auth status", execx.Result{Stderr: newFormat}), "other.example", "", false},
		{"status fails", execx.NewFake(), "", "", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := New(tt.fake, "").ActiveLogin(ctx, tt.host)
			if got != tt.want || (err != nil) != tt.error {
				t.Errorf("ActiveLogin(%q) = %q, %v; want %q (err=%v)", tt.host, got, err, tt.want, tt.error)
			}
		})
	}
}

func TestPRList(t *testing.T) {
	f := execx.NewFake().
		OnOut("gh pr list --json number,title,url,state,isDraft,headRefName,baseRefName --head develop/feat/x --state open",
			`[{"number":12,"title":"feat: x","url":"https://github.com/acme/api/pull/12","state":"OPEN","isDraft":true,"headRefName":"develop/feat/x","baseRefName":"develop/base"}]`).
		OnOut("gh pr list --json number,title,url,state,isDraft,headRefName,baseRefName", `[]`).
		OnOut("gh pr list --json number,title,url,state,isDraft,headRefName,baseRefName --base main", `not json`)
	g := New(f, "")
	prs, err := g.PRList(ctx, "develop/feat/x", "", "open")
	if err != nil {
		t.Fatal(err)
	}
	want := []PR{{Number: 12, Title: "feat: x", URL: "https://github.com/acme/api/pull/12", State: "OPEN", IsDraft: true, HeadRefName: "develop/feat/x", BaseRefName: "develop/base"}}
	if !reflect.DeepEqual(prs, want) {
		t.Errorf("PRList = %+v", prs)
	}
	if prs, err := g.PRList(ctx, "", "", ""); err != nil || len(prs) != 0 {
		t.Errorf("PRList empty = %+v %v", prs, err)
	}
	if _, err := g.PRList(ctx, "", "main", ""); err == nil || !strings.Contains(err.Error(), "decode json") {
		t.Errorf("bad json err = %v", err)
	}
	if _, err := g.PRList(ctx, "x", "y", "z"); err == nil {
		t.Error("unmatched should fail")
	}
}

func TestPRViewAndRepoView(t *testing.T) {
	f := execx.NewFake().
		OnOut("gh pr view 7 --json number,title,body,url,state,isDraft,headRefName,baseRefName",
			`{"number":7,"title":"t","body":"## Summary\n\nx","url":"u","state":"MERGED","isDraft":false,"headRefName":"h","baseRefName":"b"}`).
		OnOut("gh repo view --json nameWithOwner,name,owner,url,visibility,isPrivate,defaultBranchRef",
			`{"nameWithOwner":"acme/api","name":"api","owner":{"login":"acme"},"url":"https://github.com/acme/api","visibility":"PRIVATE","isPrivate":true,"defaultBranchRef":{"name":"develop/base"}}`).
		OnOut("gh repo view acme/other --json nameWithOwner,name,owner,url,visibility,isPrivate,defaultBranchRef",
			`{"nameWithOwner":"acme/other"}`)
	g := New(f, "")
	pr, err := g.PRView(ctx, 7)
	if err != nil || pr.Number != 7 || pr.Body != "## Summary\n\nx" || pr.State != "MERGED" {
		t.Errorf("PRView = %+v %v", pr, err)
	}
	r, err := g.RepoView(ctx, "")
	if err != nil || r.NameWithOwner != "acme/api" || r.Owner.Login != "acme" || !r.IsPrivate || r.DefaultBranchRef.Name != "develop/base" {
		t.Errorf("RepoView = %+v %v", r, err)
	}
	if r, err := g.RepoView(ctx, "acme/other"); err != nil || r.NameWithOwner != "acme/other" {
		t.Errorf("RepoView(name) = %+v %v", r, err)
	}
	if _, err := g.PRView(ctx, 8); err == nil {
		t.Error("PRView(8) should fail")
	}
}
