package workflow

import (
	"fmt"
	"regexp"
	"slices"
	"sort"
	"strings"
)

// BranchNewName expands the work-branch pattern for a type and a name (and
// optionally a ticket or version). It checks the type against the template's
// work types and fails when the pattern needs a value that was not given.
func (c *Compiled) BranchNewName(typ, name, ticket, version string) (string, error) {
	if len(c.WorkTypes) > 0 && !slices.Contains(c.WorkTypes, typ) {
		return "", fmt.Errorf("unknown branch type %q (want one of: %s)", typ, strings.Join(c.WorkTypes, ", "))
	}
	return expandAll(c.Template.Work.Pattern, map[string]string{
		"type": typ, "name": name, "ticket": ticket, "version": version,
	})
}

// BranchCut is a branch a transition creates.
type BranchCut struct {
	Transition Transition
	// Name is the new branch name; Base the branch it starts from.
	Name string
	Base string
}

// BranchTransitions returns the transitions that create a branch: "branch"
// transitions and transitions with their own work-branch pattern. The
// generic "work" transition (new work branches) is not included; use
// BranchNewName for it.
func (c *Compiled) BranchTransitions() []Transition {
	var out []Transition
	for _, tr := range c.Template.Transitions {
		if c.cutPattern(tr) != "" {
			out = append(out, tr)
		}
	}
	return out
}

// cutPattern is the name pattern of the branch a transition creates ("" when
// it creates none).
func (c *Compiled) cutPattern(tr Transition) string {
	if tr.Work != "" {
		return tr.Work
	}
	if tr.Via == "branch" {
		for _, to := range tr.To {
			if b := c.Template.Branches[to]; b.Pattern {
				return b.Name
			}
		}
	}
	return ""
}

// BranchCutFor resolves the transition id into the branch to create. value
// fills {version} (and {name}, {ticket}) of the pattern.
func (c *Compiled) BranchCutFor(id, value string) (BranchCut, error) {
	for _, tr := range c.BranchTransitions() {
		if tr.ID != id {
			continue
		}
		name, err := expandAll(c.cutPattern(tr), map[string]string{"version": value, "name": value, "ticket": value})
		if err != nil {
			return BranchCut{}, fmt.Errorf("transition %q: %w (pass it as the second argument)", id, err)
		}
		base := c.WorkFrom
		if tr.From != WorkKey {
			base = c.Template.Branches[tr.From].Name
		}
		return BranchCut{Transition: tr, Name: name, Base: base}, nil
	}
	var ids []string
	for _, tr := range c.BranchTransitions() {
		ids = append(ids, tr.ID)
	}
	if len(ids) == 0 {
		return BranchCut{}, fmt.Errorf("workflow %q has no branch transitions to cut (transitions that create a branch use via \"branch\" or a work pattern); create work branches with: mygit branch new <type> <name>", c.Template.Name)
	}
	sort.Strings(ids)
	return BranchCut{}, fmt.Errorf("unknown transition %q (available: %s)", id, strings.Join(ids, ", "))
}

// BranchPrunable reports whether name is a short-lived work branch that
// prune may delete: it is classified as work and is not protected.
func (c *Compiled) BranchPrunable(name string) bool {
	return c.BranchKey(name) == WorkKey && !c.IsProtected(name)
}

var leftoverPlaceholder = regexp.MustCompile(`\{[a-z]+\}`)

// expandAll fills pattern from values and fails when a placeholder is left
// (empty values count as missing).
func expandAll(pattern string, values map[string]string) (string, error) {
	filled := map[string]string{}
	for k, v := range values {
		if v != "" {
			filled[k] = v
		}
	}
	out := Expand(pattern, filled)
	if m := leftoverPlaceholder.FindString(out); m != "" {
		return "", fmt.Errorf("pattern %q needs a value for %s", pattern, m)
	}
	return out, nil
}
