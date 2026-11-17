package githook

import (
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

type fakeRepo struct {
	branch     string
	hasCommits bool
	remotes    map[string]string
	remoteRefs map[string]bool
}

func (f fakeRepo) CurrentBranch() (string, bool) { return f.branch, f.branch != "" }
func (f fakeRepo) HasCommits() bool              { return f.hasCommits }
func (f fakeRepo) Config(string) (string, bool)  { return "", false }
func (f fakeRepo) ConfigURLMatch(string, string) (string, bool) {
	return "", false
}
func (f fakeRepo) Remotes() []string { return nil }
func (f fakeRepo) RemoteURL(name string, _ bool) (string, bool) {
	u, ok := f.remotes[name]
	return u, ok
}
func (f fakeRepo) RemoteBranchExists(remote, branch string) bool {
	return f.remoteRefs[remote+"/"+branch]
}
func (f fakeRepo) GhActiveLogin(string, string, time.Duration) (string, string) {
	return "octo", ""
}
func (f fakeRepo) ClearGhCache() {}

func testPolicy(t *testing.T) *policy.Policy {
	t.Helper()
	cs, err := config.LoadContracts(defaults.FS)
	if err != nil {
		t.Fatal(err)
	}
	vars := map[string]any{
		"github_user":   "octo",
		"git_transport": "ssh",
	}
	for _, c := range cs {
		for i := range c.Rules {
			c.Rules[i].Params = policy.SubstituteParams(c.Rules[i].Params, vars)
		}
	}
	return &policy.Policy{Contracts: cs, Vars: vars}
}

func TestCleanMessageAndCommitMsg(t *testing.T) {
	raw := "feat: add login\r\n\r\n# comment line\r\nBody text.\r\n# ------------------------ >8 ------------------------\r\ndiff --git a/x b/x\r\n"
	got := CleanMessage(raw, "")
	if got != "feat: add login\n\nBody text." {
		t.Errorf("CleanMessage = %q", got)
	}
	semi := "feat: add login\n; comment\n; ------------------------ >8 ------------------------\nignored\n"
	if got := CleanMessage(semi, ";"); got != "feat: add login" {
		t.Errorf("CleanMessage(;) = %q", got)
	}

	pol := testPolicy(t)
	if v := CommitMsg(raw, "", pol); v.Decision() != verdict.Allow {
		t.Errorf("valid message got %v: %s", v.Decision(), v.Reason())
	}
	if v := CommitMsg("bad header", "", pol); v.Decision() != verdict.DecDeny {
		t.Errorf("bad message got %v", v.Decision())
	}
}

func TestPreCommit(t *testing.T) {
	pol := testPolicy(t)

	// Valid work branch with commits: allowed.
	v := PreCommit("/repo", pol, fakeRepo{branch: "develop/feat/login", hasCommits: true})
	if v.Decision() != verdict.Allow {
		t.Errorf("work branch got %v: %s", v.Decision(), v.Reason())
	}

	// Protected branch with commits: denied (BR001).
	v = PreCommit("/repo", pol, fakeRepo{branch: "main", hasCommits: true})
	if v.Decision() != verdict.DecDeny || !strings.Contains(v.Reason(), "BR001") {
		t.Errorf("protected branch got %v: %s", v.Decision(), v.Reason())
	}

	// Protected branch without commits (initial bootstrap commit): ask downgraded to warn.
	v = PreCommit("/repo", pol, fakeRepo{branch: "main", hasCommits: false})
	if v.Decision() != verdict.DecWarn || !strings.Contains(v.Findings[0].Message, "the git hook lets it through") {
		t.Errorf("initial commit got %v: %+v", v.Decision(), v.Findings)
	}

	// Badly named work branch: denied (BR003).
	v = PreCommit("/repo", pol, fakeRepo{branch: "bad-branch", hasCommits: true})
	if v.Decision() != verdict.DecDeny || !strings.Contains(v.Reason(), "BR003") {
		t.Errorf("bad branch got %v: %s", v.Decision(), v.Reason())
	}

	// Policy without branching contract.
	if v := PreCommit("/repo", &policy.Policy{}, fakeRepo{branch: "main", hasCommits: true}); len(v.Findings) != 0 {
		t.Errorf("empty policy got %+v", v)
	}
}

func TestParseRefUpdatesAndPrePush(t *testing.T) {
	zero := strings.Repeat("0", 40)
	sha1 := strings.Repeat("a", 40)
	sha2 := strings.Repeat("b", 40)

	updates, err := ParseRefUpdates("\nrefs/heads/develop/feat/x " + sha1 + " refs/heads/develop/feat/x " + zero + "\n")
	if err != nil || len(updates) != 1 || updates[0].LocalRef != "refs/heads/develop/feat/x" {
		t.Fatalf("ParseRefUpdates = %+v, %v", updates, err)
	}
	if _, err := ParseRefUpdates("too few fields\n"); err == nil {
		t.Error("expected error on malformed pre-push line")
	}

	pol := testPolicy(t)
	repo := fakeRepo{remotes: map[string]string{"origin": "git@github.com:octo/repo.git"}}

	// Pushing a work branch to allowed remote: allowed.
	v := PrePush("/repo", "origin", "git@github.com:octo/repo.git", updates, pol, repo, nil)
	if v.Decision() != verdict.Allow {
		t.Errorf("work push got %v: %s", v.Decision(), v.Reason())
	}

	// First push of main (remote sha is all zeros): ask downgraded to warn.
	boot := []RefUpdate{{LocalRef: "refs/heads/main", LocalSHA: sha1, RemoteRef: "refs/heads/main", RemoteSHA: zero}}
	v = PrePush("/repo", "origin", "git@github.com:octo/repo.git", boot, pol, repo, nil)
	if v.Decision() != verdict.DecWarn || !strings.Contains(v.Findings[0].Message, "the git hook lets it through") {
		t.Errorf("bootstrap push got %v: %+v", v.Decision(), v.Findings)
	}

	// Updating existing main on remote: denied (BR002).
	upd := []RefUpdate{{LocalRef: "refs/heads/main", LocalSHA: sha1, RemoteRef: "refs/heads/main", RemoteSHA: sha2}}
	v = PrePush("/repo", "origin", "git@github.com:octo/repo.git", upd, pol, repo, nil)
	if v.Decision() != verdict.DecDeny || !strings.Contains(v.Reason(), "BR002") {
		t.Errorf("direct push to main got %v: %s", v.Decision(), v.Reason())
	}

	// Deleting main on remote: denied (BR002).
	del := []RefUpdate{{LocalRef: "(delete)", LocalSHA: zero, RemoteRef: "refs/heads/main", RemoteSHA: sha2}}
	v = PrePush("/repo", "", "git@github.com:octo/repo.git", del, pol, repo, nil)
	if v.Decision() != verdict.DecDeny || !strings.Contains(v.Reason(), "BR002") {
		t.Errorf("delete main got %v: %s", v.Decision(), v.Reason())
	}

	// Pushing to disallowed remote owner: denied (ID004).
	v = PrePush("/repo", "origin", "git@github.com:stranger/repo.git", updates, pol, repo, nil)
	if v.Decision() != verdict.DecDeny || !strings.Contains(v.Reason(), "ID004") {
		t.Errorf("disallowed remote got %v: %s", v.Decision(), v.Reason())
	}
}
