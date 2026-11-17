package graph

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// Work branch states.
const (
	StateNew    = "new"     // no commits beyond its base yet
	StateNoPR   = "no-pr"   // has work, no pull request
	StatePROpen = "pr-open" // an open pull request exists
	StateMerged = "merged"  // PR merged, or fully contained in its base
)

// Flow is the workflow view: every branch placed in the active template.
type Flow struct {
	Workflow    string           `json:"workflow"`
	Remote      string           `json:"remote"`
	Columns     []FlowColumn     `json:"columns"`
	Transitions []FlowTransition `json:"transitions"`
	Long        []LongBranch     `json:"long"`
	Work        []WorkItem       `json:"work"`
	// Ignored lists branches that fit nothing in the template.
	Ignored []string `json:"ignored,omitempty"`
	// Next are repository-level next steps (e.g. cut a release).
	Next     []string `json:"next,omitempty"`
	Warnings []string `json:"warnings,omitempty"`
}

// FlowColumn is one template branch key, in flow order ("work" first).
type FlowColumn struct {
	Key       string `json:"key"`
	Name      string `json:"name"` // branch name (pattern for pattern branches); "" for work
	Protected bool   `json:"protected"`
}

// FlowTransition is a template transition with names resolved.
type FlowTransition struct {
	ID          string   `json:"id"`
	From        string   `json:"from"`
	To          []string `json:"to"`
	Via         string   `json:"via"`
	MergeMethod string   `json:"mergeMethod,omitempty"`
}

// LongBranch is a long-lived template branch (production, staging, …).
type LongBranch struct {
	Key       string `json:"key"`
	Name      string `json:"name"`
	Protected bool   `json:"protected"`
	Local     bool   `json:"local"`
	OnRemote  bool   `json:"onRemote"`
	Ahead     int    `json:"ahead"`  // local commits not on the remote
	Behind    int    `json:"behind"` // remote commits not local
	Sync      string `json:"sync"`   // human summary of local vs remote
	// Pending counts commits here that the next branch (Promotes) lacks.
	Promotes string `json:"promotes,omitempty"`
	Pending  int    `json:"pending"`
	OpenPR   *PR    `json:"openPr,omitempty"` // open PR from this branch
	LastPR   *PR    `json:"lastPr,omitempty"` // last merged PR into this branch
	Next     string `json:"next,omitempty"`
}

// WorkItem is one work branch (local, remote or both).
type WorkItem struct {
	Name       string `json:"name"`
	Key        string `json:"key"`  // "work" or a pattern-branch key
	Base       string `json:"base"` // branch its PR goes into
	Local      bool   `json:"local"`
	OnRemote   bool   `json:"onRemote"`
	Ahead      int    `json:"ahead"`  // commits not in base
	Behind     int    `json:"behind"` // base commits not in this branch
	Unpushed   int    `json:"unpushed"`
	NoUpstream bool   `json:"noUpstream"`
	PR         *PR    `json:"pr,omitempty"`
	State      string `json:"state"`
	Next       string `json:"next"`
}

// Divergence is a left-right commit count between two refs.
type Divergence struct {
	Ahead  int `json:"ahead"`  // commits in head not in base
	Behind int `json:"behind"` // commits in base not in head
}

// Compare is a base...head pair whose divergence the flow needs.
type Compare struct{ Base, Head string }

// Key returns "base...head", the map key used in BuildFlow's counts.
func (c Compare) Key() string { return c.Base + "..." + c.Head }

// CollectFlow computes the divergences BuildFlow needs with
// `git rev-list --left-right --count` and builds the flow.
func CollectFlow(ctx context.Context, git *gitx.Git, wf *workflow.Compiled, repo Repo) (Flow, error) {
	counts := map[string]Divergence{}
	for _, c := range FlowComparisons(wf, repo) {
		out, err := git.Query(ctx, "rev-list", "--left-right", "--count", c.Key())
		if err != nil {
			return Flow{}, err
		}
		d, ok := ParseLeftRight(out)
		if ok {
			counts[c.Key()] = d
		}
	}
	return BuildFlow(wf, repo, counts), nil
}

// ParseLeftRight parses "behind\tahead" from rev-list --left-right --count.
func ParseLeftRight(out string) (Divergence, bool) {
	f := strings.Fields(out)
	if len(f) != 2 {
		return Divergence{}, false
	}
	behind, err1 := strconv.Atoi(f[0])
	ahead, err2 := strconv.Atoi(f[1])
	return Divergence{Ahead: ahead, Behind: behind}, err1 == nil && err2 == nil
}

// FlowComparisons lists the base...head pairs BuildFlow looks up: each work
// branch against its base, and each promotion (staging → production).
func FlowComparisons(wf *workflow.Compiled, repo Repo) []Compare {
	var out []Compare
	for _, name := range workNames(wf, repo) {
		base, head := baseRef(repo, workBase(wf, name)), headRef(repo, name)
		if base != "" && head != "" {
			out = append(out, Compare{base, head})
		}
	}
	for _, p := range promotions(wf) {
		from, to := baseRef(repo, p[0]), baseRef(repo, p[1])
		if from != "" && to != "" {
			out = append(out, Compare{to, from})
		}
	}
	return out
}

