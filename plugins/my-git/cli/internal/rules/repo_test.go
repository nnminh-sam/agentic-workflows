package rules_test

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
)

const ghStatusCmd = "gh auth status --active --hostname github.com --json hosts"

func ghStatus(login, state string) string {
	return `{"hosts":{"github.com":[{"state":"` + state + `","active":true,"host":"github.com","login":"` + login + `"}]}}`
}

func TestGitRepoCommandLines(t *testing.T) {
	f := execx.NewFake().
		OnOut("git -C /repo config --get user.name", "  octocat \n").
		OnOut("git -C /repo config --get-urlmatch credential.helper https://github.com/o/r", "osxkeychain\n!gh auth git-credential\n").
		OnOut("git -C /repo remote", "origin\nupstream\n").
		OnOut("git -C /repo remote get-url --push origin", "git@github.com:o/r.git\nextra\n").
		OnOut("git -C /repo remote get-url upstream", "https://github.com/u/r.git\n").
		OnOut("git -C /repo symbolic-ref --short -q HEAD", "develop/feat/x\n").
		OnOut("git -C /repo rev-parse --verify -q HEAD", "abc\n").
		OnOut("git -C /repo rev-parse --verify -q refs/remotes/origin/main", "abc\n")
	g := rules.NewGitRepo(f, "/repo")

	if v, ok := g.Config("user.name"); !ok || v != "octocat" {
		t.Errorf("Config: %q %v", v, ok)
	}
	if _, ok := g.Config("user.email"); ok {
		t.Error("unset Config should be !ok")
	}
	if v, ok := g.ConfigURLMatch("credential.helper", "https://github.com/o/r"); !ok || v != "!gh auth git-credential" {
		t.Errorf("ConfigURLMatch takes the last line: %q %v", v, ok)
	}
	if _, ok := g.ConfigURLMatch("credential.helper", "https://other"); ok {
		t.Error("unmatched urlmatch should be !ok")
	}
	if got := g.Remotes(); !reflect.DeepEqual(got, []string{"origin", "upstream"}) {
		t.Errorf("Remotes: %v", got)
	}
	if u, ok := g.RemoteURL("origin", true); !ok || u != "git@github.com:o/r.git" {
		t.Errorf("RemoteURL push: %q", u)
	}
	if u, ok := g.RemoteURL("upstream", false); !ok || u != "https://github.com/u/r.git" {
		t.Errorf("RemoteURL: %q", u)
	}
	if _, ok := g.RemoteURL("nope", false); ok {
		t.Error("unknown remote should be !ok")
	}
	if b, ok := g.CurrentBranch(); !ok || b != "develop/feat/x" {
		t.Errorf("CurrentBranch: %q", b)
	}
	if !g.HasCommits() || !g.RemoteBranchExists("origin", "main") || g.RemoteBranchExists("origin", "dev") {
		t.Error("HasCommits / RemoteBranchExists")
	}

	// git answers are cached per argument list: repeat everything, no new calls.
	n := len(f.CallLines())
	g.Config("user.name")
	g.Config("user.email")
	g.Remotes()
	g.CurrentBranch()
	g.HasCommits()
	if got := len(f.CallLines()); got != n {
		t.Errorf("git queries not cached: %d -> %d calls", n, got)
	}
	want := []string{
		"git -C /repo config --get user.name",
		"git -C /repo config --get user.email",
		"git -C /repo config --get-urlmatch credential.helper https://github.com/o/r",
		"git -C /repo config --get-urlmatch credential.helper https://other",
		"git -C /repo remote",
		"git -C /repo remote get-url --push origin",
		"git -C /repo remote get-url upstream",
		"git -C /repo remote get-url nope",
		"git -C /repo symbolic-ref --short -q HEAD",
		"git -C /repo rev-parse --verify -q HEAD",
		"git -C /repo rev-parse --verify -q refs/remotes/origin/main",
		"git -C /repo rev-parse --verify -q refs/remotes/origin/dev",
	}
	if got := f.CallLines(); !reflect.DeepEqual(got, want) {
		t.Errorf("call lines:\n got %q\nwant %q", got, want)
	}
}

