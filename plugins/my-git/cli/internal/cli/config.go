package cli

import (
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
)

func init() { register(70, newConfigCmd) }

func newConfigCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "config", Short: "read and write the layered mygit config"}
	cmd.AddCommand(configGetCmd(a, s), configSetCmd(s), configUnsetCmd(s), configShowCmd(a, s),
		configSchemaCmd(a), configValidateCmd(a, s))
	return cmd
}

func configGetCmd(a *App, s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "get <key>",
		Short: "print a merged value (or one file's value with a scope flag)",
		Args:  cobra.ExactArgs(1),
	}
	sf := addScopeFlags(cmd, config.ScopeGlobal, config.ScopeProject, config.ScopeLocal)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		e, err := s.env(cmd)
		if err != nil {
			return err
		}
		key := args[0]
		scope, err := sf.scope("")
		if err != nil {
			return err
		}
		var v any
		var origin string
		var ok bool
		if scope != "" {
			path, err := e.ScopePath(scope)
			if err != nil {
				return err
			}
			data, err := e.ReadOptional(path)
			if err != nil {
				return err
			}
			if v, ok, err = config.Get(data, key); err != nil {
				return fmt.Errorf("%s: %w", e.Display(path), err)
			}
			origin = e.Display(path)
		} else {
			c, err := e.Require()
			if err != nil {
				return err
			}
			v, origin, ok = c.Get(key)
		}
		if !ok {
			return &ExitError{Code: 1, Err: fmt.Errorf("%s is not set", key)}
		}
		if a.Opts.JSON {
			return emitJSON(a.Out, map[string]any{"key": key, "value": v, "origin": origin})
		}
		a.Printf("%s\n", compactJSON(v))
		return nil
	}
	return cmd
}

func configSetCmd(s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "set <key> <value>",
		Short: "set a key in one config file (plan; default --global)",
		Long:  "set a dotted key. The value is parsed as JSON when it is valid JSON (numbers, true, arrays, objects), else taken as a string.",
		Args:  cobra.ExactArgs(2),
	}
	sf := addScopeFlags(cmd, config.ScopeGlobal, config.ScopeProject, config.ScopeLocal)
	s.addOutFlag(cmd)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		scope, err := sf.scope(config.ScopeGlobal)
		if err != nil {
			return err
		}
		p, _, err := s.planner(cmd, args)
		if err != nil {
			return err
		}
		desc := fmt.Sprintf("set %s = %s  (%s config)", args[0], args[1], scope)
		if _, err := configEdit(p, scope, desc, setKey(scope, args[0], config.ParseValue(args[1]))); err != nil {
			return err
		}
		return s.run(cmd, p, nil)
	}
	return cmd
}

func configUnsetCmd(s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "unset <key>",
		Short: "remove a key from one config file (plan; default --global)",
		Args:  cobra.ExactArgs(1),
	}
	sf := addScopeFlags(cmd, config.ScopeGlobal, config.ScopeProject, config.ScopeLocal)
	s.addOutFlag(cmd)
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		scope, err := sf.scope(config.ScopeGlobal)
		if err != nil {
			return err
		}
		p, _, err := s.planner(cmd, args)
		if err != nil {
			return err
		}
		desc := fmt.Sprintf("unset %s  (%s config)", args[0], scope)
		if _, err := configEdit(p, scope, desc, unsetKey(scope, args[0])); err != nil {
			return err
		}
		return s.run(cmd, p, nil)
	}
	return cmd
}

// configEntry is one leaf of the merged config.
type configEntry struct {
	Key    string `json:"key"`
	Value  any    `json:"value"`
	Origin string `json:"origin,omitempty"`
}

// flattenConfig lists the leaves of raw (scalars, arrays, empty objects)
// as dotted keys, sorted.
func flattenConfig(raw map[string]any, origins config.Origins) []configEntry {
	var out []configEntry
	var walk func(prefix string, v any)
	walk = func(prefix string, v any) {
		if m, ok := v.(map[string]any); ok && len(m) > 0 {
			for k, sub := range m {
				key := k
				if prefix != "" {
					key = prefix + "." + k
				}
				walk(key, sub)
			}
			return
		}
		out = append(out, configEntry{Key: prefix, Value: v, Origin: origins.Of(prefix)})
	}
	walk("", raw)
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out
}

