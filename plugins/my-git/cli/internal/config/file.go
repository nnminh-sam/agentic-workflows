// Package config loads the layered mygit configuration (built-in → global →
// project → local → environment → flags), validates every file, tracks the
// origin of every value, and resolves it into what the rest of mygit runs
// on: the active profile, rule set, workflow and the final policy.Policy.
//
// Everything is injectable: file reads, directory globbing, environment,
// home directory and GOOS come in through Inputs, so the package never
// touches process globals.
package config

import (
	"bytes"
	"encoding/json"
	"fmt"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// File is the schema shared by every config layer. Which fields a layer may
// set is checked by Parse (see scopeRules).
type File struct {
	Schema          string                       `json:"$schema,omitempty"`
	Version         int                          `json:"version,omitempty"`
	DefaultProfile  string                       `json:"defaultProfile,omitempty"`
	Profile         string                       `json:"profile,omitempty"`
	Profiles        map[string]profile.Profile   `json:"profiles,omitempty"`
	Match           []profile.Match              `json:"match,omitempty"`
	Execution       Execution                    `json:"execution"`
	Rules           map[string]RuleOverride      `json:"rules,omitempty"`
	Rulesets        map[string]Ruleset           `json:"rulesets,omitempty"`
	Ruleset         string                       `json:"ruleset,omitempty"`
	DefaultRuleset  string                       `json:"defaultRuleset,omitempty"`
	Locked          []string                     `json:"locked,omitempty"`
	Workflows       map[string]workflow.Template `json:"workflows,omitempty"`
	Workflow        string                       `json:"workflow,omitempty"`
	DefaultWorkflow string                       `json:"defaultWorkflow,omitempty"`
	Commit          Commit                       `json:"commit"`
	PR              PR                           `json:"pr"`
	GitHub          GitHub                       `json:"github"`
	Agents          []string                     `json:"agents,omitempty"`
}

// Execution controls plan execution.
type Execution struct {
	// Mode is "auto" (run the plan after approval) or "manual" (print it).
	Mode string `json:"mode,omitempty"`
	// Shell renders manual-mode output: auto, powershell, cmd, gitbash, bash, zsh.
	Shell string `json:"shell,omitempty"`
	// AgentApply is how the guard treats an agent's --apply: "ask" or "allow".
	AgentApply string `json:"agentApply,omitempty"`
}

// Commit holds the commit-message conventions.
type Commit struct {
	Convention          string   `json:"convention,omitempty"`
	Types               []string `json:"types,omitempty"`
	Scopes              []string `json:"scopes,omitempty"`
	HeaderMax           int      `json:"headerMax,omitempty"`
	ForbidAIAttribution *bool    `json:"forbidAiAttribution,omitempty"`
	Template            string   `json:"template,omitempty"`
}

// PR holds the pull-request conventions.
type PR struct {
	Base     string   `json:"base,omitempty"`
	Sections []string `json:"sections,omitempty"`
	Template string   `json:"template,omitempty"`
	Checks   Checks   `json:"checks"`
}

// Checks are the commands whose output fills a PR's test results.
type Checks struct {
	Test string `json:"test,omitempty"`
	Lint string `json:"lint,omitempty"`
}

// GitHub holds repository-level GitHub settings.
type GitHub struct {
	Owners  []string      `json:"owners,omitempty"`
	Ruleset GitHubRuleset `json:"ruleset"`
	// DeleteBranchOnMerge makes `mygit repo protect` also turn on GitHub's
	// delete_branch_on_merge. Off by default (long-lived work branches).
	DeleteBranchOnMerge bool `json:"deleteBranchOnMerge,omitempty"`
}

// GitHubRuleset tunes the ruleset `mygit repo protect` applies.
type GitHubRuleset struct {
	Approvals *int `json:"approvals,omitempty"`
}

// Ruleset is a named bundle of rule overrides.
type Ruleset struct {
	Extends string                  `json:"extends,omitempty"`
	Rules   map[string]RuleOverride `json:"rules,omitempty"`
}

// RuleOverride changes one rule. In JSON it is either a severity string
// ("off") or an object {"severity": "...", "params": {...}}.
type RuleOverride struct {
	Severity string         `json:"severity,omitempty"`
	Params   map[string]any `json:"params,omitempty"`
}

// UnmarshalJSON accepts the string shorthand or the strict object form.
func (r *RuleOverride) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		if _, err := verdict.ParseSeverity(s); err != nil {
			return err
		}
		*r = RuleOverride{Severity: s}
		return nil
	}
	type plain RuleOverride
	var p plain
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&p); err != nil {
		return fmt.Errorf("want a severity string or {\"severity\", \"params\"}: %w", err)
	}
	if p.Severity != "" {
		if _, err := verdict.ParseSeverity(p.Severity); err != nil {
			return err
		}
	}
	*r = RuleOverride(p)
	return nil
}
