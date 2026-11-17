package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

const branchFmt = "--format=%(refname:short)%09%(objectname)%09%(worktreepath)"

// branchFake scripts a repository at /repo on develop/feat/cur with the
// default workflow branches present locally.
func branchFake() *execx.Fake {
	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	f.OnOut("git rev-parse --show-toplevel", "/repo\n")
	f.OnOut("git remote get-url origin", "git@github.com:octo/repo.git\n")
	f.OnOut("git rev-parse -q --verify HEAD^{commit}", "abc123\n")
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "")
	f.OnOut("git symbolic-ref --short -q HEAD", "develop/feat/cur\n")
	f.OnOut("git show-ref --verify --quiet refs/heads/develop/base", "")
	f.OnOut("git show-ref --verify --quiet refs/heads/main", "")
	return f
}

// runBranch drives the cobra tree through Main with temp HOME/XDG dirs.
func runBranch(t *testing.T, f *execx.Fake, args ...string) (code int, stdout, stderr string) {
	t.Helper()
	return runBranchHome(t, t.TempDir(), f, args...)
}

// runBranchHome is runBranch with a caller-owned HOME/XDG tree, so a second
// run can find the plan the first one saved.
func runBranchHome(t *testing.T, home string, f *execx.Fake, args ...string) (code int, stdout, stderr string) {
	t.Helper()
	var out, errb bytes.Buffer
	io := IO{
		In: strings.NewReader(""), Out: &out, Err: &errb,
		Getenv: func(k string) string {
			if k == "XDG_CONFIG_HOME" {
				return home + "/config"
			}
			if k == "XDG_STATE_HOME" {
				return home + "/state"
			}
			return ""
		},
		Getwd:      func() (string, error) { return "/repo", nil },
		Home:       func() (string, error) { return home, nil },
		Now:        time.Now,
		Runner:     f,
		IsTerminal: func() bool { return false },
		GOOS:       "linux",
	}
	code = Main("test", args, io)
	return code, out.String(), errb.String()
}

func hasCall(f *execx.Fake, line string) bool {
	for _, c := range f.CallLines() {
		if c == line {
			return true
		}
	}
	return false
}

func TestBranchNewDryRunPlansOnly(t *testing.T) {
	f := branchFake()
	code, out, errs := runBranch(t, f, "branch", "new", "feat", "login")
	if code != 0 {
		t.Fatalf("exit %d: %s%s", code, out, errs)
	}
	if !strings.Contains(out, "git switch --no-track -c develop/feat/login develop/base") {
		t.Errorf("plan lacks the switch step:\n%s", out)
	}
	if !strings.Contains(out, "not applied (dry run)") {
		t.Errorf("not a dry run:\n%s", out)
	}
	if hasCall(f, "git switch --no-track -c develop/feat/login develop/base") {
		t.Error("dry run executed the write")
	}
}

func TestBranchNewYesApplies(t *testing.T) {
	f := branchFake()
	f.OnOut("git switch --no-track -c develop/feat/login develop/base", "")
	code, out, errs := runBranch(t, f, "branch", "new", "feat", "login", "--yes")
	if code != 0 {
		t.Fatalf("exit %d: %s%s", code, out, errs)
	}
	if !hasCall(f, "git switch --no-track -c develop/feat/login develop/base") {
		t.Errorf("switch not executed: %v", f.CallLines())
	}
	if !strings.Contains(out, "✓ develop/feat/login (from develop/base)") {
		t.Errorf("missing confirmation line:\n%s", out)
	}
}

func TestBranchNewFallsBackToRemoteBase(t *testing.T) {
	f := branchFake()
	delete(f.Responses, "git show-ref --verify --quiet refs/heads/develop/base")
	f.OnOut("git show-ref --verify --quiet refs/remotes/origin/develop/base", "")
	code, out, errs := runBranch(t, f, "branch", "new", "fix", "crash")
	if code != 0 || !strings.Contains(out, "git switch --no-track -c develop/fix/crash origin/develop/base") {
		t.Fatalf("exit %d:\n%s%s", code, out, errs)
	}
}

