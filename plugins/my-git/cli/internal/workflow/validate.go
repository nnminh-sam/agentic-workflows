package workflow

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

// Validate checks that a resolved template is complete and its transition
// graph has no gaps. All problems are reported together.
func Validate(t Template) error {
	var errs []string
	add := func(format string, args ...any) { errs = append(errs, fmt.Sprintf(format, args...)) }

	for _, key := range []string{Production, Staging} {
		if b, ok := t.Branches[key]; !ok || b.Name == "" {
			add("branches.%s.name is required", key)
		}
	}
	if p, s := t.Branches[Production].Name, t.Branches[Staging].Name; p != "" && p == s {
		add("branches.production and branches.staging must differ (both %q)", p)
	}
	for key, b := range t.Branches {
		if key == WorkKey {
			add("branches.work: %q is reserved for work branches", WorkKey)
		}
		if b.Name != "" && b.Pattern != hasPlaceholder(b.Name) {
			add("branches.%s: \"pattern\" must be true exactly when the name has {placeholders}", key)
		}
		if err := checkPlaceholders(b.Name); err != nil {
			add("branches.%s.name: %v", key, err)
		}
	}
	errs = append(errs, validateWork(t)...)
	errs = append(errs, validateTransitions(t)...)
	if len(errs) == 0 {
		errs = append(errs, validateGraph(t)...)
	}
	if len(errs) == 0 {
		return nil
	}
	return errors.New("workflow " + t.Name + ": " + strings.Join(errs, "; "))
}

func validateWork(t Template) []string {
	var errs []string
	if t.Work.Pattern == "" {
		errs = append(errs, "work.pattern is required")
	} else {
		if !strings.Contains(t.Work.Pattern, "{name}") {
			errs = append(errs, "work.pattern must contain {name}")
		}
		if err := checkPlaceholders(t.Work.Pattern); err != nil {
			errs = append(errs, fmt.Sprintf("work.pattern: %v", err))
		}
		if strings.Contains(t.Work.Pattern, "{type}") && t.Work.Types.Ref == "" && len(t.Work.Types.List) == 0 {
			errs = append(errs, "work.types is required when work.pattern uses {type}")
		}
	}
	if t.Work.From != "" {
		if _, ok := t.Branches[t.Work.From]; !ok {
			errs = append(errs, fmt.Sprintf("work.from: unknown branch %q", t.Work.From))
		}
	}
	return errs
}

func validateTransitions(t Template) []string {
	var errs []string
	seen := map[string]bool{}
	known := func(k string) bool { _, ok := t.Branches[k]; return ok }
	for i, tr := range t.Transitions {
		where := fmt.Sprintf("transitions[%d]", i)
		if tr.ID != "" {
			where = fmt.Sprintf("transition %q", tr.ID)
		}
		switch {
		case tr.ID == "":
			errs = append(errs, where+": id is required")
		case seen[tr.ID]:
			errs = append(errs, where+": duplicate id")
		}
		seen[tr.ID] = true
		if tr.From != WorkKey && !known(tr.From) {
			errs = append(errs, fmt.Sprintf("%s: from %q is not a branch key or %q", where, tr.From, WorkKey))
		}
		if len(tr.To) == 0 {
			errs = append(errs, where+": to is required")
		}
		for _, to := range tr.To {
			if !known(to) {
				errs = append(errs, fmt.Sprintf("%s: to %q is not a branch key", where, to))
			}
		}
		if tr.Via != "pr" && tr.Via != "branch" {
			errs = append(errs, fmt.Sprintf("%s: via must be \"pr\" or \"branch\", got %q", where, tr.Via))
		}
		switch tr.MergeMethod {
		case "", "squash", "merge", "rebase":
		default:
			errs = append(errs, fmt.Sprintf("%s: mergeMethod must be squash, merge or rebase, got %q", where, tr.MergeMethod))
		}
		if tr.PRTemplate != "" {
			if _, ok := t.PRTemplates[tr.PRTemplate]; !ok {
				errs = append(errs, fmt.Sprintf("%s: unknown prTemplate %q", where, tr.PRTemplate))
			}
		}
		if tr.Work != "" {
			if err := checkPlaceholders(tr.Work); err != nil {
				errs = append(errs, fmt.Sprintf("%s: work: %v", where, err))
			}
		}
	}
	return errs
}

// validateGraph checks there are no gaps: work branches have somewhere to
// go, and every branch can eventually reach production.
func validateGraph(t Template) []string {
	var errs []string
	next := map[string][]string{}
	for _, tr := range t.Transitions {
		next[tr.From] = append(next[tr.From], tr.To...)
	}
	if len(next[WorkKey]) == 0 {
		errs = append(errs, "no transition starts from \"work\" (work branches have nowhere to go)")
	}
	nodes := []string{WorkKey}
	for k := range t.Branches {
		nodes = append(nodes, k)
	}
	sort.Strings(nodes)
	for _, n := range nodes {
		if n != Production && !reaches(n, Production, next) {
			errs = append(errs, fmt.Sprintf("%q never reaches production through the transitions", n))
		}
	}
	return errs
}

func reaches(from, to string, next map[string][]string) bool {
	seen := map[string]bool{}
	queue := []string{from}
	for len(queue) > 0 {
		n := queue[0]
		queue = queue[1:]
		if n == to {
			return true
		}
		if seen[n] {
			continue
		}
		seen[n] = true
		queue = append(queue, next[n]...)
	}
	return false
}
