package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

func init() { register(60, newWorkflowCmd) }

func newWorkflowCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "workflow", Short: "git workflow templates: branches, transitions, protection"}
	cmd.AddCommand(workflowListCmd(a, s), workflowShowCmd(a, s), workflowUseCmd(s), workflowNewCmd(s),
		workflowValidateCmd(a, s))
	return cmd
}

func workflowListCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "list workflow templates (* = active)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			rows, err := e.Workflows()
			if err != nil {
				return err
			}
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"active": c.WorkflowName, "reason": c.WorkflowReason, "workflows": rows})
			}
			for _, r := range rows {
				star := " "
				if r.Active {
					star = "*"
				}
				line := fmt.Sprintf("%s %-16s  %s", star, r.Name, r.Origin)
				if len(r.Shadows) > 0 {
					line += "   (shadows " + strings.Join(r.Shadows, ", ") + ")"
				}
				a.Printf("%s\n", line)
			}
			a.Printf("active: %s (%s)\n", c.WorkflowName, c.WorkflowReason)
			return nil
		},
	}
}

// compileWorkflow resolves and compiles a named template of e's catalog.
func compileWorkflow(e *app.Env, c *config.Resolved, name string) (*workflow.Compiled, error) {
	if name == c.WorkflowName {
		return c.Workflow, nil
	}
	cat, _, err := e.WorkflowCatalog()
	if err != nil {
		return nil, err
	}
	res, err := workflow.Resolve(name, cat)
	if err != nil {
		return nil, err
	}
	return workflow.Compile(res, workflow.CompileOptions{CommitTypes: c.File.Commit.Types})
}

