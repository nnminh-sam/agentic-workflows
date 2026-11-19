package graph

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
)

// Options control Collect.
type Options struct {
	Remote string // remote whose branches count as "origin" (default "origin")
	Limit  int    // maximum commits; 0 = no limit
	// Offline skips gh entirely (no PR data).
	Offline bool
	// MergedPRs also lists recently merged PRs (the flow view needs them).
	MergedPRs bool
}

const (
	fieldSep  = "\x1f" // field separator used in every format string below
	recordSep = "\x1e" // record separator
)

// logFormat: SHA, parents, decorations, signature status, signing key,
// committer email, subject. %GK and %ce tell ssh / gpg / GitHub web-flow
// signatures apart, which %G? alone cannot.
const logFormat = "--format=%H%x1f%P%x1f%D%x1f%G?%x1f%GK%x1f%ce%x1f%s%x1e"

// refFormat: full ref name, object, upstream, upstream track.
const refFormat = "--format=%(refname)%1f%(objectname)%1f%(upstream:short)%1f%(upstream:track)"

// prFields are requested from gh pr list.
const prFields = "number,headRefName,baseRefName,isDraft,state,statusCheckRollup"

// Collect gathers commits, branches and (unless offline or gh is nil) PRs.
// gh failures are not fatal: they become a warning and PRs stay empty.
func Collect(ctx context.Context, git *gitx.Git, gh *ghx.Gh, opts Options) (Repo, error) {
	if opts.Remote == "" {
		opts.Remote = "origin"
	}
	repo := Repo{Remote: opts.Remote}
	head, err := git.CurrentBranch(ctx)
	if err != nil {
		return repo, err
	}
	repo.Head = head
	if repo.Commits, repo.Limited, err = collectLog(ctx, git, opts.Limit); err != nil {
		return repo, err
	}
	if repo.Branches, err = collectBranches(ctx, git); err != nil {
		return repo, err
	}
	ClassifyRefs(repo.Commits, repo.Branches)
	if gh == nil || opts.Offline {
		return repo, nil
	}
	states := []string{"open"}
	if opts.MergedPRs {
		states = append(states, "merged")
	}
	for _, st := range states {
		prs, err := listPRs(ctx, gh, st)
		if err != nil {
			repo.Warnings = append(repo.Warnings, fmt.Sprintf("pull requests unavailable (%s PRs): %v", st, firstLine(err.Error())))
			break
		}
		repo.PRs = append(repo.PRs, prs...)
	}
	return repo, nil
}

// collectLog runs git log over all refs. It asks for limit+1 commits so it
// can tell whether the history was cut.
func collectLog(ctx context.Context, git *gitx.Git, limit int) ([]Commit, bool, error) {
	args := []string{"log", "--all", "--topo-order", logFormat}
	if limit > 0 {
		args = append(args, "-n", strconv.Itoa(limit+1))
	}
	res, err := git.Exec(ctx, args...)
	if err != nil {
		return nil, false, err
	}
	if !res.OK() {
		// An empty repository has nothing to log; anything else is an error.
		if strings.Contains(res.Stderr, "does not have any commits") {
			return nil, false, nil
		}
		return nil, false, &gitx.ExitError{Cmd: git.Cmd(args...), Result: res}
	}
	commits := ParseLog(res.Stdout)
	if limit > 0 && len(commits) > limit {
		return commits[:limit], true, nil
	}
	return commits, false, nil
}

// collectBranches lists local and remote-tracking branches.
func collectBranches(ctx context.Context, git *gitx.Git) ([]Branch, error) {
	out, err := git.Query(ctx, "for-each-ref", refFormat, "refs/heads", "refs/remotes")
	if err != nil {
		return nil, err
	}
	return ParseBranches(out), nil
}

// ParseLog parses git log output produced with logFormat.
func ParseLog(out string) []Commit {
	var commits []Commit
	for _, rec := range strings.Split(out, recordSep) {
		f := strings.Split(strings.TrimLeft(rec, "\r\n"), fieldSep)
		if len(f) < 7 || f[0] == "" {
			continue
		}
		commits = append(commits, Commit{
			SHA:     f[0],
			Parents: strings.Fields(f[1]),
			Refs:    ParseDecorations(f[2]),
			Sig:     ParseSignature(f[3], f[4], f[5]),
			Subject: strings.TrimRight(f[6], "\r\n"),
		})
	}
	return commits
}

// ParseDecorations parses git's %D output, e.g.
// "HEAD -> main, origin/main, origin/HEAD, tag: v1.0". Remote HEAD aliases
// ("origin/HEAD") are dropped: they repeat the remote's default branch.
func ParseDecorations(d string) []Ref {
	var refs []Ref
	for _, part := range strings.Split(d, ", ") {
		part = strings.TrimSpace(part)
		switch {
		case part == "":
		case part == "HEAD":
			refs = append(refs, Ref{Name: "HEAD", Kind: RefHead})
		case strings.HasPrefix(part, "HEAD -> "):
			refs = append(refs, Ref{Name: strings.TrimPrefix(part, "HEAD -> "), Kind: RefBranch, Head: true})
		case strings.HasPrefix(part, "tag: "):
			refs = append(refs, Ref{Name: strings.TrimPrefix(part, "tag: "), Kind: RefTag})
		case strings.HasSuffix(part, "/HEAD"):
		case strings.HasPrefix(part, "refs/"):
			refs = append(refs, Ref{Name: part, Kind: RefOther})
		default:
			refs = append(refs, Ref{Name: part, Kind: RefBranch})
		}
	}
	return refs
}