func TestBranchNewJSON(t *testing.T) {
	f := branchFake()
	code, out, errs := runBranch(t, f, "branch", "new", "feat", "login", "--json")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errs)
	}
	var got struct {
		Status string
		Plan   struct{ ID string }
		Extra  map[string]any
	}
	if err := json.Unmarshal([]byte(out), &got); err != nil {
		t.Fatalf("not one JSON object: %v\n%s", err, out)
	}
	if got.Status != "planned" || got.Plan.ID == "" || got.Extra["branch"] != "develop/feat/login" || got.Extra["base"] != "develop/base" {
		t.Errorf("unexpected json: %+v", got)
	}
}

func TestBranchNewErrors(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		setup    func(*execx.Fake)
		wantCode int
		wantErr  string
	}{
		{"unknown type", []string{"branch", "new", "wip", "x"}, nil, 1, "unknown branch type"},
		{"exists", []string{"branch", "new", "feat", "cur"}, func(f *execx.Fake) {
			f.OnOut("git show-ref --verify --quiet refs/heads/develop/feat/cur", "")
		}, 1, "already exists"},
		{"no base", []string{"branch", "new", "feat", "x"}, func(f *execx.Fake) {
			delete(f.Responses, "git show-ref --verify --quiet refs/heads/develop/base")
		}, 1, "base branch develop/base not found"},
		{"BR003 name", []string{"branch", "new", "feat", "Bad Name"}, nil, 2, ""},
	}
	for _, tt := range tests {
		f := branchFake()
		if tt.setup != nil {
			tt.setup(f)
		}
		code, out, errs := runBranch(t, f, tt.args...)
		if code != tt.wantCode || !strings.Contains(errs, tt.wantErr) {
			t.Errorf("%s: exit %d (want %d)\nout: %s\nerr: %s", tt.name, code, tt.wantCode, out, errs)
		}
		if tt.wantCode == 2 && !strings.Contains(out, "BR003") {
			t.Errorf("%s: denial does not name BR003:\n%s", tt.name, out)
		}
		for _, c := range f.CallLines() {
			if strings.HasPrefix(c, "git switch") {
				t.Errorf("%s: planned write ran: %s", tt.name, c)
			}
		}
	}
}

func TestBranchCutDefaultListsNothing(t *testing.T) {
	code, _, errs := runBranch(t, branchFake(), "branch", "cut", "release", "1.0.0")
	if code != 1 || !strings.Contains(errs, "no branch transitions") {
		t.Fatalf("exit %d: %s", code, errs)
	}
}

func TestBranchPrune(t *testing.T) {
	f := branchFake()
	f.OnOut("git rev-parse develop/base", "tipbase\n")
	f.OnOut("git rev-parse main", "tipmain\n")
	f.OnOut("git branch --merged develop/base "+branchFmt,
		"develop/base\ttipbase\t\n"+
			"develop/feat/old\ttip1\t\n"+
			"develop/feat/cur\ttip2\t\n"+
			"develop/feat/empty\ttipbase\t\n"+
			"develop/feat/wt\ttip3\t/other/wt\n"+
			"main\ttipmain\t\n")
	f.OnOut("git branch --merged main "+branchFmt, "main\ttipmain\t\ndevelop/feat/old\ttip1\t\n")
	f.OnOut("gh --version", "gh version 2.50.0 (2024-01-01)\n")
	f.OnOut("gh auth status --active --hostname github.com --json hosts", `{"hosts":{"github.com":[{"active":true,"state":"success","login":"octo"}]}}`)
	f.OnOut("gh pr list --state merged --limit 200 --json number,headRefName",
		`[{"number":7,"headRefName":"develop/feat/squashed"},{"number":3,"headRefName":"develop/feat/old"}]`)
	f.OnOut("git show-ref --verify --quiet refs/heads/develop/feat/squashed", "")
	f.OnOut("git merge-base --is-ancestor develop/feat/old HEAD", "")

	code, out, errs := runBranch(t, f, "branch", "prune")
	if code != 0 {
		t.Fatalf("exit %d: %s%s", code, out, errs)
	}
	if !strings.Contains(out, "git branch -d develop/feat/old") {
		t.Errorf("old branch not planned:\n%s", out)
	}
	for _, bad := range []string{"-d develop/feat/cur", "-d develop/feat/empty", "-d develop/feat/wt", "-d develop/base", "-d main", "-D "} {
		if strings.Contains(strings.ReplaceAll(out, "-D develop/feat/squashed", ""), bad) {
			t.Errorf("plan contains %q:\n%s", bad, out)
		}
	}
	for _, want := range []string{"kept develop/feat/cur: current branch", "no work yet", "worktree /other/wt", "PR #7 merged"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing note %q:\n%s", want, out)
		}
	}
	for _, c := range f.CallLines() {
		if strings.HasPrefix(c, "git branch -d") {
			t.Errorf("dry run executed %s", c)
		}
	}
}

