package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

const commitTestConfig = `{
  "defaultProfile": "work",
  "profiles": {"work": {
    "git": {"name": "Octo Cat", "email": "octo@acme.example",
            "signing": {"format": "ssh", "key": "~/.ssh/id_sign.pub"}},
    "github": {"user": "octo"}}}
}`

const (
	stagedStatus   = "1 M. N... 100644 100644 100644 aaa bbb a.go\n1 A. N... 000000 100644 100644 000 ccc b.go\n"
	unstagedStatus = "1 .M N... 100644 100644 100644 aaa aaa a.go\n? new.txt\n"
)

// commitEnv is a temp machine for driving the cobra command.
type commitEnv struct {
	t      *testing.T
	home   string
	repo   string
	fake   *execx.Fake
	status string
	branch string
	log    string // answer of git log -1
	runner commitRunner
}

// commitRunner passes everything to the Fake except the commit step, which
// is recorded and succeeds (the Fake cannot know the plan id in its path).
type commitRunner struct {
	fake    *execx.Fake
	commits []execx.Cmd
	writes  []execx.Cmd
}

func (r *commitRunner) Run(ctx context.Context, c execx.Cmd) (execx.Result, error) {
	for _, a := range c.Args {
		if a == "commit" {
			r.commits = append(r.commits, c)
			return execx.Result{}, nil
		}
	}
	if len(c.Args) >= 2 && c.Args[0] == "add" && c.Args[1] == "-A" {
		r.writes = append(r.writes, c)
		return execx.Result{}, nil
	}
	return r.fake.Run(ctx, c)
}

func newCommitEnv(t *testing.T) *commitEnv {
	t.Helper()
	e := &commitEnv{t: t, home: t.TempDir(), repo: t.TempDir(), status: stagedStatus, branch: "develop/feat/login",
		log: "a1f9c02\x1fG"}
	cfgDir := filepath.Join(e.home, ".config", "mygit")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	e.writeGlobal(commitTestConfig)
	return e
}

func (e *commitEnv) writeGlobal(content string) {
	e.t.Helper()
	if err := os.WriteFile(filepath.Join(e.home, ".config", "mygit", "config.json"), []byte(content), 0o644); err != nil {
		e.t.Fatal(err)
	}
}

// run executes `mygit <args>` and returns stdout, stderr and the exit code.
func (e *commitEnv) run(terminal bool, stdin string, args ...string) (string, string, int) {
	e.t.Helper()
	e.fake = execx.NewFake()
	e.fake.Default = &execx.Result{ExitCode: 1}
	e.fake.OnOut("git rev-parse --show-toplevel", e.repo+"\n")
	e.fake.OnOut("git rev-parse -q --verify HEAD^{commit}", "deadbeef\n")
	e.fake.OnOut("git for-each-ref --format=%(refname) %(objectname)", "")
	e.fake.OnOut("git status --porcelain=v2", e.status)
	e.fake.OnOut("git symbolic-ref --short -q HEAD", e.branch+"\n")
	e.fake.OnOut("git -C "+e.repo+" symbolic-ref --short -q HEAD", e.branch+"\n")
	e.fake.OnOut("git -C "+e.repo+" rev-parse --verify -q HEAD", "deadbeef\n")
	e.fake.OnOut("git log -1 --format=%h%x1f%G?", e.log+"\n")
	e.runner = commitRunner{fake: e.fake}
	var out, errb bytes.Buffer
	io := IO{
		In: strings.NewReader(stdin), Out: &out, Err: &errb,
		Getenv: func(string) string { return "" },
		Getwd:  func() (string, error) { return e.repo, nil },
		Home:   func() (string, error) { return e.home, nil },
		Runner: &e.runner, IsTerminal: func() bool { return terminal }, GOOS: "linux",
	}
	code := Main("test", args, io)
	return out.String(), errb.String(), code
}