// BuildFlow classifies every branch. It is pure: counts come from
// FlowComparisons (missing entries count as unknown → zero).
func BuildFlow(wf *workflow.Compiled, repo Repo, counts map[string]Divergence) Flow {
	f := Flow{Workflow: wf.Template.Name, Remote: repo.Remote, Warnings: repo.Warnings}
	f.Columns = flowColumns(wf)
	f.Transitions = flowTransitions(wf)
	for _, col := range f.Columns {
		if col.Key == workflow.WorkKey || wf.Template.Branches[col.Key].Pattern {
			continue
		}
		f.Long = append(f.Long, longBranch(wf, repo, counts, col))
	}
	for _, name := range workNames(wf, repo) {
		f.Work = append(f.Work, workItem(wf, repo, counts, name))
	}
	f.Ignored = ignoredNames(wf, repo)
	for _, l := range f.Long {
		if l.Next != "" {
			f.Next = append(f.Next, l.Next)
		}
	}
	return f
}

// flowColumns orders template keys: work, then breadth-first along the
// transitions, then any key no transition reaches.
func flowColumns(wf *workflow.Compiled) []FlowColumn {
	order := []string{workflow.WorkKey}
	seen := map[string]bool{workflow.WorkKey: true}
	for i := 0; i < len(order); i++ {
		for _, tr := range wf.Template.Transitions {
			if tr.From != order[i] {
				continue
			}
			for _, to := range tr.To {
				if !seen[to] {
					seen[to] = true
					order = append(order, to)
				}
			}
		}
	}
	for _, k := range []string{workflow.Staging, workflow.Production} {
		if _, ok := wf.Template.Branches[k]; ok && !seen[k] {
			seen[k] = true
			order = append(order, k)
		}
	}
	cols := make([]FlowColumn, 0, len(order))
	for _, k := range order {
		b := wf.Template.Branches[k]
		cols = append(cols, FlowColumn{Key: k, Name: b.Name, Protected: b.Protected})
	}
	return cols
}

func flowTransitions(wf *workflow.Compiled) []FlowTransition {
	var out []FlowTransition
	for _, tr := range wf.Template.Transitions {
		out = append(out, FlowTransition{ID: tr.ID, From: tr.From, To: []string(tr.To), Via: tr.Via, MergeMethod: tr.MergeMethod})
	}
	return out
}

// promotions returns [from, to] branch-name pairs of PR transitions between
// long-lived (non-pattern) branches, e.g. staging → production.
func promotions(wf *workflow.Compiled) [][2]string {
	var out [][2]string
	for _, tr := range wf.Template.Transitions {
		from, ok := wf.Template.Branches[tr.From]
		if tr.Via != "pr" || !ok || from.Pattern {
			continue
		}
		for _, to := range tr.To {
			if b, ok := wf.Template.Branches[to]; ok && !b.Pattern {
				out = append(out, [2]string{from.Name, b.Name})
			}
		}
	}
	return out
}

func longBranch(wf *workflow.Compiled, repo Repo, counts map[string]Divergence, col FlowColumn) LongBranch {
	l := LongBranch{Key: col.Key, Name: col.Name, Protected: col.Protected}
	local, hasLocal := repo.Branch(col.Name)
	_, hasRemote := repo.Branch(repo.Remote + "/" + col.Name)
	l.Local, l.OnRemote = hasLocal, hasRemote
	if hasLocal {
		l.Ahead, l.Behind = local.Ahead, local.Behind
	}
	l.Sync = syncText(l, repo.Remote)
	if pr, ok := repo.LastMergedInto(col.Name); ok {
		l.LastPR = &pr
	}
	for _, p := range promotions(wf) {
		if p[0] != col.Name {
			continue
		}
		l.Promotes = p[1]
		l.Pending = counts[Compare{baseRef(repo, p[1]), baseRef(repo, p[0])}.Key()].Ahead
		if pr, ok := findOpen(repo, p[0], p[1]); ok {
			l.OpenPR = &pr
			l.Next = fmt.Sprintf("release PR #%d open: merge it on GitHub", pr.Number)
		} else if l.Pending > 0 {
			l.Next = fmt.Sprintf("mygit pr release (%d commits)", l.Pending)
		}
		break
	}
	return l
}

func syncText(l LongBranch, remote string) string {
	switch {
	case !l.Local && !l.OnRemote:
		return "missing"
	case !l.Local:
		return "only on " + remote
	case !l.OnRemote:
		return "not on " + remote
	case l.Ahead == 0 && l.Behind == 0:
		return "in sync with " + remote
	}
	var parts []string
	if l.Ahead > 0 {
		parts = append(parts, fmt.Sprintf("%d local commits not on %s", l.Ahead, remote))
	}
	if l.Behind > 0 {
		parts = append(parts, fmt.Sprintf("%d behind %s", l.Behind, remote))
	}
	return strings.Join(parts, ", ")
}

