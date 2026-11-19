package rules

import (
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// branchModel holds the branching contract's settings.
type branchModel struct{ prod, staging, remote string }

func modelOf(c *policy.Contract) branchModel {
	s := settings(c)
	return branchModel{
		prod:    policy.Str(s, "production_branch", "main"),
		staging: policy.Str(s, "staging_branch", "develop/base"),
		remote:  policy.Str(s, "remote", "origin"),
	}
}

func (m branchModel) protected() []string { return []string{m.prod, m.staging} }

// CheckGitBranching applies BR001 (no work on protected branches), BR002
// (no direct pushes to them) and BR003/BR004 (work branch name and base).
func CheckGitBranching(call *shell.GitCall, c *policy.Contract, repo Repo) verdict.Verdict {
	var v verdict.Verdict
	v.Extend(checkLocalCommit(call, c, repo))
	v.Extend(checkPush(call, c, repo))
	v.Extend(checkNewBranch(call, c, repo))
	return v
}

// ---------------------------------------------------------------- BR001

func checkLocalCommit(call *shell.GitCall, c *policy.Contract, repo Repo) verdict.Verdict {
	var v verdict.Verdict
	r := c.Rule("BR001")
	if r.Severity == verdict.Off || !contains(policy.Strings(r.Params, "subcommands"), call.Sub) {
		return v
	}
	argv := call.Argv()
	if call.Sub == "merge" && (contains(argv, "--ff-only") || contains(argv, "--abort") || contains(argv, "--continue")) {
		return v
	}
	switch call.Sub {
	case "rebase", "cherry-pick", "revert", "am":
		for _, a := range []string{"--abort", "--continue", "--skip", "--quit"} {
			if contains(argv, a) {
				return v
			}
		}
	}
	m := modelOf(c)
	branch, ok := repo.CurrentBranch()
	if !ok || !contains(m.protected(), branch) {
		return v
	}
	if call.Sub == "commit" && !repo.HasCommits() {
		v.Addf("BR001", policy.Sev(r.Params, "initial_commit_severity", verdict.Ask),
			"initial commit directly on '%s' (allowed once to bootstrap the repository)", branch)
		return v
	}
	v.Addf("BR001", r.Severity, "git %s on protected branch '%s': create a work branch first, e.g. "+
		"git switch -c develop/feat/<name> %s, and merge through a PR", call.Sub, branch, m.staging)
	return v
}

// ---------------------------------------------------------------- BR002

var pushWithValue = valueSet("--repo", "--receive-pack", "--exec", "-o", "--push-option", "--signed")

type pushTarget struct {
	dst    string
	delete bool
}

func shortRef(ref string) string {
	return strings.TrimPrefix(strings.TrimLeft(ref, "+"), "refs/heads/")
}

// pushTargets returns the remote, the destination branches and whether the
// push touches every branch (--all/--mirror/--branches).
func pushTargets(call *shell.GitCall, repo Repo) (string, []pushTarget, bool) {
	argv := call.Argv()
	everything, del := false, false
	for _, a := range argv {
		everything = everything || a == "--all" || a == "--mirror" || a == "--branches"
		del = del || a == "--delete" || a == "-d"
	}
	var pos []string
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		if a == "--" {
			pos = append(pos, argv[i+1:]...)
			break
		}
		if pushWithValue[a] {
			i++
			continue
		}
		if !strings.HasPrefix(a, "-") {
			pos = append(pos, a)
		}
	}
	remote := ""
	if len(pos) > 0 {
		remote = pos[0]
	}
	cur, hasCur := repo.CurrentBranch()
	var targets []pushTarget
	for _, spec := range tail(pos) {
		if strings.HasPrefix(spec, "refs/tags/") || spec == "tag" {
			continue
		}
		t := pushTarget{dst: shortRef(spec), delete: del}
		if src, dst, ok := strings.Cut(spec, ":"); ok {
			t.dst = shortRef(dst)
			if dst == "" {
				t.dst = shortRef(src)
			}
			t.delete = strings.TrimLeft(src, "+") == ""
		}
		if (t.dst == "HEAD" || t.dst == "@") && hasCur {
			t.dst = cur
		}
		targets = append(targets, t)
	}
	if len(tail(pos)) == 0 && !everything && hasCur {
		targets = append(targets, pushTarget{dst: cur}) // push.default simple/upstream: the current branch
	}
	return remote, targets, everything
}

