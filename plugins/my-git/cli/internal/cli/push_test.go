package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// pushEnv is a temp HOME/XDG machine with a scripted git.
type pushEnv struct {
	t      *testing.T
	repo   string
	fake   *execx.Fake
	out    bytes.Buffer
	errOut bytes.Buffer
}

func newPushEnv(t *testing.T, branch, remote string) *pushEnv {
	t.Helper()
	home := t.TempDir()
	repo := t.TempDir()
	t.Setenv("HOME", home)
	cfgDir := filepath.Join(home, ".config", "mygit")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	cfg := `{"defaultProfile":"work","profiles":{"work":{"git":{"name":"Octo","email":"o@x.io"},
	"github":{"user":"octo","transport":"ssh","sshKey":"~/.ssh/id_work"}}}}`
	if err := os.WriteFile(filepath.Join(cfgDir, "config.json"), []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	f := execx.NewFake()
	f.OnOut("git rev-parse --show-toplevel", repo)
	f.OnOut("git remote get-url origin", remote)
	f.OnOut("git -C "+repo+" remote get-url --push origin", remote)
	f.OnOut("git rev-parse -q --verify HEAD^{commit}", "abc123")
	f.OnOut("git remote get-url --push origin", remote)
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "refs/heads/x abc123")
	if branch == "" {
		f.On("git symbolic-ref --short -q HEAD", execx.Result{ExitCode: 1})
	} else {
		f.OnOut("git symbolic-ref --short -q HEAD", branch)
		f.OnOut("git -C "+repo+" symbolic-ref --short -q HEAD", branch)
		f.OnOut("git rev-parse -q --verify refs/heads/"+branch+"^{commit}", "abc123")
		f.OnOut("git rev-parse --abbrev-ref --symbolic-full-name "+branch+"@{upstream}", "origin/"+branch)
		f.OnOut("git rev-list --left-right --count origin/"+branch+"...refs/heads/"+branch, "0\t2")
		f.OnOut("git -C "+repo+" rev-parse --verify -q refs/remotes/origin/"+branch, "abc123")
	}
	return &pushEnv{t: t, repo: repo, fake: f}
}

func (e *pushEnv) run(args ...string) int {
	e.t.Helper()
	e.out.Reset()
	e.errOut.Reset()
	home := os.Getenv("HOME")
	io := IO{
		In: strings.NewReader(""), Out: &e.out, Err: &e.errOut,
		Getenv: func(k string) string {
			switch k {
			case "HOME":
				return home
			case "XDG_STATE_HOME":
				return filepath.Join(home, "state")
			}
			return ""
		},
		Getwd:  func() (string, error) { return e.repo, nil },
		Home:   func() (string, error) { return home, nil },
		Runner: e.fake, IsTerminal: func() bool { return false }, GOOS: "linux",
	}
	return Main("test", args, io)
}

func (e *pushEnv) pushCalls() []string {
	var out []string
	for _, l := range e.fake.CallLines() {
		if strings.Contains(l, " push ") {
			out = append(out, l)
		}
	}
	return out
}

// pushWant is the pinned push of develop/feat/x; the ~ in the key is expanded.
func pushWant(home string) string {
	return `-c core.sshCommand=ssh -i "` + home + `/.ssh/id_work" -o IdentitiesOnly=yes push origin develop/feat/x`
}

func TestPushDryRunPlansWithoutPushing(t *testing.T) {
	e := newPushEnv(t, "develop/feat/x", "git@github.com:octo/api.git")
	if code := e.run("push"); code != 0 {
		t.Fatalf("exit %d: %s%s", code, e.out.String(), e.errOut.String())
	}
	out := strings.Join(strings.Fields(e.out.String()), " ")
	for _, want := range []string{"rev-list --left-right --count origin/develop/feat/x...refs/heads/develop/feat/x", "↑2 ahead · ↓0 behind", "write", "core.sshCommand", "push origin", "✓ ID003", "dry-run"} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	if calls := e.pushCalls(); len(calls) != 0 {
		t.Errorf("dry run pushed: %v", calls)
	}
}

func TestPushYesRunsPinnedPush(t *testing.T) {
	e := newPushEnv(t, "develop/feat/x", "git@github.com:octo/api.git")
	want := pushWant(os.Getenv("HOME"))
	e.fake.OnOut("git "+want, "")
	if code := e.run("push", "--yes"); code != 0 {
		t.Fatalf("exit %d: %s%s", code, e.out.String(), e.errOut.String())
	}
	calls := e.pushCalls()
	if len(calls) != 1 || calls[0] != "git "+want {
		t.Fatalf("push calls = %v, want one %q", calls, "git "+want)
	}
	if !strings.Contains(e.out.String(), "✓") {
		t.Errorf("no success mark:\n%s", e.out.String())
	}
}

