// Package repoops holds the pure logic of `mygit repo`: repository slugs,
// the GitHub ruleset built from a workflow template, and the step list of
// `repo bootstrap`. Facts come in as plain values; nothing here runs a
// process, so everything is table-testable.
package repoops

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// BootstrapMessage is the subject of the first commit.
const BootstrapMessage = "chore: bootstrap repository"

// LegacyBootstrapMessage is the subject the Python gitguard used; a first
// commit with it counts as the bootstrap commit too.
const LegacyBootstrapMessage = "chore: initialize repository"

func isBootstrapMessage(s string) bool {
	return s == BootstrapMessage || s == LegacyBootstrapMessage
}

var slugRe = regexp.MustCompile(`^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$`)

// ParseSlug splits "owner/name".
func ParseSlug(s string) (owner, name string, err error) {
	m := slugRe.FindStringSubmatch(strings.TrimSpace(s))
	if m == nil {
		return "", "", fmt.Errorf("repository %q: want <owner>/<name>", s)
	}
	return m[1], m[2], nil
}

// ---------------------------------------------------------------- ruleset

// Ruleset is the GitHub repository ruleset `repo protect` applies.
type Ruleset struct {
	Name         string         `json:"name"`
	Target       string         `json:"target"`
	Enforcement  string         `json:"enforcement"`
	BypassActors []any          `json:"bypass_actors"`
	Conditions   map[string]any `json:"conditions"`
	Rules        []Rule         `json:"rules"`
}

// Rule is one ruleset rule.
type Rule struct {
	Type       string `json:"type"`
	Parameters any    `json:"parameters,omitempty"`
}

// PullRequestParams are the parameters of the pull_request rule.
type PullRequestParams struct {
	Approvals                      int  `json:"required_approving_review_count"`
	DismissStaleReviewsOnPush      bool `json:"dismiss_stale_reviews_on_push"`
	RequireCodeOwnerReview         bool `json:"require_code_owner_review"`
	RequireLastPushApproval        bool `json:"require_last_push_approval"`
	RequiredReviewThreadResolution bool `json:"required_review_thread_resolution"`
}

// RulesetOptions tune BuildRuleset. Approvals nil means the template value.
type RulesetOptions struct {
	Approvals     *int
	RequireSigned bool
}

var placeholderRe = regexp.MustCompile(`\{[^}]*\}`)

// RefPattern turns a branch name (or name pattern such as release/{version})
// into a ruleset ref pattern.
func RefPattern(branch string) string {
	return "refs/heads/" + placeholderRe.ReplaceAllString(branch, "*")
}

// RulesetName names the ruleset after the protected branches, e.g.
// "my-git: protect main and develop/base".
func RulesetName(protected []string) string {
	switch len(protected) {
	case 0:
		return "my-git: protect branches"
	case 1:
		return "my-git: protect " + protected[0]
	}
	return "my-git: protect " + strings.Join(protected[:len(protected)-1], ", ") + " and " + protected[len(protected)-1]
}

// BuildRuleset derives the ruleset from the compiled workflow template:
// every protected branch, PR required with the approvals (template value
// unless opt overrides it), no force-push (non_fast_forward) and no deletion
// unless the template allows them, signed commits when asked or required.
func BuildRuleset(c *workflow.Compiled, opt RulesetOptions) (Ruleset, error) {
	if len(c.Protected) == 0 {
		return Ruleset{}, fmt.Errorf("workflow %q has no protected branches", c.Template.Name)
	}
	pr := c.Template.Protection
	approvals := pr.Approvals
	if opt.Approvals != nil {
		approvals = *opt.Approvals
	}
	if approvals < 0 {
		return Ruleset{}, fmt.Errorf("approvals must be 0 or more, got %d", approvals)
	}
	include := make([]string, len(c.Protected))
	for i, b := range c.Protected {
		include[i] = RefPattern(b)
	}
	rs := Ruleset{
		Name: RulesetName(c.Protected), Target: "branch", Enforcement: "active",
		BypassActors: []any{},
		Conditions:   map[string]any{"ref_name": map[string]any{"include": include, "exclude": []string{}}},
	}
	if !pr.Deletion {
		rs.Rules = append(rs.Rules, Rule{Type: "deletion"})
	}
	if !pr.ForcePush {
		rs.Rules = append(rs.Rules, Rule{Type: "non_fast_forward"})
	}
	if pr.RequirePR {
		rs.Rules = append(rs.Rules, Rule{Type: "pull_request", Parameters: PullRequestParams{
			Approvals: approvals, DismissStaleReviewsOnPush: true, RequiredReviewThreadResolution: true}})
	}
	if pr.LinearHistory {
		rs.Rules = append(rs.Rules, Rule{Type: "required_linear_history"})
	}
	if opt.RequireSigned || pr.RequireSigned {
		rs.Rules = append(rs.Rules, Rule{Type: "required_signatures"})
	}
	return rs, nil
}