func tail(xs []string) []string {
	if len(xs) == 0 {
		return nil
	}
	return xs[1:]
}

func checkPush(call *shell.GitCall, c *policy.Contract, repo Repo) verdict.Verdict {
	var v verdict.Verdict
	r := c.Rule("BR002")
	if r.Severity == verdict.Off || call.Sub != "push" {
		return v
	}
	m := modelOf(c)
	remote, targets, everything := pushTargets(call, repo)
	if remote == "" {
		remote = m.remote
	}
	if everything {
		v.Addf("BR002", r.Severity, "git push --all/--mirror would also update the protected branches %s", pyList(m.protected()))
		return v
	}
	for _, t := range targets {
		switch {
		case !contains(m.protected(), t.dst):
		case t.delete:
			v.Addf("BR002", r.Severity, "deleting protected branch '%s' on the remote is not allowed", t.dst)
		case !repo.RemoteBranchExists(remote, t.dst):
			v.Addf("BR002", policy.Sev(r.Params, "new_branch_severity", verdict.Ask),
				"first push of protected branch '%s' to %s (bootstrap only; later changes go through PRs)", t.dst, remote)
		default:
			v.Addf("BR002", r.Severity,
				"direct push to protected branch '%s': push a work branch and open a PR (/my-git:pr)", t.dst)
		}
	}
	return v
}

// ---------------------------------------------------------------- BR003 / BR004

var branchListFlags = valueSet("-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-l", "--list", "-a",
	"--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--show-current", "--contains",
	"--no-contains", "--merged", "--no-merged", "--points-at", "--edit-description",
	"-u", "--set-upstream-to", "--unset-upstream", "--sort", "--format", "--column")

var createFlags = map[string][]string{
	"checkout": {"-b", "-B"},
	"switch":   {"-c", "-C", "--create", "--force-create"},
}

// newBranch returns the branch a command creates and its start point
// (hasStart false when none is given).
func newBranch(call *shell.GitCall) (name, start string, hasStart, ok bool) {
	argv := call.Argv()
	if flags, isCreate := createFlags[call.Sub]; isCreate {
		for i, a := range argv {
			flag, inline, _ := strings.Cut(a, "=")
			if !contains(flags, flag) || (inline == "" && i+1 >= len(argv)) {
				continue
			}
			from := i + 2
			name = inline
			if inline == "" {
				name = argv[i+1]
			} else {
				from = i + 1
			}
			for _, x := range argv[min(from, len(argv)):] {
				if !strings.HasPrefix(x, "-") {
					return name, x, true, true
				}
			}
			return name, "", false, true
		}
		return "", "", false, false
	}
	if call.Sub != "branch" {
		return "", "", false, false
	}
	var pos []string
	for _, a := range argv {
		if branchListFlags[strings.SplitN(a, "=", 2)[0]] {
			return "", "", false, false
		}
		if !strings.HasPrefix(a, "-") {
			pos = append(pos, a)
		}
	}
	switch len(pos) {
	case 0:
		return "", "", false, false
	case 1:
		return pos[0], "", false, true
	}
	return pos[0], pos[1], true, true
}

