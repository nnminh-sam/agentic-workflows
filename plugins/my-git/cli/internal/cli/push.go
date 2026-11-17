package cli

import (
	"errors"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
)

func init() { register(63, newPushCmd) }

func newPushCmd(a *App) *cobra.Command {
	s := newSession(a)
	var upstream bool
	cmd := &cobra.Command{
		Use:   "push [--upstream]",
		Short: "push the current branch to the workflow remote (never forced)",
		Long: "push plans `git push [-u] <remote> <current-branch>` (the workflow's remote, origin by default) with the profile's credential pinned\n" +
			"(-c core.sshCommand for an ssh key), checks it against the rules (ID003, ID004, BR002), and\n" +
			"verifies the signatures of the commits it would send when the profile signs commits (ID006).\n" +
			"It shows ahead/behind against <remote>/<branch>, adds -u for a branch with no remote counterpart, refuses a detached HEAD\n" +
			"and never forces.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, env, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			if _, err := env.Require(); err != nil {
				return err
			}
			res, err := app.PlanPush(cmd.Context(), p, app.PushOptions{Upstream: upstream})
			if err != nil {
				return err
			}
			extra := map[string]any{"push": res}
			if !res.Denied {
				return s.runExtra(cmd, p, nil, extra)
			}
			built, err := p.Build(cmd.Context())
			if err != nil {
				return err
			}
			return pushDenied(a, built, extra)
		},
	}
	cmd.Flags().BoolVarP(&upstream, "upstream", "u", false, "set the upstream (push -u; added automatically for a branch not on the remote yet)")
	s.addOutFlag(cmd)
	return cmd
}

// pushDenied prints the plan with its failed checks and exits 2; nothing is saved or run.
func pushDenied(a *App, p *plan.Plan, extra map[string]any) error {
	if a.Opts.JSON {
		if err := emitJSON(a.Out, map[string]any{"status": "denied", "plan": p, "extra": extra}); err != nil {
			return err
		}
		return Exit(2)
	}
	fmt.Fprint(a.Out, plan.Text(p))
	return &ExitError{Code: 2, Err: errors.New("push denied by the rules above")}
}
