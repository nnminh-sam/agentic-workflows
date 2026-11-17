package pr

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
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
