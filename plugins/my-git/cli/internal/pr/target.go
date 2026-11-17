// Package pr plans `mygit pr`: it resolves which branch opens a pull
// request into which (from the workflow template's transitions), runs the
// read-only checks, renders the title and body, validates them with the
// rules engine and records the push and `gh pr create` steps on a plan.
//
// Nothing here executes a write; the plan runner does that after approval.
package pr

import (
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// Target is one pull request route: head into base, via a transition.
type Target struct {
	Transition string `json:"transition"`
	Head       string `json:"head"`
	Base       string `json:"base"`
	// Kind is the built-in body template: tmpl.PRDefault or tmpl.PRRelease.
	Kind string `json:"kind"`
	// Template is the transition's prTemplate name ("default", "release", …).
	Template string `json:"template,omitempty"`
	// Protected reports that head is a protected long-lived branch. It is
	// never pushed by mygit; it must already be on the remote.
	Protected bool `json:"protected,omitempty"`
}

func (t Target) String() string { return t.Head + " → " + t.Base }

// ResolveCreate resolves the PR route of head (`mygit pr create`). to, when
// not empty, replaces the base the workflow names (BR005 then judges it).
func ResolveCreate(c *workflow.Compiled, head, to string) (Target, error) {
	if head == "" {
		return Target{}, fmt.Errorf("not on a branch (detached HEAD)")
	}
	if c.IsProtected(head) {
		return Target{}, fmt.Errorf("%s is a protected branch and takes no direct work; open work PRs from a branch named %s "+
			"(release PRs: mygit pr release, other transitions: mygit pr run <transition>)", head, c.Template.Work.Pattern)
	}
	route, ok := c.PRRoute(head)
	if !ok {
		return Target{}, fmt.Errorf("branch %s has no pull request route in workflow %s; "+
			"work on a branch named %s (release PRs: mygit pr release)", head, c.Template.Name, c.Template.Work.Pattern)
	}
	return resolve(c, route.Transition, head, to)
}

// ResolveRelease resolves the release PR: the workflow's "release"
// transition, else the first PR transition into the production branch that
// does not start from a work branch.
func ResolveRelease(c *workflow.Compiled) (Target, error) {
	var found *workflow.Transition
	for i, tr := range c.Template.Transitions {
		if tr.Via == "pr" && tr.ID == "release" {
			found = &c.Template.Transitions[i]
			break
		}
	}
	if found == nil {
		for i, tr := range c.Template.Transitions {
			if tr.Via != "pr" || tr.From == workflow.WorkKey {
				continue
			}
			for _, to := range tr.To {
				if to == workflow.Production {
					found = &c.Template.Transitions[i]
				}
			}
			if found != nil {
				break
			}
		}
	}
	if found == nil {
		return Target{}, fmt.Errorf("workflow %s has no release transition (a pr transition into the production branch)", c.Template.Name)
	}
	return resolve(c, *found, "", "")
}

// ResolveTransition resolves a PR transition by id. current is the checked
// out branch (used when the transition starts from work branches).
func ResolveTransition(c *workflow.Compiled, id, current, to string) (Target, error) {
	var ids []string
	for _, tr := range c.Template.Transitions {
		if tr.Via != "pr" {
			continue
		}
		ids = append(ids, tr.ID)
		if tr.ID == id {
			return resolve(c, tr, current, to)
		}
	}
	return Target{}, fmt.Errorf("workflow %s has no pr transition %q (have: %s)", c.Template.Name, id, strings.Join(ids, ", "))
}

func resolve(c *workflow.Compiled, tr workflow.Transition, current, to string) (Target, error) {
	t := Target{Transition: tr.ID, Template: tr.PRTemplate, Kind: tmpl.PRDefault}
	if tr.PRTemplate == tmpl.PRRelease {
		t.Kind = tmpl.PRRelease
	}
	from := c.Template.Branches[tr.From]
	if tr.From == workflow.WorkKey || from.Pattern {
		if current == "" {
			return t, fmt.Errorf("transition %s starts from %s branches; check one out first", tr.ID, tr.From)
		}
		if key := c.BranchKey(current); key != tr.From {
			return t, fmt.Errorf("transition %s starts from %s branches, but %s is not one", tr.ID, tr.From, current)
		}
		t.Head = current
	} else {
		t.Head = from.Name
		t.Protected = from.Protected
	}
	switch {
	case to != "":
		t.Base = to
	case len(tr.To) == 0:
		return t, fmt.Errorf("transition %s has no target branch", tr.ID)
	case len(tr.To) > 1:
		var names []string
		for _, k := range tr.To {
			names = append(names, c.Template.Branches[k].Name)
		}
		return t, fmt.Errorf("transition %s can target %s; choose one with --to", tr.ID, strings.Join(names, ", "))
	default:
		b := c.Template.Branches[tr.To[0]]
		if b.Pattern {
			return t, fmt.Errorf("transition %s targets the pattern %s; name the branch with --to", tr.ID, b.Name)
		}
		t.Base = b.Name
	}
	if t.Head == t.Base {
		return t, fmt.Errorf("head and base are both %s: nothing to merge", t.Head)
	}
	return t, nil
}
