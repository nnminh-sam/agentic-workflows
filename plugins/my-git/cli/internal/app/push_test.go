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
	current  string // checked-out branch when it differs from branch (PushOptions.Branch)
	remote   string
	pushURL  string // `remote get-url --push` ("" = same as remote)
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
	f.OnOut("git rev-parse -q --verify refs/heads/"+w.branch+"^{commit}", "abc123")
	pu := w.pushURL
	if pu == "" {
		pu = w.remote
	}
	f.OnOut("git remote get-url --push origin", pu)
	f.OnOut("git remote get-url --push fork", pu)
	f.OnOut("git remote get-url fork", w.remote)
	// what the rules engine's Repo asks (git -C <cwd> …)
	f.OnOut("git -C "+pushRepo+" remote get-url --push origin", w.remote)
	f.OnOut("git -C "+pushRepo+" remote get-url origin", w.remote)
	cur := w.branch
	if w.current != "" {
		cur = w.current
	}
	f.OnOut("git -C "+pushRepo+" symbolic-ref --short -q HEAD", cur)
	f.OnOut("git -C "+pushRepo+" rev-parse --verify -q HEAD", "abc123")
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "refs/heads/x abc123")
	if w.branch == "" {
		f.On("git symbolic-ref --short -q HEAD", execx.Result{ExitCode: 1})
	} else {
		f.OnOut("git symbolic-ref --short -q HEAD", cur)
	}
	if w.upstream != "" {
		f.OnOut("git rev-parse --abbrev-ref --symbolic-full-name "+w.branch+"@{upstream}", w.upstream)
		f.OnOut("git rev-list --left-right --count origin/"+w.branch+"...refs/heads/"+w.branch, w.counts)
	} else {
		f.On("git rev-parse --abbrev-ref --symbolic-full-name "+w.branch+"@{upstream}", execx.Result{ExitCode: 128, Stderr: "no upstream"})
		if w.remoteBr {
			f.OnOut("git rev-list --left-right --count origin/"+w.branch+"...refs/heads/"+w.branch, w.counts)
		}
	}
	if w.remoteBr {
		f.OnOut("git rev-parse --verify -q refs/remotes/origin/"+w.branch, "abc123")
		f.OnOut("git -C "+pushRepo+" rev-parse --verify -q refs/remotes/origin/"+w.branch, "abc123")
	}
	f.OnOut("git log --format=%H%x1f%G?%x1f%GS%x1f%s refs/heads/"+w.branch+" --not --remotes=origin", w.log)
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
			wantArgs:   "-c core.sshCommand=ssh -i \"/home/u/.ssh/id_work\" -o IdentitiesOnly=yes push origin develop/feat/x",
			wantAhead:  2,
			wantPassed: []string{"ID003", "ID004", "BR002"},
		},
		{
			name:       "new branch with --upstream",
			w:          pushWorld{branch: "develop/feat/x", remote: pushSSH},
			upstream:   true,
			wantArgs:   "-c core.sshCommand=ssh -i \"/home/u/.ssh/id_work\" -o IdentitiesOnly=yes push -u origin develop/feat/x",
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
			wantArgs:   "-c core.sshCommand=ssh -i \"/home/u/.ssh/id_work\" -o IdentitiesOnly=yes push origin develop/feat/x",
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
			wantArgs:  "-c core.sshCommand=ssh -i \"/home/u/.ssh/id_work\" -o IdentitiesOnly=yes push origin develop/feat/x",
			wantAhead: 2,
			wantRules: []string{"ID006"},
		},
		{
			name: "signing: all signed passes ID006",
			w: pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
				cfg: pushProfile(`,"signing":{"format":"ssh","key":"~/.ssh/sign.pub"}`),
				log: "aaaaaaa1111\x1fG\x1focto\x1ffeat: a\n"},
			wantArgs:   "-c core.sshCommand=ssh -i \"/home/u/.ssh/id_work\" -o IdentitiesOnly=yes push origin develop/feat/x",
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

func newBranchWorld() pushWorld {
	return pushWorld{branch: "develop/feat/x", remote: pushSSH}
}

func TestPlanPushNewBranchAddsUpstream(t *testing.T) {
	p, _ := newBranchWorld().planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !res.NewBranch || !res.SetUpstream || !res.Pushed {
		t.Fatalf("want a new branch pushed with -u: %+v", res)
	}
	want := `-c core.sshCommand=ssh -i "/home/u/.ssh/id_work" -o IdentitiesOnly=yes push -u origin develop/feat/x`
	if got := strings.Join(pushWriteArgs(t, p), " "); got != want {
		t.Errorf("push args = %q, want %q", got, want)
	}
	built, _ := p.Build(context.Background())
	if !strings.Contains(strings.Join(built.Notes, "|"), "adding -u") {
		t.Errorf("no -u note: %v", built.Notes)
	}
}

