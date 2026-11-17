// Package execx is the single seam between mygit and external processes
// (git, gh, test commands). Everything that runs a program takes a Runner so
// tests can substitute a Fake. No package may call os/exec directly.
package execx

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"sort"
	"strings"
	"sync"
)

// Cmd describes one process invocation. Args never go through a shell.
type Cmd struct {
	Name  string   // program, e.g. "git" (".exe" is resolved by exec.LookPath on Windows)
	Args  []string // argument list
	Dir   string   // working directory; "" = current
	Env   []string // extra KEY=VALUE entries appended to the process env
	Stdin string   // optional stdin
	// CmdLine, when set, is the raw Windows command line used instead of
	// quoting Args (for `cmd /S /C "line"`, where Go's \" escaping breaks
	// cmd). Other platforms ignore it; Args stay the portable form.
	CmdLine string
}

func (c Cmd) String() string { return strings.TrimSpace(c.Name + " " + strings.Join(c.Args, " ")) }

// Result of a finished process.
type Result struct {
	Stdout   string
	Stderr   string
	ExitCode int
}

// OK reports whether the process exited 0.
func (r Result) OK() bool { return r.ExitCode == 0 }

// ErrNotFound is returned (wrapped) when the program is not on PATH.
var ErrNotFound = errors.New("program not found")

// Runner runs processes. A non-zero exit is NOT an error: inspect Result.ExitCode.
// err is reserved for "could not start" (e.g. ErrNotFound) and context cancellation.
type Runner interface {
	Run(ctx context.Context, c Cmd) (Result, error)
}

// OS is the real Runner backed by os/exec.
type OS struct{}

func (OS) Run(ctx context.Context, c Cmd) (Result, error) {
	path, err := exec.LookPath(c.Name)
	if err != nil {
		return Result{ExitCode: -1}, fmt.Errorf("%s: %w", c.Name, ErrNotFound)
	}
	cmd := exec.CommandContext(ctx, path, c.Args...)
	cmd.Dir = c.Dir
	setCmdLine(cmd, c.CmdLine)
	if len(c.Env) > 0 {
		cmd.Env = append(cmd.Environ(), c.Env...)
	}
	if c.Stdin != "" {
		cmd.Stdin = strings.NewReader(c.Stdin)
	}
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err = cmd.Run()
	res := Result{Stdout: out.String(), Stderr: errb.String()}
	var ee *exec.ExitError
	switch {
	case err == nil:
	case errors.As(err, &ee):
		res.ExitCode = ee.ExitCode()
	default:
		res.ExitCode = -1
		return res, err
	}
	return res, nil
}

// Fake is a scripted Runner for tests. Responses are keyed by the command
// line "name arg1 arg2 ..." (Cmd.String()). Unmatched commands return
// Default, or exit 127 if Default is nil. Every call is recorded in Calls.
type Fake struct {
	mu        sync.Mutex
	Responses map[string]Result
	Errors    map[string]error
	Default   *Result
	Calls     []Cmd
}

// NewFake returns an empty Fake.
func NewFake() *Fake { return &Fake{Responses: map[string]Result{}, Errors: map[string]error{}} }

// On registers a response for a command line and returns the Fake for chaining.
func (f *Fake) On(cmdline string, r Result) *Fake {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Responses[cmdline] = r
	return f
}

// OnOut is On with Stdout only and exit 0.
func (f *Fake) OnOut(cmdline, stdout string) *Fake { return f.On(cmdline, Result{Stdout: stdout}) }

func (f *Fake) Run(_ context.Context, c Cmd) (Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Calls = append(f.Calls, c)
	key := c.String()
	if err, ok := f.Errors[key]; ok {
		return Result{ExitCode: -1}, err
	}
	if r, ok := f.Responses[key]; ok {
		return r, nil
	}
	if f.Default != nil {
		return *f.Default, nil
	}
	return Result{ExitCode: 127, Stderr: "fake: no response for " + key}, nil
}

// CallLines returns the recorded calls as command lines, in order.
func (f *Fake) CallLines() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]string, len(f.Calls))
	for i, c := range f.Calls {
		out[i] = c.String()
	}
	return out
}

// Unmatched lists registered responses never called (useful in assertions).
func (f *Fake) Unmatched() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	seen := map[string]bool{}
	for _, c := range f.Calls {
		seen[c.String()] = true
	}
	var out []string
	for k := range f.Responses {
		if !seen[k] {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}