func checkNewBranch(call *shell.GitCall, c *policy.Contract, repo Repo) verdict.Verdict {
	var v verdict.Verdict
	name, start, hasStart, ok := newBranch(call)
	if !ok {
		return v
	}
	m := modelOf(c)
	if name == m.prod || name == m.staging {
		return v
	}
	if r3 := c.Rule("BR003"); r3.Severity != verdict.Off {
		if pats := policy.Strings(r3.Params, "patterns"); len(pats) > 0 && !anySearch(pats, name, false) {
			v.Addf("BR003", r3.Severity, "branch name '%s' must match %s (e.g. develop/feat/<feature>)",
				name, strings.Join(pats, " or "))
		}
	}
	r4 := c.Rule("BR004")
	if r4.Severity == verdict.Off {
		return v
	}
	allowed := append([]string{m.staging, m.remote + "/" + m.staging, "refs/heads/" + m.staging,
		"refs/remotes/" + m.remote + "/" + m.staging}, policy.Strings(r4.Params, "extra_base_refs")...)
	base, hasBase := start, hasStart
	if !hasStart {
		base, hasBase = repo.CurrentBranch()
	}
	if hasBase && contains(allowed, base) {
		return v
	}
	shown := fmt.Sprintf("'%s'", start)
	if !hasStart {
		shown = fmt.Sprintf("the current branch '%s'", orNone(base, hasBase))
	}
	v.Addf("BR004", r4.Severity, "work branch '%s' must start from %s, not %s: git switch -c %s %s/%s",
		name, m.staging, shown, name, m.remote, m.staging)
	return v
}

// ---------------------------------------------------------------- BR005

// lastFlag returns the last value of -X v / --long v / --long=v.
func lastFlag(argv []string, short, long string) (string, bool) {
	val, ok := "", false
	for i, a := range argv {
		if (a == short || a == long) && i+1 < len(argv) {
			val, ok = argv[i+1], true
		} else if strings.HasPrefix(a, long+"=") {
			val, ok = strings.SplitN(a, "=", 2)[1], true
		}
	}
	return val, ok
}

// CheckGhBranching applies BR005 to gh pr create / gh pr edit.
func CheckGhBranching(inv shell.Invocation, c *policy.Contract, repo Repo) verdict.Verdict {
	var v verdict.Verdict
	r := c.Rule("BR005")
	path := strings.Join(shell.GhPath(inv), " ")
	if r.Severity == verdict.Off || (path != "pr create" && path != "pr edit") {
		return v
	}
	m := modelOf(c)
	argv := inv.Argv()
	base, hasBase := lastFlag(argv, "-B", "--base")
	if !hasBase {
		if path == "pr create" {
			v.Addf("BR005", r.Severity, "gh pr create must pass --base (%s for work, %s for a release)", m.staging, m.prod)
		}
		return v
	}
	head, hasHead := lastFlag(argv, "-H", "--head")
	if !hasHead && path == "pr create" {
		head, hasHead = repo.CurrentBranch()
	}
	if path == "pr edit" && base == m.prod {
		v.Addf("BR005", verdict.Ask, "changing a PR's base to %s: only %s may target %s", m.prod, m.staging, m.prod)
		return v
	}
	if !hasHead {
		head = ""
	}
	if problem := PRTargetProblem(base, head, c); problem != "" {
		v.Add("BR005", r.Severity, problem)
	}
	return v
}

// PRTargetProblem explains why a PR from head into base breaks the branching
// model, or returns "". An empty head means unknown.
func PRTargetProblem(base, head string, c *policy.Contract) string {
	m := modelOf(c)
	if _, after, ok := strings.Cut(head, ":"); ok {
		head = after
	}
	switch {
	case base == "":
		return fmt.Sprintf("--base is required (%s for work, %s for a release)", m.staging, m.prod)
	case base == m.prod && head != m.staging:
		return fmt.Sprintf("only %s may open a PR into %s (release); '%s' must target %s",
			m.staging, m.prod, orNone(head, head != ""), m.staging)
	case base != m.prod && base != m.staging:
		return fmt.Sprintf("PR base '%s' is not allowed; use %s (or %s for a release)", base, m.staging, m.prod)
	}
	return ""
}
