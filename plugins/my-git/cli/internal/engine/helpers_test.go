package engine

// Test helpers equivalent to ../tests/helpers.py: a fake rules.Repo that
// answers like FakeRunner, and a policy built from the embedded contracts
// with the test profile substituted (gh status cache disabled).

import (
	"errors"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const (
	goodSSH  = `ssh -i "D:/keys/gh/id_ed25519" -o IdentitiesOnly=yes`
	ghHelper = "!/opt/homebrew/bin/gh auth git-credential"
	goodBody = "## Summary\nAdd x.\n\n## Detailed changes\n- add x\n\n## Explanation\nNeeded.\n\n" +
		"## Test results\n### Unit\n`npm test`: 3 passed\n### Lint\n`npm run lint`: clean\n"
)

var cwd = string(filepath.Separator) + "repo"

// testProfile is helpers.PROFILE.
func testProfile() map[string]any {
	return map[string]any{
		"github_user": "octocat",
		"github_orgs": []any{"octo-org"},
		"git_name":    "octocat",
		"git_email":   []any{"123+octocat@users.noreply.github.com"},
		"ssh_key":     "keys/gh/id_ed25519",
	}
}

// ghProfile is test_identity.GH_PROFILE.
func ghProfile() map[string]any {
	p := testProfile()
	delete(p, "ssh_key")
	p["git_transport"] = "gh"
	return p
}

func withVars(base map[string]any, extra map[string]any) map[string]any {
	for k, v := range extra {
		base[k] = v
	}
	return base
}

// testPolicy is helpers.contracts(mutate, profile).
func testPolicy(t testing.TB, vars map[string]any, mutate func(map[string]*policy.Contract)) *policy.Policy {
	t.Helper()
	cs, err := config.LoadContracts(defaults.FS)
	if err != nil {
		t.Fatal(err)
	}
	cs[policy.Identity].Settings["gh_status_cache_seconds"] = float64(0)
	if mutate != nil {
		mutate(cs)
	}
	for _, c := range cs {
		for i := range c.Rules {
			c.Rules[i].Params = policy.SubstituteParams(c.Rules[i].Params, vars)
		}
	}
	return &policy.Policy{Contracts: cs, Vars: vars}
}

// setSeverity is helpers.set_severity.
func setSeverity(id string, sev verdict.Severity) func(map[string]*policy.Contract) {
	return func(cs map[string]*policy.Contract) {
		for _, c := range cs {
			for i := range c.Rules {
				if c.Rules[i].ID == id {
					c.Rules[i].Severity = sev
				}
			}
		}
	}
}

// fakeRepo mirrors tests/helpers.py FakeRunner.
type fakeRepo struct {
	config         map[string]string
	remotes        map[string]string
	ghLogin        string
	ghState        string
	branch         string
	hasCommits     bool
	remoteBranches map[string]bool
	ghCalls        int
	cleared        int
}

func newFake() *fakeRepo {
	return &fakeRepo{
		config:         map[string]string{"user.name": "octocat", "user.email": "123+octocat@users.noreply.github.com", "core.sshCommand": goodSSH},
		remotes:        map[string]string{"origin": "git@github.com:octocat/repo.git"},
		ghLogin:        "octocat",
		ghState:        "success",
		branch:         "develop/feat/test",
		hasCommits:     true,
		remoteBranches: map[string]bool{"main": true, "develop/base": true},
	}
}

// fake builds a fakeRepo with a mutation applied.
func fake(mut func(*fakeRepo)) *fakeRepo {
	r := newFake()
	if mut != nil {
		mut(r)
	}
	return r
}

func (f *fakeRepo) Config(k string) (string, bool) { v := f.config[k]; return v, v != "" }
func (f *fakeRepo) ConfigURLMatch(k, _ string) (string, bool) {
	v := f.config[k]
	return v, v != ""
}
func (f *fakeRepo) Remotes() []string {
	out := make([]string, 0, len(f.remotes))
	for k := range f.remotes {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func (f *fakeRepo) RemoteURL(n string, _ bool) (string, bool) { v, ok := f.remotes[n]; return v, ok }
func (f *fakeRepo) CurrentBranch() (string, bool)             { return f.branch, f.branch != "" }
func (f *fakeRepo) HasCommits() bool                          { return f.hasCommits }
func (f *fakeRepo) RemoteBranchExists(_, b string) bool       { return f.remoteBranches[b] }
func (f *fakeRepo) GhActiveLogin(host, _ string, _ time.Duration) (string, string) {
	f.ghCalls++
	switch {
	case f.ghLogin == "":
		return "", "gh is not logged in to " + host
	case f.ghState != "success":
		return "", "gh auth for " + host + " is in state '" + f.ghState + "'"
	}
	return f.ghLogin, ""
}
func (f *fakeRepo) ClearGhCache() { f.cleared++ }

type opts struct {
	repo    *fakeRepo
	pol     *policy.Policy
	files   map[string]string
	env     map[string]string
	dialect shell.Dialect
	cwd     string
}

func readFiles(files map[string]string) rules.ReadFile {
	return func(p string) ([]byte, error) {
		for k, v := range files {
			if strings.HasSuffix(filepath.ToSlash(filepath.Clean(p)), k) {
				return []byte(v), nil
			}
		}
		return nil, errors.New("not found: " + p)
	}
}

// run is helpers.evaluate.
func run(t testing.TB, cmd string, o opts) verdict.Verdict {
	t.Helper()
	if o.repo == nil {
		o.repo = newFake()
	}
	if o.pol == nil {
		o.pol = testPolicy(t, testProfile(), nil)
	}
	if o.cwd == "" {
		o.cwd = cwd
	}
	env := o.env
	if env == nil {
		env = map[string]string{"GH_CONFIG_DIR": filepath.Join(cwd, "no-gh")}
	}
	return Evaluate(Request{Command: cmd, Dialect: o.dialect, Cwd: o.cwd}, o.pol, Deps{
		Repo:     func(string) rules.Repo { return o.repo },
		ReadFile: readFiles(o.files),
		Env:      func(k string) string { return env[k] },
	})
}

// ids is helpers.ids: sorted unique rule ids.
func ids(v verdict.Verdict) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, f := range v.Findings {
		if !seen[f.RuleID] {
			seen[f.RuleID] = true
			out = append(out, f.RuleID)
		}
	}
	sort.Strings(out)
	return out
}

func messages(v verdict.Verdict) string {
	parts := make([]string, len(v.Findings))
	for i, f := range v.Findings {
		parts[i] = f.Message
	}
	return strings.Join(parts, " ")
}

// expect asserts a decision and (when ids != nil) the exact rule ids.
func expect(t *testing.T, cmd string, o opts, d verdict.Decision, wantIDs []string) verdict.Verdict {
	t.Helper()
	v := run(t, cmd, o)
	if v.Decision() != d || (wantIDs != nil && !reflect.DeepEqual(ids(v), wantIDs)) {
		t.Errorf("%q: got %s %v, want %s %v\n%s", cmd, v.Decision(), ids(v), d, wantIDs, v.Reason())
	}
	return v
}

// clean asserts no findings at all (Python: .findings == []).
func clean(t *testing.T, cmd string, o opts) {
	t.Helper()
	if v := run(t, cmd, o); len(v.Findings) != 0 {
		t.Errorf("%q: want no findings, got %v\n%s", cmd, ids(v), v.Reason())
	}
}

// has asserts rule id is among the findings.
func has(t *testing.T, cmd string, o opts, id string) verdict.Verdict {
	t.Helper()
	v := run(t, cmd, o)
	for _, x := range ids(v) {
		if x == id {
			return v
		}
	}
	t.Errorf("%q: want %s in %v\n%s", cmd, id, ids(v), v.Reason())
	return v
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}
