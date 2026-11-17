package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"slices"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(62, newBranchCmd) }

func newBranchCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{
		Use:   "branch",
		Short: "create and clean up branches following the active workflow",
		Long: "branch plans every change (dry run by default; --yes or --apply <id> runs it).\n" +
			"Names and bases come from the active workflow template (see: mygit workflow show).",
	}
	cmd.AddCommand(branchNewCmd(a, s), branchCutCmd(a, s), branchPruneCmd(a, s))
	return cmd
}

// ---------------------------------------------------------------- new / cut

func branchNewCmd(a *App, s *session) *cobra.Command {
	var ticket string
	cmd := &cobra.Command{
		Use:     "new <type> <name>",
		Short:   "create a work branch from the workflow's work pattern",
		Example: "  mygit branch new feat login --yes   # ✓ develop/feat/login (from develop/base)",
		Args:    cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			name, err := c.Workflow.BranchNewName(args[0], args[1], ticket, "")
			if err != nil {
				return err
			}
			return branchCreate(a, s, cmd, args, e, c, name, c.Workflow.WorkFrom, "new", nil)
		},
	}
	cmd.Flags().StringVar(&ticket, "ticket", "", "ticket id for patterns with {ticket}, e.g. ABC-12")
	s.addOutFlag(cmd)
	return cmd
}

func branchCutCmd(a *App, s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "cut <transition> [version]",
		Short: "create the branch a workflow transition defines (release, hotfix, …)",
		Long: "cut creates the branch of a workflow transition from the transition's source branch,\n" +
			"e.g. release/<version> from develop. Transitions that create a branch use via \"branch\"\n" +
			"or carry their own work pattern. The optional second argument fills {version} (or {name}).",
		Example: "  mygit branch cut cut 1.2.0 --workflow release-train",
		Args:    cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			value := ""
			if len(args) == 2 {
				value = args[1]
			}
			cut, err := c.Workflow.BranchCutFor(args[0], value)
			if err != nil {
				return err
			}
			// The transition itself says where the branch starts, so the
			// generic "work branches start from staging" rule (BR004) is moot.
			return branchCreate(a, s, cmd, args, e, c, cut.Name, cut.Base, "cut "+cut.Transition.ID, []string{"BR004"})
		},
	}
	s.addOutFlag(cmd)
	return cmd
}

// branchCreate plans `git switch --no-track -c name start` (no tracking: the
// new branch must not inherit the base as its upstream, or push would think it is up to date), checked against the rules
// (minus ignore). base is the branch name shown to the user.
func branchCreate(a *App, s *session, cmd *cobra.Command, args []string, e *app.Env, c *config.Resolved,
	name, base, what string, ignore []string) error {
	ctx := cmd.Context()
	if err := e.RequireRepo(); err != nil {
		return err
	}
	if ok, err := e.Git.BranchExists(ctx, name); err != nil {
		return err
	} else if ok {
		return fmt.Errorf("branch %s already exists (switch to it: git switch %s)", name, name)
	}
	start, err := branchStart(ctx, e, c, base)
	if err != nil {
		return err
	}
	v := branchEvaluate(a, e, c, execx.Cmd{Name: "git", Args: []string{"switch", "--no-track", "-c", name, start}}, ignore)
	if v.Decision() == verdict.DecDeny {
		return lintReport(a, v)
	}
	p := e.NewPlanner(planArgv(cmd, args))
	branchChecks(p, v)
	p.Git("create "+name+" from "+start, "switch", "--no-track", "-c", name, start)
	after := func(w io.Writer) { fmt.Fprintf(w, "✓ %s (from %s)\n", name, base) }
	return s.runExtra(cmd, p, after, map[string]any{"branch": name, "base": base, "startPoint": start, "action": what})
}

// branchStart returns the ref a new branch starts from: the local branch,
// else <remote>/<branch>.
func branchStart(ctx context.Context, e *app.Env, c *config.Resolved, base string) (string, error) {
	if ok, err := e.Git.BranchExists(ctx, base); err != nil {
		return "", err
	} else if ok {
		return base, nil
	}
	remote := c.Workflow.Remote
	if res, err := e.Git.Exec(ctx, "show-ref", "--verify", "--quiet", "refs/remotes/"+remote+"/"+base); err == nil && res.OK() {
		return remote + "/" + base, nil
	}
	return "", fmt.Errorf("base branch %s not found locally or on %s; fetch it first: git fetch %s", base, remote, remote)
}

