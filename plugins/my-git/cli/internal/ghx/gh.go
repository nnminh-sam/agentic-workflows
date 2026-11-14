// Package ghx is a thin, typed wrapper over the gh CLI. mygit never calls
// the GitHub REST API directly; everything goes through gh (gh api for raw
// endpoints) via an execx.Runner.
package ghx

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// Gh runs gh commands in one repository directory.
type Gh struct {
	Runner execx.Runner
	Dir    string          // working directory ("" = current)
	Trace  func(execx.Cmd) // optional; called before every command (--verbose)
}

// New returns a Gh for dir.
func New(r execx.Runner, dir string) *Gh { return &Gh{Runner: r, Dir: dir} }

// ExitError reports a gh command that exited non-zero.
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

// Cmd builds the execx.Cmd for gh args in g.Dir without running it.
func (g *Gh) Cmd(args ...string) execx.Cmd {
	return execx.Cmd{Name: "gh", Args: args, Dir: g.Dir}
}

// Exec runs gh and returns the raw result. A non-zero exit is not an error.
func (g *Gh) Exec(ctx context.Context, args ...string) (execx.Result, error) {
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

// Run runs gh and returns an *ExitError (with stderr) on a non-zero exit.
func (g *Gh) Run(ctx context.Context, args ...string) (execx.Result, error) {
	res, err := g.Exec(ctx, args...)
	if err != nil {
		return res, err
	}
	if !res.OK() {
		return res, &ExitError{Cmd: g.Cmd(args...), Result: res}
	}
	return res, nil
}

// Query runs gh and returns its trimmed stdout.
func (g *Gh) Query(ctx context.Context, args ...string) (string, error) {
	res, err := g.Run(ctx, args...)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(res.Stdout), nil
}

// JSON runs gh (args should include --json fields) and decodes stdout into v.
func (g *Gh) JSON(ctx context.Context, args []string, v any) error {
	res, err := g.Run(ctx, args...)
	if err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(res.Stdout), v); err != nil {
		return fmt.Errorf("%s: decode json: %w", g.Cmd(args...), err)
	}
	return nil
}

// Version returns gh's version number, e.g. "2.40.1".
func (g *Gh) Version(ctx context.Context) (string, error) {
	out, err := g.Query(ctx, "--version")
	if err != nil {
		return "", err
	}
	return ParseVersion(out), nil
}

// ParseVersion extracts the version from "gh version 2.40.1 (2023-12-13)".
func ParseVersion(out string) string {
	first, _, _ := strings.Cut(strings.TrimSpace(out), "\n")
	f := strings.Fields(first)
	if len(f) >= 3 && f[0] == "gh" && f[1] == "version" {
		return f[2]
	}
	return strings.TrimSpace(first)
}
