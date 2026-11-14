package graph

import (
	"fmt"
	"io"
	"strings"
	"unicode/utf8"
)

// FlowTextOptions control RenderFlowText.
type FlowTextOptions struct {
	ASCII bool
	Color bool
}

// RenderFlowText writes the flow view as terminal text: the template's
// transitions, each work branch with its state and next command, then the
// long-lived branches and the repository-level next step.
func RenderFlowText(w io.Writer, f Flow, opt FlowTextOptions) error {
	sym := SymbolsFor(opt.ASCII)
	tOpt := TextOptions{Color: opt.Color}
	var b strings.Builder
	fmt.Fprintf(&b, "workflow %s %s remote %s\n", f.Workflow, sym.Dot, f.Remote)
	for _, tr := range f.Transitions {
		fmt.Fprintf(&b, "  %-10s %s %s %s  %s\n", tr.ID, colName(f, tr.From, sym),
			sym.Arrow, joinCols(f, tr.To, sym), dim(tOpt, transitionNote(tr)))
	}
	b.WriteString("\n" + bold(opt, "WORK BRANCHES") + "\n")
	if len(f.Work) == 0 {
		b.WriteString("  (none)\n")
	}
	nameW := 0
	for _, it := range f.Work {
		nameW = max(nameW, utf8.RuneCountInString(it.Name))
	}
	for _, it := range f.Work {
		fmt.Fprintf(&b, "  %s  %s\n", padRight(it.Name, nameW), WorkStatus(it, sym))
		fmt.Fprintf(&b, "  %s  %s %s\n", strings.Repeat(" ", nameW), dim(tOpt, "next:"), colorNext(opt, it.State, it.Next))
	}
	for _, l := range f.Long {
		fmt.Fprintf(&b, "\n%s  %s\n", bold(opt, strings.ToUpper(l.Key)), longTitle(l, sym))
		fmt.Fprintf(&b, "  %s\n", LongStatus(l, sym))
	}
	if len(f.Ignored) > 0 {
		fmt.Fprintf(&b, "\n%s\n", dim(tOpt, "outside the workflow: "+strings.Join(f.Ignored, ", ")))
	}
	for _, n := range f.Next {
		fmt.Fprintf(&b, "\nnext: %s\n", colorNext(opt, "", n))
	}
	for _, warn := range f.Warnings {
		b.WriteString(dim(tOpt, "note: "+warn) + "\n")
	}
	_, err := io.WriteString(w, b.String())
	return err
}

// WorkStatus summarises a work item: "↑2 vs develop/base · 2 not pushed · no PR".
func WorkStatus(it WorkItem, sym Symbols) string {
	var parts []string
	if it.State != StateMerged {
		parts = append(parts, fmt.Sprintf("%s%d vs %s", sym.Up, it.Ahead, it.Base))
		if it.Behind > 0 {
			parts[0] += fmt.Sprintf(" %s%d", sym.Down, it.Behind)
		}
	}
	switch {
	case it.NoUpstream && it.Unpushed > 0:
		parts = append(parts, fmt.Sprintf("%d not pushed", it.Unpushed))
	case it.NoUpstream && it.Local:
		parts = append(parts, "not pushed")
	case it.Unpushed > 0:
		parts = append(parts, fmt.Sprintf("%d not pushed", it.Unpushed))
	}
	if !it.Local {
		parts = append(parts, "only on remote")
	}
	parts = append(parts, prStatus(it, sym))
	return strings.Join(parts, " "+sym.Dot+" ")
}

func prStatus(it WorkItem, sym Symbols) string {
	switch {
	case it.PR == nil && it.State == StateMerged:
		return "contained in " + it.Base + leftOver(it)
	case it.PR == nil:
		return "no PR"
	case it.PR.State == PRMerged:
		return fmt.Sprintf("PR #%d merged", it.PR.Number) + leftOver(it)
	}
	s := fmt.Sprintf("PR #%d", it.PR.Number)
	if it.PR.Draft {
		s += " draft"
	}
	switch it.PR.Checks {
	case ChecksPass:
		s += " " + sym.Dot + " checks " + sym.Check
	case ChecksFail:
		s += " " + sym.Dot + " checks " + sym.Cross
	case ChecksPending:
		s += " " + sym.Dot + " checks " + sym.Pending
	}
	return s + " " + sym.Dot + " review pending"
}

func leftOver(it WorkItem) string {
	switch {
	case it.Local && it.OnRemote:
		return " " + "· local and remote branch left"
	case it.Local:
		return " · local branch left"
	case it.OnRemote:
		return " · remote branch left"
	}
	return ""
}

// LongStatus summarises a long-lived branch: sync and pending promotion.
func LongStatus(l LongBranch, sym Symbols) string {
	parts := []string{l.Sync}
	if l.Promotes != "" {
		parts = append(parts, fmt.Sprintf("%d commits not in %s", l.Pending, l.Promotes))
	}
	if l.OpenPR != nil {
		parts = append(parts, fmt.Sprintf("PR #%d open", l.OpenPR.Number))
	}
	if l.LastPR != nil {
		parts = append(parts, fmt.Sprintf("last merged PR #%d", l.LastPR.Number))
	}
	return strings.Join(parts, " "+sym.Dot+" ")
}

func longTitle(l LongBranch, sym Symbols) string {
	if l.Protected {
		return l.Name + " " + sym.Shield
	}
	return l.Name
}

func transitionNote(tr FlowTransition) string {
	note := "via " + tr.Via
	if tr.MergeMethod != "" {
		note += ", " + tr.MergeMethod
	}
	return "(" + note + ")"
}

// colName returns a column's display name: the branch name, or "work".
func colName(f Flow, key string, sym Symbols) string {
	for _, c := range f.Columns {
		if c.Key == key && c.Name != "" {
			if c.Protected {
				return c.Name + " " + sym.Shield
			}
			return c.Name
		}
	}
	return key
}

func joinCols(f Flow, keys []string, sym Symbols) string {
	names := make([]string, len(keys))
	for i, k := range keys {
		names[i] = colName(f, k, sym)
	}
	return strings.Join(names, ", ")
}

func bold(opt FlowTextOptions, s string) string {
	if opt.Color {
		return cBold + s + cReset
	}
	return s
}

func colorNext(opt FlowTextOptions, state, s string) string {
	if !opt.Color {
		return s
	}
	if state == StateMerged {
		return cGreen + s + cReset
	}
	return cCyan + s + cReset
}

func padRight(s string, w int) string {
	if n := utf8.RuneCountInString(s); n < w {
		return s + strings.Repeat(" ", w-n)
	}
	return s
}