func workflowShowCmd(a *App, s *session) *cobra.Command {
	var origin bool
	cmd := &cobra.Command{
		Use:   "show [name]",
		Short: "print a workflow template (default: the active one)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			name := c.WorkflowName
			if len(args) == 1 {
				name = args[0]
			}
			wf, err := compileWorkflow(e, c, name)
			if err != nil {
				return err
			}
			if a.Opts.JSON {
				out := map[string]any{"name": wf.Template.Name, "origin": wf.Origin, "chain": wf.Chain,
					"active": name == c.WorkflowName, "template": wf.Template}
				if name == c.WorkflowName {
					out["reason"] = c.WorkflowReason
				}
				return emitJSON(a.Out, out)
			}
			writeWorkflow(a.Out, wf)
			if origin {
				a.Printf("  chain     %s\n", strings.Join(wf.Chain, " ← "))
				if name == c.WorkflowName {
					a.Printf("  selected  %s\n", c.WorkflowReason)
				}
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&origin, "origin", false, "show the extends chain and why the template is active")
	return cmd
}

// workflowHeader renders "(origin ← extends parent ← …)".
func workflowHeader(wf *workflow.Compiled) string {
	parts := []string{wf.Origin}
	for _, link := range wf.Chain[min(1, len(wf.Chain)):] {
		n, _, _ := strings.Cut(link, " (")
		parts = append(parts, "extends "+n)
	}
	return fmt.Sprintf("workflow %s   (%s)", wf.Template.Name, strings.Join(parts, " ← "))
}

// writeWorkflow prints the template like the plan's `mygit workflow show`.
func writeWorkflow(w io.Writer, wf *workflow.Compiled) {
	t := wf.Template
	fmt.Fprintln(w, workflowHeader(wf))
	keys := make([]string, 0, len(t.Branches))
	for k := range t.Branches {
		keys = append(keys, k)
	}
	rank := func(k string) int {
		switch k {
		case workflow.Production:
			return 0
		case workflow.Staging:
			return 1
		}
		return 2
	}
	sort.Slice(keys, func(i, j int) bool {
		if rank(keys[i]) != rank(keys[j]) {
			return rank(keys[i]) < rank(keys[j])
		}
		return keys[i] < keys[j]
	})
	kw := len(workflow.WorkKey)
	for _, k := range keys {
		kw = max(kw, len(k))
	}
	for _, tr := range t.Transitions {
		kw = max(kw, len(tr.ID))
	}
	nw := len(t.Work.Pattern)
	for _, k := range keys {
		nw = max(nw, len(t.Branches[k].Name))
	}
	for _, k := range keys {
		b := t.Branches[k]
		var flags []string
		if b.Protected {
			flags = append(flags, "protected")
		}
		if b.GithubDefault {
			flags = append(flags, "GitHub default")
		}
		fmt.Fprintln(w, strings.TrimRight(fmt.Sprintf("  %-*s  %-*s  %s", kw, k, nw, b.Name, strings.Join(flags, " · ")), " "))
	}
	work := ""
	if t.Work.From != "" {
		work = "from " + t.Work.From
	}
	fmt.Fprintln(w, strings.TrimRight(fmt.Sprintf("  %-*s  %-*s  %s", kw, workflow.WorkKey, nw, t.Work.Pattern, work), " "))
	fromW := 0
	toW := 0
	for _, tr := range t.Transitions {
		fromW = max(fromW, len(tr.From))
		toW = max(toW, len(strings.Join(tr.To, ", ")))
	}
	for _, tr := range t.Transitions {
		fmt.Fprintf(w, "  %-*s  %-*s → %-*s  %s\n", kw, tr.ID, fromW, tr.From, toW, strings.Join(tr.To, ", "), transitionDetail(tr))
	}
	fmt.Fprintf(w, "  %-*s  %s\n", kw, "protect", protectionLine(t.Protection))
}

func transitionDetail(tr workflow.Transition) string {
	var parts []string
	switch tr.Via {
	case "pr":
		parts = append(parts, "PR")
		if tr.MergeMethod != "" {
			parts = append(parts, tr.MergeMethod)
		}
		if tr.DeleteHead {
			parts = append(parts, "delete head")
		}
	case "":
	default:
		parts = append(parts, tr.Via)
	}
	if tr.Tag != "" {
		parts = append(parts, "tag "+tr.Tag)
	}
	if tr.Work != "" {
		parts = append(parts, "work "+tr.Work)
	}
	return strings.Join(parts, " · ")
}

func protectionLine(p workflow.Protection) string {
	var parts []string
	if p.RequirePR {
		parts = append(parts, "PR required")
	}
	switch p.Approvals {
	case 0:
	case 1:
		parts = append(parts, "1 approval")
	default:
		parts = append(parts, fmt.Sprintf("%d approvals", p.Approvals))
	}
	if p.RequireSigned {
		parts = append(parts, "signed commits")
	}
	if p.LinearHistory {
		parts = append(parts, "linear history")
	}
	if !p.ForcePush {
		parts = append(parts, "no force-push")
	}
	if !p.Deletion {
		parts = append(parts, "no deletion")
	}
	return strings.Join(parts, " · ")
}

func workflowUseCmd(s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "use <name>",
		Short: "select a workflow template (plan; default --project)",
		Args:  cobra.ExactArgs(1),
	}
	sf := addScopeFlags(cmd, config.ScopeGlobal, config.ScopeProject, config.ScopeLocal)
	s.addOutFlag(cmd)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		p, e, err := s.planner(cmd, args)
		if err != nil {
			return err
		}
		c, err := e.Require()
		if err != nil {
			return err
		}
		def := config.ScopeGlobal
		if e.InRepo() {
			def = config.ScopeProject
		}
		scope, err := sf.scope(def)
		if err != nil {
			return err
		}
		wf, err := compileWorkflow(e, c, args[0])
		if err != nil {
			return err
		}
		key := "workflow"
		if scope == config.ScopeGlobal {
			key = "defaultWorkflow"
		}
		if _, err := configEdit(p, scope, fmt.Sprintf("%s = %s  (%s config)", key, args[0], scope), setKey(scope, key, args[0])); err != nil {
			return err
		}
		if e.InRepo() {
			for _, b := range []string{wf.Production, wf.Staging} {
				if b == "" {
					continue
				}
				ok, err := e.Git.BranchExists(cmd.Context(), b)
				if err == nil && !ok {
					p.Note("branch %s does not exist yet: mygit repo bootstrap, or git branch %s", b, b)
				}
			}
			if c.Workflow != nil && c.Workflow.Template.Name != wf.Template.Name {
				p.Note("GitHub ruleset and default branch: run mygit repo protect after switching")
			}
		}
		return s.run(cmd, p, nil)
	}
	return cmd
}

