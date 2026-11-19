// Package policy is the resolved, read-only input of the rules engine:
// the four contracts with every override applied, the compiled workflow
// settings, and the active profile's variables. internal/config builds a
// Policy; internal/rules and internal/engine only read it.
//
// The contract JSON shape is the one shipped in defaults/contracts/*.json
// (identical to plugins/my-git/contracts/*.json), so settings and params keep
// the Python key names (snake_case) and existing overrides still apply.
package policy

import (
	"fmt"
	"sort"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// Contract names (the "contract" field of each JSON file).
const (
	Identity  = "identity"
	Commit    = "commit-message"
	GitHubCLI = "github-cli"
	Branching = "branching"
)

// Examples of good and bad commands for a rule.
type Examples struct {
	Good []string `json:"good,omitempty"`
	Bad  []string `json:"bad,omitempty"`
}

// Rule is one rule after all layers are applied. Params have ${var}
// references already substituted from the active profile (see Substitute).
type Rule struct {
	ID          string           `json:"id"`
	Name        string           `json:"name"`
	Title       string           `json:"title"`
	Description string           `json:"description"`
	Severity    verdict.Severity `json:"severity"`
	Params      map[string]any   `json:"params"`
	Examples    Examples         `json:"examples"`
	// Origin describes where the effective severity/params came from,
	// e.g. "default", "ruleset team (.mygit.json)", "override (.mygit.local.json)".
	Origin string `json:"origin,omitempty"`
}

// Contract is one rule group.
type Contract struct {
	Name        string         `json:"contract"`
	Version     string         `json:"version"` // semver, e.g. "2.2.0"
	Title       string         `json:"title"`
	Description string         `json:"description"`
	Settings    map[string]any `json:"settings"`
	Rules       []Rule         `json:"rules"`
}

// Rule returns the rule with id, or a zero Rule with Severity Off if absent.
func (c *Contract) Rule(id string) Rule {
	if c != nil {
		for _, r := range c.Rules {
			if r.ID == id {
				return r
			}
		}
	}
	return Rule{ID: id, Severity: verdict.Off, Params: map[string]any{}}
}

// Policy is everything the engine needs.
type Policy struct {
	Contracts map[string]*Contract
	// Vars are the active profile variables keyed by the Python names
	// (github_user, github_orgs, git_name, git_email, git_transport, ssh_key,
	// signing_key, signing_format). Unset variables are absent.
	Vars map[string]any
}

// Contract returns the named contract (nil if missing).
func (p *Policy) Contract(name string) *Contract {
	if p == nil {
		return nil
	}
	return p.Contracts[name]
}

// Rule finds a rule by id across all contracts.
func (p *Policy) Rule(id string) (Rule, bool) {
	if p == nil {
		return Rule{}, false
	}
	for _, c := range p.Contracts {
		for _, r := range c.Rules {
			if r.ID == id {
				return r, true
			}
		}
	}
	return Rule{}, false
}

// AllRules returns every rule sorted by id.
func (p *Policy) AllRules() []Rule {
	var out []Rule
	for _, c := range p.Contracts {
		out = append(out, c.Rules...)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// --- typed param accessors (JSON numbers decode as float64) ---

// Str returns params[key] as a string, or def.
func Str(params map[string]any, key, def string) string {
	if s, ok := params[key].(string); ok {
		return s
	}
	return def
}

// Int returns params[key] as an int, or def.
func Int(params map[string]any, key string, def int) int {
	switch v := params[key].(type) {
	case float64:
		return int(v)
	case int:
		return v
	}
	return def
}

// Bool returns params[key] as a bool, or def.
func Bool(params map[string]any, key string, def bool) bool {
	if b, ok := params[key].(bool); ok {
		return b
	}
	return def
}

// Strings returns params[key] as []string (non-string items are skipped).
func Strings(params map[string]any, key string) []string {
	switch v := params[key].(type) {
	case []string:
		return v
	case []any:
		out := make([]string, 0, len(v))
		for _, x := range v {
			if s, ok := x.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

// Sev returns params[key] parsed as a severity, or def.
func Sev(params map[string]any, key string, def verdict.Severity) verdict.Severity {
	if s, ok := params[key].(string); ok {
		if v, err := verdict.ParseSeverity(s); err == nil {
			return v
		}
	}
	return def
}

// ErrUnknownContract is returned for an unexpected contract name.
func ErrUnknownContract(name string) error { return fmt.Errorf("unknown contract %q", name) }