func TestCommitDryRunPlan(t *testing.T) {
	e := newCommitEnv(t)
	out, errs, code := e.run(false, "", "commit", "-a", "-t", "feat", "-s", "auth", "-m", "add login endpoint")
	if code != 0 {
		t.Fatalf("exit %d\nstdout:\n%s\nstderr:\n%s", code, out, errs)
	}
	for _, want := range []string{
		"dry-run", "git status --porcelain=v2", "2 files changed", "git add -A",
		"user.name=\"Octo Cat\"", "user.email=octo@acme.example", "gpg.format=ssh",
		"user.signingkey=" + e.home + "/.ssh/id_sign.pub", "commit -S -F <msg>",
		"✓ CM003", "✓ ID001", "✓ ID002", "✓ ID006 signed", "✓ BR001 on develop/feat/login",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("plan lacks %q:\n%s", want, out)
		}
	}
	if len(e.runner.commits) != 0 || len(e.runner.writes) != 0 {
		t.Errorf("dry run mutated: commits=%v writes=%v", e.runner.commits, e.runner.writes)
	}
}

func TestCommitStepsAndMessage(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		wantMsg  string
		wantStep []string // substrings of the plan text
		noAdd    bool
	}{
		{"basic", []string{"-a", "-t", "feat", "-s", "auth", "-m", "add login endpoint"},
			"feat(auth): add login endpoint\n", []string{"git add -A"}, false},
		{"no scope, no -a", []string{"-t", "fix", "-m", "handle empty input"},
			"fix: handle empty input\n", nil, true},
		{"breaking with body and trailer", []string{"-t", "feat", "--breaking", "-m", "drop v1 api", "--body", "Clients must upgrade.", "--trailer", "Refs: #12"},
			"feat!: drop v1 api\n\nClients must upgrade.\n\nRefs: #12\n", nil, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newCommitEnv(t)
			out, errs, code := e.run(false, "", append([]string{"commit", "--yes"}, tt.args...)...)
			if code != 0 {
				t.Fatalf("exit %d\nstdout:\n%s\nstderr:\n%s", code, out, errs)
			}
			if len(e.runner.commits) != 1 {
				t.Fatalf("want one commit step, got %v\n%s", e.runner.commits, out)
			}
			c := e.runner.commits[0]
			args := strings.Join(c.Args, " ")
			if !strings.Contains(args, "commit -S -F ") {
				t.Errorf("commit args = %s", args)
			}
			if c.Dir != e.repo {
				t.Errorf("commit dir = %q, want %q", c.Dir, e.repo)
			}
			if (len(e.runner.writes) == 0) != tt.noAdd {
				t.Errorf("add -A steps = %d, noAdd=%v", len(e.runner.writes), tt.noAdd)
			}
			path := c.Args[len(c.Args)-1]
			b, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("message file: %v", err)
			}
			if string(b) != tt.wantMsg {
				t.Errorf("message = %q, want %q", b, tt.wantMsg)
			}
			if !strings.Contains(out, "✓ a1f9c02 "+strings.SplitN(tt.wantMsg, "\n", 2)[0]+"   signed ssh (Good)") {
				t.Errorf("missing result line:\n%s", out)
			}
		})
	}
}

func TestCommitJSON(t *testing.T) {
	e := newCommitEnv(t)
	out, errs, code := e.run(false, "", "commit", "--json", "-t", "feat", "-m", "add thing")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errs)
	}
	var o struct {
		Status string `json:"status"`
		Plan   struct {
			ID    string `json:"id"`
			Steps []struct {
				Kind string `json:"kind"`
			} `json:"steps"`
			Checks []struct {
				Rule string `json:"rule"`
				OK   bool   `json:"ok"`
			} `json:"checks"`
		} `json:"plan"`
		Extra struct {
			Header  string `json:"header"`
			Files   int    `json:"files"`
			Signing string `json:"signing"`
			Signed  bool   `json:"signed"`
		} `json:"extra"`
	}
	if err := json.Unmarshal([]byte(out), &o); err != nil {
		t.Fatalf("not one JSON object: %v\n%s", err, out)
	}
	if o.Status != "planned" || o.Plan.ID == "" || len(o.Plan.Steps) != 2 {
		t.Errorf("outcome = %+v", o)
	}
	if o.Extra.Header != "feat: add thing" || o.Extra.Files != 2 || o.Extra.Signing != "ssh" || !o.Extra.Signed {
		t.Errorf("extra = %+v", o.Extra)
	}
	seen := map[string]bool{}
	for _, c := range o.Plan.Checks {
		seen[c.Rule] = c.OK
	}
	for _, id := range []string{"CM003", "ID001", "ID002", "ID006", "BR001"} {
		if !seen[id] {
			t.Errorf("check %s missing or failed: %v", id, seen)
		}
	}
}

