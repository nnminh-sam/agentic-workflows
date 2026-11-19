package cli

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
)

func init() { register(140, newPlanCmd) }

func newPlanCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "plan", Short: "show and verify saved plans"}
	cmd.AddCommand(planShowCmd(a, s), planVerifyCmd(a, s))
	return cmd
}

func planShowCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "show <id>",
		Short: "print a saved plan",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, err := s.env(cmd)
			if err != nil {
				return err
			}
			p, err := e.Store().Load(args[0])
			if err != nil {
				return err
			}
			if a.Opts.JSON {
				b, err := plan.JSON(p)
				if err != nil {
					return err
				}
				a.Printf("%s\n", b)
				return nil
			}
			a.Printf("%s", plan.Text(p))
			a.Printf("  for     %s\n", app.ApplyHint(p, p.Shell))
			return nil
		},
	}
}

func planVerifyCmd(a *App, s *session) *cobra.Command {
	var out string
	cmd := &cobra.Command{
		Use:   "verify <id>",
		Short: "check a saved plan still matches the repository (exit 1 if stale); --out saves its script",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, err := s.env(cmd)
			if err != nil {
				return err
			}
			p, err := e.Store().Load(args[0])
			if err != nil {
				return err
			}
			fresh, err := plan.Fresh(cmd.Context(), p, e.State)
			if err != nil {
				return err
			}
			res := map[string]any{"id": p.ID, "fresh": fresh}
			if out != "" {
				shName := s.runOptions(e).Shell
				if p.Shell != "" && a.Opts.Shell == "" {
					shName = p.Shell
				}
				sh, err := shellfmt.ParseShell(shName)
				if err != nil {
					return err
				}
				path, sh := app.ScriptPath(out, sh)
				if _, err := p.WriteFiles(os.TempDir()); err != nil {
					return err
				}
				if err := app.WriteFileAtomic(path, []byte(plan.Manual(p, sh, true))); err != nil {
					return err
				}
				res["script"], res["shell"] = path, sh.String()
			}
			if a.Opts.JSON {
				if err := emitJSON(a.Out, res); err != nil {
					return err
				}
			} else {
				if fresh {
					a.Printf("✓ plan %s is fresh: the repository has not changed since it was planned\n", p.ID)
				} else {
					a.Printf("✗ plan %s is stale: the repository or config changed since it was planned; plan again\n", p.ID)
				}
				if path, ok := res["script"]; ok {
					a.Printf("wrote %s (%s)\n", path, res["shell"])
				}
			}
			if !fresh {
				return Exit(1)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&out, "out", "", "save the plan as a script (.ps1, .cmd or .sh)")
	return cmd
}
