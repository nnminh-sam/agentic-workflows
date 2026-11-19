package cli

// Shared plumbing for mutating commands. A command group creates one
// session in its factory and uses it from every subcommand:
//
//	s := newSession(a)
//	cmd := &cobra.Command{RunE: func(cmd *cobra.Command, args []string) error {
//		p, env, err := s.planner(cmd, args) // env loaded once, plan meta filled
//		if err != nil { return err }
//		p.Git("git config user.name  Octo", "config", "--local", "user.name", "Octo")
//		p.WriteFile(env.Paths.Local, content, "wrote .mygit.local.json")
//		return s.run(cmd, p, nil) // dry run → save → manual / confirm / --yes / --apply
//	}}
//	s.addOutFlag(cmd) // --out for manual mode scripts

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
)

// session memoizes the command environment for one App and runs plans.
type session struct {
	a *App
	*app.Session
	outFile string
}

// newSession returns a session whose Env is loaded on first use (after
// flag parsing) from a's IO and global flags.
func newSession(a *App) *session {
	s := &session{a: a}
	s.Session = app.NewSession(func() app.Deps { return depsOf(a) })
	return s
}

// depsOf adapts the App to app.Deps for a's working directory.
func depsOf(a *App) app.Deps {
	cwd, _ := a.Cwd()
	return depsFor(a, cwd)
}

// depsFor adapts the App to app.Deps for cwd.
func depsFor(a *App, cwd string) app.Deps {
	home := ""
	if a.Home != nil {
		home, _ = a.Home()
	}
	o := a.Opts
	return app.Deps{
		Runner: a.Runner, Getenv: a.Getenv, Cwd: cwd, Home: home, GOOS: a.GOOS,
		Flags: config.Flags{Profile: o.Profile, Ruleset: o.Ruleset, Workflow: o.Workflow,
			Rules: o.Rules, Mode: o.Mode, Shell: o.Shell},
	}
}

// loadEnvFor loads an app.Env for cwd ("" = -C / process cwd).
func loadEnvFor(a *App, cwd string) (*app.Env, error) {
	if cwd == "" {
		var err error
		if cwd, err = a.Cwd(); err != nil {
			return nil, err
		}
	}
	return app.Load(a.Context(), depsFor(a, cwd))
}

// env returns the memoized environment (config errors are in env.ConfigErr).
func (s *session) env(cmd *cobra.Command) (*app.Env, error) {
	return s.Env(cmd.Context())
}

// config returns the environment and its valid resolved config.
func (s *session) config(cmd *cobra.Command) (*app.Env, *config.Resolved, error) {
	e, err := s.env(cmd)
	if err != nil {
		return nil, nil, err
	}
	c, err := e.Require()
	return e, c, err
}

// planner starts a plan for this command invocation.
func (s *session) planner(cmd *cobra.Command, args []string) (*app.Planner, *app.Env, error) {
	e, err := s.env(cmd)
	if err != nil {
		return nil, nil, err
	}
	return e.NewPlanner(planArgv(cmd, args)), e, nil
}

// addOutFlag adds --out (save the manual-mode script) to cmd.
func (s *session) addOutFlag(cmd *cobra.Command) {
	cmd.Flags().StringVar(&s.outFile, "out", "", "manual mode: save the script to this file (.ps1, .cmd or .sh)")
}

// runOptions resolves the execution settings of this run.
func (s *session) runOptions(e *app.Env) app.RunOptions {
	o := s.a.Opts
	ro := app.RunOptions{Mode: o.Mode, Shell: o.Shell, ApplyID: o.Apply, Yes: o.Yes, JSON: o.JSON,
		OutFile: s.outFile, TempDir: os.TempDir()}
	if s.a.IsTerminal != nil {
		ro.Interactive = s.a.IsTerminal()
	}
	if e != nil && e.Config != nil {
		ro.Mode, ro.Shell = e.Config.Execution.Mode, e.Config.Execution.Shell
	}
	if ro.Mode == "" {
		ro.Mode = "auto"
	}
	if ro.Shell == "" {
		ro.Shell = "bash"
		if s.a.GOOS == "windows" {
			ro.Shell = "powershell"
		}
	}
	if ro.OutFile != "" {
		ro.Mode = "manual"
	}
	return ro
}

// run builds p and executes it through the shared plan runner. after (may
// be nil) prints extra lines once the plan was applied.
func (s *session) run(cmd *cobra.Command, p *app.Planner, after func(io.Writer)) error {
	return s.runExtra(cmd, p, after, nil)
}

// runExtra is run with command-specific data for the JSON outcome.
func (s *session) runExtra(cmd *cobra.Command, p *app.Planner, after func(io.Writer), extra map[string]any) error {
	built, err := p.Build(cmd.Context())
	if err != nil {
		return err
	}
	return s.execute(cmd, p.Env, built, after, extra)
}

