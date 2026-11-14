package cli

import (
	"errors"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
)

// `mygit file write --from F --to T` is the step manual-mode scripts use for
// file writes (auto mode performs the same copy in-process).
func init() { register(990, newFileCmd) }

func newFileCmd(a *App) *cobra.Command {
	cmd := &cobra.Command{Use: "file", Short: "file steps of manual-mode plans", Hidden: true}
	var from, to string
	w := &cobra.Command{
		Use:   "write --from FILE --to FILE",
		Short: "copy a planned file into place",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if from == "" || to == "" {
				return errors.New("file write needs --from and --to")
			}
			return app.CopyFile(from, to)
		},
	}
	w.Flags().StringVar(&from, "from", "", "planned content")
	w.Flags().StringVar(&to, "to", "", "destination")
	cmd.AddCommand(w)
	return cmd
}