func findOpen(repo Repo, head, base string) (PR, bool) {
	for _, p := range repo.PRs {
		if p.State == PROpen && p.Head == head && p.Base == base {
			return p, true
		}
	}
	return PR{}, false
}

func workItem(wf *workflow.Compiled, repo Repo, counts map[string]Divergence, name string) WorkItem {
	w := WorkItem{Name: name, Key: wf.BranchKey(name), Base: workBase(wf, name)}
	local, hasLocal := repo.Branch(name)
	remote, hasRemote := repo.Branch(repo.Remote + "/" + name)
	w.Local, w.OnRemote = hasLocal, hasRemote
	d := counts[Compare{baseRef(repo, w.Base), headRef(repo, name)}.Key()]
	w.Ahead, w.Behind = d.Ahead, d.Behind
	switch {
	case hasLocal && local.Upstream == "":
		w.NoUpstream, w.Unpushed = true, w.Ahead
	case hasLocal:
		w.Unpushed = local.Ahead
	}
	if pr, ok := repo.OpenPR(name); ok {
		w.PR = &pr
	} else if pr, ok := repo.MergedPR(name); ok {
		w.PR = &pr
	}
	tip := local.SHA
	if !hasLocal {
		tip = remote.SHA
	}
	baseTip := tipOf(repo, w.Base)
	w.State, w.Next = classifyWork(w, tip == baseTip)
	return w
}

// classifyWork picks the state and the one next command. atBase means the
// branch tip is exactly its base's tip (a fresh branch, not merged work).
func classifyWork(w WorkItem, atBase bool) (state, next string) {
	switch {
	case w.PR != nil && w.PR.State == PROpen:
		switch {
		case w.Unpushed > 0:
			return StatePROpen, "mygit push"
		case w.PR.Checks == ChecksFail:
			return StatePROpen, "fix failing checks, then mygit push"
		case w.PR.Draft:
			return StatePROpen, "mark the PR ready for review on GitHub"
		}
		return StatePROpen, "wait for review / merge on GitHub"
	case w.PR != nil && w.PR.State == PRMerged, w.Ahead == 0 && !atBase:
		return StateMerged, "mygit branch prune"
	case w.Ahead == 0:
		return StateNew, "mygit commit"
	}
	return StateNoPR, "mygit pr create"
}

// workNames lists work and pattern-branch names, local first, then
// remote-only ones, in branch order.
func workNames(wf *workflow.Compiled, repo Repo) []string {
	var names []string
	seen := map[string]bool{}
	for _, name := range branchNames(repo) {
		key := wf.BranchKey(name)
		isWork := key == workflow.WorkKey || (key != "" && wf.Template.Branches[key].Pattern)
		if isWork && !seen[name] {
			seen[name] = true
			names = append(names, name)
		}
	}
	return names
}

func ignoredNames(wf *workflow.Compiled, repo Repo) []string {
	var out []string
	seen := map[string]bool{}
	for _, name := range branchNames(repo) {
		if wf.BranchKey(name) == "" && !seen[name] {
			seen[name] = true
			out = append(out, name)
		}
	}
	return out
}

// branchNames returns local branch names and the remote's branch names with
// the remote prefix removed (other remotes are skipped).
func branchNames(repo Repo) []string {
	var out []string
	prefix := repo.Remote + "/"
	for _, b := range repo.Branches {
		switch {
		case !b.Remote:
			out = append(out, b.Name)
		case strings.HasPrefix(b.Name, prefix):
			out = append(out, strings.TrimPrefix(b.Name, prefix))
		}
	}
	return out
}

// workBase is the branch a work branch's PR targets (its PR route), or the
// template's work start point.
func workBase(wf *workflow.Compiled, name string) string {
	if r, ok := wf.PRRoute(name); ok && len(r.Bases) > 0 && !strings.Contains(r.Bases[0], "{") {
		return r.Bases[0]
	}
	return wf.WorkFrom
}

// baseRef prefers the remote copy of a base branch (merges happen there).
func baseRef(repo Repo, name string) string {
	if _, ok := repo.Branch(repo.Remote + "/" + name); ok {
		return repo.Remote + "/" + name
	}
	if _, ok := repo.Branch(name); ok {
		return name
	}
	return ""
}

// headRef prefers the local copy of a work branch (it may hold unpushed work).
func headRef(repo Repo, name string) string {
	if _, ok := repo.Branch(name); ok {
		return name
	}
	if _, ok := repo.Branch(repo.Remote + "/" + name); ok {
		return repo.Remote + "/" + name
	}
	return ""
}

func tipOf(repo Repo, name string) string {
	if b, ok := repo.Branch(baseRef(repo, name)); ok {
		return b.SHA
	}
	return ""
}