func TestGitRepoRunnerError(t *testing.T) {
	f := execx.NewFake()
	f.Errors["git -C /r config --get user.name"] = errors.New("exec: git not found")
	f.Errors[ghStatusCmd] = errors.New("exec: gh not found")
	g := rules.NewGitRepo(f, "/r")
	g.Ctx = context.Background()
	if _, ok := g.Config("user.name"); ok {
		t.Error("runner error should be !ok")
	}
	if login, note := g.GhActiveLogin("github.com", "", time.Minute); login != "" || note == "" {
		t.Errorf("gh missing: %q %q", login, note)
	}
	if g.Remotes() != nil {
		t.Error("Remotes on failure should be nil")
	}
}

// TestGhCache ports test_identity.GhAccount.test_cache: TTL with an injected
// clock, the token is part of the key, and ClearGhCache forgets entries.
func TestGhCache(t *testing.T) {
	f := execx.NewFake().OnOut(ghStatusCmd, ghStatus("octocat", "success"))
	now := time.Unix(1000, 0)
	g := rules.NewGitRepo(f, "/repo")
	g.Now = func() time.Time { return now }
	ttl := 60 * time.Second
	ghCalls := func() int {
		n := 0
		for _, c := range f.CallLines() {
			if c == ghStatusCmd {
				n++
			}
		}
		return n
	}

	first, _ := g.GhActiveLogin("github.com", "", ttl)
	f.OnOut(ghStatusCmd, ghStatus("changed", "success"))
	now = now.Add(30 * time.Second)
	cached, _ := g.GhActiveLogin("github.com", "", ttl)
	now = now.Add(60 * time.Second)
	fresh, _ := g.GhActiveLogin("github.com", "", ttl)
	if first != "octocat" || cached != "octocat" || fresh != "changed" {
		t.Errorf("ttl: %s %s %s", first, cached, fresh)
	}
	if n := ghCalls(); n != 2 {
		t.Errorf("gh calls %d, want 2", n)
	}
	// a different token is a different cache entry
	f.OnOut(ghStatusCmd, ghStatus("tokenuser", "success"))
	if other, _ := g.GhActiveLogin("github.com", "t", ttl); other != "tokenuser" {
		t.Errorf("token key: %s", other)
	}
	if again, _ := g.GhActiveLogin("github.com", "", ttl); again != "changed" {
		t.Errorf("empty-token entry should still be cached: %s", again)
	}
	g.ClearGhCache()
	if after, _ := g.GhActiveLogin("github.com", "", ttl); after != "tokenuser" {
		t.Errorf("after clear: %s", after)
	}
	// ttl <= 0 disables the cache
	n := ghCalls()
	g.GhActiveLogin("github.com", "", 0)
	g.GhActiveLogin("github.com", "", 0)
	if ghCalls() != n+2 {
		t.Error("ttl 0 should not cache")
	}
	// failures are not cached
	f.On(ghStatusCmd, execx.Result{ExitCode: 1, Stdout: `{"hosts":{}}`})
	g.ClearGhCache()
	if l, note := g.GhActiveLogin("github.com", "", ttl); l != "" || note != "gh is not logged in to github.com" {
		t.Errorf("not logged in: %q %q", l, note)
	}
	f.OnOut(ghStatusCmd, ghStatus("back", "success"))
	if l, _ := g.GhActiveLogin("github.com", "", ttl); l != "back" {
		t.Errorf("failure was cached: %q", l)
	}
}

func TestGhStatusParsing(t *testing.T) {
	cases := []struct {
		name, out     string
		login, noteIn string
	}{
		{"bad_json", "not json", "", "could not read 'gh auth status' (exit 0)"},
		{"no_hosts_key", `{}`, "", "could not read"},
		{"state_error", ghStatus("x", "error"), "", "gh auth for github.com is in state 'error'"},
		{"inactive_fallback", `{"hosts":{"github.com":[{"state":"success","active":false,"login":"first"},{"state":"success","active":false,"login":"second"}]}}`, "first", ""},
		{"active_wins", `{"hosts":{"github.com":[{"state":"success","active":false,"login":"a"},{"state":"success","active":true,"login":"b"}]}}`, "b", ""},
		{"other_host", `{"hosts":{"ghe.io":[{"state":"success","active":true,"login":"x"}]}}`, "", "gh is not logged in to github.com"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := execx.NewFake().OnOut(ghStatusCmd, c.out)
			login, note := rules.NewGitRepo(f, "/r").GhActiveLogin("github.com", "", 0)
			if login != c.login || (c.noteIn != "" && !strings.Contains(note, c.noteIn)) {
				t.Errorf("got %q %q, want %q %q", login, note, c.login, c.noteIn)
			}
		})
	}
}
