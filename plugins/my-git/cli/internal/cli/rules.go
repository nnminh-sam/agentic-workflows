package cli

import (
	"fmt"
	"reflect"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(50, newRulesCmd) }

func newRulesCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "rules", Short: "rules, rule sets and per-rule overrides"}
	cmd.AddCommand(rulesListCmd(a, s), rulesExplainCmd(a, s), rulesShowCmd(a, s), rulesSetsCmd(a, s),
		rulesUseCmd(s), rulesSetCmd(s))
	return cmd
}

func rulesListCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "list every rule with its effective severity",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			rules := c.Policy.AllRules()
			if a.Opts.JSON {
				type row struct {
					ID       string           `json:"id"`
					Name     string           `json:"name"`
					Title    string           `json:"title"`
					Severity verdict.Severity `json:"severity"`
				}
				rows := make([]row, len(rules))
				for i, r := range rules {
					rows[i] = row{r.ID, r.Name, r.Title, r.Severity}
				}
				return emitJSON(a.Out, map[string]any{"ruleset": c.RulesetName, "rules": rows})
			}
			for _, r := range rules {
				a.Printf("%-6s  %-4s  %s\n", r.ID, r.Severity, r.Title)
			}
			return nil
		},
	}
}

func rulesExplainCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "explain <ID>",
		Short: "describe a rule: severity, params, good and bad examples",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			r, ok := c.Policy.Rule(strings.ToUpper(args[0]))
			if !ok {
				return fmt.Errorf("unknown rule %q (see mygit rules list)", args[0])
			}
			if a.Opts.JSON {
				return emitJSON(a.Out, r)
			}
			a.Printf("%s  %s  (%s)\n", r.ID, r.Title, r.Name)
			a.Printf("severity  %s   %s\n", r.Severity, r.Origin)
			a.Printf("\n%s\n", wrapText(r.Description, 88, ""))
			if len(r.Params) > 0 {
				a.Printf("\nparams\n")
				for _, k := range sortedKeys(r.Params) {
					a.Printf("  %-22s  %s\n", k, compactJSON(r.Params[k]))
				}
			}
			if len(r.Examples.Good) > 0 {
				a.Printf("\ngood\n")
				for _, ex := range r.Examples.Good {
					a.Printf("  ✓ %s\n", strings.ReplaceAll(ex, "\n", "\n    "))
				}
			}
			if len(r.Examples.Bad) > 0 {
				a.Printf("\nbad\n")
				for _, ex := range r.Examples.Bad {
					a.Printf("  ✗ %s\n", strings.ReplaceAll(ex, "\n", "\n    "))
				}
			}
			return nil
		},
	}
}

// defaultRules loads the shipped contracts' rules by id (for param diffs).
func defaultRules() map[string]policy.Rule {
	out := map[string]policy.Rule{}
	cs, err := config.LoadContracts(defaults.FS)
	if err != nil {
		return out
	}
	for _, c := range cs {
		for _, r := range c.Rules {
			out[r.ID] = r
		}
	}
	return out
}

// changedParams renders the params that differ from the shipped contract
// as "k=v" pairs.
func changedParams(r policy.Rule, def policy.Rule) string {
	var parts []string
	for _, k := range sortedKeys(r.Params) {
		if dv, ok := def.Params[k]; ok && reflect.DeepEqual(dv, r.Params[k]) {
			continue
		}
		v := compactJSON(r.Params[k])
		if len(v) > 40 {
			v = v[:37] + "..."
		}
		parts = append(parts, k+"="+v)
	}
	return strings.Join(parts, " ")
}