// githubKeys are the key IDs GitHub signs web-flow commits (merges made on
// github.com) with.
var githubKeys = map[string]bool{"4AEE18F83AFDEB23": true, "B5690EEEBB952194": true}

// ParseSignature maps %G? (plus %GK and the committer email) to a Signature.
// GitHub web-flow commits count as ok even when this machine cannot verify
// GitHub's key (E): GitHub verified them when it created them.
func ParseSignature(code, key, committerEmail string) Signature {
	s := Signature{Code: code, Kind: signatureKind(code, key, committerEmail)}
	switch code {
	case "G", "U", "X", "Y":
		s.Status = SigOK
	case "B", "R":
		s.Status = SigBad
	case "E":
		s.Status = SigUnknown
		if s.Kind == "github" {
			s.Status = SigOK
		}
	default:
		s.Status, s.Kind = SigUnsigned, ""
	}
	return s
}

func signatureKind(code, key, email string) string {
	switch {
	case code == "N" || code == "":
		return ""
	case githubKeys[strings.ToUpper(key)] || email == "noreply@github.com":
		return "github"
	case strings.HasPrefix(key, "SHA256:"):
		return "ssh"
	case strings.Contains(key, "/") || strings.Contains(key, "CN="):
		return "x509"
	case key != "":
		return "gpg"
	}
	return ""
}

// ParseBranches parses for-each-ref output produced with refFormat.
// Symbolic remote HEADs (refs/remotes/origin/HEAD) are skipped.
func ParseBranches(out string) []Branch {
	var branches []Branch
	for _, line := range strings.Split(out, "\n") {
		f := strings.Split(strings.TrimRight(line, "\r"), fieldSep)
		if len(f) < 4 || f[0] == "" {
			continue
		}
		b := Branch{SHA: f[1], Upstream: f[2]}
		switch {
		case strings.HasPrefix(f[0], "refs/heads/"):
			b.Name = strings.TrimPrefix(f[0], "refs/heads/")
		case strings.HasPrefix(f[0], "refs/remotes/"):
			b.Name, b.Remote = strings.TrimPrefix(f[0], "refs/remotes/"), true
			if strings.HasSuffix(b.Name, "/HEAD") {
				continue
			}
		default:
			continue
		}
		b.Ahead, b.Behind, b.Gone = gitx.ParseTrack(f[3])
		branches = append(branches, b)
	}
	return branches
}

// ghPR is the JSON shape of one gh pr list entry.
type ghPR struct {
	Number      int       `json:"number"`
	HeadRefName string    `json:"headRefName"`
	BaseRefName string    `json:"baseRefName"`
	IsDraft     bool      `json:"isDraft"`
	State       string    `json:"state"`
	Checks      []ghCheck `json:"statusCheckRollup"`
}

// ghCheck is one entry of statusCheckRollup: a CheckRun (status,
// conclusion) or a StatusContext (state).
type ghCheck struct {
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
	State      string `json:"state"`
}

func listPRs(ctx context.Context, gh *ghx.Gh, state string) ([]PR, error) {
	var raw []ghPR
	args := []string{"pr", "list", "--state", state, "--limit", "100", "--json", prFields}
	if err := gh.JSON(ctx, args, &raw); err != nil {
		return nil, err
	}
	prs := make([]PR, 0, len(raw))
	for _, p := range raw {
		st := strings.ToUpper(p.State)
		if st == "" {
			st = strings.ToUpper(state)
		}
		prs = append(prs, PR{
			Number: p.Number, Head: p.HeadRefName, Base: p.BaseRefName,
			Draft: p.IsDraft, State: st, Checks: summarizeChecks(p.Checks),
		})
	}
	return prs, nil
}

// summarizeChecks folds a status check rollup into one word: any failure
// wins, then anything still running, then pass.
func summarizeChecks(checks []ghCheck) string {
	if len(checks) == 0 {
		return ChecksNone
	}
	pending := false
	for _, c := range checks {
		switch strings.ToUpper(c.Conclusion + c.State) {
		case "FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE":
			return ChecksFail
		case "PENDING", "EXPECTED", "":
			pending = true
		}
		if c.Status != "" && !strings.EqualFold(c.Status, "COMPLETED") {
			pending = true
		}
	}
	if pending {
		return ChecksPending
	}
	return ChecksPass
}

func firstLine(s string) string {
	line, _, _ := strings.Cut(strings.TrimSpace(s), "\n")
	return line
}

// ClassifyRefs marks decorations that name remote-tracking branches as
// RefRemote: %D prints "origin/main" and a local branch "origin/main" alike,
// so the branch list decides.
func ClassifyRefs(commits []Commit, branches []Branch) {
	remote := map[string]bool{}
	local := map[string]bool{}
	for _, b := range branches {
		if b.Remote {
			remote[b.Name] = true
		} else {
			local[b.Name] = true
		}
	}
	for i := range commits {
		for j, r := range commits[i].Refs {
			if r.Kind == RefBranch && !r.Head && remote[r.Name] && !local[r.Name] {
				commits[i].Refs[j].Kind = RefRemote
			}
		}
	}
}
