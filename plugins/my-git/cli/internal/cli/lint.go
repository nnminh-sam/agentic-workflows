package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/agent/githook"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(130, newLintCmd) }

func newLintCmd(a *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "lint",
		Short: "check a commit message, a pull request or a shell command against the active rules",
		Long: "lint runs the same rules as the agent guard and the git hooks, without changing anything.\n" +
			"Exit code: 0 ok or warnings only, 1 ask, 2 deny.",
	}
	cmd.AddCommand(newLintMsgCmd(a), newLintPRCmd(a), newLintCmdCmd(a))
	return cmd
}

func newLintMsgCmd(a *App) *cobra.Command {
	var file string
	var title bool
	cmd := &cobra.Command{
		Use:   "msg [text|-]",
		Short: "check a commit message (or, with --title, a PR title)",
		Example: `  mygit lint msg "feat(api): add retry"
  mygit lint msg -f .git/COMMIT_EDITMSG
  echo "fix: x" | mygit lint msg -`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var text string
			switch {
			case file != "" && len(args) > 0:
				return fmt.Errorf("give the message as text or with -f, not both")
			case file != "":
				b, err := os.ReadFile(file)
				if err != nil {
					return err
				}
				text = githook.CleanMessage(string(b), "#") // like a commit-msg hook sees it
			case len(args) == 0 || args[0] == "-":
				b, err := io.ReadAll(a.In)
				if err != nil {
					return fmt.Errorf("read stdin: %w", err)
				}
				text = string(b)
			default:
				text = args[0]
			}
			cfg, err := guardResolve(a, "")
			if err != nil {
				return err
			}
			c := cfg.Policy.Contract(policy.Commit)
			if title {
				return lintReport(a, rules.CheckHeader(text, c, verdict.Off))
			}
			return lintReport(a, rules.CheckMessage(text, c))
		},
	}
	cmd.Flags().StringVarP(&file, "file", "f", "", "read the message from a file (comment lines are dropped like git does)")
	cmd.Flags().BoolVar(&title, "title", false, "check the header rules only (PR titles)")
	return cmd
}

func newLintPRCmd(a *App) *cobra.Command {
	var title, bodyFile, base, head string
	cmd := &cobra.Command{
		Use:     "pr --title <title> --body-file <file|-> [--base BRANCH] [--head BRANCH]",
		Short:   "check a pull request title, body and target before gh pr create",
		Example: `  mygit lint pr --title "feat(auth): add login" --body-file pr.md --base develop/base --head develop/feat/login`,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var body []byte
			var err error
			if bodyFile == "-" {
				body, err = io.ReadAll(a.In)
			} else {
				body, err = os.ReadFile(bodyFile)
			}
			if err != nil {
				return err
			}
			cfg, err := guardResolve(a, "")
			if err != nil {
				return err
			}
			pol := cfg.Policy
			v := rules.CheckPR(title, string(body), pol.Contract(policy.GitHubCLI), pol.Contract(policy.Commit))
			if br := pol.Contract(policy.Branching); br != nil {
				if r := br.Rule("BR005"); r.Severity != verdict.Off {
					if p := rules.PRTargetProblem(base, head, br); p != "" {
						v.Add("BR005", r.Severity, p)
					}
				}
			}
			return lintReport(a, v)
		},
	}
	cmd.Flags().StringVar(&title, "title", "", "PR title")
	cmd.Flags().StringVar(&bodyFile, "body-file", "", "markdown file with the PR body ('-' for stdin)")
	cmd.Flags().StringVar(&base, "base", "", "target branch (BR005)")
	cmd.Flags().StringVar(&head, "head", "", "source branch (BR005)")
	_ = cmd.MarkFlagRequired("title")
	_ = cmd.MarkFlagRequired("body-file")
	return cmd
}

func newLintCmdCmd(a *App) *cobra.Command {
	var dialect string
	cmd := &cobra.Command{
		Use:     `cmd "<shell command>" [--dialect bash|powershell]`,
		Short:   "check a shell command the way the agent guard would",
		Example: `  mygit lint cmd 'git commit -m "Added stuff."'`,
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var d shell.Dialect
			switch strings.ToLower(dialect) {
			case "", "bash", "sh", "zsh", "gitbash":
				d = shell.Bash
			case "powershell", "pwsh":
				d = shell.PowerShell
			default:
				return fmt.Errorf("unknown dialect %q (want bash|powershell)", dialect)
			}
			cwd, err := a.Cwd()
			if err != nil {
				return err
			}
			cfg, err := guardResolve(a, cwd)
			if err != nil {
				return err
			}
			v := engine.Evaluate(engine.Request{Command: args[0], Dialect: d, Cwd: cwd}, cfg.Policy, guardEngineDeps(a))
			return lintReport(a, v)
		},
	}
	cmd.Flags().StringVar(&dialect, "dialect", "bash", "shell grammar of the command: bash|powershell")
	return cmd
}

// lintJSON is the --json output of every lint and hook check.
type lintJSON struct {
	Decision verdict.Decision  `json:"decision"`
	Findings []verdict.Finding `json:"findings"`
	Reason   string            `json:"reason,omitempty"`
}

// lintReport prints v (text or --json) and returns the decision's exit code.
func lintReport(a *App, v verdict.Verdict) error {
	d := v.Decision()
	if a.Opts.JSON {
		out := lintJSON{Decision: d, Findings: v.Findings}
		if out.Findings == nil {
			out.Findings = []verdict.Finding{}
		}
		if d != verdict.Allow {
			out.Reason = v.Reason()
		}
		b, err := json.MarshalIndent(out, "", "  ")
		if err != nil {
			return err
		}
		fmt.Fprintln(a.Out, string(b))
	} else if len(v.Findings) == 0 {
		fmt.Fprintln(a.Out, "✓ ok")
	} else {
		printFindings(a.Out, v)
	}
	if code := d.ExitCode(); code != 0 {
		return Exit(code)
	}
	return nil
}

// printFindings writes one line per finding:
//
//	✗ deny  CM003  header must be '<type>(<scope>)!: <subject>'
func printFindings(w io.Writer, v verdict.Verdict) {
	marks := map[verdict.Severity]string{verdict.Deny: "✗", verdict.Ask: "?", verdict.Warn: "!"}
	for _, f := range v.Findings {
		prefix := fmt.Sprintf("%s %-5s %-6s ", marks[f.Severity], f.Severity, f.RuleID)
		lines := strings.Split(f.Message, "\n")
		fmt.Fprintln(w, prefix+lines[0])
		for _, l := range lines[1:] {
			fmt.Fprintln(w, strings.Repeat(" ", len([]rune(prefix)))+l)
		}
	}
}
