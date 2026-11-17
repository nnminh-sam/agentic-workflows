// Package graph builds the read-only repository views behind `mygit graph`
// and `mygit flow`: it collects commits, branches and pull requests through
// gitx/ghx (Collect), lays commits out into lanes (Layout, a pure function),
// and renders the result as terminal text or a self-contained HTML page.
// The flow view (BuildFlow) classifies work branches against the active
// workflow template and names the one command that moves each forward.
//
// Collection is the only part that runs processes; everything else is pure
// and testable with fixtures.
package graph

// Ref kinds as parsed from git's %D decoration.
const (
	RefHead   = "head"   // HEAD itself (detached HEAD, or HEAD -> branch)
	RefBranch = "branch" // local branch (refs/heads)
	RefRemote = "remote" // remote-tracking branch (refs/remotes)
	RefTag    = "tag"    // tag
	RefOther  = "other"  // anything else, e.g. refs/stash
)

// Signature statuses (from %G?).
const (
	SigOK       = "ok"       // good signature (G, U, X, Y) or GitHub web-flow
	SigBad      = "bad"      // bad or revoked (B, R)
	SigUnsigned = "unsigned" // no signature (N)
	SigUnknown  = "unknown"  // signed but cannot be checked here (E)
)

// Check summaries of a PR's status check rollup.
const (
	ChecksNone    = "none"
	ChecksPass    = "pass"
	ChecksFail    = "fail"
	ChecksPending = "pending"
)

// PR states (gh uses upper case).
const (
	PROpen   = "OPEN"
	PRMerged = "MERGED"
)

// Repo is everything the graph and flow views show.
type Repo struct {
	Remote   string   `json:"remote"`
	Head     string   `json:"head"`     // checked-out branch; "" when detached
	Commits  []Commit `json:"commits"`  // topo order, children first
	Branches []Branch `json:"branches"` // local and remote-tracking branches
	PRs      []PR     `json:"prs"`      // open (and recently merged) PRs; empty offline
	// Limited is true when the log was cut at Options.Limit commits.
	Limited bool `json:"limited"`
	// Warnings are non-fatal problems (gh unavailable, …).
	Warnings []string `json:"warnings,omitempty"`
}

// Commit is one commit with its decorations.
type Commit struct {
	SHA     string    `json:"sha"`
	Parents []string  `json:"parents"`
	Refs    []Ref     `json:"refs,omitempty"`
	Sig     Signature `json:"signature"`
	Subject string    `json:"subject"`
}

// Short returns the abbreviated SHA (7 characters).
func (c Commit) Short() string {
	if len(c.SHA) > 7 {
		return c.SHA[:7]
	}
	return c.SHA
}

// Ref is one decoration on a commit.
type Ref struct {
	Name string `json:"name"` // e.g. "main", "origin/main", "v1.0", "HEAD"
	Kind string `json:"kind"` // RefHead, RefBranch, RefRemote, RefTag, RefOther
	// Head is true for the branch HEAD points at ("HEAD -> name").
	Head bool `json:"head,omitempty"`
}

// Signature is a commit's signature check result.
type Signature struct {
	Status string `json:"status"`         // SigOK, SigBad, SigUnsigned, SigUnknown
	Kind   string `json:"kind,omitempty"` // ssh, gpg, x509, github
	Code   string `json:"code"`           // raw %G? letter
}

// Branch is a local or remote-tracking branch.
type Branch struct {
	Name     string `json:"name"` // short name: "main" or "origin/main"
	SHA      string `json:"sha"`
	Remote   bool   `json:"remote"`
	Upstream string `json:"upstream,omitempty"`
	Ahead    int    `json:"ahead"`
	Behind   int    `json:"behind"`
	Gone     bool   `json:"gone,omitempty"` // upstream configured but deleted
}

// PR is a pull request with its checks summarised.
type PR struct {
	Number int    `json:"number"`
	Head   string `json:"head"`
	Base   string `json:"base"`
	Draft  bool   `json:"draft"`
	State  string `json:"state"`  // PROpen or PRMerged
	Checks string `json:"checks"` // ChecksNone, ChecksPass, ChecksFail, ChecksPending
}

// Branch returns the branch with the given short name.
func (r Repo) Branch(name string) (Branch, bool) {
	for _, b := range r.Branches {
		if b.Name == name {
			return b, true
		}
	}
	return Branch{}, false
}

// OpenPR returns the open PR whose head is branch.
func (r Repo) OpenPR(branch string) (PR, bool) {
	return r.findPR(branch, PROpen)
}

// MergedPR returns the most recent merged PR whose head is branch.
func (r Repo) MergedPR(branch string) (PR, bool) {
	return r.findPR(branch, PRMerged)
}

func (r Repo) findPR(head, state string) (PR, bool) {
	best, found := PR{}, false
	for _, p := range r.PRs {
		if p.Head == head && p.State == state && (!found || p.Number > best.Number) {
			best, found = p, true
		}
	}
	return best, found
}

// LastMergedInto returns the most recent merged PR targeting base.
func (r Repo) LastMergedInto(base string) (PR, bool) {
	best, found := PR{}, false
	for _, p := range r.PRs {
		if p.Base == base && p.State == PRMerged && (!found || p.Number > best.Number) {
			best, found = p, true
		}
	}
	return best, found
}
