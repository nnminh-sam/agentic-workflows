package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/graph"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

func init() { register(80, newGraphCmd) }

// graphFlags are the flags of `mygit graph`.
type graphFlags struct {
	remote  string
	limit   int
	fetch   bool
	html    string
	ascii   bool
	offline bool
}

func newGraphCmd(app *App) *cobra.Command {
	var fl graphFlags
	cmd := &cobra.Command{
		Use:   "graph",
		Short: "show commits, branches, signatures and PRs as a lane graph",
		Long: "Show local and remote branches, commits, signature status, ahead/behind and open PRs.\n" +
			"Read-only. --fetch runs `git fetch --prune <remote>` first (only with --yes).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return runGraph(app, fl) },
	}
	f := cmd.Flags()
	f.StringVar(&fl.remote, "remote", "", "remote whose branches are shown as origin (default: the workflow's remote)")
	f.IntVar(&fl.limit, "limit", 50, "maximum commits to show (0 = all)")
	f.BoolVar(&fl.fetch, "fetch", false, "run git fetch --prune <remote> first (needs --yes)")
	f.StringVar(&fl.html, "html", "", "write a self-contained HTML page to this file")
	f.BoolVar(&fl.ascii, "ascii", false, "draw lanes with ASCII characters")
	f.BoolVar(&fl.offline, "offline", false, "skip GitHub (no PR data)")
	return cmd
}

func runGraph(app *App, fl graphFlags) error {
	ctx := app.Context()
	git, gh, err := graphTools(app)
	if err != nil {
		return err
	}
	wf, warn := graphWorkflow(app, git)
	remote := graphRemote(fl.remote, wf)
	if err := graphFetch(app, git, remote, fl.fetch); err != nil {
		return err
	}
	repo, err := graph.Collect(ctx, git, gh, graph.Options{Remote: remote, Limit: fl.limit, Offline: fl.offline})
	if err != nil {
		return err
	}
	if warn != "" {
		repo.Warnings = append(repo.Warnings, warn)
	}
	rows := graph.Layout(repo.Commits)
	protected := graphProtected(wf)
	if fl.html != "" {
		err := graphWriteFile(fl.html, func(w io.Writer) error {
			return graph.RenderHTML(w, repo, rows, graph.HTMLOptions{Title: "mygit graph", Protected: protected})
		})
		if err != nil {
			return err
		}
		if !app.Opts.JSON {
			app.Errorf("wrote %s\n", fl.html)
		}
	}
	if app.Opts.JSON {
		return graphJSON(app.Out, struct {
			graph.Repo
			Rows []graph.Row `json:"rows"`
		}{repo, rows})
	}
	if fl.html != "" {
		return nil
	}
	return graph.RenderText(app.Out, repo, rows, graph.TextOptions{
		ASCII: fl.ascii, Color: graphUseColor(app), Protected: protected,
	})
}

// graphTools returns git and gh bound to the working directory.
func graphTools(app *App) (*gitx.Git, *ghx.Gh, error) {
	dir, err := app.Cwd()
	if err != nil {
		return nil, nil, err
	}
	return gitx.New(app.Runner, dir), ghx.New(app.Runner, dir), nil
}

// graphResolve resolves the configuration for the repository of git.
// TODO(orchestrator): replace with the shared internal/app resolver.
func graphResolve(app *App, git *gitx.Git) (*config.Resolved, error) {
	ctx := app.Context()
	cwd, err := app.Cwd()
	if err != nil {
		return nil, err
	}
	root, _ := git.RepoRoot(ctx)
	owner, _ := git.RemoteOwner(ctx, workflow.DefaultRemote)
	home, _ := app.Home()
	o := app.Opts
	return config.Resolve(config.Inputs{
		Cwd: cwd, RepoRoot: root, RemoteOwner: owner, GOOS: app.GOOS,
		Getenv: app.Getenv, Home: home, ReadFile: os.ReadFile, Glob: filepath.Glob,
		Flags: config.Flags{
			Profile: o.Profile, Ruleset: o.Ruleset, Workflow: o.Workflow,
			Rules: o.Rules, Mode: o.Mode, Shell: o.Shell,
		},
	})
}

// graphWorkflow returns the active workflow, or nil plus a warning when the
// configuration cannot be resolved (the graph still works without it).
func graphWorkflow(app *App, git *gitx.Git) (*workflow.Compiled, string) {
	res, err := graphResolve(app, git)
	if err != nil {
		return nil, fmt.Sprintf("workflow unavailable, protected markers off: %v", err)
	}
	return res.Workflow, ""
}

func graphRemote(flag string, wf *workflow.Compiled) string {
	switch {
	case flag != "":
		return flag
	case wf != nil && wf.Remote != "":
		return wf.Remote
	}
	return workflow.DefaultRemote
}

func graphProtected(wf *workflow.Compiled) func(string) bool {
	if wf == nil {
		return nil
	}
	return wf.IsProtected
}

// graphFetch runs `git fetch --prune <remote>` when asked. Fetching writes
// refs, so it only runs with --yes; otherwise it says what it would run.
// TODO(orchestrator): route through the plan/mode flow.
func graphFetch(app *App, git *gitx.Git, remote string, fetch bool) error {
	if !fetch {
		return nil
	}
	c := git.Cmd("fetch", "--prune", remote)
	if !app.Opts.Yes {
		app.Errorf("would run: %s (updates remote-tracking refs; pass --yes to run it)\n", c)
		return nil
	}
	if _, err := git.Run(app.Context(), "fetch", "--prune", remote); err != nil {
		return fmt.Errorf("fetch: %w", err)
	}
	return nil
}

// graphUseColor enables ANSI color only for a terminal without NO_COLOR.
func graphUseColor(app *App) bool {
	if app.Getenv != nil && (app.Getenv("NO_COLOR") != "" || app.Getenv("TERM") == "dumb") {
		return false
	}
	f, ok := app.Out.(*os.File)
	if !ok {
		return false
	}
	fi, err := f.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}

// graphWriteFile renders into path.
func graphWriteFile(path string, render func(io.Writer) error) error {
	f, err := os.Create(path)
	if err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := render(f); err != nil {
		f.Close()
		return fmt.Errorf("write %s: %w", path, err)
	}
	return f.Close()
}

func graphJSON(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