func TestBranchPruneYesDeletesAndJSONNothing(t *testing.T) {
	f := branchFake()
	f.Responses["git remote get-url origin"] = execx.Result{ExitCode: 1} // no remote: gh is not consulted
	f.OnOut("git rev-parse develop/base", "tipbase\n")
	f.OnOut("git rev-parse main", "tipmain\n")
	f.OnOut("git branch --merged develop/base "+branchFmt, "develop/feat/old\ttip1\t\n")
	f.OnOut("git branch --merged main "+branchFmt, "")
	f.OnOut("git branch -d develop/feat/old", "")
	f.OnOut("git merge-base --is-ancestor develop/feat/old HEAD", "")
	code, out, errs := runBranch(t, f, "branch", "prune", "--yes")
	if code != 0 || !strings.Contains(out, "✓ deleted develop/feat/old") || !hasCall(f, "git branch -d develop/feat/old") {
		t.Fatalf("exit %d:\n%s%s\n%v", code, out, errs, f.CallLines())
	}
	for _, c := range f.CallLines() {
		if strings.HasPrefix(c, "gh ") {
			t.Errorf("gh called without a remote: %s", c)
		}
	}

	f = branchFake()
	f.Responses["git remote get-url origin"] = execx.Result{ExitCode: 1}
	f.OnOut("git rev-parse develop/base", "tipbase\n")
	f.OnOut("git rev-parse main", "tipmain\n")
	f.OnOut("git branch --merged develop/base "+branchFmt, "develop/base\ttipbase\t\n")
	f.OnOut("git branch --merged main "+branchFmt, "")
	code, out, _ = runBranch(t, f, "branch", "prune", "--json")
	var got struct {
		Status string
		Extra  struct{ Delete []string }
	}
	if err := json.Unmarshal([]byte(out), &got); err != nil || code != 0 || got.Status != "noop" || len(got.Extra.Delete) != 0 {
		t.Fatalf("exit %d, %v, %+v\n%s", code, err, got, out)
	}
}

func TestBranchPruneSkipsWrongGhAccount(t *testing.T) {
	f := branchFake()
	f.OnOut("git rev-parse develop/base", "tipbase\n")
	f.OnOut("git rev-parse main", "tipmain\n")
	f.OnOut("git branch --merged develop/base "+branchFmt, "")
	f.OnOut("git branch --merged main "+branchFmt, "")
	f.OnOut("gh --version", "gh version 2.50.0 (2024-01-01)\n")
	// gh not logged in: ID005 reports it, so the merged-PR query must not run.
	code, out, errs := runBranch(t, f, "branch", "prune")
	if code != 2 || !strings.Contains(out, "ID005") {
		t.Fatalf("a denied ID005 must be reported with exit 2, got %d: %s%s", code, out, errs)
	}
	for _, c := range f.CallLines() {
		if strings.HasPrefix(c, "gh pr list") {
			t.Errorf("gh pr list ran despite ID005: %s", c)
		}
	}
}