func TestPushUpstreamFlagAndNeverForce(t *testing.T) {
	e := newPushEnv(t, "develop/feat/x", "git@github.com:octo/api.git")
	if code := e.run("push", "--upstream", "--json"); code != 0 {
		t.Fatalf("exit %d: %s%s", code, e.out.String(), e.errOut.String())
	}
	var o struct {
		Status string `json:"status"`
		Plan   struct {
			Steps []struct {
				Kind    string `json:"kind"`
				Command []string
			} `json:"steps"`
		} `json:"plan"`
		Extra struct {
			Push struct {
				Branch string   `json:"branch"`
				Remote string   `json:"remote"`
				Ahead  int      `json:"ahead"`
				Args   []string `json:"args"`
				Pushed bool     `json:"pushed"`
			} `json:"push"`
		} `json:"extra"`
	}
	if err := json.Unmarshal(e.out.Bytes(), &o); err != nil {
		t.Fatalf("not one JSON object: %v\n%s", err, e.out.String())
	}
	p := o.Extra.Push
	if o.Status != "planned" || p.Branch != "develop/feat/x" || p.Remote != "origin" || p.Ahead != 2 || !p.Pushed {
		t.Errorf("json = %+v status %q", p, o.Status)
	}
	if got := strings.Join(p.Args, " "); !strings.Contains(got, "push -u origin develop/feat/x") || strings.Contains(got, "force") {
		t.Errorf("args = %q", got)
	}
}

func TestPushDeniedOnProtectedBranch(t *testing.T) {
	e := newPushEnv(t, "develop/base", "git@github.com:octo/api.git")
	if code := e.run("push"); code != 2 {
		t.Fatalf("exit %d, want 2: %s%s", code, e.out.String(), e.errOut.String())
	}
	if !strings.Contains(e.out.String(), "BR002") || !strings.Contains(e.errOut.String(), "denied") {
		t.Errorf("output:\n%s\nstderr: %s", e.out.String(), e.errOut.String())
	}
	if calls := e.pushCalls(); len(calls) != 0 {
		t.Errorf("pushed anyway: %v", calls)
	}
	if code := e.run("push", "--json"); code != 2 || !strings.Contains(e.out.String(), `"status": "denied"`) {
		t.Errorf("json denial: exit %d\n%s", code, e.out.String())
	}
}

func TestPushRefusesDetachedHead(t *testing.T) {
	e := newPushEnv(t, "", "git@github.com:octo/api.git")
	if code := e.run("push"); code != 1 || !strings.Contains(e.errOut.String(), "detached") {
		t.Fatalf("exit %d stderr %q", code, e.errOut.String())
	}
}

var pushApplyRe = regexp.MustCompile(`--apply ([0-9a-f]{6})`)

func TestPushDryRunThenApplyID(t *testing.T) {
	e := newPushEnv(t, "develop/feat/x", "git@github.com:octo/api.git")
	want := pushWant(os.Getenv("HOME"))
	e.fake.OnOut("git "+want, "")
	if code := e.run("push"); code != 0 {
		t.Fatalf("dry run exit %d: %s%s", code, e.out.String(), e.errOut.String())
	}
	m := pushApplyRe.FindStringSubmatch(e.out.String())
	if m == nil {
		t.Fatalf("no --apply hint:\n%s", e.out.String())
	}
	if calls := e.pushCalls(); len(calls) != 0 {
		t.Fatalf("dry run pushed: %v", calls)
	}
	if code := e.run("push", "--apply", m[1]); code != 0 {
		t.Fatalf("--apply %s exit %d: %s%s", m[1], code, e.out.String(), e.errOut.String())
	}
	if calls := e.pushCalls(); len(calls) != 1 || calls[0] != "git "+want {
		t.Errorf("push calls = %v", calls)
	}
}

func TestPushNewBranchWithoutUpstreamFlagAddsDashU(t *testing.T) {
	e := newPushEnv(t, "develop/feat/x", "git@github.com:octo/api.git")
	e.fake.On("git rev-parse --abbrev-ref --symbolic-full-name develop/feat/x@{upstream}", execx.Result{ExitCode: 128, Stderr: "no upstream"})
	want := `-c core.sshCommand=ssh -i "` + os.Getenv("HOME") + `/.ssh/id_work" -o IdentitiesOnly=yes push -u origin develop/feat/x`
	e.fake.OnOut("git "+want, "")
	if code := e.run("push", "--yes"); code != 0 {
		t.Fatalf("exit %d: %s%s", code, e.out.String(), e.errOut.String())
	}
	if calls := e.pushCalls(); len(calls) != 1 || calls[0] != "git "+want {
		t.Errorf("push calls = %v, want %q", calls, "git "+want)
	}
	if !strings.Contains(e.out.String(), "adding -u") {
		t.Errorf("no -u note:\n%s", e.out.String())
	}
}
