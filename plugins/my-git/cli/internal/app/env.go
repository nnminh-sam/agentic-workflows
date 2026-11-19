// Package app is the shared plumbing of mygit's commands: it resolves the
// environment a command runs in (repository, remote owner, layered config),
// fingerprints the repo state plan ids depend on, turns declarative actions
// (git config, gh calls, file writes) into plan steps, and runs a built
// plan the way every mutating command does: dry run, save, then manual
// script, confirmation, --yes or --apply <id>.
//
// The package never imports internal/cli. Commands adapt their cli.App into
// Deps (see internal/cli/plumbing.go) and call into here.
package app

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sync"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
)

// Deps are the process boundaries Load reads. Nil funcs get OS defaults.
type Deps struct {
	Runner execx.Runner
	Getenv func(string) string
	Cwd    string
	Home   string
	GOOS   string
	// ReadFile must return an error wrapping fs.ErrNotExist for a missing
	// file (os.ReadFile does). Nil means os.ReadFile.
	ReadFile func(string) ([]byte, error)
	// Glob lists files matching a pattern. Nil means filepath.Glob.
	Glob func(string) ([]string, error)
	// Flags are the per-run config overrides (--profile, --rule, …).
	Flags config.Flags
}

func (d Deps) withDefaults() Deps {
	if d.Getenv == nil {
		d.Getenv = func(string) string { return "" }
	}
	if d.ReadFile == nil {
		d.ReadFile = os.ReadFile
	}
	if d.Glob == nil {
		d.Glob = filepath.Glob
	}
	return d
}

// Env is the resolved environment of one command run.
type Env struct {
	Deps
	// RepoRoot is the work-tree top level; "" outside a repository.
	RepoRoot string
	// RemoteURL and RemoteOwner describe the origin remote ("" when absent).
	RemoteURL   string
	RemoteOwner string
	Paths       config.Paths
	// Config is the resolved configuration; nil when ConfigErr is set.
	Config    *config.Resolved
	ConfigErr error
	// Git and Gh run in RepoRoot (or Cwd outside a repository).
	Git *gitx.Git
	Gh  *ghx.Gh
}

// Load finds the repository (it is fine not to be in one), the origin
// owner, and resolves the layered config. An invalid config does not fail
// Load: it is recorded in ConfigErr so commands such as doctor, config set
// and config validate still work; Require returns it.
func Load(ctx context.Context, d Deps) (*Env, error) {
	d = d.withDefaults()
	if d.Runner == nil {
		return nil, errors.New("app: no process runner")
	}
	e := &Env{Deps: d}
	probe := gitx.New(d.Runner, d.Cwd)
	if root, err := probe.RepoRoot(ctx); err == nil && root != "" {
		e.RepoRoot = filepath.FromSlash(root)
	}
	dir := e.RepoRoot
	if dir == "" {
		dir = d.Cwd
	}
	e.Git, e.Gh = gitx.New(d.Runner, dir), ghx.New(d.Runner, dir)
	if e.RepoRoot != "" {
		if u, err := e.Git.RemoteURL(ctx, "origin"); err == nil {
			e.RemoteURL = u
			if ref, err := gitx.ParseRemoteURL(u); err == nil {
				e.RemoteOwner = ref.Owner
			}
		}
	}
	e.Paths = config.ResolvePaths(d.GOOS, d.Getenv, d.Home, e.RepoRoot)
	e.Config, e.ConfigErr = config.Resolve(config.Inputs{
		Cwd: d.Cwd, RepoRoot: e.RepoRoot, RemoteOwner: e.RemoteOwner, GOOS: d.GOOS,
		Getenv: d.Getenv, Home: d.Home, ReadFile: d.ReadFile, Glob: d.Glob, Flags: d.Flags,
	})
	return e, nil
}

// InRepo reports whether the command runs inside a git work tree.
func (e *Env) InRepo() bool { return e.RepoRoot != "" }

// Require returns the resolved config, or the config error.
func (e *Env) Require() (*config.Resolved, error) {
	if e.ConfigErr != nil {
		return nil, e.ConfigErr
	}
	return e.Config, nil
}

// RequireRepo fails outside a git repository.
func (e *Env) RequireRepo() error {
	if !e.InRepo() {
		return errors.New("not inside a git repository")
	}
	return nil
}

// ProfileName is the active profile name ("" when none or config invalid).
func (e *Env) ProfileName() string {
	if e.Config == nil {
		return ""
	}
	return e.Config.ProfileName
}

// Display shortens a path for messages (repo-relative or ~/…).
func (e *Env) Display(path string) string { return config.Display(path, e.RepoRoot, e.Home) }

// ReadOptional reads a file; a missing file yields nil content and no error.
func (e *Env) ReadOptional(path string) ([]byte, error) {
	b, err := e.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	return b, err
}

// EngineDeps returns the rules engine side-effect dependencies bound to e.
func (e *Env) EngineDeps() engine.Deps {
	return engine.Deps{
		Repo:     func(dir string) rules.Repo { return rules.NewGitRepo(e.Runner, dir) },
		ReadFile: e.ReadFile,
		Env:      e.Getenv,
	}
}

// ScopePath returns the config file of a writable scope.
func (e *Env) ScopePath(s config.Scope) (string, error) {
	switch s {
	case config.ScopeGlobal:
		return e.Paths.Global, nil
	case config.ScopeProject, config.ScopeLocal:
		if !e.InRepo() {
			return "", fmt.Errorf("--%s needs a git repository", s)
		}
		if s == config.ScopeProject {
			return e.Paths.Project, nil
		}
		return e.Paths.Local, nil
	}
	return "", fmt.Errorf("scope %q cannot be written", s)
}

// Session memoizes Load for one command run. The deps func is called on
// first use, after flag parsing, so global flags are visible.
type Session struct {
	deps func() Deps
	once sync.Once
	env  *Env
	err  error
}

// NewSession returns a Session that loads its Env lazily from deps().
func NewSession(deps func() Deps) *Session { return &Session{deps: deps} }

// Env returns the memoized environment.
func (s *Session) Env(ctx context.Context) (*Env, error) {
	s.once.Do(func() { s.env, s.err = Load(ctx, s.deps()) })
	return s.env, s.err
}