func TestCommitRefusals(t *testing.T) {
	long := strings.Repeat("x", 90)
	tests := []struct {
		name     string
		status   string
		branch   string
		args     []string
		wantCode int
		wantOut  string // in stdout
		wantErr  string // in stderr
	}{
		{"nothing to commit", "", "develop/feat/x", []string{"-a", "-t", "feat", "-m", "x"}, 1, "", "nothing to commit"},
		{"nothing staged", unstagedStatus, "develop/feat/x", []string{"-t", "feat", "-m", "x"}, 1, "", "nothing staged to commit (2 unstaged changes)"},
		{"conflicts", "u UU N... 1 1 1 1 a b c f.go\n", "develop/feat/x", []string{"-a", "-t", "feat", "-m", "x"}, 1, "", "unresolved merge conflicts"},
		{"missing type and subject", stagedStatus, "develop/feat/x", nil, 1, "", "missing --type and --message"},
		{"unknown type", stagedStatus, "develop/feat/x", []string{"-t", "nope", "-m", "add x"}, 2, "CM004", "refused by the rules"},
		{"header too long", stagedStatus, "develop/feat/x", []string{"-t", "feat", "-m", long}, 2, "deny", "refused by the rules"},
		{"protected branch", stagedStatus, "main", []string{"-t", "feat", "-m", "add x"}, 2, "BR001", "refused by the rules"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newCommitEnv(t)
			e.status, e.branch = tt.status, tt.branch
			out, errs, code := e.run(false, "", append([]string{"commit", "--yes"}, tt.args...)...)
			if code != tt.wantCode {
				t.Fatalf("exit %d, want %d\nstdout:\n%s\nstderr:\n%s", code, tt.wantCode, out, errs)
			}
			if !strings.Contains(out, tt.wantOut) || !strings.Contains(errs, tt.wantErr) {
				t.Errorf("stdout:\n%s\nstderr:\n%s", out, errs)
			}
			if len(e.runner.commits) != 0 || len(e.runner.writes) != 0 {
				t.Errorf("a refused commit must not run anything: %v %v", e.runner.commits, e.runner.writes)
			}
		})
	}
}

func TestCommitRefusalJSON(t *testing.T) {
	e := newCommitEnv(t)
	e.branch = "main"
	out, _, code := e.run(false, "", "commit", "--json", "-t", "feat", "-m", "add x")
	if code != 2 {
		t.Fatalf("exit %d", code)
	}
	var o struct {
		Decision string `json:"decision"`
		Findings []struct {
			Rule string `json:"rule"`
		} `json:"findings"`
	}
	if err := json.Unmarshal([]byte(out), &o); err != nil || o.Decision != "deny" || len(o.Findings) == 0 || o.Findings[0].Rule != "BR001" {
		t.Errorf("out = %s (%v)", out, err)
	}
}

func TestCommitPromptsOnTerminal(t *testing.T) {
	e := newCommitEnv(t)
	out, errs, code := e.run(true, "feat\nadd prompt\n", "commit")
	if code != 0 {
		t.Fatalf("exit %d\n%s\n%s", code, out, errs)
	}
	if !strings.Contains(out, "type: ") || !strings.Contains(out, "subject: ") {
		t.Errorf("no prompts:\n%s", out)
	}
	if !strings.Contains(out, "dry-run") || !strings.Contains(out, "commit -S -F <msg>") {
		t.Errorf("no plan:\n%s", out)
	}
}

func TestCommitUnsignedAndNoProfile(t *testing.T) {
	tests := []struct {
		name, cfg string
		wantSign  bool
		wantArg   string
	}{
		{"signing none", `{"defaultProfile":"w","profiles":{"w":{"git":{"name":"A B","email":"a@b.example","signing":{"format":"none"}},"github":{"user":"u"}}}}`,
			false, "commit.gpgsign=false"},
		{"signing default", `{"defaultProfile":"w","profiles":{"w":{"git":{"name":"A B","email":"a@b.example","signing":{"format":"default"}},"github":{"user":"u"}}}}`,
			true, "commit -S -F"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newCommitEnv(t)
			e.writeGlobal(tt.cfg)
			out, errs, code := e.run(false, "", "commit", "--yes", "-t", "feat", "-m", "add x")
			if code != 0 {
				t.Fatalf("exit %d\n%s\n%s", code, out, errs)
			}
			args := strings.Join(e.runner.commits[0].Args, " ")
			if !strings.Contains(args, tt.wantArg) || strings.Contains(args, " -S ") != tt.wantSign {
				t.Errorf("args = %s", args)
			}
		})
	}
}

