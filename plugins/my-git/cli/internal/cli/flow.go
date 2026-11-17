package cli

import (
	"errors"
	"io"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/graph"
)

func init() { register(81, newFlowCmd) }

// flowFlags are the flags of `mygit flow`.
type flowFlags struct {
	fetch   bool
	html    string
	ascii   bool
	offline bool
}

func newFlowCmd(app *App) *cobra.Command {
	s := newSession(app)
	var fl flowFlags
	cmd := &cobra.Command{
		Use:   "flow",
		Short: "show where each branch is in the workflow and the next command",
		Long: "Place every work branch in the active workflow template (ahead of its base, pushed,\n" +
			"PR state) and name the one command that moves it forward. Read-only.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error { return runFlow(app, s, cmd, args, fl) },
	}
	f := cmd.Flags()
	f.BoolVar(&fl.fetch, "fetch", false, "run git fetch --prune <remote> first (plan)")
	f.StringVar(&fl.html, "html", "", "write a self-contained HTML page to this file")
	f.BoolVar(&fl.ascii, "ascii", false, "plain ASCII symbols")
	f.BoolVar(&fl.offline, "offline", false, "skip GitHub (no PR data)")
	s.addOutFlag(cmd)
	return cmd
}

func runFlow(app *App, s *session, cmd *cobra.Command, args []string, fl flowFlags) error {
	ctx := cmd.Context()
	env, res, err := s.config(cmd)
	if err != nil {
		return err
	}
	wf := res.Workflow
	if wf == nil {
		return errors.New("no workflow template is active")
	}
	if fl.fetch {
		applied, err := graphFetch(s, cmd, args, env, wf.Remote)
		if err != nil || !applied {
			return err
		}
	}
	// The flow only needs branch tips and PRs, not history: limit the log.
	repo, err := graph.Collect(ctx, env.Git, env.Gh, graph.Options{
		Remote: wf.Remote, Limit: 1, Offline: fl.offline, MergedPRs: true,
	})
	if err != nil {
		return err
	}
	flow, err := graph.CollectFlow(ctx, env.Git, wf, repo)
	if err != nil {
		return err
	}
	if fl.html != "" {
		if err := graphWriteFile(fl.html, func(w io.Writer) error {
			return graph.RenderFlowHTML(w, flow, "mygit flow")
		}); err != nil {
			return err
		}
		if !app.Opts.JSON {
			app.Errorf("wrote %s\n", fl.html)
		}
	}
	if app.Opts.JSON {
		return emitJSON(app.Out, flow)
	}
	if fl.html != "" {
		return nil
	}
	return graph.RenderFlowText(app.Out, flow, graph.FlowTextOptions{ASCII: fl.ascii, Color: graphUseColor(app)})
}
