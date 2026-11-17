package plan

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"testing/iotest"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

var ctx = context.Background()

func git(args ...string) execx.Cmd { return execx.Cmd{Name: "git", Args: args} }

func stateOf(s string) StateFunc { return func(context.Context) (string, error) { return s, nil } }

// specPlan builds the "What a plan looks like" example.
func specPlan(t *testing.T, f *execx.Fake, state string) *Plan {
	t.Helper()
	b := NewBuilder(f, Meta{
		Command: []string{"mygit", "commit", "-a", "-t", "feat", "-s", "auth", "-m", "add login endpoint"},
		Profile: "work", Ruleset: "team", Mode: "auto", Shell: "powershell",
	})
	_, err := b.Read(ctx, git("status", "--porcelain=v2"), "changed files", func(r execx.Result) string {
		return "3 files changed"
	})
	if err != nil {
		t.Fatal(err)
	}
	b.Write(git("add", "-A"), "stage all")
	msg := b.File("msg", "feat(auth): add login endpoint\n")
	b.Write(git("-c", "user.name=Octo Cat", "-c", "user.email=octo@acme.example",
		"-c", "gpg.format=ssh", "-c", "user.signingkey=~/.ssh/id_sign.pub", "commit", "-S", "-F", msg), "commit")
	for _, c := range []struct{ id, msg string }{{"CM003", ""}, {"CM007", ""}, {"ID001", ""}, {"ID002", ""}, {"ID006", "signed"}, {"BR001", "on develop/feat/login"}} {
		b.Check(c.id, true, verdict.Deny, c.msg)
	}
	p, err := b.Build(ctx, stateOf(state))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func statusFake() *execx.Fake {
	return execx.NewFake().OnOut("git status --porcelain=v2", "1 .M N... a\n1 .M N... b\n? c\n")
}

func TestBuilderRunsReadsRecordsWrites(t *testing.T) {
	f := statusFake()
	var traced []string
	b := NewBuilder(f, Meta{Command: []string{"mygit", "x"}})
	b.Trace = func(c execx.Cmd) { traced = append(traced, c.String()) }
	res, err := b.Read(ctx, git("status", "--porcelain=v2"), "", nil)
	if err != nil || !strings.Contains(res.Stdout, "? c") {
		t.Fatalf("Read = %+v %v", res, err)
	}
	b.Write(git("add", "-A"), "")
	b.Write(git("commit", "-F", "x"), "")
	b.Note("hello %d", 1)
	p, err := b.Build(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := f.CallLines(); !reflect.DeepEqual(got, []string{"git status --porcelain=v2"}) {
		t.Errorf("runner calls = %q; writes must not run while planning", got)
	}
	if !reflect.DeepEqual(traced, []string{"git status --porcelain=v2"}) {
		t.Errorf("trace = %q", traced)
	}
	if len(p.Steps) != 3 || p.Steps[0].Kind != Read || p.Steps[0].Result == nil || p.Steps[1].Result != nil {
		t.Errorf("steps = %+v", p.Steps)
	}
	if w := p.Writes(); len(w) != 2 || w[0].Cmd.Args[0] != "add" || w[1].Cmd.Args[0] != "commit" {
		t.Errorf("Writes = %+v", w)
	}
	if len(p.ID) != 6 || p.Notes[0] != "hello 1" {
		t.Errorf("id=%q notes=%q", p.ID, p.Notes)
	}
}

func TestBuilderReadFailures(t *testing.T) {
	f := execx.NewFake().On("git a", execx.Result{ExitCode: 1, Stderr: "no"})
	f.Errors["git b"] = execx.ErrNotFound
	b := NewBuilder(f, Meta{})
	called := false
	res, err := b.Read(ctx, git("a"), "", func(execx.Result) string { called = true; return "sum" })
	if err != nil || res.ExitCode != 1 || !called {
		t.Fatalf("non-zero exit: %+v %v called=%v", res, err, called)
	}
	_, err = b.Read(ctx, git("b"), "", func(execx.Result) string { t.Error("summarize called on start error"); return "" })
	if !errors.Is(err, execx.ErrNotFound) {
		t.Fatalf("start error = %v", err)
	}
	p, _ := b.Build(ctx, nil)
	if len(p.Steps) != 2 || p.Steps[0].Summary != "sum" || !strings.Contains(p.Steps[1].Summary, "not found") {
		t.Errorf("steps = %+v", p.Steps)
	}
	if _, err := b.Build(ctx, func(context.Context) (string, error) { return "", errors.New("bad head") }); err == nil || !strings.Contains(err.Error(), "repo state: bad head") {
		t.Errorf("state error = %v", err)
	}
}

func TestIDDeterminismAndSensitivity(t *testing.T) {
	base := func() *Builder {
		b := NewBuilder(execx.NewFake(), Meta{Command: []string{"mygit", "commit"}, Profile: "work"})
		b.Write(git("add", "-A"), "d")
		b.File("msg", "hello")
		b.Check("CM003", true, verdict.Deny, "")
		return b
	}
	build := func(b *Builder, state string) string {
		p, err := b.Build(ctx, stateOf(state))
		if err != nil {
			t.Fatal(err)
		}
		return p.ID
	}
	id := build(base(), "head=abc")
	if id != build(base(), "head=abc") {
		t.Fatal("same inputs must give the same id")
	}
	if len(id) != 6 || strings.Trim(id, "0123456789abcdef") != "" {
		t.Fatalf("id %q is not 6 hex digits", id)
	}

	same := map[string]func(*Builder){
		"description": func(b *Builder) { b.plan.Steps[0].Description = "other" },
		"check":       func(b *Builder) { b.Check("X", false, verdict.Ask, "m") },
		"note":        func(b *Builder) { b.Note("n") },
		"profile":     func(b *Builder) { b.plan.Profile = "personal" },
		"mode/shell":  func(b *Builder) { b.plan.Mode, b.plan.Shell = "manual", "cmd" },
	}
	for name, mut := range same {
		b := base()
		mut(b)
		if got := build(b, "head=abc"); got != id {
			t.Errorf("%s changed the id (%s → %s); display metadata is not hashed", name, id, got)
		}
	}

	diff := map[string]func(*Builder) string{
		"argv":         func(b *Builder) string { b.plan.Command = []string{"mygit", "commit", "-a"}; return "head=abc" },
		"step arg":     func(b *Builder) string { b.plan.Steps[0].Cmd.Args = []string{"add", "-u"}; return "head=abc" },
		"extra step":   func(b *Builder) string { b.Write(git("push"), ""); return "head=abc" },
		"step kind":    func(b *Builder) string { b.plan.Steps[0].Kind = Read; return "head=abc" },
		"step dir":     func(b *Builder) string { b.plan.Steps[0].Cmd.Dir = "/x"; return "head=abc" },
		"step env":     func(b *Builder) string { b.plan.Steps[0].Cmd.Env = []string{"A=1"}; return "head=abc" },
		"file content": func(b *Builder) string { b.plan.Files[0].Content = "hello!"; return "head=abc" },
		"extra file":   func(b *Builder) string { b.File("body", ""); return "head=abc" },
		"state":        func(b *Builder) string { return "head=abd" },
		"no state":     func(b *Builder) string { return "" },
	}
	for name, mut := range diff {
		b := base()
		st := mut(b)
		if got := build(b, st); got == id {
			t.Errorf("%s did not change the id", name)
		}
	}
}

func TestApply(t *testing.T) {
	tmp := t.TempDir()
	p := specPlan(t, statusFake(), "s1")

	// stale: nothing runs
	f := execx.NewFake()
	res, err := Apply(ctx, p, f, stateOf("s2"), ApplyOptions{TempDir: tmp})
	if !errors.Is(err, ErrStale) || res != nil || len(f.Calls) != 0 {
		t.Fatalf("stale: res=%v err=%v calls=%v", res, err, f.CallLines())
	}
	if _, err := os.Stat(filepath.Join(tmp, "mygit-"+p.ID+".msg")); err == nil {
		t.Error("stale apply wrote the message file")
	}
	// tampered plan is stale too
	q := *p
	q.Steps = append([]Step(nil), p.Steps...)
	q.Steps[1].Cmd = git("push", "--force")
	if _, err := Apply(ctx, &q, f, stateOf("s1"), ApplyOptions{TempDir: tmp}); !errors.Is(err, ErrStale) {
		t.Errorf("tampered: %v", err)
	}

	// success
	msgPath := filepath.Join(tmp, "mygit-"+p.ID+".msg")
	commitLine := "git -c user.name=Octo Cat -c user.email=octo@acme.example -c gpg.format=ssh -c user.signingkey=~/.ssh/id_sign.pub commit -S -F " + msgPath
	f = execx.NewFake().OnOut("git add -A", "").OnOut(commitLine, "[main abc] ok")
	var traced []string
	res, err = Apply(ctx, p, f, stateOf("s1"), ApplyOptions{TempDir: tmp, Trace: func(c execx.Cmd) { traced = append(traced, c.String()) }})
	if err != nil {
		t.Fatalf("apply: %v (calls %q)", err, f.CallLines())
	}
	if got := f.CallLines(); !reflect.DeepEqual(got, []string{"git add -A", commitLine}) {
		t.Errorf("calls = %q", got)
	}
	if len(traced) != 2 || len(res) != 2 || res[0].Index != 2 || res[1].Index != 3 || res[1].Result.Stdout != "[main abc] ok" {
		t.Errorf("results = %+v traced=%v", res, traced)
	}
	b, err := os.ReadFile(msgPath)
	if err != nil || string(b) != "feat(auth): add login endpoint\n" {
		t.Errorf("msg file = %q %v", b, err)
	}
	if fi, _ := os.Stat(msgPath); fi.Mode().Perm() != 0o600 {
		t.Errorf("msg file mode = %v", fi.Mode())
	}
}

func TestApplyStopsAtFirstFailure(t *testing.T) {
	b := NewBuilder(execx.NewFake(), Meta{})
	b.Write(git("one"), "")
	b.Write(git("two"), "")
	b.Write(git("three"), "")
	p, _ := b.Build(ctx, nil)

	f := execx.NewFake().OnOut("git one", "").On("git two", execx.Result{ExitCode: 1, Stderr: "error: boom\nmore"}).OnOut("git three", "")
	res, err := Apply(ctx, p, f, nil, ApplyOptions{})
	var se *StepError
	if !errors.As(err, &se) {
		t.Fatalf("err = %v, want *StepError", err)
	}
	if se.Index != 2 || se.Err != nil || se.Result.ExitCode != 1 || errors.Unwrap(se) != nil {
		t.Errorf("StepError = %+v", se)
	}
	if want := "step 2 (git two): exit 1: error: boom"; se.Error() != want {
		t.Errorf("Error() = %q, want %q", se.Error(), want)
	}
	if len(res) != 2 || !reflect.DeepEqual(f.CallLines(), []string{"git one", "git two"}) {
		t.Errorf("res=%+v calls=%q", res, f.CallLines())
	}

	f = execx.NewFake()
	f.Errors["git one"] = execx.ErrNotFound
	_, err = Apply(ctx, p, f, nil, ApplyOptions{})
	if !errors.As(err, &se) || !errors.Is(err, execx.ErrNotFound) || se.Index != 1 || !strings.HasPrefix(se.Error(), "step 1 (git one): ") {
		t.Errorf("start error = %v", err)
	}

	if _, err := Apply(ctx, p, f, func(context.Context) (string, error) { return "", errors.New("x") }, ApplyOptions{}); err == nil || errors.Is(err, ErrStale) {
		t.Errorf("state error = %v", err)
	}
}

func TestWriteFilesNeedsTempDir(t *testing.T) {
	b := NewBuilder(execx.NewFake(), Meta{})
	b.Write(git("commit", "-F", b.File("msg", "m")), "")
	p, _ := b.Build(ctx, nil)
	if _, err := Apply(ctx, p, execx.NewFake(), nil, ApplyOptions{}); err == nil {
		t.Error("apply with files and no temp dir should fail")
	}
	paths, err := p.WriteFiles(filepath.Join(t.TempDir(), "nested", "dir"))
	if err != nil || len(paths) != 1 {
		t.Errorf("WriteFiles = %v %v", paths, err)
	}
	// a file where the directory should be
	blocker := filepath.Join(t.TempDir(), "f")
	os.WriteFile(blocker, nil, 0o600)
	if _, err := p.WriteFiles(blocker); err == nil {
		t.Error("WriteFiles into a file path should fail")
	}
	empty := &Plan{}
	if paths, err := empty.WriteFiles(""); paths != nil || err != nil {
		t.Errorf("no files = %v %v", paths, err)
	}
}

func TestResolvePath(t *testing.T) {
	p := &Plan{ID: "abc123"}
	tests := []struct{ in, tmp, want string }{
		{"{{TEMP}}/mygit-{{id}}.msg", "/tmp", "/tmp/mygit-abc123.msg"},
		{"{{TEMP}}/mygit-{{id}}.msg", "/tmp/", "/tmp/mygit-abc123.msg"},
		{"{{TEMP}}/mygit-{{id}}.msg", `C:\Users\o\AppData\Local\Temp`, `C:\Users\o\AppData\Local\Temp\mygit-abc123.msg`},
		{"{{TEMP}}/mygit-{{id}}.msg", "", "{{TEMP}}/mygit-abc123.msg"},
		{"--file={{TEMP}}/x", "/t", "--file=/t/x"},
		{"{{TEMP}}", "/t", "/t"},
		{"plain", "/t", "plain"},
	}
	for _, tt := range tests {
		if got := p.ResolvePath(tt.in, tt.tmp); got != tt.want {
			t.Errorf("ResolvePath(%q, %q) = %q, want %q", tt.in, tt.tmp, got, tt.want)
		}
	}
	c := p.Resolve(execx.Cmd{Name: "git", Args: []string{"-F", "{{TEMP}}/{{id}}"}, Env: []string{"F={{TEMP}}/e"}, Dir: "{{TEMP}}", Stdin: "{{id}}"}, "/t")
	if !reflect.DeepEqual(c, execx.Cmd{Name: "git", Args: []string{"-F", "/t/abc123"}, Env: []string{"F=/t/e"}, Dir: "/t", Stdin: "abc123"}) {
		t.Errorf("Resolve = %+v", c)
	}
}

func TestPlanOK(t *testing.T) {
	p := &Plan{Checks: []Check{{RuleID: "A", OK: true}}}
	if !p.OK() {
		t.Error("all passed should be OK")
	}
	p.Checks = append(p.Checks, Check{RuleID: "B", OK: false})
	if p.OK() {
		t.Error("a failed check should not be OK")
	}
}

func TestTextSpecExample(t *testing.T) {
	p := specPlan(t, statusFake(), "s")
	want := "plan " + p.ID + "   dry-run · mode auto · profile work · ruleset team · shell powershell\n" +
		"  1  read   git status --porcelain=v2                     3 files changed\n" +
		"  2  write  git add -A\n" +
		"  3  write  git -c user.name=\"Octo Cat\" -c user.email=octo@acme.example\n" +
		"                -c gpg.format=ssh -c user.signingkey=~/.ssh/id_sign.pub commit -S -F <msg>\n" +
		"  checks  ✓ CM003 ✓ CM007 ✓ ID001 ✓ ID002 ✓ ID006 signed ✓ BR001 on develop/feat/login\n"
	// The spec's sample wraps the commit line at ~72 columns; Text wraps at
	// textWidth (92). Compare with continuation lines joined, then check
	// the wrap shape separately.
	join := func(s string) string { return strings.ReplaceAll(s, "\n                ", " ") }
	got := Text(p)
	if join(got) != join(want) {
		t.Errorf("Text =\n%s\nwant\n%s", got, want)
	}
	for _, l := range strings.Split(got, "\n") {
		if n := len([]rune(l)); n > textWidth {
			t.Errorf("line longer than %d: %q", textWidth, l)
		}
	}
	if !strings.Contains(got, "\n                -c ") {
		t.Errorf("long commit line should wrap with a 16-space indent before a -c flag:\n%s", got)
	}
}

func TestTextVariants(t *testing.T) {
	f := execx.NewFake().On("git rev-parse --verify develop/base", execx.Result{ExitCode: 128})
	b := NewBuilder(f, Meta{Workflow: "default"})
	b.Read(ctx, git("rev-parse", "--verify", "develop/base"), "", nil)
	c := git("push", "-u", "origin", "HEAD")
	c.Env = []string{"GIT_SSH_COMMAND=ssh -i ~/.ssh/id_acme"}
	b.Write(c, "")
	b.Write(execx.Cmd{Name: "gh", Args: []string{"pr", "create", "--title", `say "hi"`, "--body-file", "{{TEMP}}/other-{{id}}"}}, "")
	b.Check("BR005", false, verdict.Deny, "base must be develop/base")
	b.Check("GH008", false, verdict.Warn, "missing section")
	b.Note("dry run only")
	p, _ := b.Build(ctx, nil)
	want := "plan " + p.ID + "   dry-run · workflow default\n" +
		"  1  read   git rev-parse --verify develop/base           exit 128\n" +
		"  2  write  GIT_SSH_COMMAND=\"ssh -i ~/.ssh/id_acme\" git push -u origin HEAD\n" +
		"  3  write  gh pr create --title \"say \\\"hi\\\"\" --body-file $TEMP/other-" + p.ID + "\n" +
		"  checks\n" +
		"          ✗ deny  BR005  base must be develop/base\n" +
		"          ✗ warn  GH008  missing section\n" +
		"  note    dry run only\n"
	if got := Text(p); got != want {
		t.Errorf("Text =\n%s\nwant\n%s", got, want)
	}
	// no checks: no checks line
	p.Checks = nil
	p.Notes = nil
	if strings.Contains(Text(p), "checks") {
		t.Error("checks line printed without checks")
	}
}

func TestJSONStable(t *testing.T) {
	p := specPlan(t, statusFake(), "s")
	a, err := JSON(p)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := JSON(p)
	if string(a) != string(b) {
		t.Error("JSON is not deterministic")
	}
	var m map[string]any
	if err := json.Unmarshal(a, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"id", "command", "profile", "ruleset", "mode", "shell", "steps", "checks", "files"} {
		if _, ok := m[k]; !ok {
			t.Errorf("missing top-level key %q in %s", k, a)
		}
	}
	step0 := m["steps"].([]any)[0].(map[string]any)
	for _, k := range []string{"kind", "cmd", "description", "result", "summary"} {
		if _, ok := step0[k]; !ok {
			t.Errorf("missing step key %q: %v", k, step0)
		}
	}
	cmd := step0["cmd"].(map[string]any)
	if cmd["name"] != "git" || cmd["args"] == nil {
		t.Errorf("cmd = %v", cmd)
	}
	if r := step0["result"].(map[string]any); r["exitCode"] != float64(0) {
		t.Errorf("result = %v", r)
	}
	step1 := m["steps"].([]any)[1].(map[string]any)
	if _, ok := step1["result"]; ok {
		t.Error("write step must not carry a result")
	}
	chk := m["checks"].([]any)[0].(map[string]any)
	if chk["rule"] != "CM003" || chk["ok"] != true || chk["severity"] != "deny" {
		t.Errorf("check = %v", chk)
	}
	for _, bad := range []string{`"Cmd"`, `"Result"`, `"ExitCode"`, `"Args"`, `"RuleID"`} {
		if strings.Contains(string(a), bad) {
			t.Errorf("JSON contains non-lowerCamelCase key %s", bad)
		}
	}
	var back Step
	if err := back.UnmarshalJSON([]byte(`{"kind":`)); err == nil {
		t.Error("bad step json should fail")
	}
}

func TestStoreRoundTrip(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "plans")
	s := Store{Dir: dir}
	p := specPlan(t, statusFake(), "s1")
	p.Notes = []string{"n"}
	if err := s.Save(p); err != nil {
		t.Fatal(err)
	}
	if fi, err := os.Stat(filepath.Join(dir, p.ID+".json")); err != nil || fi.Mode().Perm() != 0o600 {
		t.Errorf("saved file: %v %v", fi, err)
	}
	got, err := s.Load(p.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, p) {
		t.Errorf("round trip mismatch:\n got %+v\nwant %+v", got, p)
	}
	if ok, err := Fresh(ctx, got, stateOf("s1")); !ok || err != nil {
		t.Errorf("loaded plan is not fresh: %v %v", ok, err)
	}
	if err := s.Delete(p.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Load(p.ID); !errors.Is(err, ErrNotFound) {
		t.Errorf("after delete: %v", err)
	}
	if err := s.Delete(p.ID); err != nil {
		t.Errorf("deleting a missing plan: %v", err)
	}
	for _, bad := range []string{"", "../etc", "ABCDEF", "12345", "abc12g"} {
		if _, err := s.Load(bad); err == nil || errors.Is(err, ErrNotFound) {
			t.Errorf("Load(%q) = %v, want invalid id error", bad, err)
		}
		if err := s.Delete(bad); err == nil {
			t.Errorf("Delete(%q) should fail", bad)
		}
		if err := s.Save(&Plan{ID: bad}); err == nil {
			t.Errorf("Save(%q) should fail", bad)
		}
	}
	// corrupt file
	os.WriteFile(filepath.Join(dir, "abcdef.json"), []byte("{"), 0o600)
	if _, err := s.Load("abcdef"); err == nil || errors.Is(err, ErrNotFound) {
		t.Errorf("corrupt: %v", err)
	}
	// unreadable: a directory named like a plan
	os.Mkdir(filepath.Join(dir, "fedcba.json"), 0o700)
	if _, err := s.Load("fedcba"); err == nil || errors.Is(err, ErrNotFound) {
		t.Errorf("dir as plan: %v", err)
	}
	// Save into a path blocked by a file
	blocker := filepath.Join(t.TempDir(), "file")
	os.WriteFile(blocker, nil, 0o600)
	if err := (Store{Dir: blocker}).Save(p); err == nil {
		t.Error("Save under a file should fail")
	}
	if err := (Store{Dir: filepath.Join(dir, "fedcba.json")}).Delete("fedcba"); err != nil {
		t.Errorf("Delete missing in odd dir: %v", err)
	}
	nonEmpty := filepath.Join(dir, "aaaaaa.json")
	os.MkdirAll(filepath.Join(nonEmpty, "x"), 0o700)
	if err := s.Delete("aaaaaa"); err == nil {
		t.Error("Delete of a non-empty dir should fail")
	}
	if err := (Store{Dir: dir}).Save(&Plan{ID: "aaaaaa"}); err == nil {
		t.Error("Save over a directory should fail")
	}
}

func TestManual(t *testing.T) {
	p := specPlan(t, statusFake(), "s")
	for i := range p.Steps {
		p.Steps[i].Cmd.Dir = `C:\src\api`
	}
	push := git("push")
	push.Env = []string{"GIT_SSH_COMMAND=ssh -i k=v"}
	p.Steps = append(p.Steps, Step{Kind: Write, Cmd: push})

	got := Manual(p, shellfmt.PowerShell, false)
	want := "# plan " + p.ID + " · manual · shell powershell · run these, then: mygit plan verify " + p.ID + "\n" +
		"git -C 'C:\\src\\api' add -A\n" +
		"if (-not $?) { exit 1 }\n" +
		"git -C 'C:\\src\\api' -c user.name='Octo Cat' -c user.email='octo@acme.example' `\n" +
		"    -c gpg.format=ssh -c user.signingkey='~/.ssh/id_sign.pub' commit -S `\n" +
		"    -F \"$env:TEMP\\mygit-" + p.ID + ".msg\"\n" +
		"if (-not $?) { exit 1 }\n" +
		"$env:GIT_SSH_COMMAND='ssh -i k=v'\n" +
		"git push\n" +
		"if (-not $?) { exit 1 }\n"
	if got != want {
		t.Errorf("Manual(powershell) =\n%s\nwant\n%s", got, want)
	}
	if strings.Contains(got, "status") {
		t.Error("manual output includes a read step")
	}
	sh := Manual(p, shellfmt.Bash, true)
	if !strings.HasPrefix(sh, "#!/usr/bin/env bash\n# plan "+p.ID) || !strings.Contains(sh, "set -e\n") {
		t.Errorf("bash script = %s", sh)
	}
	if envMap(nil) != nil {
		t.Error("envMap(nil) should be nil")
	}
}

func TestConfirm(t *testing.T) {
	tests := []struct {
		in   string
		want bool
	}{
		{"y\n", true}, {"Y\n", true}, {"yes\n", true}, {"YES\r\n", true}, {"  y  \n", true}, {"y", true},
		{"n\n", false}, {"\n", false}, {"", false}, {"no\n", false}, {"yy\n", false}, {"ye\n", false},
		{"\ny\n", false},
	}
	for _, tt := range tests {
		var out strings.Builder
		got, err := Confirm(strings.NewReader(tt.in), &out)
		if err != nil || got != tt.want {
			t.Errorf("Confirm(%q) = %v %v, want %v", tt.in, got, err, tt.want)
		}
		if out.String() != "apply? [y/N] " {
			t.Errorf("prompt = %q", out.String())
		}
	}
	if ok, err := Confirm(iotest.ErrReader(errors.New("tty gone")), io.Discard); ok || err == nil {
		t.Errorf("read error = %v %v", ok, err)
	}
	if ok, err := Confirm(strings.NewReader("y\n"), errWriter{}); ok || err == nil {
		t.Errorf("write error = %v %v", ok, err)
	}
}

type errWriter struct{}

func (errWriter) Write([]byte) (int, error) { return 0, errors.New("closed") }
