package cli

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

func init() { register(10, newInitCmd) }

func newInitCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{
		Use:   "init",
		Short: "create .mygit.json and git-ignore .mygit.local.json (plan)",
		Long: "create the project config .mygit.json (version 1 and the workflow from the global --workflow flag, " +
			"default \"default\"), add .mygit.local.json to .gitignore, and suggest a profile, rule set and workflow.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			if err := e.RequireRepo(); err != nil {
				return err
			}
			wfName := a.Opts.Workflow
			if wfName == "" {
				wfName = "default"
			}
			cat, _, err := e.WorkflowCatalog()
			if err != nil {
				return err
			}
			if _, err := workflow.Resolve(wfName, cat); err != nil {
				return fmt.Errorf("--workflow: %w", err)
			}
			if err := planInit(p, e, wfName, a.Opts.Workflow != ""); err != nil {
				return err
			}
			initSuggestions(p, e)
			return s.run(cmd, p, nil)
		},
	}
	s.addOutFlag(cmd)
	return cmd
}

// planInit records the .mygit.json and .gitignore writes.
func planInit(p *app.Planner, e *app.Env, wfName string, explicit bool) error {
	existing, err := e.ReadOptional(e.Paths.Project)
	if err != nil {
		return err
	}
	if existing == nil {
		b, err := json.MarshalIndent(map[string]any{"version": 1, "workflow": wfName}, "", "  ")
		if err != nil {
			return err
		}
		if _, err := config.Parse(b, config.ProjectFileName, config.ScopeProject); err != nil {
			return err
		}
		p.WriteFile(e.Paths.Project, string(b)+"\n", fmt.Sprintf("wrote %-22s workflow %s", config.ProjectFileName, wfName))
	} else {
		cur, _, _ := config.Get(existing, "workflow")
		if explicit && cur != wfName {
			if _, err := configEdit(p, config.ScopeProject, fmt.Sprintf("set workflow = %s in %s", wfName, config.ProjectFileName),
				setKey(config.ScopeProject, "workflow", wfName)); err != nil {
				return err
			}
		} else {
			p.Note("%s already exists", config.ProjectFileName)
		}
	}
	gi := e.RepoRoot + string(sepOf(e.GOOS)) + ".gitignore"
	old, err := e.ReadOptional(gi)
	if err != nil {
		return err
	}
	if content, changed := ensureIgnored(string(old), config.LocalFileName); changed {
		p.WriteFile(gi, content, "added "+config.LocalFileName+" to .gitignore")
	}
	return nil
}

func sepOf(goos string) rune {
	if goos == "windows" {
		return '\\'
	}
	return '/'
}

// ensureIgnored appends entry to a .gitignore unless a line already
// ignores it exactly (or with a leading slash).
func ensureIgnored(content, entry string) (string, bool) {
	for _, l := range strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n") {
		t := strings.TrimSpace(l)
		if t == entry || t == "/"+entry {
			return content, false
		}
	}
	if content != "" && !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	return content + "# mygit: personal settings (profile choice)\n" + entry + "\n", true
}

// initSuggestions adds notes about profile, rule set and workflow.
func initSuggestions(p *app.Planner, e *app.Env) {
	c := e.Config
	if c == nil {
		p.Note("config invalid: %v (mygit doctor)", e.ConfigErr)
		return
	}
	switch {
	case c.ProfileName != "":
		p.Note("profile %s (%s); apply it with: mygit profile use %s", c.ProfileName, c.ProfileReason, c.ProfileName)
	case len(c.File.Profiles) > 0:
		p.Note("no profile selected: mygit profile use <name>   (profiles: %s)", strings.Join(sortedProfileNames(c.File.Profiles), ", "))
	default:
		p.Note("no profiles yet: mygit profile add <name> --from-current")
	}
	p.Note("rule set %s (%s); see mygit rules sets", c.RulesetName, c.RulesetReason)
	p.Note("templates: mygit workflow list; next: mygit repo bootstrap, mygit hooks install")
}
