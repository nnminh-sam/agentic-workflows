package workflow

import (
	"fmt"
	"regexp"
	"sort"
)

// DefaultRemote is used when a template does not name one.
const DefaultRemote = "origin"

// Compiled is a validated template turned into what the consumers need.
type Compiled struct {
	Template Template
	// Origin is where the selected template was defined; Chain is its extends chain.
	Origin string
	Chain  []string

	Remote     string
	Production string // production branch name
	Staging    string // staging branch name
	// Protected lists the protected branch names (patterns for pattern branches).
	Protected []string
	// WorkFrom is the branch name new work branches start from.
	WorkFrom string
	// WorkTypes is the expanded work-branch type list.
	WorkTypes []string
	// WorkPatterns are the anchored regexes a new branch name must match
	// (BR003): the work pattern, per-transition work patterns and pattern
	// branches, in that order.
	WorkPatterns []string

	work        *regexp.Regexp
	transWork   map[string]*regexp.Regexp // transition id → its work regex
	branchRegex map[string]*regexp.Regexp // pattern-branch key → regex
}

// CompileOptions carry values the template may reference.
type CompileOptions struct {
	// CommitTypes fill work.types "{commit.types}".
	CommitTypes []string
}

// Compile validates r and compiles it.
func Compile(r *Resolved, opts CompileOptions) (*Compiled, error) {
	t := r.Template
	if err := Validate(t); err != nil {
		return nil, err
	}
	c := &Compiled{
		Template: t, Origin: r.Origin, Chain: r.Chain,
		Remote:      t.Remote,
		Production:  t.Branches[Production].Name,
		Staging:     t.Branches[Staging].Name,
		WorkTypes:   t.Work.Types.Expand(opts.CommitTypes),
		transWork:   map[string]*regexp.Regexp{},
		branchRegex: map[string]*regexp.Regexp{},
	}
	if c.Remote == "" {
		c.Remote = DefaultRemote
	}
	from := t.Work.From
	if from == "" {
		from = Staging
	}
	c.WorkFrom = t.Branches[from].Name
	if err := c.compilePatterns(); err != nil {
		return nil, fmt.Errorf("workflow %s: %w", t.Name, err)
	}
	return c, nil
}

func (c *Compiled) compilePatterns() error {
	t := c.Template
	re, src, err := compileOne(t.Work.Pattern, c.WorkTypes)
	if err != nil {
		return fmt.Errorf("work.pattern: %w", err)
	}
	c.work = re
	c.WorkPatterns = append(c.WorkPatterns, src)
	for _, tr := range t.Transitions {
		if tr.Work == "" {
			continue
		}
		re, src, err := compileOne(tr.Work, c.WorkTypes)
		if err != nil {
			return fmt.Errorf("transition %s work: %w", tr.ID, err)
		}
		c.transWork[tr.ID] = re
		c.WorkPatterns = appendUnique(c.WorkPatterns, src)
	}
	for _, key := range c.branchKeys() {
		b := t.Branches[key]
		if b.Protected {
			c.Protected = append(c.Protected, b.Name)
		}
		if !b.Pattern {
			continue
		}
		re, src, err := compileOne(b.Name, c.WorkTypes)
		if err != nil {
			return fmt.Errorf("branches.%s: %w", key, err)
		}
		c.branchRegex[key] = re
		c.WorkPatterns = appendUnique(c.WorkPatterns, src)
	}
	return nil
}

func compileOne(pattern string, types []string) (*regexp.Regexp, string, error) {
	src, err := PatternRegex(pattern, types)
	if err != nil {
		return nil, "", err
	}
	re, err := regexp.Compile(src)
	return re, src, err
}

// branchKeys returns production, staging, then the other keys sorted.
func (c *Compiled) branchKeys() []string {
	keys := []string{Production, Staging}
	var rest []string
	for k := range c.Template.Branches {
		if k != Production && k != Staging {
			rest = append(rest, k)
		}
	}
	sort.Strings(rest)
	return append(keys, rest...)
}

func appendUnique(l []string, s string) []string {
	for _, x := range l {
		if x == s {
			return l
		}
	}
	return append(l, s)
}

// BranchKey classifies a branch name: a branch key ("production",
// "staging", a pattern branch key), "work" for a work branch, or "" when
// the name fits nothing in the model.
func (c *Compiled) BranchKey(name string) string {
	for _, key := range c.branchKeys() {
		b := c.Template.Branches[key]
		if !b.Pattern && b.Name == name {
			return key
		}
		if re := c.branchRegex[key]; re != nil && re.MatchString(name) {
			return key
		}
	}
	for _, re := range c.transWork {
		if re.MatchString(name) {
			return WorkKey
		}
	}
	if c.work != nil && c.work.MatchString(name) {
		return WorkKey
	}
	return ""
}

// IsProtected reports whether name is (or matches) a protected branch.
func (c *Compiled) IsProtected(name string) bool {
	key := c.BranchKey(name)
	if key == "" || key == WorkKey {
		return false
	}
	return c.Template.Branches[key].Protected
}

// Route is where a PR from a head branch goes.
type Route struct {
	Transition Transition
	// Bases are the target branch names (a pattern for pattern branches).
	Bases []string
}

// PRRoute returns the PR transition for head: a branch matching a
// transition's own work pattern uses that transition; otherwise the first
// "pr" transition from the head's branch key. ok is false when head has no
// PR route (for example production, or a name outside the model).
func (c *Compiled) PRRoute(head string) (Route, bool) {
	for _, tr := range c.Template.Transitions {
		if re := c.transWork[tr.ID]; re != nil && tr.Via == "pr" && re.MatchString(head) {
			return c.route(tr), true
		}
	}
	key := c.BranchKey(head)
	if key == "" {
		return Route{}, false
	}
	for _, tr := range c.Template.Transitions {
		if tr.Via == "pr" && tr.From == key && tr.Work == "" {
			return c.route(tr), true
		}
	}
	return Route{}, false
}

func (c *Compiled) route(tr Transition) Route {
	r := Route{Transition: tr}
	for _, to := range tr.To {
		r.Bases = append(r.Bases, c.Template.Branches[to].Name)
	}
	return r
}

// AllowedBases returns every base branch name a PR from head may target.
func (c *Compiled) AllowedBases(head string) []string {
	key := c.BranchKey(head)
	var out []string
	for _, tr := range c.Template.Transitions {
		if tr.Via != "pr" {
			continue
		}
		own := c.transWork[tr.ID]
		if (own != nil && own.MatchString(head)) || (own == nil && key != "" && tr.From == key) {
			for _, b := range c.route(tr).Bases {
				out = appendUnique(out, b)
			}
		}
	}
	return out
}

// BranchingSettings returns the branching contract settings the template
// implies (production_branch, staging_branch, remote).
func (c *Compiled) BranchingSettings() map[string]any {
	return map[string]any{
		"production_branch": c.Production,
		"staging_branch":    c.Staging,
		"remote":            c.Remote,
	}
}

// RuleParams returns the rule params the template drives, by rule id
// (currently BR003 "patterns"). They are merged over the contract params.
func (c *Compiled) RuleParams() map[string]map[string]any {
	patterns := make([]any, len(c.WorkPatterns))
	for i, p := range c.WorkPatterns {
		patterns[i] = p
	}
	return map[string]map[string]any{
		"BR003": {"patterns": patterns},
	}
}
