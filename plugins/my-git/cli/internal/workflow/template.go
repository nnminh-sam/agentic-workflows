// Package workflow models git workflow templates: the branching model
// (long-lived branches, work-branch naming, transitions between branches,
// protection and PR templates). Templates are loaded from the built-in
// defaults and from config layers, resolved through their "extends" chains,
// validated, and compiled into the settings the BR rules, the branch/pr
// commands, the GitHub ruleset and the flow view consume.
package workflow

import (
	"encoding/json"
	"fmt"
)

// Well-known branch keys and the pseudo-branch "work".
const (
	Production = "production"
	Staging    = "staging"
	WorkKey    = "work"
)

// CommitTypesRef is the work.types value that means "reuse the commit types".
const CommitTypesRef = "{commit.types}"

// Template is one workflow template document (see defaults/workflows/default.json).
type Template struct {
	Name        string                `json:"name"`
	Description string                `json:"description,omitempty"`
	Extends     string                `json:"extends,omitempty"`
	Remote      string                `json:"remote,omitempty"`
	Branches    map[string]Branch     `json:"branches,omitempty"`
	Work        Work                  `json:"work"`
	Transitions []Transition          `json:"transitions,omitempty"`
	Protection  Protection            `json:"protection"`
	PRTemplates map[string]PRTemplate `json:"prTemplates,omitempty"`
}

// Branch is a long-lived (or pattern) branch of the model.
type Branch struct {
	// Name is the branch name, or a name pattern such as release/{version}
	// when Pattern is true.
	Name          string `json:"name"`
	Protected     bool   `json:"protected,omitempty"`
	GithubDefault bool   `json:"githubDefault,omitempty"`
	Pattern       bool   `json:"pattern,omitempty"`
}

// Work describes short-lived work branches.
type Work struct {
	// Pattern such as develop/{type}/{name}. Placeholders: {type}, {name},
	// {ticket}, {version}.
	Pattern string `json:"pattern"`
	Types   Types  `json:"types,omitempty"`
	// From is the branch key work branches start from.
	From string `json:"from,omitempty"`
}

// Types is either an explicit list of work-branch types or the reference
// "{commit.types}" (reuse the commit-message types).
type Types struct {
	List []string
	Ref  string
}

// UnmarshalJSON accepts a string reference or a list of strings.
func (t *Types) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		if s != CommitTypesRef {
			return fmt.Errorf("types: unknown reference %q (want %q or a list)", s, CommitTypesRef)
		}
		*t = Types{Ref: s}
		return nil
	}
	var l []string
	if err := json.Unmarshal(b, &l); err != nil {
		return fmt.Errorf("types: want %q or a list of strings", CommitTypesRef)
	}
	*t = Types{List: l}
	return nil
}

// MarshalJSON writes the reference or the list.
func (t Types) MarshalJSON() ([]byte, error) {
	if t.Ref != "" {
		return json.Marshal(t.Ref)
	}
	if t.List == nil {
		return []byte("null"), nil
	}
	return json.Marshal(t.List)
}

// Expand returns the concrete type list, substituting commitTypes for the reference.
func (t Types) Expand(commitTypes []string) []string {
	if t.Ref != "" {
		return commitTypes
	}
	return t.List
}

// Targets is a transition's "to": one branch key or a list of them.
type Targets []string

// UnmarshalJSON accepts a string or a list of strings.
func (t *Targets) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		*t = Targets{s}
		return nil
	}
	var l []string
	if err := json.Unmarshal(b, &l); err != nil {
		return fmt.Errorf("to: want a branch key or a list of branch keys")
	}
	*t = l
	return nil
}

// MarshalJSON writes a single target as a string.
func (t Targets) MarshalJSON() ([]byte, error) {
	if len(t) == 1 {
		return json.Marshal(t[0])
	}
	return json.Marshal([]string(t))
}

// Transition is one allowed move between branches.
type Transition struct {
	ID   string  `json:"id"`
	From string  `json:"from,omitempty"`
	To   Targets `json:"to,omitempty"`
	// Via is "pr" (pull request) or "branch" (create a branch from From).
	Via string `json:"via,omitempty"`
	// MergeMethod is squash, merge or rebase (PR transitions).
	MergeMethod string `json:"mergeMethod,omitempty"`
	PRTemplate  string `json:"prTemplate,omitempty"`
	DeleteHead  bool   `json:"deleteHead,omitempty"`
	// Tag is the tag pattern created after the transition, e.g. v{version}.
	Tag string `json:"tag,omitempty"`
	// Work is a dedicated work-branch pattern for this transition, e.g. hotfix/{name}.
	Work string `json:"work,omitempty"`
	// Remove drops the inherited transition with this id (extends only).
	Remove bool `json:"remove,omitempty"`
}

// Protection is the GitHub ruleset the template asks for on protected branches.
type Protection struct {
	RequirePR     bool `json:"requirePr"`
	Approvals     int  `json:"approvals"`
	ForcePush     bool `json:"forcePush"`
	Deletion      bool `json:"deletion"`
	RequireSigned bool `json:"requireSigned"`
	LinearHistory bool `json:"linearHistory"`
}

// PRTemplate is a named PR body layout.
type PRTemplate struct {
	Sections []string `json:"sections"`
}
