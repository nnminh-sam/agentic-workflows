package cli

import (
	"fmt"
	"io"
	"os"

	"github.com/spf13/cobra"

	mgapp "github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
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
	s := newSession(app)
	var fl graphFlags
	cmd := &cobra.Command{
		Use:   "graph",
		Short: "show commits, branches, signatures and PRs as a lane graph",
		Long: "Show local and remote branches, commits, signature status, ahead/behind and open PRs.\n" +
			"Read-only. --fetch runs `git fetch --prune <remote>` first (plan).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error { return runGraph(app, s, cmd, args, fl) },
	}
	f := cmd.Flags()
	f.StringVar(&fl.remote, "remote", "", "remote whose branches are shown as origin (default: the workflow's remote)")
	f.IntVar(&fl.limit, "limit", 50, "maximum commits to show (0 = all)")
	f.BoolVar(&fl.fetch, "fetch", false, "run git fetch --prune <remote> first (plan)")
	f.StringVar(&fl.html, "html", "", "write a self-contained HTML page to this file")
	f.BoolVar(&fl.ascii, "ascii", false, "draw lanes with ASCII characters")
	f.BoolVar(&fl.offline, "offline", false, "skip GitHub (no PR data)")
	s.addOutFlag(cmd)
	return cmd
}

func runGraph(app *App, s *session, cmd *cobra.Command, args []string, fl graphFlags) error {
	ctx := cmd.Context()
	env, err := s.env(cmd)
	if err != nil {
		return err
	}
	wf, warn := graphWorkflow(env)
	remote := graphRemote(fl.remote, wf)
	if fl.fetch {
		applied, err := graphFetch(s, cmd, args, env, remote)
		if err != nil || !applied {
			return err
		}
	}
	repo, err := graph.Collect(ctx, env.Git, env.Gh, graph.Options{Remote: remote, Limit: fl.limit, Offline: fl.offline})
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
		return emitJSON(app.Out, struct {
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

// graphWorkflow returns the active workflow, or nil plus a warning when the
// configuration cannot be resolved (the graph still works without it).
func graphWorkflow(env *mgapp.Env) (*workflow.Compiled, string) {
	res, err := env.Require()
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

// graphFetch plans and runs `git fetch --prune <remote>`. Fetching writes
// remote-tracking refs, so it goes through the shared plan runner; it reports
// whether the fetch was applied and the caller should proceed to render.
func graphFetch(s *session, cmd *cobra.Command, args []string, env *mgapp.Env, remote string) (bool, error) {
	if err := env.RequireRepo(); err != nil {
		return false, err
	}
	p := env.NewPlanner(planArgv(cmd, args))
	p.Git("fetch "+remote, "fetch", "--prune", remote)
	built, err := p.Build(cmd.Context())
	if err != nil {
		return false, err
	}
	ro := s.runOptions(env)
	applied := false
	ro.JSON = false
	out := s.a.Out
	if s.a.Opts.JSON {
		applied = ro.Yes || ro.ApplyID != ""
		if !applied {
			ro.JSON = true
		} else {
			out = s.a.Err
		}
	}
	x := &mgapp.Executor{Env: env, In: s.a.In, Out: out, Err: s.a.Err}
	res, err := x.Run(cmd.Context(), built, ro)
	if err != nil {
		return false, err
	}
	return res.Status == mgapp.StatusApplied, nil
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
