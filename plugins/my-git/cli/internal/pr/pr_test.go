package pr

import (
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
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

func TestTestResults(t *testing.T) {
	out := TestResults([]CheckRun{
		{Label: "Unit tests", Command: "go test ./...", Output: "ok pkg 0.253s\n"},
		{Label: "Lint", Command: "golangci-lint run", ExitCode: 1, Output: "a.go:1: bad\nb.go:2: worse"},
	})
	for _, want := range []string{
		"- Unit tests: `go test ./...` → passed", "ok pkg Ns",
		"- Lint: `golangci-lint run` → FAILED (exit 1)", "a.go:1: bad",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("lacks %q:\n%s", want, out)
		}
	}
	none := TestResults([]CheckRun{{Label: "Unit tests"}, {Label: "Lint"}})
	if !strings.Contains(none, "Unit tests: none configured") || !strings.Contains(none, "Lint: none configured") {
		t.Errorf("none = %q", none)
	}
	started := TestResults([]CheckRun{{Label: "Lint", Command: "nolint", StartError: "program not found"}})
	if !strings.Contains(started, "could not run: program not found") {
		t.Errorf("start error = %q", started)
	}
	// Only the last lines of long output are kept.
	var long []string
	for i := 0; i < 30; i++ {
		long = append(long, "line")
	}
	long = append(long, "last")
	if got := TestResults([]CheckRun{{Label: "Lint", Command: "x", Output: strings.Join(long, "\n")}}); strings.Count(got, "line") != outputTail-1 || !strings.Contains(got, "last") {
		t.Errorf("tail: %q", got)
	}
}

func TestCheckCmd(t *testing.T) {
	cases := []struct {
		line, goos string
		want       []string
	}{
		{"go test ./...", "linux", []string{"go", "test", "./..."}},
		{`pytest -k "a b" -q`, "linux", []string{"pytest", "-k", "a b", "-q"}},
		{"npm test && npm run lint", "linux", []string{"sh", "-c", "npm test && npm run lint"}},
		{"CI=1 npm test", "linux", []string{"sh", "-c", "CI=1 npm test"}},
		{"make test | tee out", "windows", []string{"cmd", "/C", "make test | tee out"}},
		{`echo "unterminated`, "linux", []string{"sh", "-c", `echo "unterminated`}},
	}
	for _, tc := range cases {
		c := CheckCmd(tc.line, "/repo", tc.goos)
		got := append([]string{c.Name}, c.Args...)
		if !reflect.DeepEqual(got, tc.want) || c.Dir != "/repo" {
			t.Errorf("%q → %v dir %q; want %v", tc.line, got, c.Dir, tc.want)
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

func TestPushArgs(t *testing.T) {
	ssh := profile.Profile{GitHub: profile.GitHub{Transport: profile.TransportSSH, SSHKey: "/k/id"}}
	if got := PushArgs(ssh, "origin", "develop/feat/x"); !reflect.DeepEqual(got,
		[]string{"-c", "core.sshCommand=ssh -i /k/id -o IdentitiesOnly=yes", "push", "-u", "origin", "develop/feat/x"}) {
		t.Errorf("ssh: %v", got)
	}
	gh := profile.Profile{GitHub: profile.GitHub{Transport: profile.TransportGH, SSHKey: "/k/id"}}
	if got := PushArgs(gh, "origin", "b"); !reflect.DeepEqual(got, []string{"push", "-u", "origin", "b"}) {
		t.Errorf("gh: %v", got)
	}
}
