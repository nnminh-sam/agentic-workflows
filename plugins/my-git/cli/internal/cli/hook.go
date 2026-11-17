package cli

import (
	"fmt"
	"io"
	"os"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/agent/githook"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(150, newHookCmd) }

func newHookCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{
		Use:   "hook <commit-msg|pre-commit|pre-push> …",
		Short: "run a git hook check (called by the .githooks shims, not by you)",
		Long: "hook is what the .githooks shims written by `mygit hooks install` forward to. It checks only;\n" +
			"it never plans or changes anything. A hook cannot prompt, so an \"ask\" rule blocks here.",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "commit-msg <message-file>",
			Short: "check the commit message git is about to record",
			Args:  cobra.ExactArgs(1),
			RunE: func(cmd *cobra.Command, args []string) error {
				return hookRun(a, s, cmd, "commit-msg", func(cwd string, cfg *config.Resolved, repo rules.Repo) (verdict.Verdict, error) {
					raw, err := os.ReadFile(args[0])
					if err != nil {
						return verdict.Verdict{}, err
					}
					cc, _ := repo.Config("core.commentchar")
					if cc == "auto" {
						cc = "" // git picks a free char; "#" is its first choice
					}
					return githook.CommitMsg(string(raw), cc, cfg.Policy), nil
				})
			},
		},
		&cobra.Command{
			Use:   "pre-commit",
			Short: "block commits on protected branches and badly named work branches",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				return hookRun(a, s, cmd, "pre-commit", func(cwd string, cfg *config.Resolved, repo rules.Repo) (verdict.Verdict, error) {
					return githook.PreCommit(cwd, cfg.Policy, repo), nil
				})
			},
		},
		&cobra.Command{
			Use:   "pre-push <remote> <url>",
			Short: "block deleting or pushing directly to protected branches, and disallowed remotes",
			Args:  cobra.RangeArgs(1, 2),
			RunE: func(cmd *cobra.Command, args []string) error {
				remote, url := args[0], args[0]
				if len(args) > 1 {
					url = args[1]
				}
				return hookRun(a, s, cmd, "pre-push", func(cwd string, cfg *config.Resolved, repo rules.Repo) (verdict.Verdict, error) {
					in, err := io.ReadAll(a.In)
					if err != nil {
						return verdict.Verdict{}, fmt.Errorf("read stdin: %w", err)
					}
					updates, err := githook.ParseRefUpdates(string(in))
					if err != nil {
						return verdict.Verdict{}, err
					}
					return githook.PrePush(cwd, remote, url, updates, cfg.Policy, repo, a.Getenv), nil
				})
			},
		},
	)
	return cmd
}

type hookCheck func(cwd string, cfg *config.Resolved, repo rules.Repo) (verdict.Verdict, error)

// hookRun resolves the config, runs check and turns the verdict into git's
// hook contract: findings on stderr, non-zero exit on deny or ask. An
// invalid config fails closed (the rules cannot be checked), with a hint
// to fix it or bypass the hook once.
func hookRun(a *App, s *session, cmd *cobra.Command, name string, check hookCheck) error {
	cwd, err := a.Cwd()
	if err != nil {
		return err
	}
	_, cfg, err := s.config(cmd)
	if err != nil {
		a.Errorf("mygit %s: the configuration is invalid, so the rules cannot be checked:\n  %v\n"+
			"  fix it (see mygit doctor), or skip the hook once with --no-verify\n", name, err)
		return Exit(1)
	}
	v, err := check(cwd, cfg, rules.NewGitRepo(a.Runner, cwd))
	if err != nil {
		return &ExitError{Code: 1, Err: fmt.Errorf("%s hook: %w", name, err)}
	}
	if len(v.Findings) == 0 {
		return nil
	}
	printFindings(a.Err, v)
	switch v.Decision() {
	case verdict.DecDeny:
		a.Errorf("mygit %s: blocked. explain a rule: mygit rules explain <ID>\n", name)
	case verdict.DecAsk:
		a.Errorf("mygit %s: blocked. a git hook cannot ask for confirmation, so \"ask\" rules block here;\n"+
			"  if this is intended, run the command again with --no-verify\n", name)
	default:
		return nil // warnings pass
	}
	return Exit(v.Decision().ExitCode())
}