// JSON renders the ruleset as indented JSON with a trailing newline.
func (r Ruleset) JSON() string {
	b, _ := json.MarshalIndent(r, "", "  ") // plain values: cannot fail
	return string(b) + "\n"
}

// Has reports whether the ruleset contains a rule of the type.
func (r Ruleset) Has(typ string) bool {
	for _, x := range r.Rules {
		if x.Type == typ {
			return true
		}
	}
	return false
}

// Existing is one ruleset returned by GET repos/{r}/rulesets.
type Existing struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

// ParseExisting decodes the list of rulesets of a repository.
func ParseExisting(out string) ([]Existing, error) {
	out = strings.TrimSpace(out)
	if out == "" {
		return nil, nil
	}
	var l []Existing
	if err := json.Unmarshal([]byte(out), &l); err != nil {
		return nil, fmt.Errorf("decode rulesets: %w", err)
	}
	return l, nil
}

// FindByName returns the ID of the ruleset with name.
func FindByName(l []Existing, name string) (int64, bool) {
	for _, e := range l {
		if e.Name == name {
			return e.ID, true
		}
	}
	return 0, false
}

// RulesetPrefix starts the name of every ruleset mygit creates.
const RulesetPrefix = "my-git: "

// FindRuleset returns the ruleset to update: the one named exactly name,
// else the first whose name starts with RulesetPrefix (the protected set
// changed since it was created).
func FindRuleset(l []Existing, name string) (int64, bool) {
	if id, ok := FindByName(l, name); ok {
		return id, true
	}
	for _, e := range l {
		if strings.HasPrefix(e.Name, RulesetPrefix) {
			return e.ID, true
		}
	}
	return 0, false
}

// DefaultBranch returns the branch GitHub should use as default: the one
// marked githubDefault (staging first), else the staging branch.
func DefaultBranch(c *workflow.Compiled) string {
	for _, k := range []string{workflow.Staging, workflow.Production} {
		if b, ok := c.Template.Branches[k]; ok && b.GithubDefault {
			return b.Name
		}
	}
	return c.Staging
}

// ---------------------------------------------------------------- bootstrap

// Facts describe the repository `repo bootstrap` starts from.
type Facts struct {
	InRepo         bool
	Production     string
	Staging        string
	CurrentBranch  string // "" when detached or no repository
	HasCommits     bool   // HEAD resolves to a commit
	ProductionRef  bool   // refs/heads/<production> exists
	StagingRef     bool   // refs/heads/<staging> exists
	ProductionRoot string // subject of the first commit on production ("" when none)
	ProductionLen  int    // number of commits on production
	Staged         bool   // something is staged
}

// Step is one planned git write.
type Step struct {
	Description string
	// Args are the git arguments after "git". For a Commit step the caller
	// prefixes the identity pairs and adds -S when the profile signs.
	Args   []string
	Commit bool
}

// Plan lists the steps to bootstrap the repository, skipping what already
// exists. done is true when nothing is left to do. It refuses a repository
// whose production branch already has unrelated history, unless both
// long-lived branches exist (then the setup already matches).
func Plan(f Facts) (steps []Step, done bool, err error) {
	prod, staging := f.Production, f.Staging
	switch {
	case !f.InRepo:
		steps = append(steps, Step{Description: "initialize the repository on " + prod, Args: []string{"init", "-b", prod}})
	case !f.HasCommits && f.CurrentBranch != prod:
		steps = append(steps, Step{Description: "point the unborn branch at " + prod, Args: []string{"symbolic-ref", "HEAD", "refs/heads/" + prod}})
	}
	if f.HasCommits {
		if !f.ProductionRef {
			return nil, false, fmt.Errorf("the repository has commits but no %q branch; bootstrap starts a fresh repository (create or rename %s yourself first)", prod, prod)
		}
		ours := f.ProductionLen == 1 && isBootstrapMessage(f.ProductionRoot)
		switch {
		case ours:
		case f.StagingRef:
			return nil, true, nil // both branches exist: already set up
		default:
			return nil, false, fmt.Errorf("%s already has %d commit(s) and the first is not the bootstrap commit; refusing to bootstrap. "+
				"Create the staging branch yourself: git branch %s %s", prod, f.ProductionLen, staging, prod)
		}
	} else {
		args := []string{"commit", "-m", BootstrapMessage}
		desc := "first commit on " + prod
		if !f.Staged {
			args = []string{"commit", "--allow-empty", "-m", BootstrapMessage}
			desc += " (empty: nothing is staged)"
		}
		steps = append(steps, Step{Description: desc, Args: args, Commit: true})
	}
	if !f.StagingRef {
		steps = append(steps, Step{Description: "create " + staging + " from " + prod, Args: []string{"branch", staging, prod}})
	}
	return steps, len(steps) == 0, nil
}