func configShowCmd(a *App, s *session) *cobra.Command {
	var origin bool
	cmd := &cobra.Command{
		Use:   "show",
		Short: "print the merged config (with --origin: which layer set each value)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			entries := flattenConfig(c.Raw, c.Origins)
			sel := map[string]string{
				"profile":  c.ProfileName + "  (" + c.ProfileReason + ")",
				"ruleset":  c.RulesetName + "  (" + c.RulesetReason + ")",
				"workflow": c.WorkflowName + "  (" + c.WorkflowReason + ")",
			}
			if a.Opts.JSON {
				if !origin {
					return emitJSON(a.Out, c.Raw)
				}
				return emitJSON(a.Out, map[string]any{"values": entries, "active": map[string]any{
					"profile":  map[string]string{"name": c.ProfileName, "reason": c.ProfileReason},
					"ruleset":  map[string]string{"name": c.RulesetName, "reason": c.RulesetReason},
					"workflow": map[string]string{"name": c.WorkflowName, "reason": c.WorkflowReason},
				}, "warnings": c.Warnings})
			}
			width := 0
			for _, en := range entries {
				width = max(width, len(en.Key))
			}
			for _, en := range entries {
				v := compactJSON(en.Value)
				if len(v) > 60 && origin {
					v = v[:57] + "..."
				}
				if origin {
					a.Printf("%-*s  %-40s  %s\n", width, en.Key, v, en.Origin)
				} else {
					a.Printf("%-*s  %s\n", width, en.Key, v)
				}
			}
			if origin {
				a.Printf("\n")
				for _, k := range []string{"profile", "ruleset", "workflow"} {
					a.Printf("active %-8s  %s\n", k, sel[k])
				}
			}
			for _, w := range c.Warnings {
				a.Errorf("warning: %s\n", w)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&origin, "origin", false, "show the layer each value came from")
	return cmd
}

func configSchemaCmd(a *App) *cobra.Command {
	return &cobra.Command{
		Use:   "schema",
		Short: "print the JSON Schema of a config file",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, err := a.Out.Write(config.Schema())
			return err
		},
	}
}

func configValidateCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "validate",
		Short: "check every config file (exit 1 when invalid)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, err := s.env(cmd)
			if err != nil {
				return err
			}
			res := validateConfig(e)
			if a.Opts.JSON {
				if err := emitJSON(a.Out, res); err != nil {
					return err
				}
			} else {
				a.Printf("%s %s\n", mark(res.OK), res.Summary)
				for _, w := range res.Warnings {
					a.Printf("  warning: %s\n", w)
				}
			}
			if !res.OK {
				return Exit(1)
			}
			return nil
		},
	}
}

// configValidation is the result of validating the config files.
type configValidation struct {
	OK       bool     `json:"ok"`
	Files    []string `json:"files"`
	Summary  string   `json:"summary"`
	File     string   `json:"file,omitempty"`
	Field    string   `json:"field,omitempty"`
	Line     int      `json:"line,omitempty"`
	Error    string   `json:"error,omitempty"`
	Warnings []string `json:"warnings,omitempty"`
}

func validateConfig(e *app.Env) configValidation {
	var v configValidation
	for _, p := range []string{e.Paths.Global, e.Paths.Project, e.Paths.Local} {
		if p == "" {
			continue
		}
		if b, err := e.ReadOptional(p); err == nil && b != nil {
			v.Files = append(v.Files, e.Display(p))
		}
	}
	if e.ConfigErr != nil {
		v.Error = e.ConfigErr.Error()
		var ce *config.Error
		if errors.As(e.ConfigErr, &ce) {
			v.File, v.Field, v.Line = e.Display(ce.File), ce.Field, ce.Line
		}
		v.Summary = "config invalid: " + v.Error
		return v
	}
	v.OK = true
	v.Warnings = e.Config.Warnings
	v.Summary = fmt.Sprintf("config valid (%d %s)", len(v.Files), plural(len(v.Files), "file", "files"))
	if len(v.Files) > 0 {
		v.Summary += ": " + strings.Join(v.Files, ", ")
	}
	return v
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}
