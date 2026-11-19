package agent

// Test helpers mirroring ../tests/helpers.py (and internal/engine's
// helpers_test.go): a fake rules.Repo and a policy built from the embedded
// contracts with the fictional test profile.

import (
	"errors"
	"path/filepath"
	"sort"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const goodSSH = `ssh -i "D:/keys/gh/id_ed25519" -o IdentitiesOnly=yes`

var testCwd = string(filepath.Separator) + "repo"

func testProfile() map[string]any {
	return map[string]any{
		"github_user": "octocat",
		"github_orgs": []any{"octo-org"},
		"git_name":    "octocat",
		"git_email":   []any{"123+octocat@users.noreply.github.com"},
		"ssh_key":     "keys/gh/id_ed25519",
	}
}

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

type fakeRepo struct {
	config         map[string]string
	remotes        map[string]string
	ghLogin        string
	ghState        string
	branch         string
	hasCommits     bool
	remoteBranches map[string]bool
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
	switch {
	case f.ghLogin == "":
		return "", "gh is not logged in to " + host
	case f.ghState != "success":
		return "", "gh auth for " + host + " is in state '" + f.ghState + "'"
	}
	return f.ghLogin, ""
}
func (f *fakeRepo) ClearGhCache() {}

var testEnv = map[string]string{"GH_CONFIG_DIR": filepath.Join(testCwd, "no-gh")}

// guard is the guard pipeline (as cli.guardRun does it) with fakes:
// decode → skip/empty → evaluate + CheckApply → encode.
func guard(t testing.TB, ad Adapter, payload string, repo *fakeRepo, pol *policy.Policy, agentApply string) ([]byte, int) {
	t.Helper()
	if repo == nil {
		repo = newFake()
	}
	if pol == nil {
		pol = testPolicy(t, testProfile(), nil)
	}
	req, err := ad.Decode([]byte(payload), PreCommand)
	if err != nil {
		return ad.Encode(FailSafe(err.Error()))
	}
	if req.Skip || req.Command == "" {
		return ad.Encode(Allow(PreCommand))
	}
	cwd := req.Cwd
	if cwd == "" {
		cwd = testCwd
	}
	v := engine.Evaluate(engine.Request{Command: req.Command, Dialect: req.Dialect, Cwd: cwd}, pol, engine.Deps{
		Repo:     func(string) rules.Repo { return repo },
		ReadFile: func(p string) ([]byte, error) { return nil, errors.New("not found: " + p) },
		Env:      func(k string) string { return testEnv[k] },
	})
	v.Extend(CheckApply(req, agentApply))
	return ad.Encode(Result{Kind: PreCommand, Verdict: v})
}
