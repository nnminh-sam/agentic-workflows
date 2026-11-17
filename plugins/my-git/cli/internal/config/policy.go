package config

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// buildPolicy applies, in order: the compiled workflow, the active rule
// set, then every layer (global → project → local → env → flags) with its
// commit/pr conventions first and its own rules map second, and finally
// ${var} substitution. So a later layer always wins, whichever form it uses.
func (r *Resolved) buildPolicy(in Inputs, contracts map[string]*policy.Contract) error {
	a := &ruleApplier{rules: ruleIndex(contracts), locked: map[string]bool{}}
	if g := r.layer(ScopeGlobal); g != nil {
		a.lockedBy = g.Origin
		for _, id := range g.File.Locked {
			a.locked[id] = true
		}
	}
	r.applyWorkflow(contracts)
	chain, err := newRulesetCatalog(r.fileLayers()).resolve(r.RulesetName)
	if err != nil {
		return fmt.Errorf("rule set (%s): %w", r.RulesetReason, err)
	}
	if err := a.applyChain(chain); err != nil {
		return err
	}
	if err := r.applyLayers(a, contracts); err != nil {
		return err
	}
	vars := profile.Vars(r.Profile)
	for _, c := range contracts {
		for i := range c.Rules {
			c.Rules[i].Params = policy.SubstituteParams(c.Rules[i].Params, vars)
		}
	}
	r.Warnings = append(r.Warnings, a.warnings...)
	r.Policy = &policy.Policy{Contracts: contracts, Vars: vars}
	return nil
}

// fileLayers returns the global, project and local layers in order.
func (r *Resolved) fileLayers() []*Layer {
	var out []*Layer
	for _, l := range r.Layers {
		if l.Scope == ScopeGlobal || l.Scope == ScopeProject || l.Scope == ScopeLocal {
			out = append(out, l)
		}
	}
	return out
}

// applyWorkflow injects the branching settings and BR rule params.
func (r *Resolved) applyWorkflow(contracts map[string]*policy.Contract) {
	wf := r.Workflow
	br := contracts[policy.Branching]
	for k, v := range wf.BranchingSettings() {
		br.Settings[k] = v
	}
	builtin := wf.Origin == workflow.Builtin && wf.Template.Name == "default"
	idx := ruleIndex(contracts)
	for id, params := range wf.RuleParams() {
		rule := idx[id]
		if rule == nil {
			continue
		}
		rule.Params = deepMerge(rule.Params, params)
		if !builtin {
			rule.Origin = fmt.Sprintf("workflow %s (%s)", wf.Template.Name, wf.Origin)
		}
	}
}

// applyLayers applies each layer in order: its commit/pr conventions, then
// its rules map. Only the global layer may weaken a locked rule.
func (r *Resolved) applyLayers(a *ruleApplier, contracts map[string]*policy.Contract) error {
	for _, l := range r.Layers {
		if l.Scope == ScopeBuiltin {
			continue // the built-in conventions are the contract defaults
		}
		if err := applyConventions(a, l, contracts); err != nil {
			return err
		}
		if err := applyRules(a, l); err != nil {
			return err
		}
	}
	return nil
}

// convention maps one commit.*/pr.* key onto a rule override.
type convention struct {
	key, rule string
	// ov returns the override; apply=false skips it (e.g. headerMax 0).
	ov func(f File, gh008 map[string]any) (ov RuleOverride, apply bool)
}

func conventions() []convention {
	return []convention{
		{"commit.headerMax", "CM007", func(f File, _ map[string]any) (RuleOverride, bool) {
			return RuleOverride{Params: map[string]any{"max": float64(f.Commit.HeaderMax)}}, f.Commit.HeaderMax > 0
		}},
		{"commit.scopes", "CM005", func(f File, _ map[string]any) (RuleOverride, bool) {
			return RuleOverride{Params: map[string]any{"allowed_scopes": toAny(f.Commit.Scopes)}}, true
		}},
		{"commit.forbidAiAttribution", "CM016", func(f File, _ map[string]any) (RuleOverride, bool) {
			// true keeps whatever severity CM016 already has.
			v := f.Commit.ForbidAIAttribution
			return RuleOverride{Severity: "off"}, v != nil && !*v
		}},
		{"pr.sections", "GH008", func(f File, gh008 map[string]any) (RuleOverride, bool) {
			return RuleOverride{Params: map[string]any{"sections": prSections(f.PR.Sections, gh008)}}, true
		}},
	}
}

// applyConventions maps the commit.* and pr.* keys set in layer l onto
// the contracts (commit.types → settings, headerMax → CM007 max,
// scopes → CM005 allowed_scopes, forbidAiAttribution → CM016 severity,
// pr.sections → GH008 sections). Params replace the rule's value outright,
// since they are lists or scalars.
func applyConventions(a *ruleApplier, l *Layer, contracts map[string]*policy.Contract) error {
	if _, ok := getPath(l.Raw, "commit.types"); ok {
		contracts[policy.Commit].Settings["types"] = toAny(l.File.Commit.Types)
	}
	for _, c := range conventions() {
		if _, ok := getPath(l.Raw, c.key); !ok {
			continue
		}
		ov, apply := c.ov(l.File, a.rules["GH008"].Params)
		if !apply {
			continue
		}
		origin := fmt.Sprintf("%s (%s)", c.key, l.Origin)
		rule := a.rules[c.rule]
		for k, v := range ov.Params {
			rule.Params[k] = v
			rule.Origin = origin
		}
		ov.Params = nil
		if ov.Severity == "" {
			continue
		}
		if err := a.apply(c.rule, ov, origin, l.Scope == ScopeGlobal, layerFile(l), c.key); err != nil {
			return err
		}
	}
	return nil
}

// applyRules applies layer l's "rules" map in sorted id order.
func applyRules(a *ruleApplier, l *Layer) error {
	ids := make([]string, 0, len(l.File.Rules))
	for id := range l.File.Rules {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		origin := fmt.Sprintf("override rules.%s (%s)", id, l.Origin)
		if err := a.apply(id, l.File.Rules[id], origin, l.Scope == ScopeGlobal, layerFile(l), "rules."+id); err != nil {
			return err
		}
	}
	return nil
}

// layerFile names a layer in errors: its path, or its origin label.
func layerFile(l *Layer) string {
	if l.Path != "" {
		return l.Path
	}
	return l.Origin
}

// prSections turns section names into GH008 {name, heading} entries,
// reusing the contract's heading regex when a name is already known.
func prSections(names []string, gh008 map[string]any) []any {
	known := map[string]map[string]any{}
	for _, s := range sectionList(gh008) {
		if n, ok := s["name"].(string); ok {
			known[strings.ToLower(n)] = s
		}
	}
	out := make([]any, 0, len(names))
	for _, n := range names {
		if s, ok := known[strings.ToLower(n)]; ok {
			out = append(out, cloneValue(s))
			continue
		}
		out = append(out, map[string]any{
			"name":    n,
			"heading": `^#{1,4}\s*` + regexp.QuoteMeta(strings.ToLower(n)) + `\b`,
		})
	}
	return out
}

// sectionList returns GH008's sections param as maps.
func sectionList(params map[string]any) []map[string]any {
	l, _ := params["sections"].([]any)
	var out []map[string]any
	for _, x := range l {
		if m, ok := x.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func toAny(l []string) []any {
	out := make([]any, len(l))
	for i, s := range l {
		out[i] = s
	}
	return out
}
