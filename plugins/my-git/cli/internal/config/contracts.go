package config

import (
	"encoding/json"
	"fmt"
	"io/fs"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
)

// contractFiles maps each contract name to its file under contracts/.
func contractFiles() map[string]string {
	return map[string]string{
		policy.Identity:  "contracts/identity.json",
		policy.Commit:    "contracts/commit-message.json",
		policy.GitHubCLI: "contracts/github-cli.json",
		policy.Branching: "contracts/branching.json",
	}
}

// LoadContracts reads the four built-in contracts from fsys (defaults.FS).
// Every rule gets Origin "default" and a non-nil Params map.
func LoadContracts(fsys fs.FS) (map[string]*policy.Contract, error) {
	out := map[string]*policy.Contract{}
	for name, file := range contractFiles() {
		data, err := fs.ReadFile(fsys, file)
		if err != nil {
			return nil, fmt.Errorf("built-in contract %s: %w", name, err)
		}
		c := &policy.Contract{}
		if err := json.Unmarshal(data, c); err != nil {
			return nil, fmt.Errorf("built-in contract %s: %w", file, err)
		}
		if c.Name != name {
			return nil, fmt.Errorf("built-in contract %s: contract is %q, want %q", file, c.Name, name)
		}
		if c.Settings == nil {
			c.Settings = map[string]any{}
		}
		for i := range c.Rules {
			if c.Rules[i].Params == nil {
				c.Rules[i].Params = map[string]any{}
			}
			c.Rules[i].Origin = "default"
		}
		out[name] = c
	}
	return out, nil
}

// ruleIndex maps rule id → pointer into its contract.
func ruleIndex(contracts map[string]*policy.Contract) map[string]*policy.Rule {
	idx := map[string]*policy.Rule{}
	for _, c := range contracts {
		for i := range c.Rules {
			idx[c.Rules[i].ID] = &c.Rules[i]
		}
	}
	return idx
}
