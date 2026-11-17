// Package cli wires cobra commands. One file per command group; each group
// registers itself with register() from an init function, so groups can be
// added without touching this file. Commands stay thin: parse flags, call
// into internal/* packages through the App, print results.
package cli

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"time"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// IO is the process boundary of the CLI, injectable in tests.
type IO struct {
	In     io.Reader
	Out    io.Writer
	Err    io.Writer
	Getenv func(string) string
	Getwd  func() (string, error)
	Home   func() (string, error)
	Now    func() time.Time
	Runner execx.Runner
	// IsTerminal reports whether stdin is an interactive terminal (for y/N prompts).
	IsTerminal func() bool
	GOOS       string
}

// DefaultIO returns the real process IO.
func DefaultIO() IO {
	return IO{
		In: os.Stdin, Out: os.Stdout, Err: os.Stderr,
		Getenv: os.Getenv, Getwd: os.Getwd, Home: os.UserHomeDir,
		Now: time.Now, Runner: execx.OS{},
		IsTerminal: stdinIsTerminal,
		GOOS:       goos(),
	}
}

// GlobalOptions are the flags every command accepts (see the plan's command tree).
type GlobalOptions struct {
	Profile  string
	Ruleset  string
	Workflow string
	Rules    []string // ID=severity, repeatable
	Mode     string   // auto|manual
	Shell    string   // powershell|cmd|gitbash|bash|zsh
	Apply    string   // plan id; "" = dry-run
	Yes      bool
	JSON     bool
	Chdir    string // -C <dir>
}

// App is the dependency container handed to every command.
type App struct {
	IO
	Version string
	Opts    GlobalOptions
}

// Context returns a background context (commands may add timeouts).
func (a *App) Context() context.Context { return context.Background() }

// Cwd returns -C if given, else the process working directory.
func (a *App) Cwd() (string, error) {
	if a.Opts.Chdir != "" {
		return a.Opts.Chdir, nil
	}
	return a.Getwd()
}

// Printf writes to stdout.
func (a *App) Printf(format string, args ...any) { fmt.Fprintf(a.Out, format, args...) }

// Errorf writes to stderr.
func (a *App) Errorf(format string, args ...any) { fmt.Fprintf(a.Err, format, args...) }

// ExitError carries a specific process exit code out of a command.
// Use it for policy outcomes (1 ask, 2 deny); any other error exits 1.
type ExitError struct {
	Code int
	Err  error // may be nil when the command already printed its output
}

func (e *ExitError) Error() string {
	if e.Err == nil {
		return fmt.Sprintf("exit %d", e.Code)
	}
	return e.Err.Error()
}
func (e *ExitError) Unwrap() error { return e.Err }

// Exit returns an ExitError with no message.
func Exit(code int) error { return &ExitError{Code: code} }

type factory struct {
	order int
	fn    func(*App) *cobra.Command
}

var factories []factory

// register adds a top-level command group. order sorts help output.
func register(order int, fn func(*App) *cobra.Command) {
	factories = append(factories, factory{order, fn})
}

// NewRoot builds the command tree for app.
func NewRoot(app *App) *cobra.Command {
	root := &cobra.Command{
		Use:           "mygit",
		Short:         "git/gh policy, profiles and workflows for people and AI agents",
		Version:       app.Version,
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.SetIn(app.In)
	root.SetOut(app.Out)
	root.SetErr(app.Err)
	f := root.PersistentFlags()
	o := &app.Opts
	f.StringVar(&o.Profile, "profile", "", "use this profile for this run")
	f.StringVar(&o.Ruleset, "ruleset", "", "use this rule set for this run")
	f.StringVar(&o.Workflow, "workflow", "", "use this workflow template for this run")
	f.StringArrayVar(&o.Rules, "rule", nil, "override a rule for this run: ID=severity (repeatable)")
	f.StringVar(&o.Mode, "mode", "", "execution mode: auto|manual")
	f.StringVar(&o.Shell, "shell", "", "shell for manual mode: powershell|cmd|gitbash|bash|zsh")
	f.StringVar(&o.Apply, "apply", "", "apply the plan with this id (from a dry run)")
	f.BoolVarP(&o.Yes, "yes", "y", false, "apply without asking")
	f.BoolVar(&o.JSON, "json", false, "machine-readable JSON output")
	f.StringVarP(&o.Chdir, "chdir", "C", "", "run as if mygit was started in this directory")

	sorted := append([]factory(nil), factories...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].order < sorted[j].order })
	for _, fc := range sorted {
		root.AddCommand(fc.fn(app))
	}
	return root
}

// Main runs the CLI and returns the process exit code.
func Main(version string, args []string, io IO) int {
	app := &App{IO: io, Version: version}
	root := NewRoot(app)
	root.SetArgs(args)
	err := root.Execute()
	if err == nil {
		return 0
	}
	var ee *ExitError
	if errors.As(err, &ee) {
		if ee.Err != nil {
			fmt.Fprintln(io.Err, "mygit:", ee.Err)
		}
		return ee.Code
	}
	fmt.Fprintln(io.Err, "mygit:", err)
	return 1
}

// stdinIsTerminal reports whether stdin is an interactive terminal. A char
// device is not enough: </dev/null (and NUL on Windows) is one too.
func stdinIsTerminal() bool {
	fi, err := os.Stdin.Stat()
	if err != nil || fi.Mode()&os.ModeCharDevice == 0 {
		return false
	}
	if null, err := os.Stat(os.DevNull); err == nil && os.SameFile(fi, null) {
		return false
	}
	return true
}
