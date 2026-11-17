package execx

import (
	"context"
	"errors"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

func TestCmdString(t *testing.T) {
	tests := []struct {
		c    Cmd
		want string
	}{
		{Cmd{Name: "git"}, "git"},
		{Cmd{Name: "git", Args: []string{"status", "--porcelain"}}, "git status --porcelain"},
		{Cmd{Name: "git", Args: []string{"commit", "-m", "a b"}}, "git commit -m a b"},
		{Cmd{Name: "gh", Args: []string{""}}, "gh"},
	}
	for _, tt := range tests {
		if got := tt.c.String(); got != tt.want {
			t.Errorf("%#v.String() = %q, want %q", tt.c, got, tt.want)
		}
	}
}

func TestResultOK(t *testing.T) {
	if !(Result{}).OK() {
		t.Error("exit 0 should be OK")
	}
	for _, code := range []int{1, 2, 127, -1} {
		if (Result{ExitCode: code}).OK() {
			t.Errorf("exit %d should not be OK", code)
		}
	}
}

func TestFakeMatching(t *testing.T) {
	ctx := context.Background()
	boom := errors.New("boom")
	f := NewFake().
		OnOut("git rev-parse HEAD", "abc\n").
		On("git status", Result{ExitCode: 1, Stderr: "bad"})
	f.Errors["git push"] = boom

	res, err := f.Run(ctx, Cmd{Name: "git", Args: []string{"rev-parse", "HEAD"}})
	if err != nil || res.Stdout != "abc\n" || !res.OK() {
		t.Fatalf("rev-parse: %+v %v", res, err)
	}
	res, err = f.Run(ctx, Cmd{Name: "git", Args: []string{"status"}})
	if err != nil || res.ExitCode != 1 || res.Stderr != "bad" {
		t.Fatalf("status: %+v %v", res, err)
	}
	res, err = f.Run(ctx, Cmd{Name: "git", Args: []string{"push"}})
	if !errors.Is(err, boom) || res.ExitCode != -1 {
		t.Fatalf("push: %+v %v", res, err)
	}
	res, err = f.Run(ctx, Cmd{Name: "git", Args: []string{"unknown"}})
	if err != nil || res.ExitCode != 127 || !strings.Contains(res.Stderr, "git unknown") {
		t.Fatalf("unmatched: %+v %v", res, err)
	}

	want := []string{"git rev-parse HEAD", "git status", "git push", "git unknown"}
	if got := f.CallLines(); !reflect.DeepEqual(got, want) {
		t.Errorf("CallLines = %q, want %q", got, want)
	}
	if len(f.Calls) != 4 || f.Calls[0].Name != "git" {
		t.Errorf("Calls = %+v", f.Calls)
	}
}

func TestFakeErrorBeatsResponse(t *testing.T) {
	boom := errors.New("boom")
	f := NewFake().OnOut("x", "ok")
	f.Errors["x"] = boom
	if _, err := f.Run(context.Background(), Cmd{Name: "x"}); !errors.Is(err, boom) {
		t.Fatalf("err = %v, want boom", err)
	}
}

func TestFakeDefault(t *testing.T) {
	f := NewFake()
	f.Default = &Result{Stdout: "dflt"}
	f.OnOut("a", "A")
	if r, _ := f.Run(context.Background(), Cmd{Name: "a"}); r.Stdout != "A" {
		t.Errorf("matched = %q", r.Stdout)
	}
	if r, _ := f.Run(context.Background(), Cmd{Name: "b", Args: []string{"c"}}); r.Stdout != "dflt" || !r.OK() {
		t.Errorf("default = %+v", r)
	}
}

func TestFakeUnmatched(t *testing.T) {
	f := NewFake().OnOut("z", "").OnOut("a", "").OnOut("m n", "")
	if got := f.Unmatched(); !reflect.DeepEqual(got, []string{"a", "m n", "z"}) {
		t.Errorf("Unmatched before = %q", got)
	}
	f.Run(context.Background(), Cmd{Name: "m", Args: []string{"n"}})
	if got := f.Unmatched(); !reflect.DeepEqual(got, []string{"a", "z"}) {
		t.Errorf("Unmatched after = %q", got)
	}
	f.Run(context.Background(), Cmd{Name: "a"})
	f.Run(context.Background(), Cmd{Name: "z"})
	if got := f.Unmatched(); len(got) != 0 {
		t.Errorf("Unmatched all = %q", got)
	}
}

func TestFakeConcurrent(t *testing.T) {
	f := NewFake()
	done := make(chan struct{})
	for i := 0; i < 8; i++ {
		go func() {
			defer func() { done <- struct{}{} }()
			f.Run(context.Background(), Cmd{Name: "x"})
			f.On("y", Result{})
			_ = f.CallLines()
			_ = f.Unmatched()
		}()
	}
	for i := 0; i < 8; i++ {
		<-done
	}
	if len(f.Calls) != 8 {
		t.Errorf("calls = %d", len(f.Calls))
	}
}

func TestOSRunner(t *testing.T) {
	if testing.Short() {
		t.Skip("runs a real program")
	}
	ctx := context.Background()
	res, err := OS{}.Run(ctx, Cmd{Name: "go", Args: []string{"env", "GOOS"}})
	if err != nil {
		t.Fatalf("go env: %v", err)
	}
	if strings.TrimSpace(res.Stdout) != runtime.GOOS || !res.OK() {
		t.Errorf("go env GOOS = %+v", res)
	}
}

func TestOSRunnerPosix(t *testing.T) {
	if testing.Short() || runtime.GOOS == "windows" {
		t.Skip("needs /bin/sh")
	}
	ctx := context.Background()
	dir := t.TempDir()
	res, err := OS{}.Run(ctx, Cmd{
		Name:  "sh",
		Args:  []string{"-c", `pwd; echo "$MYGIT_X"; cat; echo err >&2; exit 3`},
		Dir:   dir,
		Env:   []string{"MYGIT_X=hello"},
		Stdin: "in\n",
	})
	if err != nil {
		t.Fatalf("err = %v (non-zero exit must not be an error)", err)
	}
	if res.ExitCode != 3 || res.Stderr != "err\n" {
		t.Errorf("res = %+v", res)
	}
	lines := strings.Split(strings.TrimSpace(res.Stdout), "\n")
	if len(lines) != 3 || !strings.HasSuffix(lines[0], strings.TrimPrefix(dir, "/private")) || lines[1] != "hello" || lines[2] != "in" {
		t.Errorf("stdout = %q (dir %s)", res.Stdout, dir)
	}
}

func TestOSRunnerNotFound(t *testing.T) {
	res, err := OS{}.Run(context.Background(), Cmd{Name: "mygit-definitely-not-a-program-xyz"})
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("err = %v, want ErrNotFound", err)
	}
	if res.ExitCode != -1 {
		t.Errorf("exit = %d, want -1", res.ExitCode)
	}
}

func TestOSRunnerCancelled(t *testing.T) {
	if testing.Short() || runtime.GOOS == "windows" {
		t.Skip("needs sleep")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	res, err := OS{}.Run(ctx, Cmd{Name: "sleep", Args: []string{"5"}})
	if err == nil {
		t.Fatalf("cancelled ctx: res=%+v, want error", res)
	}
	if res.ExitCode != -1 {
		t.Errorf("exit = %d, want -1", res.ExitCode)
	}
}
