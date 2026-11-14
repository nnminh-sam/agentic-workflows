package app

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
)

// Planner is a plan.Builder bound to an Env: git and gh steps run in the
// repository, file writes become `mygit file write` steps, and Build
// fingerprints the Env's state.
type Planner struct {
	*plan.Builder
	Env   *Env
	names fileNames
	// writes counts recorded write steps (for "nothing to do" checks).
	writes int
}

// NewPlanner starts a plan for the mygit invocation argv (without the
// program name, e.g. ["profile", "use", "work"]). Mode, shell, profile,
// rule set and workflow come from the resolved config when it is valid.
func (e *Env) NewPlanner(argv []string) *Planner {
	m := plan.Meta{Command: argv}
	if c := e.Config; c != nil {
		m.Profile, m.Ruleset, m.Workflow = c.ProfileName, c.RulesetName, c.WorkflowName
		m.Mode, m.Shell = c.Execution.Mode, c.Execution.Shell
	}
	return &Planner{Builder: plan.NewBuilder(e.Runner, m), Env: e}
}

// Writes returns the number of write steps recorded so far.
func (p *Planner) Writes() int { return p.writes }

// Git records a git write step in the repository directory.
func (p *Planner) Git(description string, args ...string) {
	p.write(p.Env.Git.Cmd(args...), description)
}

// Gh records a gh write step in the repository directory.
func (p *Planner) Gh(description string, args ...string) {
	p.write(p.Env.Gh.Cmd(args...), description)
}

// Cmd records an arbitrary write step.
func (p *Planner) Cmd(c execx.Cmd, description string) { p.write(c, description) }

func (p *Planner) write(c execx.Cmd, description string) {
	p.Write(c, description)
	p.writes++
}

// WriteFile records writing content to path (absolute). The plan shows it
// as `mygit file write --from <base> --to path`.
func (p *Planner) WriteFile(path, content, description string) {
	from := p.File(p.names.unique(strings.TrimLeft(filepath.Base(path), ".")), content)
	p.write(fileWriteCmd(from, path), description)
}

// Build fingerprints the Env's state and returns the plan with its id.
func (p *Planner) Build(ctx context.Context) (*plan.Plan, error) {
	return p.Builder.Build(ctx, p.Env.State)
}

// Action is one declarative change, as returned by profile.ApplySteps or
// sign.SetupSteps: Kind "git-config" (args of git), "gh" (args of gh) or
// "write-file" (args [path, content]).
type Action struct {
	Kind        string
	Args        []string
	Description string
}

// Action kinds (the same strings as profile.Kind*).
const (
	ActGit       = profile.KindGitConfig
	ActGh        = profile.KindGH
	ActWriteFile = profile.KindWriteFile
)

// FromProfileSteps converts profile.ApplySteps output into actions.
func FromProfileSteps(steps []profile.Step) []Action {
	out := make([]Action, len(steps))
	for i, s := range steps {
		out[i] = Action{Kind: s.Kind, Args: s.Args, Description: s.Description}
	}
	return out
}

// Add records actions as write steps. Git config descriptions are
// rewritten into the aligned "git config <key>  <value>" form.
func (p *Planner) Add(acts []Action) error {
	for _, a := range acts {
		switch a.Kind {
		case ActGit:
			p.Git(GitDescription(a.Args, a.Description), a.Args...)
		case ActGh:
			p.Gh(a.Description, a.Args...)
		case ActWriteFile:
			if len(a.Args) != 2 {
				return fmt.Errorf("write-file action needs [path, content], got %d args", len(a.Args))
			}
			desc := a.Description
			if desc == "" {
				desc = "wrote " + p.Env.Display(a.Args[0])
			}
			p.WriteFile(a.Args[0], a.Args[1], desc)
		default:
			return fmt.Errorf("unknown action kind %q", a.Kind)
		}
	}
	return nil
}

// GitDescription renders `git config [--scope] key value` as an aligned
// "git config key  value" line; other git args keep fallback.
func GitDescription(args []string, fallback string) string {
	if len(args) >= 3 && args[0] == "config" {
		rest := args[1:]
		if strings.HasPrefix(rest[0], "--") && len(rest) >= 3 {
			rest = rest[1:]
		}
		if len(rest) == 2 {
			return fmt.Sprintf("%-28s %s", "git config "+rest[0], rest[1])
		}
	}
	if fallback != "" {
		return fallback
	}
	return "git " + strings.Join(args, " ")
}

// FromSignSteps converts sign.SetupSteps output into actions.
func FromSignSteps(steps []sign.Step) []Action {
	out := make([]Action, len(steps))
	for i, s := range steps {
		out[i] = Action{Kind: s.Kind, Args: s.Args, Description: s.Description}
	}
	return out
}