// execute runs an already built plan.
func (s *session) execute(cmd *cobra.Command, e *app.Env, p *plan.Plan, after func(io.Writer), extra map[string]any) error {
	x := &app.Executor{Env: e, In: s.a.In, Out: s.a.Out, Err: s.a.Err, After: after}
	ro := s.runOptions(e)
	ro.Extra = extra
	_, err := x.Run(cmd.Context(), p, ro)
	if errors.Is(err, app.ErrStepFailed) {
		return &ExitError{Code: 1, Err: err}
	}
	return err
}

// planArgv is the mygit argv a plan is made for: the command path, the
// positional args, the command's own changed flags and the global flags
// that change what is planned (--profile, --ruleset, --workflow, --rule).
// Execution flags (--apply, --yes, --json, --mode, --shell, --out, -C) are
// left out so the dry run and the --apply run produce the same plan id.
func planArgv(cmd *cobra.Command, args []string) []string {
	argv := strings.Fields(cmd.CommandPath())[1:]
	argv = append(argv, args...)
	skip := map[string]bool{"apply": true, "yes": true, "json": true, "mode": true, "shell": true, "out": true, "chdir": true, "help": true}
	var flags []string
	visit := func(f *pflag.Flag) {
		if skip[f.Name] || !f.Changed {
			return
		}
		if sv, ok := f.Value.(pflag.SliceValue); ok {
			for _, v := range sv.GetSlice() {
				flags = append(flags, "--"+f.Name+"="+v)
			}
			return
		}
		if f.Value.Type() == "bool" && f.Value.String() == "true" {
			flags = append(flags, "--"+f.Name)
			return
		}
		flags = append(flags, "--"+f.Name+"="+f.Value.String())
	}
	cmd.Flags().VisitAll(visit)
	sort.Strings(flags)
	return append(argv, flags...)
}

// scopeFlags are the --global/--project/--local switches of a command.
type scopeFlags struct {
	global, project, local bool
	allowed                []config.Scope
}

// addScopeFlags registers the switches for the allowed scopes.
func addScopeFlags(cmd *cobra.Command, allowed ...config.Scope) *scopeFlags {
	f := &scopeFlags{allowed: allowed}
	for _, sc := range allowed {
		switch sc {
		case config.ScopeGlobal:
			cmd.Flags().BoolVar(&f.global, "global", false, "write the global config (~/.config/mygit/config.json)")
		case config.ScopeProject:
			cmd.Flags().BoolVar(&f.project, "project", false, "write the project config (.mygit.json, committed)")
		case config.ScopeLocal:
			cmd.Flags().BoolVar(&f.local, "local", false, "write the project-local config (.mygit.local.json, git-ignored)")
		}
	}
	return f
}

// scope returns the chosen scope, or def when none was given.
func (f *scopeFlags) scope(def config.Scope) (config.Scope, error) {
	var picked []config.Scope
	if f.global {
		picked = append(picked, config.ScopeGlobal)
	}
	if f.project {
		picked = append(picked, config.ScopeProject)
	}
	if f.local {
		picked = append(picked, config.ScopeLocal)
	}
	switch len(picked) {
	case 0:
		return def, nil
	case 1:
		return picked[0], nil
	}
	return "", errors.New("choose one of --global, --project, --local")
}

// configEdit records a write of a config file after applying edits to its
// current content. Nothing is recorded when the content does not change;
// changed reports whether a write step was added.
func configEdit(p *app.Planner, scope config.Scope, description string, edits ...func([]byte) ([]byte, error)) (changed bool, err error) {
	path, err := p.Env.ScopePath(scope)
	if err != nil {
		return false, err
	}
	old, err := p.Env.ReadOptional(path)
	if err != nil {
		return false, err
	}
	cur := old
	for _, ed := range edits {
		if cur, err = ed(cur); err != nil {
			return false, fmt.Errorf("%s: %w", p.Env.Display(path), err)
		}
	}
	if old != nil && bytes.Equal(bytes.TrimSpace(old), bytes.TrimSpace(cur)) {
		return false, nil
	}
	if description == "" {
		description = "wrote " + p.Env.Display(path)
	}
	p.WriteFile(path, string(cur), description)
	return true, nil
}

// setKey returns an edit that sets a dotted key in a scope's file.
func setKey(scope config.Scope, key string, value any) func([]byte) ([]byte, error) {
	return func(b []byte) ([]byte, error) { return config.Set(b, scope, key, value) }
}

// unsetKey returns an edit that removes a dotted key.
func unsetKey(scope config.Scope, key string) func([]byte) ([]byte, error) {
	return func(b []byte) ([]byte, error) { return config.Unset(b, scope, key) }
}

// emitJSON prints v as one indented JSON object.
func emitJSON(w io.Writer, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(w, string(b))
	return err
}

// mark renders a check result.
func mark(ok bool) string {
	if ok {
		return "✓"
	}
	return "✗"
}

// compactJSON renders a value on one line (strings unquoted).
func compactJSON(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	return string(b)
}
