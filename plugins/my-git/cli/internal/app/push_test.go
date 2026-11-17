package app

import (
	"context"
	"errors"
	"io/fs"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const (
	pushHome = "/home/u"
	pushRepo = "/home/u/work/api"
	pushCfg  = "/home/u/.config/mygit/config.json"
	pushKey  = "~/.ssh/id_work"
	pushSSH  = "git@github.com:octo/api.git"
)

func pushProfile(extra string) string {
	return `{"defaultProfile":"work","profiles":{"work":{"git":{"name":"Octo","email":"o@x.io"` + extra + `},
	"github":{"user":"octo","transport":"ssh","sshKey":"` + pushKey + `"}}}}`
}

// pushWorld scripts the git answers PlanPush reads.
type pushWorld struct {
	cfg      string
	branch   string // "" = detached
	remote   string
	upstream string // configured upstream ("" = none)
	remoteBr bool   // refs/remotes/origin/<branch> exists
	counts   string // rev-list --left-right --count output
	log      string // sign log output
}

func (w pushWorld) planner(t *testing.T) (*Planner, *execx.Fake) {
	t.Helper()
	f := execx.NewFake()
	f.OnOut("git rev-parse --show-toplevel", pushRepo)
	f.OnOut("git remote get-url origin", w.remote)
	f.OnOut("git rev-parse -q --verify HEAD^{commit}", "abc123")
	f.OnOut("git rev-parse --verify -q HEAD", "abc123")
	// what the rules engine's Repo asks (git -C <cwd> …)
	f.OnOut("git -C "+pushRepo+" remote get-url --push origin", w.remote)
	f.OnOut("git -C "+pushRepo+" remote get-url origin", w.remote)
	f.OnOut("git -C "+pushRepo+" symbolic-ref --short -q HEAD", w.branch)
	f.OnOut("git -C "+pushRepo+" rev-parse --verify -q HEAD", "abc123")
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "refs/heads/x abc123")
	if w.branch == "" {
		f.On("git symbolic-ref --short -q HEAD", execx.Result{ExitCode: 1})
	} else {
		f.OnOut("git symbolic-ref --short -q HEAD", w.branch)
		f.OnOut("git config --get branch."+w.branch+".merge", "refs/heads/"+w.branch)
	}
	if w.upstream != "" {
		f.OnOut("git rev-parse --abbrev-ref --symbolic-full-name @{upstream}", w.upstream)
		f.OnOut("git rev-list --left-right --count "+w.upstream+"...HEAD", w.counts)
	} else {
		f.On("git rev-parse --abbrev-ref --symbolic-full-name @{upstream}", execx.Result{ExitCode: 128, Stderr: "no upstream"})
	}
	if w.remoteBr {
		f.OnOut("git rev-parse --verify -q refs/remotes/origin/"+w.branch, "abc123")
		f.OnOut("git -C "+pushRepo+" rev-parse --verify -q refs/remotes/origin/"+w.branch, "abc123")
	}
	f.OnOut("git log --format=%H%x1f%G?%x1f%GS%x1f%s HEAD --not --remotes=origin", w.log)
	cfg := w.cfg
	if cfg == "" {
		cfg = pushProfile("")
	}
	env, err := Load(context.Background(), Deps{
		Runner: f, Cwd: pushRepo, Home: pushHome, GOOS: "linux",
		ReadFile: func(p string) ([]byte, error) {
			if p == pushCfg {
				return []byte(cfg), nil
			}
			return nil, fs.ErrNotExist
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	return env.NewPlanner([]string{"push"}), f
}

func pushWriteArgs(t *testing.T, p *Planner) []string {
	t.Helper()
	built, err := p.Build(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	w := built.Writes()
	if len(w) != 1 {
		t.Fatalf("want 1 write step, got %d", len(w))
	}
	return w[0].Cmd.Args
}

func TestPlanPush(t *testing.T) {
	tests := []struct {
		name         string
		w            pushWorld
		upstream     bool
		wantArgs     string // "" = no write step
		wantAhead    int
		wantBehind   int
		wantNew      bool
		wantUpToDate bool
		wantDenied   bool
		wantRules    []string // rule ids that must have findings
		wantPassed   []string // rule ids that must pass as checks
	}{
		{
			name:       "ssh key pinned, ahead of upstream",
			w:          pushWorld{branch: "develop/feat/x", remote: pushSSH, upstream: "origin/develop/feat/x", remoteBr: true, counts: "0\t2"},
			wantArgs:   "-c core.sshCommand=ssh -i ~/.ssh/id_work -o IdentitiesOnly=yes push origin develop/feat/x",
			wantAhead:  2,
			wantPassed: []string{"ID003", "ID004", "BR002"},
		},
		{
			name:       "new branch with --upstream",
			w:          pushWorld{branch: "develop/feat/x", remote: pushSSH},
			upstream:   true,
			wantArgs:   "-c core.sshCommand=ssh -i ~/.ssh/id_work -o IdentitiesOnly=yes push -u origin develop/feat/x",
			wantNew:    true,
			wantPassed: []string{"ID003"},
		},
		{
			name:         "nothing to push",
			w:            pushWorld{branch: "develop/feat/x", remote: pushSSH, upstream: "origin/develop/feat/x", remoteBr: true, counts: "0\t0"},
			wantUpToDate: true,
		},
		{
			name:         "behind only: nothing to push, never forced",
			w:            pushWorld{branch: "develop/feat/x", remote: pushSSH, upstream: "origin/develop/feat/x", remoteBr: true, counts: "3\t0"},
			wantBehind:   3,
			wantUpToDate: true,
		},
		{
			name:       "diverged: still planned, git rejects it, note added",
			w:          pushWorld{branch: "develop/feat/x", remote: pushSSH, upstream: "origin/develop/feat/x", remoteBr: true, counts: "1\t2"},
			wantArgs:   "-c core.sshCommand=ssh -i ~/.ssh/id_work -o IdentitiesOnly=yes push origin develop/feat/x",
			wantAhead:  2,
			wantBehind: 1,
		},
		{
			name:       "protected branch: BR002 denies",
			w:          pushWorld{branch: "develop/base", remote: pushSSH, upstream: "origin/develop/base", remoteBr: true, counts: "0\t1"},
			wantDenied: true,
			wantAhead:  1,
			wantRules:  []string{"BR002"},
		},
		{
			name:       "foreign owner: ID004 denies",
			w:          pushWorld{branch: "develop/feat/x", remote: "git@github.com:evil/api.git", remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1"},
			wantDenied: true,
			wantAhead:  1,
			wantRules:  []string{"ID004"},
		},
		{
			name:       "https remote with ssh transport: ID004 denies",
			w:          pushWorld{branch: "develop/feat/x", remote: "https://github.com/octo/api.git", remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1"},
			wantDenied: true,
			wantAhead:  1,
			wantRules:  []string{"ID004"},
		},
		{
			name: "no ssh key in profile and none pinned: ID003 denies",
			w: pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
				cfg: `{"defaultProfile":"work","profiles":{"work":{"github":{"user":"octo","transport":"ssh"}}}}`},
			wantDenied: true,
			wantAhead:  1,
			wantRules:  []string{"ID003"},
		},
		{
			name: "transport gh pins nothing and needs the credential helper (ID003)",
			w: pushWorld{branch: "develop/feat/x", remote: "https://github.com/octo/api.git", remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
				cfg: `{"defaultProfile":"work","profiles":{"work":{"github":{"user":"octo","transport":"gh"}}}}`},
			wantDenied: true,
			wantAhead:  1,
			wantRules:  []string{"ID003"},
		},
		{
			name: "signing: unsigned commit is an ID006 warning, push still planned",
			w: pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t2",
				cfg: pushProfile(`,"signing":{"format":"ssh","key":"~/.ssh/sign.pub"}`),
				log: "aaaaaaa1111\x1fG\x1focto\x1ffeat: a\nbbbbbbb2222\x1fN\x1f\x1ffix: b\n"},
			wantArgs:  "-c core.sshCommand=ssh -i ~/.ssh/id_work -o IdentitiesOnly=yes push origin develop/feat/x",
			wantAhead: 2,
			wantRules: []string{"ID006"},
		},
		{
			name: "signing: all signed passes ID006",
			w: pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
				cfg: pushProfile(`,"signing":{"format":"ssh","key":"~/.ssh/sign.pub"}`),
				log: "aaaaaaa1111\x1fG\x1focto\x1ffeat: a\n"},
			wantArgs:   "-c core.sshCommand=ssh -i ~/.ssh/id_work -o IdentitiesOnly=yes push origin develop/feat/x",
			wantAhead:  1,
			wantPassed: []string{"ID006"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p, _ := tt.w.planner(t)
			res, err := PlanPush(context.Background(), p, PushOptions{Upstream: tt.upstream})
			if err != nil {
				t.Fatal(err)
			}
			if res.Ahead != tt.wantAhead || res.Behind != tt.wantBehind || res.NewBranch != tt.wantNew {
				t.Errorf("ahead/behind/new = %d/%d/%v, want %d/%d/%v", res.Ahead, res.Behind, res.NewBranch, tt.wantAhead, tt.wantBehind, tt.wantNew)
			}
			if res.Denied != tt.wantDenied || res.UpToDate != tt.wantUpToDate {
				t.Errorf("denied/upToDate = %v/%v, want %v/%v (findings %+v)", res.Denied, res.UpToDate, tt.wantDenied, tt.wantUpToDate, res.Findings)
			}
			if tt.wantArgs == "" {
				if p.Writes() != 0 || res.Pushed {
					t.Errorf("no write step expected, got %d (pushed %v)", p.Writes(), res.Pushed)
				}
			} else if got := strings.Join(pushWriteArgs(t, p), " "); got != tt.wantArgs {
				t.Errorf("push args = %q, want %q", got, tt.wantArgs)
			}
			for _, id := range tt.wantRules {
				if !hasFinding(res.Findings, id) {
					t.Errorf("no %s finding in %+v", id, res.Findings)
				}
			}
			built, _ := p.Build(context.Background())
			for _, id := range tt.wantPassed {
				ok := false
				for _, c := range built.Checks {
					ok = ok || c.RuleID == id && c.OK
				}
				if !ok {
					t.Errorf("check %s did not pass: %+v", id, built.Checks)
				}
			}
			for _, s := range built.Steps {
				if strings.Contains(s.Cmd.String(), "--force") || strings.Contains(s.Cmd.String(), " -f ") {
					t.Errorf("forced push planned: %s", s.Cmd)
				}
			}
		})
	}
}

func hasFinding(fs []verdict.Finding, id string) bool {
	for _, f := range fs {
		if f.RuleID == id {
			return true
		}
	}
	return false
}

func TestPlanPushSigningDeny(t *testing.T) {
	w := pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
		cfg: `{"defaultProfile":"work","rules":{"ID006":"deny"},"profiles":{"work":{"git":{"signing":{"format":"ssh","key":"k.pub"}},
		"github":{"user":"octo","transport":"ssh","sshKey":"` + pushKey + `"}}}}`,
		log: "aaaaaaa1111\x1fN\x1f\x1ffeat: a\n"}
	p, _ := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Denied || res.Pushed || p.Writes() != 0 {
		t.Fatalf("want denied without a write step: %+v", res)
	}
	if !res.SigningChecked || len(res.Unsigned) != 1 || !strings.Contains(res.Findings[0].Message, "aaaaaaa") {
		t.Errorf("unsigned commits not reported: %+v", res)
	}
}

func TestPlanPushReadsShowAheadBehind(t *testing.T) {
	w := pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "1\t2"}
	p, _ := w.planner(t)
	if _, err := PlanPush(context.Background(), p, PushOptions{}); err != nil {
		t.Fatal(err)
	}
	built, _ := p.Build(context.Background())
	var sums []string
	for _, s := range built.Steps {
		sums = append(sums, s.Summary)
	}
	if !strings.Contains(strings.Join(sums, "|"), "↑2 ahead · ↓1 behind") {
		t.Errorf("read summaries = %q", sums)
	}
}

func TestPlanPushErrors(t *testing.T) {
	t.Run("detached HEAD", func(t *testing.T) {
		p, _ := pushWorld{remote: pushSSH}.planner(t)
		if _, err := PlanPush(context.Background(), p, PushOptions{}); !errors.Is(err, ErrDetachedHead) {
			t.Fatalf("err = %v, want ErrDetachedHead", err)
		}
	})
	t.Run("no remote", func(t *testing.T) {
		p, _ := pushWorld{branch: "develop/feat/x"}.planner(t)
		if _, err := PlanPush(context.Background(), p, PushOptions{}); err == nil || !strings.Contains(err.Error(), "remote") {
			t.Fatalf("err = %v", err)
		}
	})
}
