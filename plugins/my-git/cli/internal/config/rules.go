package config

import (
	"fmt"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// DefaultRuleset is the built-in rule set: the contracts as shipped.
const DefaultRuleset = "default"

// ruleApplier applies overrides to the policy's rules, enforcing locks.
type ruleApplier struct {
	rules    map[string]*policy.Rule
	locked   map[string]bool
	lockedBy string
	warnings []string
}

// apply merges ov into rule id. canWeaken is true for the global layer (the
// owner of "locked"); everyone else may only make a locked rule stricter.
// file and field locate the override for errors.
func (a *ruleApplier) apply(id string, ov RuleOverride, origin string, canWeaken bool, file, field string) error {
	r, ok := a.rules[id]
	if !ok {
		return &Error{File: file, Field: field, Msg: fmt.Sprintf("unknown rule id %q", id)}
	}
	changed := false
	if ov.Severity != "" {
		sev, err := verdict.ParseSeverity(ov.Severity)
		if err != nil {
			return &Error{File: file, Field: field, Msg: err.Error()}
		}
		if a.locked[id] && !canWeaken && sev < r.Severity {
			a.warnings = append(a.warnings, fmt.Sprintf(
				"rule %s is locked in %s: %s tried to lower it from %s to %s (ignored)",
				id, a.lockedBy, origin, r.Severity, sev))
		} else {
			r.Severity = sev
			changed = true
		}
	}
	if len(ov.Params) > 0 {
		r.Params = deepMerge(r.Params, ov.Params)
		changed = true
	}
	if changed {
		r.Origin = origin
	}
	return nil
}

// applyMap applies a whole rules map in sorted id order.
func (a *ruleApplier) applyMap(rules map[string]RuleOverride, origin string, canWeaken bool, file, fieldPrefix string) error {
	ids := make([]string, 0, len(rules))
	for id := range rules {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		if err := a.apply(id, rules[id], origin, canWeaken, file, fieldPrefix+"."+id); err != nil {
			return err
		}
	}
	return nil
}

// rulesetDef is one rule set as defined in one layer.
type rulesetDef struct {
	Ruleset
	layer *Layer
}

// rulesetCatalog holds rule sets by name, least specific scope first.
type rulesetCatalog map[string][]rulesetDef

func newRulesetCatalog(layers []*Layer) rulesetCatalog {
	cat := rulesetCatalog{}
	for _, l := range layers {
		for _, name := range sortedRulesetNames(l.File.Rulesets) {
			cat[name] = append(cat[name], rulesetDef{Ruleset: l.File.Rulesets[name], layer: l})
		}
	}
	return cat
}

func sortedRulesetNames(m map[string]Ruleset) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// chainLink is one resolved step of a rule set's extends chain.
type chainLink struct {
	name string
	def  rulesetDef
}

// resolve returns the extends chain of name, leaf first. The built-in
// "default" (no overrides) terminates every chain and is not included. A
// set without "extends" implicitly extends "default"; a set extending its
// own name inherits the definition from a less specific scope.
func (c rulesetCatalog) resolve(name string) ([]chainLink, error) {
	if name == DefaultRuleset && len(c[name]) == 0 {
		return nil, nil
	}
	if len(c[name]) == 0 {
		return nil, fmt.Errorf("unknown rule set %q (known: %s)", name, strings.Join(c.names(), ", "))
	}
	var chain []chainLink
	seen := map[string]bool{}
	var path []string
	level := len(c[name]) - 1
	for {
		key := fmt.Sprintf("%s#%d", name, level)
		path = append(path, name)
		if seen[key] {
			return nil, fmt.Errorf("rule set extends cycle: %s", strings.Join(path, " → "))
		}
		seen[key] = true
		def := c[name][level]
		chain = append(chain, chainLink{name: name, def: def})
		parent := def.Extends
		if parent == "" {
			parent = DefaultRuleset
		}
		switch {
		case parent == name:
			level--
		default:
			level = len(c[parent]) - 1
		}
		if level < 0 {
			if parent == DefaultRuleset {
				return chain, nil
			}
			return nil, fmt.Errorf("rule set %q (%s) extends unknown rule set %q", name, def.layer.Origin, parent)
		}
		name = parent
	}
}

func (c rulesetCatalog) names() []string {
	out := []string{DefaultRuleset}
	for k := range c {
		if k != DefaultRuleset {
			out = append(out, k)
		}
	}
	sort.Strings(out[1:])
	return out
}

// applyChain applies a rule-set chain root first. Each rule's origin reads
// "ruleset team (.mygit.json) ← extends strict ← default".
func (a *ruleApplier) applyChain(chain []chainLink) error {
	for i := len(chain) - 1; i >= 0; i-- {
		link := chain[i]
		names := []string{}
		for _, l := range chain[i+1:] {
			names = append(names, l.name)
		}
		origin := fmt.Sprintf("ruleset %s (%s)", link.name, link.def.layer.Origin)
		if len(names) > 0 {
			origin += " ← extends " + strings.Join(append(names, DefaultRuleset), " ← ")
		}
		field := "rulesets." + link.name + ".rules"
		if err := a.applyMap(link.def.Rules, origin, false, link.def.layer.Path, field); err != nil {
			return err
		}
	}
	return nil
}

// ParseRuleFlag parses "ID=severity" (from --rule or MYGIT_RULES).
func ParseRuleFlag(s string) (string, RuleOverride, error) {
	id, sev, ok := strings.Cut(strings.TrimSpace(s), "=")
	id, sev = strings.TrimSpace(id), strings.TrimSpace(sev)
	if !ok || id == "" || sev == "" {
		return "", RuleOverride{}, fmt.Errorf("rule override %q: want ID=severity", s)
	}
	if _, err := verdict.ParseSeverity(sev); err != nil {
		return "", RuleOverride{}, fmt.Errorf("rule override %q: %w", s, err)
	}
	return strings.ToUpper(id), RuleOverride{Severity: strings.ToLower(sev)}, nil
}