func TestPlanPushIgnoresForeignUpstream(t *testing.T) {
	// A branch cut from origin/<base> tracks the base: that is not its remote counterpart.
	w := pushWorld{branch: "develop/feat/x", remote: pushSSH, upstream: "origin/develop/base", remoteBr: true, counts: "0\t2"}
	p, f := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if res.Tracked || res.Upstream != "origin/develop/feat/x" || res.Ahead != 2 {
		t.Errorf("tracked/upstream/ahead = %v/%q/%d", res.Tracked, res.Upstream, res.Ahead)
	}
	built, _ := p.Build(context.Background())
	if !strings.Contains(strings.Join(built.Notes, "|"), "tracks origin/develop/base") {
		t.Errorf("no note: %v", built.Notes)
	}
	for _, l := range f.CallLines() {
		if strings.Contains(l, "origin/develop/base") && strings.Contains(l, "rev-list") {
			t.Errorf("compared against the foreign upstream: %s", l)
		}
	}
	// Already on the remote but untracked: --upstream still pushes to set tracking.
	w.counts = "0\t0"
	p2, _ := w.planner(t)
	res, err = PlanPush(context.Background(), p2, PushOptions{Upstream: true})
	if err != nil || res.UpToDate || !res.Pushed {
		t.Errorf("untracked + --upstream must push: %+v %v", res, err)
	}
}

func TestPlanPushBranchOptionNeverReadsHEAD(t *testing.T) {
	w := pushWorld{branch: "develop/feat/y", current: "develop/feat/x", remote: pushSSH, upstream: "origin/develop/feat/y", remoteBr: true, counts: "0\t1",
		cfg: pushProfile(`,"signing":{"format":"ssh","key":"~/.ssh/sign.pub"}`), log: "aaaaaaa1111\x1fG\x1focto\x1ffeat: a\n"}
	p, f := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{Branch: "develop/feat/y", Upstream: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Branch != "develop/feat/y" || !res.Pushed {
		t.Fatalf("res = %+v", res)
	}
	if got := strings.Join(pushWriteArgs(t, p), " "); !strings.HasSuffix(got, "push -u origin develop/feat/y") {
		t.Errorf("push args = %q", got)
	}
	for _, l := range f.CallLines() {
		if strings.Contains(l, "rev-list") || strings.Contains(l, "git log") {
			if strings.Contains(l, "HEAD") {
				t.Errorf("read used HEAD: %s", l)
			}
		}
	}
}

func TestPlanPushSkipSigning(t *testing.T) {
	w := pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
		cfg: pushProfile(`,"signing":{"format":"ssh","key":"~/.ssh/sign.pub"}`), log: "aaaaaaa1111\x1fN\x1f\x1ffeat: a\n"}
	p, f := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{SkipSigning: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.SigningChecked || hasFinding(res.Findings, "ID006") || !res.Pushed {
		t.Errorf("signing not skipped: %+v", res)
	}
	for _, l := range f.CallLines() {
		if strings.HasPrefix(l, "git log") {
			t.Errorf("ran %s", l)
		}
	}
}

func TestPlanPushQuotesWindowsKey(t *testing.T) {
	key := `C:\Users\Jo Doe\.ssh\id_work`
	w := pushWorld{branch: "develop/feat/x", remote: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1",
		cfg: `{"defaultProfile":"work","profiles":{"work":{"git":{"name":"Octo","email":"o@x.io"},"github":{"user":"octo","transport":"ssh","sshKey":"C:\\Users\\Jo Doe\\.ssh\\id_work"}}}}`}
	p, _ := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	want := `-c core.sshCommand=ssh -i "` + key + `" -o IdentitiesOnly=yes push origin develop/feat/x`
	if got := strings.Join(res.Args, " "); got != want {
		t.Errorf("args = %q\nwant   %q", got, want)
	}
	if res.Denied {
		t.Errorf("denied: %+v", res.Findings)
	}
}

func TestPlanPushPinsByPushURL(t *testing.T) {
	// The fetch URL is https but the push URL is ssh: the key is pinned.
	w := pushWorld{branch: "develop/feat/x", remote: "https://github.com/octo/api.git", pushURL: pushSSH, remoteBr: true, upstream: "origin/develop/feat/x", counts: "0\t1"}
	p, _ := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Args) < 2 || !strings.HasPrefix(res.Args[1], "core.sshCommand=") {
		t.Errorf("key not pinned for an ssh push URL: %v", res.Args)
	}
}

func TestPlanPushDefaultsToWorkflowRemote(t *testing.T) {
	wf := `{"name":"fork","remote":"fork","branches":{"production":{"name":"main","protected":true},
	  "staging":{"name":"develop/base","protected":true}},
	  "work":{"pattern":"develop/{type}/{name}","types":["feat"],"from":"staging"},
	  "transitions":[{"id":"merge","from":"work","to":"staging","via":"pr","prTemplate":"default"},
	    {"id":"release","from":"staging","to":"production","via":"pr","prTemplate":"release"}],
	  "prTemplates":{"default":{"sections":["Summary"]},"release":{"sections":["Summary"]}}}`
	cfg := `{"defaultProfile":"work","defaultWorkflow":"fork","workflows":{"fork":` + wf + `},
	  "profiles":{"work":{"git":{"name":"Octo","email":"o@x.io"},"github":{"user":"octo","transport":"ssh","sshKey":"` + pushKey + `"}}}}`
	w := pushWorld{branch: "develop/feat/x", remote: pushSSH, cfg: cfg}
	p, f := w.planner(t)
	res, err := PlanPush(context.Background(), p, PushOptions{})
	if err != nil {
		t.Fatalf("%v\ncalls: %v", err, f.CallLines())
	}
	if res.Remote != "fork" || !strings.HasSuffix(strings.Join(res.Args, " "), "push -u fork develop/feat/x") {
		t.Errorf("remote = %q args = %v", res.Remote, res.Args)
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