func rulesShowCmd(a *App, s *session) *cobra.Command {
	var origin bool
	cmd := &cobra.Command{
		Use:   "show [IDs…]",
		Short: "print effective severities and changed params (--origin: where each came from)",
		RunE: func(cmd *cobra.Command, args []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			var rules []policy.Rule
			if len(args) == 0 {
				rules = c.Policy.AllRules()
			}
			for _, id := range args {
				r, ok := c.Policy.Rule(strings.ToUpper(id))
				if !ok {
					return fmt.Errorf("unknown rule %q", id)
				}
				rules = append(rules, r)
			}
			defs := defaultRules()
			if a.Opts.JSON {
				type row struct {
					ID       string           `json:"id"`
					Severity verdict.Severity `json:"severity"`
					Params   map[string]any   `json:"params"`
					Changed  string           `json:"changedParams,omitempty"`
					Origin   string           `json:"origin"`
				}
				rows := make([]row, len(rules))
				for i, r := range rules {
					rows[i] = row{r.ID, r.Severity, r.Params, changedParams(r, defs[r.ID]), r.Origin}
				}
				return emitJSON(a.Out, map[string]any{"ruleset": c.RulesetName, "rulesetReason": c.RulesetReason, "rules": rows, "warnings": c.Warnings})
			}
			for _, r := range rules {
				params := changedParams(r, defs[r.ID])
				if origin {
					a.Printf("%-6s %-4s  %-9s %s\n", r.ID, r.Severity, params, r.Origin)
				} else {
					a.Printf("%-6s %-4s  %s\n", r.ID, r.Severity, params)
				}
			}
			for _, w := range c.Warnings {
				a.Errorf("warning: %s\n", w)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&origin, "origin", false, "show which rule set or override set each rule")
	return cmd
}

// rulesetRow is one defined rule set.
type rulesetRow struct {
	Name    string `json:"name"`
	Extends string `json:"extends,omitempty"`
	Rules   int    `json:"rules"`
	Origin  string `json:"origin"`
	Active  bool   `json:"active"`
}

func rulesets(c *config.Resolved) []rulesetRow {
	byName := map[string]rulesetRow{config.DefaultRuleset: {Name: config.DefaultRuleset, Origin: "built-in"}}
	for _, l := range c.Layers {
		for n, rs := range l.File.Rulesets {
			ext := rs.Extends
			if ext == "" && n != config.DefaultRuleset {
				ext = config.DefaultRuleset
			}
			byName[n] = rulesetRow{Name: n, Extends: ext, Rules: len(rs.Rules), Origin: l.Origin}
		}
	}
	var out []rulesetRow
	for _, r := range byName {
		r.Active = r.Name == c.RulesetName
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func rulesSetsCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "sets",
		Short: "list rule sets (* = active)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			rows := rulesets(c)
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"active": c.RulesetName, "reason": c.RulesetReason, "rulesets": rows})
			}
			for _, r := range rows {
				star := " "
				if r.Active {
					star = "*"
				}
				ext := ""
				if r.Extends != "" {
					ext = "extends " + r.Extends
				}
				a.Printf("%s %-10s  %-18s  %2d rules  %s\n", star, r.Name, ext, r.Rules, r.Origin)
			}
			a.Printf("active: %s (%s)\n", c.RulesetName, c.RulesetReason)
			return nil
		},
	}
}

func rulesUseCmd(s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "use <set>",
		Short: "select a rule set for this repository (plan; default --local)",
		Args:  cobra.ExactArgs(1),
	}
	sf := addScopeFlags(cmd, config.ScopeProject, config.ScopeLocal)
	s.addOutFlag(cmd)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		scope, err := sf.scope(config.ScopeLocal)
		if err != nil {
			return err
		}
		p, e, err := s.planner(cmd, args)
		if err != nil {
			return err
		}
		c, err := e.Require()
		if err != nil {
			return err
		}
		known := false
		var names []string
		for _, r := range rulesets(c) {
			names = append(names, r.Name)
			known = known || r.Name == args[0]
		}
		if !known {
			return fmt.Errorf("unknown rule set %q (known: %s)", args[0], strings.Join(names, ", "))
		}
		if _, err := configEdit(p, scope, fmt.Sprintf("ruleset = %s  (%s config)", args[0], scope),
			setKey(scope, "ruleset", args[0])); err != nil {
			return err
		}
		return s.run(cmd, p, nil)
	}
	return cmd
}

func rulesSetCmd(s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "set <ID> <severity>",
		Short: "override one rule's severity (plan; default --local in a repo, else --global)",
		Args:  cobra.ExactArgs(2),
	}
	sf := addScopeFlags(cmd, config.ScopeGlobal, config.ScopeProject, config.ScopeLocal)
	s.addOutFlag(cmd)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		p, e, err := s.planner(cmd, args)
		if err != nil {
			return err
		}
		def := config.ScopeGlobal
		if e.InRepo() {
			def = config.ScopeLocal
		}
		scope, err := sf.scope(def)
		if err != nil {
			return err
		}
		id := strings.ToUpper(args[0])
		sev, err := verdict.ParseSeverity(args[1])
		if err != nil {
			return err
		}
		if _, ok := defaultRules()[id]; !ok {
			return fmt.Errorf("unknown rule %q (see mygit rules list)", args[0])
		}
		edit := func(b []byte) ([]byte, error) {
			cur, ok, err := config.Get(b, "rules."+id)
			if err != nil {
				return nil, err
			}
			if _, isObj := cur.(map[string]any); ok && isObj {
				return config.Set(b, scope, "rules."+id+".severity", sev.String())
			}
			return config.Set(b, scope, "rules."+id, sev.String())
		}
		if _, err := configEdit(p, scope, fmt.Sprintf("rules.%s = %s  (%s config)", id, sev, scope), edit); err != nil {
			return err
		}
		return s.run(cmd, p, nil)
	}
	return cmd
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// wrapText wraps s at width on word boundaries, indenting continuation lines.
func wrapText(s string, width int, indent string) string {
	var lines []string
	for _, para := range strings.Split(s, "\n") {
		line := ""
		for _, w := range strings.Fields(para) {
			if line != "" && len(line)+1+len(w) > width {
				lines = append(lines, line)
				line = indent + w
				continue
			}
			if line == "" {
				line = w
			} else {
				line += " " + w
			}
		}
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}
