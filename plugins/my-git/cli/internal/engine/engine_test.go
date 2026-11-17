package engine

import (
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func TestDialectFor(t *testing.T) {
	for name, want := range map[string]shell.Dialect{"PowerShell": shell.PowerShell, "powershell": shell.PowerShell,
		"Bash": shell.Bash, "shell": shell.Bash, "": shell.Bash} {
		if got := DialectFor(name); got != want {
			t.Errorf("%q: %s", name, got)
		}
	}
}

func TestPrefilter(t *testing.T) {
	repo := newFake()
	called := 0
	deps := Deps{Repo: func(string) rules.Repo { called++; return repo }}
	pol := testPolicy(t, testProfile(), nil)
	for _, cmd := range []string{"", "ls -la", "echo digit && legit-tool"} {
		if v := Evaluate(Request{Command: cmd, Cwd: cwd}, pol, deps); len(v.Findings) != 0 {
			t.Errorf("%q: %v", cmd, v.Reason())
		}
	}
	if called != 0 {
		t.Errorf("repo consulted %d times for uninteresting commands", called)
	}
}

func TestRepoPerCwd(t *testing.T) {
	seen := map[string]int{}
	pol := testPolicy(t, testProfile(), nil)
	Evaluate(Request{Command: "git status && git -C sub status && git status && cd sub && git log", Cwd: cwd}, pol,
		Deps{Repo: func(dir string) rules.Repo { seen[dir]++; return newFake() }})
	for dir, n := range seen {
		if n != 1 {
			t.Errorf("repo for %s created %d times", dir, n)
		}
	}
	if len(seen) != 2 {
		t.Errorf("want 2 directories, got %v", seen)
	}
}

// With no Deps at all the engine must not panic; unknown repo facts never
// produce repo-dependent findings beyond the policy's own checks.
func TestNoDeps(t *testing.T) {
	pol := testPolicy(t, testProfile(), nil)
	for _, cmd := range []string{"git commit -m 'feat: add x'", "git push origin main", "gh pr list", "git commit -F msg.txt",
		"gh pr create --base develop/base --title 'feat: x' -F pr.md", "git --version"} {
		Evaluate(Request{Command: cmd, Cwd: cwd}, pol, Deps{})
	}
	v := Evaluate(Request{Command: "gh pr list", Cwd: cwd}, pol, Deps{Repo: func(string) rules.Repo { return nil }})
	if !contains(ids(v), "ID005") {
		t.Errorf("noRepo gh login is unknown: want ID005, got %v", ids(v))
	}
	// nil policy: no contracts, nothing fires, no panic
	if v := Evaluate(Request{Command: "git commit -m x && gh repo delete a/b --yes && curl https://api.github.com", Cwd: cwd}, nil, Deps{}); v.Decision() != verdict.Allow {
		t.Errorf("nil policy: %s", v.Reason())
	}
}
