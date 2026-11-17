package rules_test

// The engine golden cases (../engine/testdata/cases.json) run again here, but
// with the real GitRepo on top of fakeRunner, a port of tests/helpers.py
// FakeRunner that answers at the process level like the Python tests do.
// This checks that GitRepo issues the git/gh command lines the Python
// checks issue and parses their output the same way.

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const goodSSH = `ssh -i "D:/keys/gh/id_ed25519" -o IdentitiesOnly=yes`

// fakeRunner is helpers.FakeRunner as an execx.Runner.
type fakeRunner struct {
	mu             sync.Mutex
	config         map[string]string
	remotes        map[string]string
	ghLogin        string // "" = not logged in
	ghState        string
	branch         string
	hasCommits     bool
	remoteBranches map[string]bool
	calls          []string
}

func newRunner() *fakeRunner {
	return &fakeRunner{
		config:         map[string]string{"user.name": "octocat", "user.email": "123+octocat@users.noreply.github.com", "core.sshCommand": goodSSH},
		remotes:        map[string]string{"origin": "git@github.com:octocat/repo.git"},
		ghLogin:        "octocat",
		ghState:        "success",
		branch:         "develop/feat/test",
		hasCommits:     true,
		remoteBranches: map[string]bool{"main": true, "develop/base": true},
	}
}

func (f *fakeRunner) Run(_ context.Context, c execx.Cmd) (execx.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, c.String())
	ok := func(out string) (execx.Result, error) { return execx.Result{Stdout: out}, nil }
	fail := func(code int) (execx.Result, error) { return execx.Result{ExitCode: code}, nil }
	if c.Name == "gh" {
		if f.ghLogin == "" {
			return execx.Result{ExitCode: 1, Stdout: `{"hosts": {}}`}, nil
		}
		b, _ := json.Marshal(map[string]any{"hosts": map[string]any{"github.com": []any{
			map[string]any{"state": f.ghState, "active": true, "host": "github.com", "login": f.ghLogin}}}})
		return ok(string(b))
	}
	if c.Name != "git" {
		return fail(127)
	}
	rest := c.Args
	if len(rest) >= 2 && rest[0] == "-C" {
		rest = rest[2:]
	}
	switch {
	case len(rest) >= 3 && rest[0] == "config" && (rest[1] == "--get-urlmatch" || rest[1] == "--get"):
		if v := f.config[rest[2]]; v != "" {
			return ok(v + "\n")
		}
		return fail(1)
	case len(rest) >= 2 && rest[0] == "remote" && rest[1] == "get-url":
		if u, found := f.remotes[rest[len(rest)-1]]; found {
			return ok(u + "\n")
		}
		return fail(2)
	case len(rest) == 1 && rest[0] == "remote":
		names := make([]string, 0, len(f.remotes))
		for n := range f.remotes {
			names = append(names, n)
		}
		sort.Strings(names)
		return ok(strings.Join(names, "\n") + "\n")
	case len(rest) >= 4 && rest[0] == "rev-parse" && rest[1] == "--verify" && rest[2] == "-q":
		ref := rest[3]
		if ref == "HEAD" {
			if f.hasCommits {
				return ok("abc\n")
			}
			return fail(1)
		}
		if strings.HasPrefix(ref, "refs/remotes/") {
			parts := strings.SplitN(ref, "/", 4)
			if len(parts) == 4 && f.remoteBranches[parts[3]] {
				return ok("abc\n")
			}
		}
		return fail(1)
	case len(rest) >= 1 && rest[0] == "symbolic-ref":
		if f.branch != "" {
			return ok(f.branch + "\n")
		}
		return fail(1)
	}
	return fail(1)
}

type goldenCase struct {
	Name     string                     `json:"name"`
	Command  string                     `json:"command"`
	Dialect  string                     `json:"dialect"`
	Cwd      string                     `json:"cwd"`
	Profile  string                     `json:"profile"`
	Vars     map[string]any             `json:"vars"`
	Repo     map[string]json.RawMessage `json:"repo"`
	Env      map[string]string          `json:"env"`
	Files    map[string]string          `json:"files"`
	Decision verdict.Decision           `json:"decision"`
	Rules    []string                   `json:"rules"`
}

func (f *fakeRunner) apply(t *testing.T, kw map[string]json.RawMessage) {
	str := func(raw json.RawMessage) string {
		var s *string
		if err := json.Unmarshal(raw, &s); err != nil {
			t.Fatal(err)
		}
		if s == nil {
			return ""
		}
		return *s
	}
	cfg := map[string]string{"name": "user.name", "email": "user.email", "ssh": "core.sshCommand", "helper": "credential.helper"}
	for k, raw := range kw {
		switch k {
		case "name", "email", "ssh", "helper":
			f.config[cfg[k]] = str(raw)
		case "gh_login":
			f.ghLogin = str(raw)
		case "branch":
			f.branch = str(raw)
		case "remotes":
			f.remotes = map[string]string{}
			_ = json.Unmarshal(raw, &f.remotes)
		case "has_commits":
			_ = json.Unmarshal(raw, &f.hasCommits)
		case "remote_branches":
			var bs []string
			_ = json.Unmarshal(raw, &bs)
			f.remoteBranches = map[string]bool{}
			for _, b := range bs {
				f.remoteBranches[b] = true
			}
		default:
			t.Fatalf("unknown repo key %q", k)
		}
	}
}

func TestGoldenThroughGitRepo(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "engine", "testdata", "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []goldenCase
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			vars := testProfile()
			switch c.Profile {
			case "gh":
				delete(vars, "ssh_key")
				vars["git_transport"] = "gh"
			case "none":
				vars = map[string]any{}
			}
			for k, v := range c.Vars {
				vars[k] = v
			}
			pol := testPolicy(t, vars)
			r := newRunner()
			r.apply(t, c.Repo)
			env := c.Env
			if len(env) == 0 {
				env = map[string]string{"GH_CONFIG_DIR": "/repo/no-gh"}
			}
			d := shell.Bash
			if c.Dialect == "powershell" {
				d = shell.PowerShell
			}
			cwd := c.Cwd
			if cwd == "" {
				cwd = string(filepath.Separator) + "repo"
			}
			v := engine.Evaluate(engine.Request{Command: c.Command, Dialect: d, Cwd: cwd}, pol, engine.Deps{
				Repo: func(dir string) rules.Repo { return rules.NewGitRepo(r, dir) },
				ReadFile: func(p string) ([]byte, error) {
					for k, s := range c.Files {
						if strings.HasSuffix(filepath.ToSlash(p), k) {
							return []byte(s), nil
						}
					}
					return nil, errors.New("not found")
				},
				Env: func(k string) string { return env[k] },
			})
			want := c.Rules
			if want == nil {
				want = []string{}
			}
			if v.Decision() != c.Decision || !reflect.DeepEqual(ruleIDs(v), want) {
				t.Errorf("%q: got %s %v want %s %v\n%s\ncalls: %q", c.Command, v.Decision(), ruleIDs(v), c.Decision, want, v.Reason(), r.calls)
			}
		})
	}
}
