package cli

import (
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
)

func init() { register(20, newDoctorCmd) }

func newDoctorCmd(a *App) *cobra.Command {
	s := newSession(a)
	return &cobra.Command{
		Use:   "doctor",
		Short: "check git/gh, config, identity, signing, hooks and shell (exit 1 on ✗)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, err := s.env(cmd)
			if err != nil {
				return err
			}
			checks := app.Diagnose(cmd.Context(), e, nil)
			ok := true
			for _, c := range checks {
				ok = ok && c.Level != app.LevelFail
			}
			if a.Opts.JSON {
				if err := emitJSON(a.Out, map[string]any{"ok": ok, "checks": checks}); err != nil {
					return err
				}
			} else {
				for _, c := range checks {
					line := c.Mark() + " " + c.Detail
					if c.File != "" {
						loc := c.File
						if c.Field != "" {
							loc += "  field " + c.Field
						}
						line += "\n    " + strings.TrimSpace(loc)
					}
					a.Printf("%s\n", line)
				}
			}
			if !ok {
				return Exit(1)
			}
			return nil
		},
	}
}