func TestCommitManualMode(t *testing.T) {
	e := newCommitEnv(t)
	out, errs, code := e.run(false, "", "commit", "-a", "-t", "feat", "-m", "add x", "--mode", "manual", "--shell", "bash")
	if code != 0 {
		t.Fatalf("exit %d\n%s\n%s", code, out, errs)
	}
	for _, want := range []string{"add -A", "-c user.name='Octo Cat'", "commit -S -F", "mygit-"} {
		if !strings.Contains(out, want) {
			t.Errorf("script lacks %q:\n%s", want, out)
		}
	}
	if len(e.runner.commits) != 0 {
		t.Error("manual mode must not run the commit")
	}
}

// A custom template can never get a message past the rules.
func TestCommitCustomTemplateIsValidated(t *testing.T) {
	e := newCommitEnv(t)
	if err := os.MkdirAll(filepath.Join(e.repo, ".mygit"), 0o755); err != nil {
		t.Fatal(err)
	}
	write := func(name, content string) {
		if err := os.WriteFile(filepath.Join(e.repo, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(".mygit.json", `{"commit": {"template": ".mygit/commit.tmpl"}}`)

	write(".mygit/commit.tmpl", "{{.Subject}}\n")
	out, errs, code := e.run(false, "", "commit", "--yes", "-t", "feat", "-m", "add x")
	if code != 2 || !strings.Contains(out, "CM003") || len(e.runner.commits) != 0 {
		t.Fatalf("bad template: exit %d\n%s\n%s", code, out, errs)
	}

	write(".mygit/commit.tmpl", "{{.Type}}: {{.Subject}}\n\nCustom body.\n")
	out, errs, code = e.run(false, "", "commit", "--yes", "-t", "feat", "-m", "add x")
	if code != 0 || len(e.runner.commits) != 1 {
		t.Fatalf("good template: exit %d\n%s\n%s", code, out, errs)
	}
	b, err := os.ReadFile(e.runner.commits[0].Args[len(e.runner.commits[0].Args)-1])
	if err != nil || string(b) != "feat: add x\n\nCustom body.\n" {
		t.Errorf("message = %q (%v)", b, err)
	}
}

// dry run -> --apply <id>: the second run loads the saved plan and commits.
func TestCommitApplyByID(t *testing.T) {
	e := newCommitEnv(t)
	args := []string{"commit", "-t", "feat", "-m", "add login"}
	out, errs, code := e.run(false, "", args...)
	_, rest, ok := strings.Cut(out, "plan ")
	if code != 0 || !ok {
		t.Fatalf("dry run: exit %d\n%s%s", code, out, errs)
	}
	id, _, _ := strings.Cut(rest, " ")
	out, errs, code = e.run(false, "", append(args, "--apply", id)...)
	if code != 0 || len(e.runner.commits) != 1 {
		t.Fatalf("apply: exit %d, commits %v\n%s%s", code, e.runner.commits, out, errs)
	}
}

// Prompted values are part of the plan id: it equals the id of the same
// commit made with explicit flags, so --apply <id> never re-prompts.
func TestCommitPromptedValuesInPlanID(t *testing.T) {
	e := newCommitEnv(t)
	id := func(terminal bool, stdin string, args ...string) string {
		out, _, code := e.run(terminal, stdin, args...)
		_, rest, _ := strings.Cut(out, "plan ")
		got, _, _ := strings.Cut(rest, " ")
		if code != 0 || got == "" {
			t.Fatalf("exit %d, no plan id:\n%s", code, out)
		}
		return got
	}
	prompted := id(true, "feat\nadd prompt\n", "commit")
	flagged := id(false, "", "commit", "-t", "feat", "-m", "add prompt")
	if prompted != flagged {
		t.Errorf("prompted plan id %s != flag plan id %s", prompted, flagged)
	}
}
