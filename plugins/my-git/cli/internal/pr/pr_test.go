package pr

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

func defaultWorkflow(t *testing.T) *workflow.Compiled {
	t.Helper()
	r, err := config.Resolve(config.Inputs{GOOS: "linux", Getenv: func(string) string { return "" }})
	if err != nil {
		t.Fatal(err)
	}
	return r.Workflow
}

func TestResolve(t *testing.T) {
	wf := defaultWorkflow(t)
	cases := []struct {
		name    string
		resolve func() (Target, error)
		want    Target
		errHas  string
	}{
		{"create work branch", func() (Target, error) { return ResolveCreate(wf, "develop/feat/login", "") },
			Target{Transition: "merge", Head: "develop/feat/login", Base: "develop/base", Kind: tmpl.PRDefault, Template: "default"}, ""},
		{"create --to override", func() (Target, error) { return ResolveCreate(wf, "develop/fix/x", "main") },
			Target{Transition: "merge", Head: "develop/fix/x", Base: "main", Kind: tmpl.PRDefault, Template: "default"}, ""},
		{"create on staging", func() (Target, error) { return ResolveCreate(wf, "develop/base", "") }, Target{}, "protected branch"},
		{"create on production", func() (Target, error) { return ResolveCreate(wf, "main", "") }, Target{}, "protected branch"},
		{"create unknown branch", func() (Target, error) { return ResolveCreate(wf, "login-feature", "") }, Target{}, "no pull request route"},
		{"create detached", func() (Target, error) { return ResolveCreate(wf, "", "") }, Target{}, "detached"},
		{"release", func() (Target, error) { return ResolveRelease(wf) },
			Target{Transition: "release", Head: "develop/base", Base: "main", Kind: tmpl.PRRelease, Template: "release", Protected: true}, ""},
		{"run merge", func() (Target, error) { return ResolveTransition(wf, "merge", "develop/chore/deps", "") },
			Target{Transition: "merge", Head: "develop/chore/deps", Base: "develop/base", Kind: tmpl.PRDefault, Template: "default"}, ""},
		{"run release ignores checkout", func() (Target, error) { return ResolveTransition(wf, "release", "develop/feat/x", "") },
			Target{Transition: "release", Head: "develop/base", Base: "main", Kind: tmpl.PRRelease, Template: "release", Protected: true}, ""},
		{"run work from non-work branch", func() (Target, error) { return ResolveTransition(wf, "merge", "main", "") }, Target{}, "is not one"},
		{"run unknown", func() (Target, error) { return ResolveTransition(wf, "ship", "x", "") }, Target{}, `no pr transition "ship" (have: merge, release)`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := tc.resolve()
			if tc.errHas != "" {
				if err == nil || !strings.Contains(err.Error(), tc.errHas) {
					t.Fatalf("err = %v, want it to contain %q", err, tc.errHas)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got != tc.want {
				t.Errorf("got %+v\nwant %+v", got, tc.want)
			}
		})
	}
}

func TestParseCommitsAndTitle(t *testing.T) {
	cs := ParseCommits("a1\x1ffeat(auth): add login\x1fBody line.\n\nCo-Authored-By: X <x@y.z>\x1e\nb2\x1ffix: retry\x1f\x1e")
	if len(cs) != 2 || cs[0].Hash != "a1" || cs[0].Subject != "feat(auth): add login" || cs[1].Body != "" {
		t.Fatalf("commits = %+v", cs)
	}
	now := time.Date(2026, 11, 17, 0, 0, 0, 0, time.UTC)
	work := Target{Head: "develop/feat/oauth-login", Base: "develop/base", Kind: tmpl.PRDefault}
	types := []string{"feat", "fix"}
	if got := DeriveTitle(work, cs[:1], types, "", now); got != "feat(auth): add login" {
		t.Errorf("single commit title %q", got)
	}
	if got := DeriveTitle(work, cs, types, "", now); got != "feat: oauth login" {
		t.Errorf("branch title %q", got)
	}
	rel := Target{Head: "develop/base", Base: "main", Kind: tmpl.PRRelease}
	if got := DeriveTitle(rel, cs, types, "", now); got != "chore(release): 2026-11-17" {
		t.Errorf("release title %q", got)
	}
	if got := DeriveTitle(rel, cs, types, "v1.2.0", now); got != "chore(release): v1.2.0" {
		t.Errorf("release title %q", got)
	}
}

func TestAutoSectionsDropsTrailers(t *testing.T) {
	cs := ParseCommits("a1\x1ffeat: x\x1fWhy it matters.\n\nCo-Authored-By: Claude <n@a.com>\n🤖 Generated with Claude Code\x1e")
	got := AutoSections(Target{Head: "develop/feat/x", Base: "develop/base", Kind: tmpl.PRDefault}, cs, "1 file changed\n", "", time.Time{})
	if got["explanation"] != "Why it matters." {
		t.Errorf("explanation = %q", got["explanation"])
	}
	if !strings.Contains(got["detailed changes"], "- feat: x") || !strings.Contains(got["detailed changes"], "1 file changed") {
		t.Errorf("detailed changes = %q", got["detailed changes"])
	}
	if got["summary"] != "feat: x" {
		t.Errorf("summary = %q", got["summary"])
	}
}

func TestTestResultsHasNoOutput(t *testing.T) {
	out := TestResults([]CheckRun{
		{Label: "Unit tests", Command: "go test ./..."},
		{Label: "Lint", Command: "golangci-lint run", ExitCode: 1},
	})
	for _, want := range []string{
		"- Unit tests: `go test ./...` → passed",
		"- Lint: `golangci-lint run` → FAILED (exit 1)",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("lacks %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "```") {
		t.Errorf("command output in the body:\n%s", out)
	}
	none := TestResults([]CheckRun{{Label: "Unit tests"}, {Label: "Lint"}})
	if !strings.Contains(none, "Unit tests: none configured") || !strings.Contains(none, "Lint: none configured") {
		t.Errorf("none = %q", none)
	}
	started := TestResults([]CheckRun{{Label: "Lint", Command: "nolint", StartError: "program not found"}})
	if !strings.Contains(started, "could not run: program not found") {
		t.Errorf("start error = %q", started)
	}
	skipped := TestResults([]CheckRun{{Label: "Unit tests", Command: "make test", Skipped: true}, {Label: "Lint", Command: "make lint", Skipped: true}})
	if !strings.Contains(skipped, "Unit tests: `make test` → not run (--no-checks)") || !strings.Contains(skipped, "Lint: `make lint` → not run") {
		t.Errorf("skipped = %q", skipped)
	}
}

func TestCheckCmd(t *testing.T) {
	cases := []struct {
		line, goos string
		want       []string
		cmdLine    string
	}{
		{"go test ./...", "linux", []string{"sh", "-c", "go test ./..."}, ""},
		{`pytest -k "a b" -q`, "linux", []string{"sh", "-c", `pytest -k "a b" -q`}, ""},
		{"npm test && npm run lint", "linux", []string{"sh", "-c", "npm test && npm run lint"}, ""},
		{"make test | tee out", "windows", []string{"cmd", "/S", "/C", "make test | tee out"}, `cmd /S /C "make test | tee out"`},
		// Quotes reach cmd raw, not as \" escapes.
		{`go test -run "A B" ./...`, "windows", []string{"cmd", "/S", "/C", `go test -run "A B" ./...`}, `cmd /S /C "go test -run "A B" ./..."`},
	}
	for _, tc := range cases {
		c := CheckCmd(tc.line, "/repo", tc.goos)
		got := append([]string{c.Name}, c.Args...)
		if !reflect.DeepEqual(got, tc.want) || c.Dir != "/repo" || c.CmdLine != tc.cmdLine {
			t.Errorf("%q → %v cmdline %q dir %q; want %v %q", tc.line, got, c.CmdLine, c.Dir, tc.want, tc.cmdLine)
		}
	}
}

func TestSectionNames(t *testing.T) {
	gh008 := map[string]any{"sections": []any{
		map[string]any{"name": "Summary", "heading": `^#{1,4}\s*summary\b`},
		map[string]any{"name": "Detailed changes", "heading": `^#{1,4}\s*(detailed changes|changes)\b`},
		map[string]any{"name": "Explanation", "heading": `^#{1,4}\s*(explanation|why|rationale)\b`},
		map[string]any{"name": "Test results (local)", "heading": `^#{1,4}\s*(local )?test(s|ing)?( results?)?\b`},
	}}
	if got := SectionNames(nil, nil, gh008); !reflect.DeepEqual(got, tmpl.DefaultSections()) {
		t.Errorf("defaults: %v", got)
	}
	// The release template lacks two required sections: they are appended.
	got := SectionNames(nil, []string{"Summary", "Included PRs", "Test results"}, gh008)
	want := []string{"Summary", "Included PRs", "Test results", "Detailed changes", "Explanation"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("release: %v want %v", got, want)
	}
	// Configured sections win over the template.
	if got := SectionNames([]string{"Summary", "Why", "Tests"}, []string{"X"}, nil); !reflect.DeepEqual(got, []string{"Summary", "Why", "Tests"}) {
		t.Errorf("configured: %v", got)
	}
}

func TestFillForMatchesLoosely(t *testing.T) {
	got := fillFor([]string{"Summary", "Test results (local)"}, map[string]string{"SUMMARY": "s", "test results": "t"})
	if got["Summary"] != "s" || got["Test results (local)"] != "t" {
		t.Errorf("fillFor = %v", got)
	}
}

func TestBodyForCheckResolvesUnderRepoRoot(t *testing.T) {
	root := t.TempDir() // not "/": the body placeholder must resolve under it on every OS
	e := &app.Env{RepoRoot: root}
	e.ReadFile = func(string) ([]byte, error) { return []byte("disk"), nil }
	read := Input{Env: e}.bodyForCheck("planned body")
	// The engine joins the relative --body-file under its cwd (the repo root).
	got, err := read(filepath.Join(root, bodyCheckPath))
	if err != nil || string(got) != "planned body" {
		t.Errorf("planned body = %q, %v", got, err)
	}
	if got, _ := read(filepath.Join(root, "other.md")); string(got) != "disk" {
		t.Errorf("other file = %q", got)
	}
	if filepath.IsAbs(bodyCheckPath) {
		t.Errorf("bodyCheckPath %q must be relative (a /-rooted path is not absolute on Windows)", bodyCheckPath)
	}
}

func testPRWorld(t *testing.T) (*app.Env, *config.Resolved, *execx.Fake) {
	t.Helper()
	home := t.TempDir()
	repo := t.TempDir()
	cfgDir := filepath.Join(home, ".config", "mygit")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	global := `{"defaultProfile":"work","profiles":{"work":{
		"git":{"name":"Octo","email":["o@x.io"],"signing":{"format":"ssh","key":"~/.ssh/id.pub"}},
		"github":{"user":"octo","transport":"ssh","sshKey":"~/.ssh/id"}}}}`
	if err := os.WriteFile(filepath.Join(cfgDir, "config.json"), []byte(global), 0o644); err != nil {
		t.Fatal(err)
	}
	project := `{"version":1,"pr":{"checks":{"test":"go test ./...","lint":"go vet ./..."}}}`
	if err := os.WriteFile(filepath.Join(repo, ".mygit.json"), []byte(project), 0o644); err != nil {
		t.Fatal(err)
	}
	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	f.OnOut("git rev-parse --show-toplevel", repo+"\n")
	f.OnOut("git remote get-url origin", "git@github.com:octo/repo.git\n")
	f.OnOut("git remote get-url --push origin", "git@github.com:octo/repo.git\n")
	f.OnOut("git rev-parse -q --verify HEAD^{commit}", "deadbeef\n")
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "refs/heads/develop/feat/login deadbeef\n")
	f.OnOut("git symbolic-ref --short -q HEAD", "develop/feat/login\n")
	f.OnOut("git -C "+repo+" symbolic-ref --short -q HEAD", "develop/feat/login\n")
	f.OnOut("git show-ref --verify --quiet refs/heads/develop/feat/login", "")
	f.OnOut("git show-ref --verify --quiet refs/heads/develop/base", "")
	f.OnOut("git rev-parse --verify -q refs/remotes/origin/develop/base", "base123\n")
	f.OnOut("git log "+LogFormat+" origin/develop/base..develop/feat/login", "abc1234\x1ffeat(auth): add login\x1fWhy we added login.\x1e")
	f.OnOut("git diff --stat origin/develop/base...develop/feat/login", " 1 file changed, 5 insertions(+)\n")
	f.OnOut("sh -c go test ./...", "ok\n")
	f.OnOut("sh -c go vet ./...", "")
	f.OnOut("gh auth status", "✓ Logged in to github.com account octo (keyring)\n  - Active account: true\n")
	f.OnOut("gh auth status --active --hostname github.com --json hosts",
		`{"hosts":{"github.com":[{"active":true,"state":"success","login":"octo"}]}}`)
	f.OnOut("gh pr list --head develop/feat/login --base develop/base --state open --json url", "[]")
	f.OnOut("git log --format=%H%x1f%G?%x1f%GS%x1f%s origin/develop/base..develop/feat/login",
		"abc12340000\x1fG\x1fOcto\x1ffeat(auth): add login\n")
	f.OnOut("git rev-parse -q --verify refs/heads/develop/feat/login^{commit}", "abc1234\n")
	f.OnOut("git rev-parse --abbrev-ref --symbolic-full-name develop/feat/login@{upstream}", "origin/develop/feat/login\n")
	f.OnOut("git rev-list --left-right --count origin/develop/feat/login...refs/heads/develop/feat/login", "0\t1\n")

	e, err := app.Load(context.Background(), app.Deps{
		Runner: f, Cwd: repo, Home: home, GOOS: "linux",
		Getenv: func(k string) string {
			if k == "HOME" {
				return home
			}
			return ""
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	c, err := e.Require()
	if err != nil {
		t.Fatal(err)
	}
	return e, c, f
}

func TestPlanCreateAndRelease(t *testing.T) {
	e, c, f := testPRWorld(t)
	ctx := context.Background()
	now := time.Date(2026, 11, 17, 0, 0, 0, 0, time.UTC)

	// 1. Happy path work-branch PR plan.
	target, err := ResolveCreate(c.Workflow, "develop/feat/login", "")
	if err != nil {
		t.Fatal(err)
	}
	if target.String() != "develop/feat/login → develop/base" {
		t.Errorf("Target.String() = %q", target.String())
	}
	p := e.NewPlanner([]string{"pr", "create"})
	res, err := Plan(ctx, p, Input{Env: e, Config: c, Target: target, Current: "develop/feat/login", Draft: true, Now: now})
	if err != nil {
		t.Fatal(err)
	}
	if res.Blocked() || res.Title != "feat(auth): add login" || res.Push == "" || !strings.Contains(res.Create, "--draft") || res.Signed != 1 || res.Total != 1 {
		t.Errorf("Plan result = %+v", res)
	}

	// 2. Failing check blocks unless AllowFailingChecks is true.
	f.On("sh -c go vet ./...", execx.Result{ExitCode: 2, Stderr: "vet error\n"})
	pFail := e.NewPlanner([]string{"pr", "create"})
	resFail, err := Plan(ctx, pFail, Input{Env: e, Config: c, Target: target, Current: "develop/feat/login", Now: now})
	if err != nil || !resFail.Blocked() {
		t.Errorf("expected failing check to block: %+v, %v", resFail, err)
	}
	pAllow := e.NewPlanner([]string{"pr", "create"})
	resAllow, err := Plan(ctx, pAllow, Input{Env: e, Config: c, Target: target, Current: "develop/feat/login", AllowFailingChecks: true, Now: now})
	if err != nil || resAllow.Blocked() {
		t.Errorf("AllowFailingChecks should not block: %+v, %v", resAllow, err)
	}
	f.OnOut("sh -c go vet ./...", "")

	// 3. Existing open PR short-circuits.
	f.OnOut("gh pr list --head develop/feat/login --base develop/base --state open --json url", `[{"url":"https://github.com/octo/repo/pull/9"}]`)
	pExist := e.NewPlanner([]string{"pr", "create"})
	resExist, err := Plan(ctx, pExist, Input{Env: e, Config: c, Target: target, Now: now})
	if err != nil || resExist.Existing != "https://github.com/octo/repo/pull/9" {
		t.Errorf("existing PR: %+v, %v", resExist, err)
	}
	f.OnOut("gh pr list --head develop/feat/login --base develop/base --state open --json url", "[]")

	// 4. Release PR (protected head, skips GitHub noreply unsigned commits).
	relTarget, err := ResolveRelease(c.Workflow)
	if err != nil {
		t.Fatal(err)
	}
	f.OnOut("git rev-parse --verify -q refs/remotes/origin/main", "main123\n")
	f.OnOut("git log "+LogFormat+" origin/main..origin/develop/base", "c1\x1ffeat: one\x1fBody.\x1ec2\x1ffix: two\x1fBody 2.\x1e")
	f.OnOut("git diff --stat origin/main...origin/develop/base", " 2 files changed\n")
	f.OnOut("git rev-list --count origin/develop/base..develop/base", "1\n")
	f.OnOut("gh pr list --head develop/base --base main --state open --json url", "[]")
	f.OnOut("git log --no-merges --format=%H%x1f%G?%x1f%GS%x1f%s%x1f%ce origin/main..origin/develop/base",
		"c1\x1fN\x1f\x1fMerge PR\x1fnoreply@github.com\nc2\x1fN\x1f\x1ffix: two\x1fo@x.io\n")
	pRel := e.NewPlanner([]string{"pr", "release"})
	resRel, err := Plan(ctx, pRel, Input{Env: e, Config: c, Target: relTarget, Current: "develop/feat/login", NoChecks: true, Sections: map[string]string{"Explanation": "Release."}, Now: now})
	if err != nil {
		t.Fatal(err)
	}
	if resRel.Push != "" || resRel.Total != 1 || resRel.Signed != 0 {
		t.Errorf("release result = %+v", resRel)
	}
}

func TestCheckAndURLCapture(t *testing.T) {
	e, c, f := testPRWorld(t)
	f.OnOut("gh pr view 42 --json title,body,baseRefName,headRefName",
		`{"title":"feat: add x","body":"## Summary\nx\n## Detailed changes\ny\n## Explanation\nz\n## Test results\n- unit: ok\n- lint: ok\n","baseRefName":"develop/base","headRefName":"develop/feat/x"}`)
	info, v, err := Check(context.Background(), e, c, 42)
	if err != nil || info.Title != "feat: add x" || len(v.Findings) != 0 {
		t.Errorf("Check = %+v, %+v, %v", info, v, err)
	}

	var url string
	f.OnOut("gh pr create --title x", "creating...\nhttps://github.com/octo/repo/pull/12\n")
	cap := URLCapture{Next: f, URL: &url}
	if _, err := cap.Run(context.Background(), execx.Cmd{Name: "gh", Args: []string{"pr", "create", "--title", "x"}}); err != nil || url != "https://github.com/octo/repo/pull/12" {
		t.Errorf("URLCapture = %q, %v", url, err)
	}
	if s := Sections(c, Target{Template: "default"}); len(s) == 0 {
		t.Error("Sections empty")
	}
}