// branchEvaluate runs the rules engine over one planned command, dropping
// findings of the ignored rule ids.
func branchEvaluate(a *App, e *app.Env, c *config.Resolved, cmd execx.Cmd, ignore []string) verdict.Verdict {
	words := make([]string, 0, len(cmd.Args)+1)
	words = append(words, cmd.Name)
	for _, w := range cmd.Args {
		words = append(words, shellfmt.Quote(shellfmt.Bash, w))
	}
	v := engine.Evaluate(engine.Request{Command: strings.Join(words, " "), Dialect: shell.Bash, Cwd: e.RepoRoot},
		c.Policy, e.EngineDeps())
	var out verdict.Verdict
	for _, f := range v.Findings {
		if !slices.Contains(ignore, f.RuleID) {
			out.Findings = append(out.Findings, f)
		}
	}
	return out
}

// branchChecks records the findings as plan checks (a passing BR003 when clean).
func branchChecks(p *app.Planner, v verdict.Verdict) {
	if len(v.Findings) == 0 {
		p.Check("BR003", true, verdict.Off, "branch name matches the workflow")
		return
	}
	for _, f := range v.Findings {
		p.Check(f.RuleID, false, f.Severity, f.Message)
	}
}

// ---------------------------------------------------------------- prune

func branchPruneCmd(a *App, s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "prune",
		Short: "delete local work branches that are already merged",
		Long: "prune plans `git branch -d` for local work branches merged into the workflow's base branches\n" +
			"(git branch --merged). It never touches protected branches, the current branch or branches\n" +
			"checked out in another worktree. Branches whose pull request was merged on GitHub (gh pr list\n" +
			"--state merged) but that git cannot see as merged, such as squash merges, are only reported:\n" +
			"-d would refuse them and prune never uses -D.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			if err := e.RequireRepo(); err != nil {
				return err
			}
			return branchPrune(a, s, cmd, args, e, c)
		},
	}
	s.addOutFlag(cmd)
	return cmd
}

type pruneRow struct {
	Name   string `json:"name"`
	Reason string `json:"reason,omitempty"`
}

func branchPrune(a *App, s *session, cmd *cobra.Command, args []string, e *app.Env, c *config.Resolved) error {
	ctx := cmd.Context()
	w := c.Workflow
	current, err := e.Git.CurrentBranch(ctx)
	if err != nil {
		return err
	}
	p := e.NewPlanner(planArgv(cmd, args))

	// Bases: staging (where work lands) and production, each as local ref or remote tracking ref.
	var bases []string
	for _, b := range []string{w.WorkFrom, w.Production} {
		if b == "" || slices.Contains(bases, b) {
			continue
		}
		bases = append(bases, b)
	}
	merged := map[string]bool{}
	noted := map[string]bool{}
	var candidates []string
	var skipped []pruneRow
	for _, b := range bases {
		start, err := branchStart(ctx, e, c, b)
		if err != nil {
			p.Note("skipped base %s: not found locally or on %s", b, w.Remote)
			continue
		}
		tip, _ := e.Git.Query(ctx, "rev-parse", start)
		res, err := p.Read(ctx, e.Git.Cmd("branch", "--merged", start, "--format=%(refname:short)%09%(objectname)%09%(worktreepath)"),
			"merged into "+start, func(r execx.Result) string {
				n := 0
				for _, l := range strings.Split(r.Stdout, "\n") {
					if strings.TrimSpace(l) != "" {
						n++
					}
				}
				return fmt.Sprintf("%d branch(es)", n)
			})
		if err != nil {
			return err
		}
		if !res.OK() {
			return fmt.Errorf("git branch --merged %s: %s", start, strings.TrimSpace(res.Stderr))
		}
		for _, line := range strings.Split(res.Stdout, "\n") {
			f := strings.Split(strings.TrimRight(line, "\r"), "\t")
			name := strings.TrimSpace(f[0])
			if name == "" || merged[name] {
				continue
			}
			switch {
			case !w.BranchPrunable(name):
				continue // protected or outside the workflow: not ours to prune, not worth a note
			case name == current:
				if !noted[name] {
					noted[name] = true
					skipped = append(skipped, pruneRow{name, "current branch"})
				}
			case len(f) > 1 && f[1] == tip:
				skipped = append(skipped, pruneRow{name, "same commit as " + start + " (no work yet)"})
			case len(f) > 2 && strings.TrimSpace(f[2]) != "":
				if !noted[name] {
					noted[name] = true
					skipped = append(skipped, pruneRow{name, "checked out in worktree " + strings.TrimSpace(f[2])})
				}
			default:
				merged[name] = true
				candidates = append(candidates, name)
			}
		}
	}

	prMerged, idDeny := branchMergedPRs(ctx, a, e, c, p)
	if idDeny != nil {
		return lintReport(a, *idDeny)
	}
	var unseen []pruneRow
	var names []string
	for n := range prMerged {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		if merged[n] || n == current || !w.BranchPrunable(n) {
			continue
		}
		if ok, _ := e.Git.BranchExists(ctx, n); ok {
			unseen = append(unseen, pruneRow{n, fmt.Sprintf("PR #%d merged, but git does not see it as merged (squash?); check and run: git branch -D %s", prMerged[n], n)})
		}
	}

	sort.Strings(candidates)
	var deletable []string
	for _, n := range candidates {
		v := branchEvaluate(a, e, c, execx.Cmd{Name: "git", Args: []string{"branch", "-d", n}}, nil)
		if v.Decision() == verdict.DecDeny {
			skipped = append(skipped, pruneRow{n, v.Reason()})
			continue
		}
		if !branchDeletable(ctx, e, n) {
			skipped = append(skipped, pruneRow{n, "merged into a base branch but not into HEAD or its upstream, so git branch -d would refuse; switch to the base branch first"})
			continue
		}
		deletable = append(deletable, n)
		p.Git("delete "+n, "branch", "-d", n)
	}
	candidates = deletable
	for _, r := range append(skipped, unseen...) {
		p.Note("kept %s: %s", r.Name, r.Reason)
	}
	after := func(w io.Writer) {
		for _, n := range candidates {
			fmt.Fprintf(w, "✓ deleted %s\n", n)
		}
	}
	return s.runExtra(cmd, p, after, map[string]any{"delete": nonNil(candidates), "kept": nonNilRows(append(skipped, unseen...))})
}

