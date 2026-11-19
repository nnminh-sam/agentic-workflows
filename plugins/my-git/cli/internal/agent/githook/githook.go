// Package githook holds the checks behind `mygit hook <name>`, the target of
// the .githooks shims. It reuses the rule functions of internal/rules on
// synthetic git calls, so git's own hooks and the agent guard give the same
// verdicts. Everything here is pure: repository facts come through
// rules.Repo.
package githook

import (
	"bufio"
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// scissors is git's "everything below is removed" line (commit -v).
const scissors = "# ------------------------ >8 ------------------------"

// CleanMessage drops what git's default cleanup drops before a message is
// stored: everything from the scissors line on, and lines starting with
// commentChar ("" means "#").
func CleanMessage(raw, commentChar string) string {
	if commentChar == "" {
		commentChar = "#"
	}
	raw = strings.ReplaceAll(raw, "\r\n", "\n")
	var keep []string
	for _, ln := range strings.Split(raw, "\n") {
		if ln == scissors || (commentChar != "#" && ln == commentChar+scissors[1:]) {
			break
		}
		if strings.HasPrefix(ln, commentChar) {
			continue
		}
		keep = append(keep, ln)
	}
	return strings.Join(keep, "\n")
}

// CommitMsg checks a commit message file's content (commit-msg hook).
func CommitMsg(raw, commentChar string, pol *policy.Policy) verdict.Verdict {
	return rules.CheckMessage(CleanMessage(raw, commentChar), pol.Contract(policy.Commit))
}

// only keeps the findings of the given rules.
func only(v verdict.Verdict, ids ...string) verdict.Verdict {
	var out verdict.Verdict
	for _, f := range v.Findings {
		for _, id := range ids {
			if f.RuleID == id {
				out.Findings = append(out.Findings, f)
			}
		}
	}
	return out
}

// downgrade turns ask findings into warnings: a hook cannot prompt, and the
// asks it downgrades are the bootstrap cases the user ran explicitly.
func downgrade(v verdict.Verdict, note string) verdict.Verdict {
	for i, f := range v.Findings {
		if f.Severity == verdict.Ask {
			v.Findings[i].Severity = verdict.Warn
			v.Findings[i].Message += note
		}
	}
	return v
}

// PreCommit applies BR001 (no commits on protected branches; the initial
// commit is allowed unless its initial_commit_severity is deny) and BR003
// (the current branch name follows the work-branch pattern).
func PreCommit(cwd string, pol *policy.Policy, repo rules.Repo) verdict.Verdict {
	c := pol.Contract(policy.Branching)
	if c == nil {
		return verdict.Verdict{}
	}
	commit := &shell.GitCall{Sub: "commit", Cwd: cwd, Config: map[string]string{}}
	v := only(rules.CheckGitBranching(commit, c, repo), "BR001")
	if !repo.HasCommits() {
		v = downgrade(v, "; the git hook lets it through")
	}
	if branch, ok := repo.CurrentBranch(); ok {
		asBranch := &shell.GitCall{Sub: "branch", Cwd: cwd, Config: map[string]string{},
			Args: []shell.Word{{Value: branch, Raw: branch}}}
		v.Extend(only(rules.CheckGitBranching(asBranch, c, repo), "BR003"))
	}
	return v
}

// isZero reports git's all-zero sha (a missing object in pre-push lines).
func isZero(sha string) bool { return strings.Trim(sha, "0") == "" }

// RefUpdate is one pre-push stdin line.
type RefUpdate struct {
	LocalRef, LocalSHA, RemoteRef, RemoteSHA string
}

// ParseRefUpdates reads git's pre-push stdin: "<local ref> <local sha>
// <remote ref> <remote sha>" per line.
func ParseRefUpdates(stdin string) ([]RefUpdate, error) {
	var out []RefUpdate
	sc := bufio.NewScanner(strings.NewReader(stdin))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) == 0 {
			continue
		}
		if len(f) != 4 {
			return nil, fmt.Errorf("pre-push: unexpected input line %q", sc.Text())
		}
		out = append(out, RefUpdate{f[0], f[1], f[2], f[3]})
	}
	return out, sc.Err()
}

// pushRepo answers RemoteBranchExists from the pre-push line itself (the
// remote sha git reports), so bootstrap detection does not depend on stale
// remote-tracking refs.
type pushRepo struct {
	rules.Repo
	exists bool
}

func (p pushRepo) RemoteBranchExists(string, string) bool { return p.exists }

// PrePush applies BR002 (no deletion of, and no direct push to, protected
// branches; the first push of one is the allowed bootstrap) to every ref
// update, and ID004 (allowed remotes) to the push URL.
func PrePush(cwd, remote, url string, updates []RefUpdate, pol *policy.Policy, repo rules.Repo, getenv func(string) string) verdict.Verdict {
	var v verdict.Verdict
	if getenv == nil {
		getenv = func(string) string { return "" }
	}
	if remote == "" {
		remote = url
	}
	if c := pol.Contract(policy.Branching); c != nil {
		for _, u := range updates {
			spec := u.LocalRef + ":" + u.RemoteRef
			deleting := isZero(u.LocalSHA)
			if deleting {
				spec = ":" + u.RemoteRef
			}
			call := &shell.GitCall{Sub: "push", Cwd: cwd, Config: map[string]string{},
				Args: []shell.Word{{Value: remote, Raw: remote}, {Value: spec, Raw: spec}}}
			bootstrap := isZero(u.RemoteSHA) && !deleting
			got := only(rules.CheckGitBranching(call, c, pushRepo{Repo: repo, exists: !isZero(u.RemoteSHA)}), "BR002")
			if bootstrap {
				got = downgrade(got, "; the git hook lets it through")
			}
			v.Extend(got)
		}
	}
	if id := pol.Contract(policy.Identity); id != nil && url != "" {
		call := &shell.GitCall{Sub: "push", Cwd: cwd, Config: map[string]string{},
			Args: []shell.Word{{Value: url, Raw: url}}}
		v.Extend(only(rules.CheckNetwork(call, rules.NetworkInput{Contract: id, Vars: pol.Vars, Repo: repo, Getenv: getenv}), "ID004"))
	}
	return v
}