func workflowNewCmd(s *session) *cobra.Command {
	var from string
	cmd := &cobra.Command{
		Use:   "new <name>",
		Short: "create a template that extends another (plan; default --project in a repo)",
		Args:  cobra.ExactArgs(1),
	}
	sf := addScopeFlags(cmd, config.ScopeGlobal, config.ScopeProject)
	s.addOutFlag(cmd)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		p, e, err := s.planner(cmd, args)
		if err != nil {
			return err
		}
		c, err := e.Require()
		if err != nil {
			return err
		}
		def := config.ScopeGlobal
		if e.InRepo() {
			def = config.ScopeProject
		}
		scope, err := sf.scope(def)
		if err != nil {
			return err
		}
		name := args[0]
		if strings.ContainsAny(name, `/\ `) || name == "" {
			return fmt.Errorf("invalid template name %q", name)
		}
		parent, err := compileWorkflow(e, c, from)
		if err != nil {
			return fmt.Errorf("--from %s: %w", from, err)
		}
		dir := e.Paths.GlobalWorkflows
		if scope == config.ScopeProject {
			if err := e.RequireRepo(); err != nil {
				return err
			}
			dir = e.Paths.ProjectWorkflows
		}
		path := filepath.Join(dir, name+".json")
		if old, err := e.ReadOptional(path); err != nil {
			return err
		} else if old != nil {
			return fmt.Errorf("%s already exists", e.Display(path))
		}
		doc := map[string]any{
			"name":        name,
			"extends":     from,
			"description": "custom template based on " + parent.Template.Name + "; change only what differs",
		}
		b, err := json.MarshalIndent(doc, "", "  ")
		if err != nil {
			return err
		}
		p.WriteFile(path, string(b)+"\n", "wrote "+e.Display(path))
		p.Note("edit it, then: mygit workflow validate %s && mygit workflow use %s", e.Display(path), name)
		return s.run(cmd, p, nil)
	}
	cmd.Flags().StringVar(&from, "from", "default", "template to extend")
	return cmd
}

func workflowValidateCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "validate [file]",
		Short: "validate a template file, or every known template (exit 1 on errors)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			cat, _, err := e.WorkflowCatalog()
			if err != nil {
				return err
			}
			names := cat.Names()
			if len(args) == 1 {
				data, err := os.ReadFile(args[0])
				if err != nil {
					return err
				}
				base := strings.TrimSuffix(filepath.Base(args[0]), ".json")
				if err := cat.AddJSON(data, args[0], base); err != nil {
					return reportValidation(a, []wfCheck{{Name: base, Error: err.Error()}})
				}
				var head struct {
					Name string `json:"name"`
				}
				_ = json.Unmarshal(data, &head)
				if head.Name == "" {
					head.Name = base
				}
				names = []string{head.Name}
			}
			var checks []wfCheck
			for _, n := range names {
				chk := wfCheck{Name: n, OK: true}
				res, err := workflow.Resolve(n, cat)
				if err == nil {
					chk.Origin = res.Origin
					_, err = workflow.Compile(res, workflow.CompileOptions{CommitTypes: c.File.Commit.Types})
				}
				if err != nil {
					chk.OK, chk.Error = false, err.Error()
				}
				checks = append(checks, chk)
			}
			return reportValidation(a, checks)
		},
	}
}

type wfCheck struct {
	Name   string `json:"name"`
	Origin string `json:"origin,omitempty"`
	OK     bool   `json:"ok"`
	Error  string `json:"error,omitempty"`
}

func reportValidation(a *App, checks []wfCheck) error {
	ok := true
	for _, c := range checks {
		ok = ok && c.OK
	}
	if a.Opts.JSON {
		if err := emitJSON(a.Out, map[string]any{"ok": ok, "workflows": checks}); err != nil {
			return err
		}
	} else {
		for _, c := range checks {
			if c.OK {
				a.Printf("✓ %-16s  %s\n", c.Name, c.Origin)
			} else {
				a.Printf("✗ %-16s  %s\n", c.Name, strings.ReplaceAll(c.Error, "\n", "\n    "))
			}
		}
	}
	if !ok {
		return Exit(1)
	}
	return nil
}
