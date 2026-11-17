// Package gitx is a thin, typed wrapper over the git binary. Every call goes
// through an execx.Runner, so tests substitute execx.Fake.
package gitx

import (
	"context"
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// Git runs git commands in one repository directory.
type Git struct {
	Runner execx.Runner
	Dir    string          // working directory ("" = current)
	Trace  func(execx.Cmd) // optional; called before every command (--verbose)
}

// New returns a Git for dir.
func New(r execx.Runner, dir string) *Git { return &Git{Runner: r, Dir: dir} }

// ExitError reports a git command that exited non-zero.
type ExitError struct {
	Cmd    execx.Cmd
	Result execx.Result
}

func (e *ExitError) Error() string {
	msg := strings.TrimSpace(e.Result.Stderr)
	if msg == "" {
		msg = strings.TrimSpace(e.Result.Stdout)
	}
	return fmt.Sprintf("%s: exit %d: %s", e.Cmd, e.Result.ExitCode, msg)
}

// Cmd builds the execx.Cmd for git args in g.Dir without running it
// (used to record plan steps).
func (g *Git) Cmd(args ...string) execx.Cmd {
	return execx.Cmd{Name: "git", Args: args, Dir: g.Dir}
}

// Exec runs git and returns the raw result. A non-zero exit is not an error.
func (g *Git) Exec(ctx context.Context, args ...string) (execx.Result, error) {
	c := g.Cmd(args...)
	if g.Trace != nil {
		g.Trace(c)
	}
	res, err := g.Runner.Run(ctx, c)
	if err != nil {
		return res, fmt.Errorf("%s: %w", c, err)
	}
	return res, nil
}

// Run runs git and returns an *ExitError (with stderr) on a non-zero exit.
func (g *Git) Run(ctx context.Context, args ...string) (execx.Result, error) {
	res, err := g.Exec(ctx, args...)
	if err != nil {
		return res, err
	}
	if !res.OK() {
		return res, &ExitError{Cmd: g.Cmd(args...), Result: res}
	}
	return res, nil
}

// Query runs git and returns its trimmed stdout.
func (g *Git) Query(ctx context.Context, args ...string) (string, error) {
	res, err := g.Run(ctx, args...)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(res.Stdout), nil
}

// probe runs git and returns (stdout, true) on exit 0, ("", false) on exit
// codes listed in miss, and an error otherwise.
func (g *Git) probe(ctx context.Context, miss []int, args ...string) (string, bool, error) {
	res, err := g.Exec(ctx, args...)
	if err != nil {
		return "", false, err
	}
	if res.OK() {
		return strings.TrimSpace(res.Stdout), true, nil
	}
	for _, code := range miss {
		if res.ExitCode == code {
			return "", false, nil
		}
	}
	return "", false, &ExitError{Cmd: g.Cmd(args...), Result: res}
}

// Version returns git's version number, e.g. "2.43.0".
func (g *Git) Version(ctx context.Context) (string, error) {
	out, err := g.Query(ctx, "version")
	if err != nil {
		return "", err
	}
	return ParseVersion(out), nil
}

// ParseVersion extracts the version from "git version 2.43.0.windows.1".
func ParseVersion(out string) string {
	f := strings.Fields(out)
	if len(f) >= 3 && f[0] == "git" && f[1] == "version" {
		return f[2]
	}
	return strings.TrimSpace(out)
}
