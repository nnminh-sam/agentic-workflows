package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// repoRunner answers commands by exact line (execx.Fake), then by prefix,
// then with exit 1. Every call is recorded.
type repoRunner struct {
	*execx.Fake
	mu       sync.Mutex
	prefixes []repoPrefix
	calls    []string
}

type repoPrefix struct {
	prefix string
	res    execx.Result
}

func newRepoRunner() *repoRunner {
	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	return &repoRunner{Fake: f}
}

func (r *repoRunner) on(prefix string, res execx.Result) *repoRunner {
	r.prefixes = append([]repoPrefix{{prefix, res}}, r.prefixes...) // later registrations win
	return r
}

func (r *repoRunner) Run(ctx context.Context, c execx.Cmd) (execx.Result, error) {
	r.mu.Lock()
	r.calls = append(r.calls, c.String())
	r.mu.Unlock()
	if _, ok := r.Responses[c.String()]; ok {
		return r.Fake.Run(ctx, c)
	}
	for _, p := range r.prefixes {
		if strings.HasPrefix(c.String(), p.prefix) {
			return p.res, nil
		}
	}
	return r.Fake.Run(ctx, c)
}

func (r *repoRunner) ran(prefix string) bool {
	for _, c := range r.calls {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

const repoConfig = `{
  "profiles": {
    "work":     {"git": {"name": "Octo Work", "email": ["octo@acme.io"], "signing": {"format": "ssh", "key": "/k/work.pub"}}, "github": {"user": "octo-work"}},
    "personal": {"git": {"name": "Octo", "email": ["octo@home.io"]}, "github": {"user": "octo"}}
  },
  "defaultProfile": "personal",
  "match": [{"remoteOwner": "acme", "profile": "work"}]
}`

// repoEnv is a temp HOME with a global config and a fake process runner.
type repoEnv struct {
	t      *testing.T
	home   string
	runner *repoRunner
}

func newRepoEnv(t *testing.T, config string) *repoEnv {
	t.Helper()
	home := t.TempDir()
	if config != "" {
		dir := filepath.Join(home, ".config", "mygit")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte(config), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return &repoEnv{t: t, home: home, runner: newRepoRunner()}
}

// inRepo answers the git queries of a repository at /repo with the given
// HEAD ("" = unborn) and current branch.
func (e *repoEnv) inRepo(head, branch string) {
	r := e.runner
	r.on("git rev-parse --show-toplevel", execx.Result{Stdout: "/repo\n"})
	r.on("git symbolic-ref --short -q HEAD", execx.Result{Stdout: branch + "\n"})
	r.on("git status --porcelain=v2", execx.Result{})
	r.on("git status --porcelain=v1 -z", execx.Result{})
	r.on("git for-each-ref", execx.Result{})
	r.on("git config --get", execx.Result{ExitCode: 1})
	if head != "" {
		r.on("git rev-parse -q --verify HEAD^{commit}", execx.Result{Stdout: head + "\n"})
	}
}

// ghLogin makes gh report the active login.
func (e *repoEnv) ghLogin(login string) {
	e.runner.on("gh auth status --active --hostname github.com --json hosts",
		execx.Result{Stdout: `{"hosts":{"github.com":[{"active":true,"state":"success","login":"` + login + `"}]}}`})
}

func (e *repoEnv) run(args ...string) (stdout string, code int) {
	e.t.Helper()
	return e.runEnv(nil, args...)
}

// runEnv is run with extra environment variables.
func (e *repoEnv) runEnv(env map[string]string, args ...string) (stdout string, code int) {
	e.t.Helper()
	var out, errb bytes.Buffer
	io := IO{
		In: strings.NewReader(""), Out: &out, Err: &errb,
		Getenv: func(k string) string {
			if v, ok := env[k]; ok {
				return v
			}
			switch k {
			case "HOME":
				return e.home
			case "XDG_CONFIG_HOME":
				return filepath.Join(e.home, ".config")
			case "XDG_STATE_HOME":
				return filepath.Join(e.home, ".state")
			}
			return ""
		},
		Getwd:      func() (string, error) { return "/repo", nil },
		Home:       func() (string, error) { return e.home, nil },
		Runner:     e.runner,
		IsTerminal: func() bool { return false },
		GOOS:       "linux",
	}
	code = Main("test", args, io)
	return out.String() + errb.String(), code
}

// flat runs the command with --json and flattens the plan (write commands,
// descriptions, notes, file bodies, check messages) into one string for
// substring assertions. Failures and non-plan output are returned as is.
func (e *repoEnv) flat(args ...string) (string, int) {
	e.t.Helper()
	out, code := e.run(append(args, "--json")...)
	var o struct {
		Plan struct {
			Steps []struct {
				Kind, Description string
				Cmd               struct {
					Name string
					Args []string
				}
			}
			Files  []struct{ Content string }
			Notes  []string
			Checks []struct{ Message string }
		}
	}
	// stderr notes ("mygit: ...") trail the JSON document in the combined output
	doc := out
	if i := strings.LastIndex(out, "\n}\n"); i >= 0 {
		doc = out[:i+3]
	}
	if code != 0 || json.Unmarshal([]byte(doc), &o) != nil {
		return out, code
	}
	var b strings.Builder
	for _, st := range o.Plan.Steps {
		if st.Kind == "write" {
			b.WriteString(st.Cmd.Name + " " + strings.Join(st.Cmd.Args, " ") + "   # " + st.Description + "\n")
		}
	}
	for _, f := range o.Plan.Files {
		b.WriteString(f.Content)
	}
	b.WriteString(strings.Join(o.Plan.Notes, "\n"))
	return b.String() + "\n" + out, code
}

func TestRepoCreate(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		setup    func(*repoEnv)
		wantCode int
		want     []string // substrings of the output
		notWant  []string
		wantCall string // a gh/git call that must have run during planning
	}{
		{
			name: "private repo, profile picked by owner match",
			args: []string{"repo", "create", "acme/api", "--private"},
			setup: func(e *repoEnv) {
				e.inRepo("abc123", "develop/base")
				e.ghLogin("octo-work")
			},
			want: []string{
				"profile work", "match remoteOwner=acme",
				"gh repo create acme/api --private --source . --push",
				"not applied (dry run)",
			},
		},
		{
			name:  "public repo",
			args:  []string{"repo", "create", "octo/site", "--public"},
			setup: func(e *repoEnv) { e.inRepo("abc123", "main"); e.ghLogin("octo") },
			want:  []string{"gh repo create octo/site --public --source . --push", "profile personal"},
		},
		{
			name:     "wrong gh account is denied (ID005)",
			args:     []string{"repo", "create", "acme/api", "--private"},
			setup:    func(e *repoEnv) { e.inRepo("abc123", "main"); e.ghLogin("octo") },
			wantCode: 2,
			want:     []string{"ID005", "octo-work"},
			notWant:  []string{"not applied"},
		},
		{
			name:     "neither --private nor --public",
			args:     []string{"repo", "create", "acme/api"},
			setup:    func(e *repoEnv) { e.inRepo("abc123", "main") },
			wantCode: 1, want: []string{"exactly one of --private or --public"},
		},
		{
			name:     "both visibilities",
			args:     []string{"repo", "create", "acme/api", "--private", "--public"},
			setup:    func(e *repoEnv) { e.inRepo("abc123", "main") },
			wantCode: 1, want: []string{"exactly one of"},
		},
		{
			name:     "bad slug",
			args:     []string{"repo", "create", "api", "--private"},
			setup:    func(e *repoEnv) { e.inRepo("abc123", "main") },
			wantCode: 1, want: []string{"want <owner>/<name>"},
		},
		{
			name: "origin already exists",
			args: []string{"repo", "create", "acme/api", "--private"},
			setup: func(e *repoEnv) {
				e.inRepo("abc123", "main")
				e.runner.on("git remote get-url origin", execx.Result{Stdout: "git@github.com:acme/api.git\n"})
			},
			wantCode: 1, want: []string{"remote origin already exists"},
		},
		{
			name:     "no commits yet",
			args:     []string{"repo", "create", "acme/api", "--private"},
			setup:    func(e *repoEnv) { e.inRepo("", "main"); e.ghLogin("octo-work") },
			wantCode: 1, want: []string{"no commits", "repo bootstrap"},
		},
		{
			name:     "outside a repository",
			args:     []string{"repo", "create", "acme/api", "--private"},
			setup:    func(e *repoEnv) {},
			wantCode: 1, want: []string{"not inside a git repository"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newRepoEnv(t, repoConfig)
			tt.setup(e)
			out, code := e.run(tt.args...)
			if code != tt.wantCode {
				t.Fatalf("exit %d, want %d\n%s", code, tt.wantCode, out)
			}
			for _, w := range tt.want {
				if !strings.Contains(out, w) {
					t.Errorf("output lacks %q:\n%s", w, out)
				}
			}
			for _, w := range tt.notWant {
				if strings.Contains(out, w) {
					t.Errorf("output has %q:\n%s", w, out)
				}
			}
			if e.runner.ran("gh repo create") {
				t.Errorf("dry run executed gh repo create")
			}
		})
	}
}

func TestRepoCreateJSONAndApply(t *testing.T) {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("abc123", "main")
	e.ghLogin("octo-work")
	out, code := e.run("repo", "create", "acme/api", "--private", "--json")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	var o struct {
		Status string `json:"status"`
		Extra  struct {
			Repo       string `json:"repo"`
			Visibility string `json:"visibility"`
			Profile    string `json:"profile"`
		} `json:"extra"`
		Plan struct {
			Profile string `json:"profile"`
		} `json:"plan"`
	}
	if err := json.Unmarshal([]byte(out), &o); err != nil {
		t.Fatalf("bad json: %v\n%s", err, out)
	}
	if o.Status != "planned" || o.Extra.Repo != "acme/api" || o.Extra.Visibility != "private" || o.Extra.Profile != "work" || o.Plan.Profile != "work" {
		t.Errorf("unexpected json: %+v", o)
	}

	e.runner.on("gh repo create acme/api --private --source . --push", execx.Result{Stdout: "https://github.com/acme/api\n"})
	out, code = e.run("repo", "create", "acme/api", "--private", "--yes")
	if code != 0 || !e.runner.ran("gh repo create acme/api --private --source . --push") {
		t.Fatalf("exit %d, gh not run:\n%s", code, out)
	}
}

func TestRepoCreateProfileFlagSkipsMatch(t *testing.T) {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("abc123", "main")
	e.ghLogin("octo")
	out, code := e.run("repo", "create", "acme/api", "--private", "--profile", "personal")
	if code != 0 || !strings.Contains(out, "profile personal") {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if !strings.Contains(out, "owner") || !strings.Contains(out, "acme is not the profile") {
		t.Errorf("want an owner warning:\n%s", out)
	}
}

const rulesetList = `[{"id":7,"name":"other"},{"id":42,"name":"my-git: protect main and develop/base"}]`

func protectEnv(t *testing.T, list string, listCode int) *repoEnv {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("abc123", "develop/base")
	e.runner.on("git remote get-url origin", execx.Result{Stdout: "git@github.com:acme/api.git\n"})
	e.ghLogin("octo-work")
	e.runner.on("gh api repos/acme/api/rulesets", execx.Result{Stdout: list, ExitCode: listCode})
	e.runner.on("gh api repos/acme/api/branches/develop/base --silent", execx.Result{})
	return e
}

func TestRepoProtect(t *testing.T) {
	tests := []struct {
		name    string
		args    []string
		list    string
		code    int
		want    []string
		notWant []string
	}{
		{
			name: "create when none exists",
			list: `[]`,
			want: []string{
				"gh api -X POST repos/acme/api/rulesets --input",
				"gh api -X PATCH repos/acme/api -f default_branch=develop/base",
				"create ruleset my-git: protect main and develop/base",
			},
			notWant: []string{"-X PUT", "delete_branch_on_merge"},
		},
		{
			name:    "update when the named ruleset exists",
			list:    rulesetList,
			want:    []string{"gh api -X PUT repos/acme/api/rulesets/42 --input", "update ruleset"},
			notWant: []string{"-X POST"},
		},
		{
			name:    "renamed set: the my-git: ruleset is updated, not duplicated",
			list:    `[{"id":5,"name":"unrelated"},{"id":77,"name":"my-git: protect main"}]`,
			want:    []string{"gh api -X PUT repos/acme/api/rulesets/77 --input"},
			notWant: []string{"-X POST"},
		},
		{
			name: "approvals and signed flags reach the body",
			args: []string{"--approvals", "2", "--require-signed"},
			list: `[]`,
			want: []string{`"required_approving_review_count": 2`, `"type": "required_signatures"`, `"type": "non_fast_forward"`, `"type": "deletion"`},
		},
		{
			name:    "default approvals are 0 and unsigned",
			list:    `[]`,
			want:    []string{`"required_approving_review_count": 0`},
			notWant: []string{"required_signatures"},
		},
		{
			name: "listing fails: plan a new ruleset with a note",
			list: ``, code: 1,
			want: []string{"gh api -X POST repos/acme/api/rulesets", "could not list rulesets"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := protectEnv(t, tt.list, tt.code)
			out, code := e.flat(append([]string{"repo", "protect"}, tt.args...)...)
			if code != 0 {
				t.Fatalf("exit %d\n%s", code, out)
			}
			for _, w := range tt.want {
				if !strings.Contains(out, w) {
					t.Errorf("output lacks %q:\n%s", w, out)
				}
			}
			for _, w := range tt.notWant {
				if strings.Contains(out, w) {
					t.Errorf("output has %q:\n%s", w, out)
				}
			}
			// gh api -X ... is ask (GH003): it must be listed, never run in a dry run.
			for _, c := range e.runner.calls {
				if strings.Contains(c, "-X ") {
					t.Errorf("dry run executed %s", c)
				}
			}
		})
	}
}

func TestRepoProtectBodyAndDefaultBranchMissing(t *testing.T) {
	e := protectEnv(t, `[]`, 0)
	delete(e.runner.Responses, "gh api repos/acme/api/branches/develop/base --silent")
	e.runner.prefixes = nil
	e.inRepo("abc123", "develop/base")
	e.runner.on("git remote get-url origin", execx.Result{Stdout: "git@github.com:acme/api.git\n"})
	e.ghLogin("octo-work")
	e.runner.on("gh api repos/acme/api/rulesets", execx.Result{Stdout: `[]`})
	// branches/develop/base falls through to exit 1: not on GitHub yet.
	out, code := e.run("repo", "protect", "--json")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	var o struct {
		Plan struct {
			Files []struct{ Content string } `json:"files"`
			Notes []string                   `json:"notes"`
		} `json:"plan"`
		Extra map[string]any `json:"extra"`
	}
	if err := json.Unmarshal([]byte(out), &o); err != nil {
		t.Fatalf("bad json: %v\n%s", err, out)
	}
	if len(o.Plan.Files) != 1 || !json.Valid([]byte(o.Plan.Files[0].Content)) {
		t.Fatalf("want one valid ruleset body, got %+v", o.Plan.Files)
	}
	if strings.Contains(out, "default_branch=") {
		t.Errorf("default branch must not be planned while it is missing on GitHub:\n%s", out)
	}
	if !strings.Contains(strings.Join(o.Plan.Notes, "\n"), "is not on GitHub yet") {
		t.Errorf("want a note, got %v", o.Plan.Notes)
	}
	if o.Extra["method"] != "POST" || o.Extra["repo"] != "acme/api" {
		t.Errorf("extra = %v", o.Extra)
	}
}

func TestRepoProtectConfigApprovalsAndRepoFlag(t *testing.T) {
	cfg := strings.Replace(repoConfig, `"defaultProfile"`, `"github": {"ruleset": {"approvals": 3}}, "defaultProfile"`, 1)
	e := newRepoEnv(t, cfg)
	e.runner.on("gh api repos/acme/other/rulesets", execx.Result{Stdout: `[]`})
	e.ghLogin("octo-work")                                         // --repo picks the profile by owner (acme -> work)
	out, code := e.flat("repo", "protect", "--repo", "acme/other") // outside a repository
	if code != 0 || !strings.Contains(out, `"required_approving_review_count": 3`) {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if !strings.Contains(out, "repos/acme/other/rulesets") {
		t.Errorf("repo flag ignored:\n%s", out)
	}
}

func TestRepoProtectDeniedAccountAndNoRemote(t *testing.T) {
	e := protectEnv(t, `[]`, 0)
	e.runner.prefixes = nil
	e.inRepo("abc123", "develop/base")
	e.runner.on("git remote get-url origin", execx.Result{Stdout: "git@github.com:acme/api.git\n"})
	e.ghLogin("someone-else")
	out, code := e.run("repo", "protect")
	if code != 2 || !strings.Contains(out, "ID005") {
		t.Fatalf("exit %d, want deny:\n%s", code, out)
	}
	for _, c := range e.runner.calls {
		if strings.HasPrefix(c, "gh api repos/") {
			t.Errorf("gh was called before the account check passed: %s", c)
		}
	}

	e2 := newRepoEnv(t, repoConfig)
	e2.inRepo("abc123", "main")
	out, code = e2.run("repo", "protect")
	if code != 1 || !strings.Contains(out, "no \"origin\" remote") {
		t.Fatalf("exit %d\n%s", code, out)
	}
}

func TestRepoBootstrap(t *testing.T) {
	tests := []struct {
		name     string
		setup    func(*repoEnv)
		args     []string
		wantCode int
		want     []string
		notWant  []string
	}{
		{
			name:  "fresh repo, unborn master, nothing staged",
			setup: func(e *repoEnv) { e.inRepo("", "master") },
			want: []string{
				"git symbolic-ref HEAD refs/heads/main",
				"-c user.name=", "Octo", "-c user.email=octo@home.io",
				"commit --allow-empty -m",
				"chore: bootstrap repository",
				"git branch develop/base main",
			},
			notWant: []string{"gpg.format", " -S "},
		},
		{
			name: "signing profile pins identity and signs",
			setup: func(e *repoEnv) {
				e.inRepo("", "main")
			},
			args: []string{"--profile", "work"},
			want: []string{"user.name=", "Octo Work", "user.email=octo@acme.io", "gpg.format=ssh", "user.signingkey=/k/work.pub", "commit -S --allow-empty"},
		},
		{
			name: "staged files: no --allow-empty",
			setup: func(e *repoEnv) {
				e.inRepo("", "main")
				e.runner.on("git status --porcelain=v1 -z", execx.Result{Stdout: "A  README.md\x00"})
			},
			want:    []string{"chore: bootstrap repository"},
			notWant: []string{"--allow-empty"},
		},
		{
			name: "bootstrap commit already there: only the staging branch",
			setup: func(e *repoEnv) {
				e.inRepo("abc123", "main")
				e.runner.on("git show-ref --verify --quiet refs/heads/main", execx.Result{})
				e.runner.on("git rev-list --count refs/heads/main", execx.Result{Stdout: "1\n"})
				e.runner.on("git log --max-parents=0 --format=%s refs/heads/main", execx.Result{Stdout: "chore: bootstrap repository\n"})
			},
			want:    []string{"git branch develop/base main"},
			notWant: []string{"git commit", "symbolic-ref"},
		},
		{
			name: "everything exists: nothing to do",
			setup: func(e *repoEnv) {
				e.inRepo("abc123", "main")
				e.runner.on("git show-ref --verify --quiet refs/heads/", execx.Result{})
				e.runner.on("git rev-list --count refs/heads/main", execx.Result{Stdout: "9\n"})
				e.runner.on("git log --max-parents=0 --format=%s refs/heads/main", execx.Result{Stdout: "init\n"})
			},
			want: []string{"already exist", `"alreadyDone": true`},
		},
		{
			name: "history on main and no staging branch: refused",
			setup: func(e *repoEnv) {
				e.inRepo("abc123", "main")
				e.runner.on("git show-ref --verify --quiet refs/heads/main", execx.Result{})
				e.runner.on("git rev-list --count refs/heads/main", execx.Result{Stdout: "9\n"})
				e.runner.on("git log --max-parents=0 --format=%s refs/heads/main", execx.Result{Stdout: "init\n"})
			},
			wantCode: 1, want: []string{"refusing to bootstrap"},
		},
		{
			name:     "commits but no main branch: refused",
			setup:    func(e *repoEnv) { e.inRepo("abc123", "master") },
			wantCode: 1, want: []string{`no "main" branch`},
		},
		{
			name:  "not a repository: init first",
			setup: func(e *repoEnv) {},
			want:  []string{"git init -b main", "commit --allow-empty", "git branch develop/base main"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newRepoEnv(t, repoConfig)
			tt.setup(e)
			out, code := e.flat(append([]string{"repo", "bootstrap"}, tt.args...)...)
			if code != tt.wantCode {
				t.Fatalf("exit %d, want %d\n%s", code, tt.wantCode, out)
			}
			for _, w := range tt.want {
				if !strings.Contains(out, w) {
					t.Errorf("output lacks %q:\n%s", w, out)
				}
			}
			for _, w := range tt.notWant {
				if strings.Contains(out, w) {
					t.Errorf("output has %q:\n%s", w, out)
				}
			}
			for _, c := range e.runner.calls {
				if strings.HasPrefix(c, "git commit") || strings.HasPrefix(c, "git -c") || strings.HasPrefix(c, "git init") || strings.HasPrefix(c, "git branch ") {
					t.Errorf("dry run executed %s", c)
				}
			}
		})
	}
}

func TestRepoBootstrapApplyAndJSON(t *testing.T) {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("", "main")
	e.runner.on("git -c", execx.Result{})
	e.runner.on("git branch develop/base main", execx.Result{})
	out, code := e.run("repo", "bootstrap", "--yes")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if !e.runner.ran("git -c user.name=Octo -c user.email=octo@home.io commit --allow-empty -m chore: bootstrap repository") ||
		!e.runner.ran("git branch develop/base main") {
		t.Errorf("steps not run: %v", e.runner.calls)
	}

	e = newRepoEnv(t, repoConfig)
	e.inRepo("", "main")
	out, code = e.run("repo", "bootstrap", "--json")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	var o struct {
		Status string `json:"status"`
		Extra  struct {
			Production  string   `json:"production"`
			Staging     string   `json:"staging"`
			Steps       []string `json:"steps"`
			AlreadyDone bool     `json:"alreadyDone"`
		} `json:"extra"`
	}
	if err := json.Unmarshal([]byte(out), &o); err != nil {
		t.Fatalf("bad json: %v\n%s", err, out)
	}
	if o.Status != "planned" || o.Extra.Production != "main" || o.Extra.Staging != "develop/base" || len(o.Extra.Steps) != 2 || o.Extra.AlreadyDone {
		t.Errorf("unexpected json: %+v", o)
	}
}

func TestRepoProtectDeleteBranchOnMergeConfig(t *testing.T) {
	cfg := strings.Replace(repoConfig, `"defaultProfile"`, `"github": {"deleteBranchOnMerge": true}, "defaultProfile"`, 1)
	e := newRepoEnv(t, cfg)
	e.inRepo("abc123", "develop/base")
	e.runner.on("git remote get-url origin", execx.Result{Stdout: "git@github.com:acme/api.git\n"})
	e.ghLogin("octo-work")
	e.runner.on("gh api repos/acme/api/rulesets", execx.Result{Stdout: `[]`})
	e.runner.on("gh api repos/acme/api/branches/develop/base --silent", execx.Result{})
	out, code := e.flat("repo", "protect")
	if code != 0 || !strings.Contains(out, "-f default_branch=develop/base -F delete_branch_on_merge=true") {
		t.Fatalf("exit %d\n%s", code, out)
	}
}

func TestRepoProtectNoPatchWhenNothingToSet(t *testing.T) {
	e := protectEnv(t, `[]`, 0)
	e.runner.prefixes = nil
	delete(e.runner.Responses, "gh api repos/acme/api/branches/develop/base --silent")
	e.inRepo("abc123", "develop/base")
	e.runner.on("git remote get-url origin", execx.Result{Stdout: "git@github.com:acme/api.git\n"})
	e.ghLogin("octo-work")
	e.runner.on("gh api repos/acme/api/rulesets", execx.Result{Stdout: `[]`})
	out, code := e.flat("repo", "protect")
	if code != 0 || strings.Contains(out, "-X PATCH") {
		t.Fatalf("exit %d, want no PATCH step\n%s", code, out)
	}
}

func TestRepoProtectListFailureNotesOnStderr(t *testing.T) {
	e := protectEnv(t, ``, 1)
	out, code := e.run("repo", "protect")
	if code != 0 || !strings.Contains(out, "mygit: could not list rulesets") {
		t.Fatalf("exit %d\n%s", code, out)
	}
}

func TestRepoProtectManualRender(t *testing.T) {
	e := protectEnv(t, `[]`, 0)
	out, code := e.run("repo", "protect", "--mode", "manual", "--shell", "bash")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	for _, w := range []string{"gh api -X POST repos/acme/api/rulesets --input", "mygit-", ".ruleset.json"} {
		if !strings.Contains(out, w) {
			t.Errorf("manual script lacks %q:\n%s", w, out)
		}
	}
}

func TestRepoCreatePushesLongLivedBranches(t *testing.T) {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("abc123", "main")
	e.ghLogin("octo")
	e.runner.on("git show-ref --verify --quiet refs/heads/", execx.Result{})
	out, code := e.flat("repo", "create", "octo/site", "--public")
	if code != 0 || !strings.Contains(out, "git push -u origin main develop/base") {
		t.Fatalf("exit %d\n%s", code, out)
	}
}

func TestRepoCreateEnvProfileBeatsOwnerMatch(t *testing.T) {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("abc123", "main")
	e.ghLogin("octo")
	out, code := e.runEnv(map[string]string{"MYGIT_PROFILE": "personal"}, "repo", "create", "acme/api", "--private", "--json")
	if code != 0 || !strings.Contains(out, `"profile": "personal"`) {
		t.Fatalf("exit %d, want profile personal (env var beats owner match)\n%s", code, out)
	}
}

func TestRepoBootstrapSigningNoneAndHome(t *testing.T) {
	none := strings.Replace(repoConfig, `"email": ["octo@home.io"]`, `"email": ["octo@home.io"], "signing": {"format": "none"}`, 1)
	e := newRepoEnv(t, none)
	e.inRepo("", "main")
	out, code := e.flat("repo", "bootstrap")
	if code != 0 || !strings.Contains(out, "commit.gpgsign=false") || strings.Contains(out, " -S ") {
		t.Fatalf("none: exit %d\n%s", code, out)
	}
	tilde := strings.Replace(repoConfig, `"key": "/k/work.pub"`, `"key": "~/.ssh/work.pub"`, 1)
	e = newRepoEnv(t, tilde)
	e.inRepo("", "main")
	out, code = e.flat("repo", "bootstrap", "--profile", "work")
	if code != 0 || !strings.Contains(out, "user.signingkey="+e.home+"/.ssh/work.pub") {
		t.Fatalf("tilde: exit %d\n%s", code, out)
	}
}

func TestRepoBootstrapManualQuoting(t *testing.T) {
	e := newRepoEnv(t, repoConfig)
	e.inRepo("", "main")
	out, code := e.run("repo", "bootstrap", "--mode", "manual", "--shell", "bash")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if !strings.Contains(out, "'Octo'") && !strings.Contains(out, "user.name=Octo") {
		t.Errorf("identity not rendered:\n%s", out)
	}
	if !strings.Contains(out, "'chore: bootstrap repository'") {
		t.Errorf("commit subject must be quoted:\n%s", out)
	}
}

func TestRepoCreateAskSeverityID005IsPlanCheck(t *testing.T) {
	cfg := strings.Replace(repoConfig, `"defaultProfile"`, `"rules": {"ID005": "ask"}, "defaultProfile"`, 1)
	e := newRepoEnv(t, cfg)
	e.inRepo("abc123", "main")
	e.ghLogin("octo") // not octo-work
	out, code := e.run("repo", "create", "acme/api", "--private")
	if code != 0 || !strings.Contains(out, "ID005") || !strings.Contains(out, "ask") {
		t.Fatalf("exit %d, want a plan with an ask-level ID005 check\n%s", code, out)
	}
}