// A branch merged into the base but not into HEAD would make `git branch -d`
// refuse, so prune keeps it instead of planning a failing delete.
func TestBranchPruneKeepsBranchNotMergedIntoHead(t *testing.T) {
	f := branchFake()
	f.Responses["git remote get-url origin"] = execx.Result{ExitCode: 1}
	f.OnOut("git rev-parse develop/base", "tipbase\n")
	f.OnOut("git rev-parse main", "tipmain\n")
	f.OnOut("git branch --merged develop/base "+branchFmt, "develop/feat/old\ttip1\t\n")
	f.OnOut("git branch --merged main "+branchFmt, "")
	// merge-base --is-ancestor <b> HEAD and the upstream lookup fall to the exit-1 default.
	code, out, errs := runBranch(t, f, "branch", "prune", "--yes")
	if code != 0 {
		t.Fatalf("exit %d: %s%s", code, out, errs)
	}
	if hasCall(f, "git branch -d develop/feat/old") || strings.Contains(out, "✓ deleted") {
		t.Errorf("planned a delete git would refuse:\n%s", out)
	}
	if !strings.Contains(out, "kept develop/feat/old") || !strings.Contains(out, "not into HEAD") {
		t.Errorf("missing kept note:\n%s", out)
	}
}

// dry run -> --apply <id> reaches the same plan and runs the write.
func TestBranchNewApplyByID(t *testing.T) {
	home := t.TempDir()
	code, out, errs := runBranchHome(t, home, branchFake(), "branch", "new", "feat", "login")
	id := planID(out)
	if code != 0 || id == "" {
		t.Fatalf("dry run: exit %d, no plan id:\n%s%s", code, out, errs)
	}
	f := branchFake()
	f.OnOut("git switch --no-track -c develop/feat/login develop/base", "")
	code, out, errs = runBranchHome(t, home, f, "branch", "new", "feat", "login", "--apply", id)
	if code != 0 || !hasCall(f, "git switch --no-track -c develop/feat/login develop/base") {
		t.Fatalf("apply: exit %d:\n%s%s\n%v", code, out, errs, f.CallLines())
	}
}

// planID extracts the id from the "plan <id>   dry-run" header.
func planID(out string) string {
	_, rest, ok := strings.Cut(out, "plan ")
	if !ok {
		return ""
	}
	id, _, _ := strings.Cut(rest, " ")
	return id
}

const branchReleaseTrain = `{"name":"release-train","extends":"default",
 "branches":{"staging":{"name":"develop"},"release":{"name":"release/{version}","protected":true,"pattern":true}},
 "transitions":[{"id":"merge","from":"work","to":"staging","via":"pr"},
  {"id":"cut","from":"staging","to":"release","via":"branch"},
  {"id":"release","from":"release","to":"production","via":"pr"}]}`

// branch cut through the CLI with a custom workflow: the branch comes from the
// transition's source branch and starts untracked.
func TestBranchCutCustomWorkflow(t *testing.T) {
	home := t.TempDir()
	dir := home + "/config/mygit/workflows"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(dir+"/release-train.json", []byte(branchReleaseTrain), 0o644); err != nil {
		t.Fatal(err)
	}
	f := branchFake()
	f.OnOut("git show-ref --verify --quiet refs/heads/develop", "")
	f.OnOut("git switch --no-track -c release/1.2.0 develop", "")
	code, out, errs := runBranchHome(t, home, f, "--workflow", "release-train", "branch", "cut", "cut", "1.2.0", "--yes")
	if code != 0 {
		t.Fatalf("exit %d:\n%s%s", code, out, errs)
	}
	if !hasCall(f, "git switch --no-track -c release/1.2.0 develop") || !strings.Contains(out, "✓ release/1.2.0 (from develop)") {
		t.Errorf("cut not applied:\n%s\n%v", out, f.CallLines())
	}
}