// branchDeletable reports whether `git branch -d name` would accept the
// branch: it is merged into HEAD or into its own upstream.
func branchDeletable(ctx context.Context, e *app.Env, name string) bool {
	if res, err := e.Git.Exec(ctx, "merge-base", "--is-ancestor", name, "HEAD"); err == nil && res.OK() {
		return true
	}
	up, err := e.Git.Query(ctx, "rev-parse", "--abbrev-ref", name+"@{upstream}")
	if err != nil || strings.TrimSpace(up) == "" {
		return false
	}
	res, err := e.Git.Exec(ctx, "merge-base", "--is-ancestor", name, strings.TrimSpace(up))
	return err == nil && res.OK()
}

func branchDecodePRs(out string, v *[]ghx.PR) error { return json.Unmarshal([]byte(out), v) }

func nonNil(l []string) []string {
	if l == nil {
		return []string{}
	}
	return l
}

func nonNilRows(l []pruneRow) []pruneRow {
	if l == nil {
		return []pruneRow{}
	}
	return l
}

// branchMergedPRs returns head branch → PR number for merged pull requests,
// or nil when gh is unavailable, the account fails ID005 or there is no remote.
func branchMergedPRs(ctx context.Context, a *App, e *app.Env, c *config.Resolved, p *app.Planner) (map[string]int, *verdict.Verdict) {
	if e.RemoteURL == "" {
		return nil, nil
	}
	if _, err := e.Gh.Version(ctx); err != nil {
		p.Note("gh not available: only git's own merge information was used")
		return nil, nil
	}
	v := branchEvaluate(a, e, c, execx.Cmd{Name: "gh", Args: []string{"pr", "list", "--state", "merged"}}, nil)
	clean := true
	for _, f := range v.Findings {
		if f.RuleID != "ID005" {
			continue
		}
		clean = false
		p.Check("ID005", false, f.Severity, f.Message)
		if f.Severity >= verdict.Ask {
			p.Note("skipped merged pull requests: gh account check failed (ID005)")
			if f.Severity == verdict.Deny {
				return nil, &verdict.Verdict{Findings: []verdict.Finding{f}}
			}
			return nil, nil
		}
	}
	if clean {
		p.Check("ID005", true, verdict.Off, "gh account matches the profile")
	}
	var prs []ghx.PR
	args := []string{"pr", "list", "--state", "merged", "--limit", "200", "--json", "number,headRefName"}
	cmd := e.Gh.Cmd(args...)
	res, err := p.Read(ctx, cmd, "merged pull requests", nil)
	if err != nil || !res.OK() {
		p.Note("could not list merged pull requests; only git's own merge information was used")
		return nil, nil
	}
	if err := branchDecodePRs(res.Stdout, &prs); err != nil {
		p.Note("could not read merged pull requests: %v", err)
		return nil, nil
	}
	out := map[string]int{}
	for _, pr := range prs {
		if _, dup := out[pr.HeadRefName]; !dup {
			out[pr.HeadRefName] = pr.Number
		}
	}
	return out, nil
}
