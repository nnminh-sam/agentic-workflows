package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// prRunner answers commands by exact line, else by the longest registered
// prefix, else exit 0 with no output. It records every call.
type prRunner struct {
	mu    sync.Mutex
	resp  map[string]execx.Result
	calls []string
}

func (r *prRunner) on(prefix, stdout string) { r.resp[prefix] = execx.Result{Stdout: stdout} }

func (r *prRunner) fail(prefix string, code int, stderr string) {
	r.resp[prefix] = execx.Result{ExitCode: code, Stderr: stderr}
}

func (r *prRunner) Run(_ context.Context, c execx.Cmd) (execx.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	line := c.String()
	r.calls = append(r.calls, line)
	if res, ok := r.resp[line]; ok {
		return res, nil
	}
	best := ""
	for k := range r.resp {
		if strings.HasPrefix(line, k) && len(k) > len(best) {
			best = k
		}
	}
	if best != "" {
		return r.resp[best], nil
	}
	return execx.Result{}, nil
}

func (r *prRunner) ran(prefix string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, c := range r.calls {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

const (
	prLogOut = "a1f9c02\x1ffeat(auth): add login endpoint\x1fSessions expire after an hour.\n\nCo-Authored-By: Someone <a@b.c>\x1e"
	prGhUser = `{"hosts":{"github.com":[{"active":true,"state":"success","login":"octo-acme"}]}}`
)

type prWorld struct {
	t    *testing.T
	repo string
	run  *prRunner
	out  *bytes.Buffer
	errb *bytes.Buffer
}

func newPRWorld(t *testing.T, local string) *prWorld {
	t.Helper()
	root := t.TempDir()
	repo := filepath.Join(root, "api")
	if err := os.MkdirAll(repo, 0o755); err != nil {
		t.Fatal(err)
	}
	if local == "" {
		local = `{"profile":"work","profiles":{"work":{"git":{"name":"Octo Cat","email":"octo@acme.example","signing":{"format":"none"}},
		  "github":{"user":"octo-acme","transport":"ssh","sshKey":"/keys/id_acme"}}}}`
	}
	project := `{"version":1,"pr":{"checks":{"test":"go test ./...","lint":"go vet ./..."}}}`
	for name, content := range map[string]string{".mygit.local.json": local, ".mygit.json": project} {
		if err := os.WriteFile(filepath.Join(repo, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	r := &prRunner{resp: map[string]execx.Result{}}
	r.on("git rev-parse --show-toplevel", repo+"\n")
	r.on("git remote get-url origin", "git@github.com:octo-acme/api.git\n")
	r.on("git symbolic-ref --short -q HEAD", "develop/feat/login\n")
	r.on("git log --format=%h%x1f%s%x1f%b%x1e", prLogOut)
	r.on("git diff --stat", " 2 files changed, 10 insertions(+)\n")
	r.on("gh auth status --active", prGhUser)
	r.on("gh pr list", "[]")
	r.on("sh -c go test ./...", "ok  \texample/pkg\t0.253s\n")
	r.on("sh -c go vet ./...", "")
	r.on("gh pr create", "https://github.com/octo-acme/api/pull/17\n")
	r.on("git rev-parse -q --verify HEAD", "deadbeef\n")
	r.on("git rev-parse -q --verify refs/heads/", "deadbeef\n")
	r.on("git remote get-url --push origin", "git@github.com:octo-acme/api.git\n")
	// A new branch: no upstream, no remote counterpart.
	r.fail("git rev-parse --abbrev-ref --symbolic-full-name", 128, "no upstream")
	r.fail("git rev-parse --verify -q refs/remotes/origin/develop/feat/login", 1, "")
	return &prWorld{t: t, repo: repo, run: r, out: &bytes.Buffer{}, errb: &bytes.Buffer{}}
}

// prFlat joins wrapped plan lines and script continuations into one line.
func prFlat(s string) string {
	return strings.Join(strings.Fields(strings.ReplaceAll(s, "\\\n", " ")), " ")
}

func (w *prWorld) main(args ...string) int {
	w.out.Reset()
	w.errb.Reset()
	home := filepath.Join(filepath.Dir(w.repo), "home")
	_ = os.MkdirAll(home, 0o755)
	env := map[string]string{"HOME": home, "XDG_CONFIG_HOME": filepath.Join(home, ".config"),
		"XDG_STATE_HOME": filepath.Join(home, ".state"), "XDG_CACHE_HOME": filepath.Join(home, ".cache")}
	io := IO{
		In: strings.NewReader(""), Out: w.out, Err: w.errb,
		Getenv: func(k string) string { return env[k] },
		Getwd:  func() (string, error) { return w.repo, nil },
		Home:   func() (string, error) { return home, nil },
		Now:    func() time.Time { return time.Date(2026, 11, 17, 0, 0, 0, 0, time.UTC) },
		Runner: w.run, IsTerminal: func() bool { return false }, GOOS: "linux",
	}
	return Main("test", args, io)
}

func TestPRCreateDryRun(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "create"); code != 0 {
		t.Fatalf("exit %d\nout:\n%s\nerr:\n%s", code, w.out, w.errb)
	}
	out := prFlat(w.out.String())
	for _, want := range []string{
		"title feat(auth): add login endpoint",
		"base develop/base ← develop/feat/login",
		"## Summary", "## Detailed changes", "## Explanation", "## Test results",
		"- Unit tests: `go test ./...` → passed",
		"- Lint: `go vet ./...` → passed",
		"Sessions expire after an hour.",
		`core.sshCommand="ssh -i \"/keys/id_acme\" -o IdentitiesOnly=yes"`, "push -u origin develop/feat/login",
		"gh pr create --base develop/base --head develop/feat/login --title",
		"not applied (dry run)",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q\n%s", want, out)
		}
	}
	if strings.Contains(out, "Co-Authored-By") {
		t.Error("commit trailer leaked into the body")
	}
	// Dry run runs reads only: never a push or a create.
	for _, bad := range []string{"git -c core.sshCommand", "git push", "gh pr create"} {
		if w.run.ran(bad) {
			t.Errorf("dry run executed %q", bad)
		}
	}
	for _, read := range []string{"sh -c go test ./...", "sh -c go vet ./...", "gh auth status", "gh pr list --head develop/feat/login --base develop/base --state open --json url"} {
		if !w.run.ran(read) {
			t.Errorf("did not run read step %q", read)
		}
	}
}

func TestPRCreateYesAppliesAndPrintsURL(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "create", "--draft", "--yes"); code != 0 {
		t.Fatalf("exit %d\nout:\n%s\nerr:\n%s", code, w.out, w.errb)
	}
	var push, create string
	for _, c := range w.run.calls {
		switch {
		case strings.Contains(c, " push "):
			push = c
		case strings.HasPrefix(c, "gh pr create"):
			create = c
		}
	}
	if !strings.Contains(push, `-c core.sshCommand=ssh -i "/keys/id_acme" -o IdentitiesOnly=yes push -u origin develop/feat/login`) {
		t.Errorf("push = %q", push)
	}
	if !strings.Contains(create, "--base develop/base --head develop/feat/login --title feat(auth): add login endpoint --body-file ") ||
		!strings.HasSuffix(create, "--draft") {
		t.Errorf("create = %q", create)
	}
	if out := w.out.String(); !strings.Contains(out, "✓ https://github.com/octo-acme/api/pull/17   develop/feat/login → develop/base") {
		t.Errorf("no URL line:\n%s", out)
	}
}

func TestPRCreateAlreadyOpenStops(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("gh pr list", `[{"url":"https://github.com/octo-acme/api/pull/9"}]`)
	if code := w.main("pr", "create", "--yes"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.out.String(), "PR already open: https://github.com/octo-acme/api/pull/9") {
		t.Errorf("output:\n%s", w.out)
	}
	if w.run.ran("gh pr create") || w.run.ran("git -c") {
		t.Error("planned a write although a PR is open")
	}
}

func TestPRCreateWrongGhAccountDenied(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("gh auth status --active", `{"hosts":{"github.com":[{"active":true,"state":"success","login":"someone-else"}]}}`)
	if code := w.main("pr", "create", "--yes"); code != 2 {
		t.Fatalf("exit %d, want 2\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.out.String(), "ID005") {
		t.Errorf("no ID005 finding:\n%s", w.out)
	}
	if w.run.ran("gh pr list") || w.run.ran("gh pr create") {
		t.Error("ran a gh command before ID005 passed")
	}
}

func TestPRCreateBranchAndBodyRules(t *testing.T) {
	// A one-commit branch with no commit body: Explanation is empty (GH008),
	// and --title violates GH005.
	w := newPRWorld(t, "")
	w.run.on("git log --format=%h%x1f%s%x1f%b%x1e", "a1f9c02\x1ffeat(auth): add login endpoint\x1f\x1e")
	code := w.main("pr", "create", "--title", "Added stuff.", "--yes")
	if code != 2 {
		t.Fatalf("exit %d, want 2\n%s%s", code, w.out, w.errb)
	}
	out := w.out.String()
	for _, id := range []string{"GH005", "GH008"} {
		if !strings.Contains(out, id) {
			t.Errorf("no %s finding:\n%s", id, out)
		}
	}
	if w.run.ran("gh pr create") {
		t.Error("created a PR despite deny findings")
	}
	// Supplying the section fixes GH008; a proper title fixes GH005.
	if code := w.main("pr", "create", "--section", "Explanation=Users asked for login.", "--yes"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
}

func TestPRCreateBR003AndNoRoute(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("git symbolic-ref --short -q HEAD", "login-feature\n")
	if code := w.main("pr", "create"); code != 1 {
		t.Errorf("unknown branch exit %d, want 1\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.errb.String(), "no pull request route") {
		t.Errorf("stderr: %s", w.errb)
	}
	w.run.on("git symbolic-ref --short -q HEAD", "develop/base\n")
	if code := w.main("pr", "create"); code != 1 || !strings.Contains(w.errb.String(), "mygit pr release") {
		t.Errorf("staging exit %d: %s", code, w.errb)
	}
}

func TestPRCreateBR005ToMain(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "create", "--to", "main"); code != 2 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.out.String(), "BR005") {
		t.Errorf("no BR005:\n%s", w.out)
	}
}

func TestPRFailingChecksBlockUnlessAllowed(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.resp["sh -c go test ./..."] = execx.Result{ExitCode: 1, Stdout: "FAIL example/pkg 0.1s\n"}
	// D2: failing checks are an ask-level finding (exit 1) and nothing is created.
	if code := w.main("pr", "create", "--yes"); code != 1 {
		t.Fatalf("exit %d, want 1\n%s%s", code, w.out, w.errb)
	}
	out := w.out.String()
	if !strings.Contains(out, "PR-CHECKS") || !strings.Contains(out, "--allow-failing-checks") {
		t.Errorf("no blocking finding:\n%s", out)
	}
	if !strings.Contains(prFlat(out), "FAIL example/pkg") {
		t.Errorf("failing output not shown as a note:\n%s", out)
	}
	if w.run.ran("gh pr create") {
		t.Error("created a PR with failing checks")
	}
	// --allow-failing-checks: the body reports the failure, the output is not in it.
	if code := w.main("pr", "create", "--allow-failing-checks"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	out = w.out.String()
	if !strings.Contains(out, "→ FAILED (exit 1)") || strings.Contains(out, "| ```") {
		t.Errorf("body:\n%s", out)
	}
}

func TestPRNoChecksSkipsTestAndLint(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.resp["sh -c go test ./..."] = execx.Result{ExitCode: 1}
	if code := w.main("pr", "create", "--no-checks"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if w.run.ran("sh -c") {
		t.Errorf("ran a check: %v", w.run.calls)
	}
	if out := w.out.String(); !strings.Contains(out, "`go test ./...` → not run (--no-checks)") {
		t.Errorf("body:\n%s", out)
	}
}

func TestPRDryRunThenApplyIDSurvivesTimingNoise(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "create"); code != 0 {
		t.Fatalf("dry run exit %d\n%s%s", code, w.out, w.errb)
	}
	m := regexp.MustCompile(`--apply ([0-9a-f]{6})`).FindStringSubmatch(w.out.String())
	if m == nil {
		t.Fatalf("no --apply hint:\n%s", w.out)
	}
	if w.run.ran("gh pr create") || w.run.ran("git -c") {
		t.Fatal("dry run wrote")
	}
	// Same state, different test output (timing and cache marks): the id holds.
	w.run.on("sh -c go test ./...", "ok  \texample/pkg\t(cached)\n")
	w.run.on("sh -c go vet ./...", "vet: 2.31 s total\n")
	if code := w.main("pr", "create", "--apply", m[1]); code != 0 {
		t.Fatalf("--apply %s exit %d\n%s%s", m[1], code, w.out, w.errb)
	}
	var push, create bool
	for _, c := range w.run.calls {
		push = push || strings.Contains(c, " push -u origin develop/feat/login")
		create = create || strings.HasPrefix(c, "gh pr create")
	}
	if !push || !create {
		t.Errorf("apply did not push (%v) and create (%v): %v", push, create, w.run.calls)
	}
}

func TestPRBehindRemoteBlocks(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("git rev-parse --abbrev-ref --symbolic-full-name", "origin/develop/feat/login\n")
	w.run.on("git rev-list --left-right --count", "2\t1\n")
	if code := w.main("pr", "create", "--yes"); code != 1 {
		t.Fatalf("exit %d, want 1\n%s%s", code, w.out, w.errb)
	}
	if out := w.out.String(); !strings.Contains(out, "PR-SYNC") || !strings.Contains(out, "behind") {
		t.Errorf("no behind finding:\n%s", out)
	}
	if w.run.ran("gh pr create") {
		t.Error("created a PR for a behind branch")
	}
}

func TestPRBodyFileIsRelativeToTheRepo(t *testing.T) {
	w := newPRWorld(t, "")
	body := "## Summary\nFrom a file.\n\n## Detailed changes\n- x\n\n## Explanation\nBecause.\n\n## Test results\n- Unit tests: `go test` ok\n- Lint: `go vet` clean\n"
	if err := os.WriteFile(filepath.Join(w.repo, "body.md"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if code := w.main("pr", "create", "--body-file", "body.md"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.out.String(), "From a file.") {
		t.Errorf("body file not used:\n%s", w.out)
	}
}

func TestPRUnsignedCommitsID006(t *testing.T) {
	proj := `{"profile":"work","profiles":{"work":{"git":{"name":"Octo Cat","email":"octo@acme.example",
	  "signing":{"format":"ssh","key":"/keys/id_sign.pub"}},"github":{"user":"octo-acme","transport":"ssh","sshKey":"/keys/id_acme"}}},
	  "rules":{"ID006":"deny"}}`
	w := newPRWorld(t, proj)
	w.run.on("git log --format=%H%x1f%G?%x1f%GS%x1f%s", "a1f9c02aaaaaaaa\x1fN\x1f\x1ffeat(auth): add login endpoint\n")
	if code := w.main("pr", "create"); code != 2 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.out.String(), "ID006") || !strings.Contains(w.out.String(), "not signed") {
		t.Errorf("no ID006 finding:\n%s", w.out)
	}
}

func TestPRReleaseUsesWorkflowTransition(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("git symbolic-ref --short -q HEAD", "develop/feat/login\n") // release ignores the checkout
	w.run.on("git log --format=%h%x1f%s%x1f%b%x1e", "b2\x1ffeat: one\x1f\x1e3c\x1ffix: two\x1fBecause.\x1e")
	code := w.main("pr", "release", "--version", "v1.2.0", "--section", "Explanation=Ship it.", "--json")
	if code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	var got struct {
		Extra struct {
			Head, Base, Title string
			Commands          []string
		} `json:"extra"`
		Plan struct{ Steps []map[string]any } `json:"plan"`
	}
	if err := json.Unmarshal(w.out.Bytes(), &got); err != nil {
		t.Fatalf("json: %v\n%s", err, w.out)
	}
	if got.Extra.Head != "develop/base" || got.Extra.Base != "main" || got.Extra.Title != "chore(release): v1.2.0" {
		t.Errorf("extra = %+v", got.Extra)
	}
	// The protected head is never pushed.
	for _, c := range got.Extra.Commands {
		if strings.Contains(c, "push") {
			t.Errorf("release plans a push: %q", c)
		}
	}
	if len(got.Extra.Commands) != 1 || !strings.HasPrefix(got.Extra.Commands[0], "gh pr create --base main --head develop/base") {
		t.Errorf("commands = %v", got.Extra.Commands)
	}
}

func TestPRReleaseReadsRemoteTrackingHead(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("git log --format=%h%x1f%s%x1f%b%x1e", "b2\x1ffeat: one\x1f\x1e")
	if code := w.main("pr", "release", "--section", "Explanation=Ship it."); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	// The stale local develop/base is not used: the range is remote-tracking on both sides.
	if !w.run.ran("git log --format=%h%x1f%s%x1f%b%x1e origin/main..origin/develop/base") {
		t.Errorf("calls: %v", w.run.calls)
	}
	// Missing remote-tracking head: an error that says how to fetch, no fetch of its own.
	w.run.fail("git rev-parse --verify -q refs/remotes/origin/develop/base", 1, "")
	if code := w.main("pr", "release"); code != 1 || !strings.Contains(w.errb.String(), "mygit graph --fetch") {
		t.Errorf("exit %d: %s", code, w.errb)
	}
	if w.run.ran("git fetch") {
		t.Error("fetched on its own")
	}
}

func TestPRReleaseSigningSkipsMergesAndGitHubCommits(t *testing.T) {
	proj := `{"profile":"work","profiles":{"work":{"git":{"name":"Octo Cat","email":"octo@acme.example",
	  "signing":{"format":"ssh","key":"/keys/id_sign.pub"}},"github":{"user":"octo-acme","transport":"ssh","sshKey":"/keys/id_acme"}}},
	  "rules":{"ID006":"deny"}}`
	w := newPRWorld(t, proj)
	w.run.on("git log --format=%h%x1f%s%x1f%b%x1e", "b2\x1ffeat: one\x1f\x1e")
	w.run.on("git log --no-merges --format=%H%x1f%G?%x1f%GS%x1f%s%x1f%ce origin/main..origin/develop/base",
		"aaaaaaa1\x1fN\x1f\x1ffeat: squashed on github\x1fnoreply@github.com\n"+
			"bbbbbbb2\x1fG\x1fOcto\x1ffix: mine\x1focto@acme.example\n")
	if code := w.main("pr", "release", "--section", "Explanation=Ship it."); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if strings.Contains(w.out.String(), "ID006") && strings.Contains(w.out.String(), "not signed") {
		t.Errorf("GitHub-committed commit flagged:\n%s", w.out)
	}
	// A commit by the profile identity that is unsigned still is.
	w.run.on("git log --no-merges --format=%H%x1f%G?%x1f%GS%x1f%s%x1f%ce origin/main..origin/develop/base",
		"ccccccc3\x1fN\x1f\x1ffeat: mine unsigned\x1focto@acme.example\n")
	if code := w.main("pr", "release", "--section", "Explanation=Ship it."); code != 2 || !strings.Contains(w.out.String(), "not signed") {
		t.Errorf("unsigned commit: exit %d\n%s", code, w.out)
	}
}

func TestPRBranchRulesStopBeforeChecks(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "create", "--to", "main"); code != 2 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if w.run.ran("sh -c") || w.run.ran("gh pr") {
		t.Errorf("ran checks or gh before BR005 stopped the plan: %v", w.run.calls)
	}
}

func TestPRRunTransitionAndErrors(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "run", "merge"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	if !strings.Contains(w.out.String(), "base   develop/base ← develop/feat/login") {
		t.Errorf("out:\n%s", w.out)
	}
	if code := w.main("pr", "run", "nope"); code != 1 || !strings.Contains(w.errb.String(), `no pr transition "nope"`) {
		t.Errorf("exit %d: %s", code, w.errb)
	}
	if code := w.main("pr", "run"); code != 1 {
		t.Errorf("missing arg exit %d", code)
	}
}

func TestPRNothingToMerge(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("git log --format=%h%x1f%s%x1f%b%x1e", "")
	if code := w.main("pr", "create"); code != 1 || !strings.Contains(w.errb.String(), "nothing to merge") {
		t.Errorf("exit %d: %s", code, w.errb)
	}
}

func TestPRCheck(t *testing.T) {
	good := `{"title":"feat(auth): add login","body":"## Summary\nAdd login.\n\n## Detailed changes\n- route\n\n## Explanation\nSSO.\n\n## Test results\n- Unit tests: ` + "`go test`" + ` ok\n- Lint: ` + "`go vet`" + ` clean\n","baseRefName":"develop/base","headRefName":"develop/feat/login"}`
	cases := []struct {
		name  string
		args  []string
		json  string
		code  int
		wants []string
		call  string
	}{
		{"ok with number", []string{"pr", "check", "17"}, good, 0, []string{"✓ ok"}, "gh pr view 17 --json title,body,baseRefName,headRefName"},
		{"current branch", []string{"pr", "check"}, good, 0, []string{"✓ ok"}, "gh pr view --json title,body,baseRefName,headRefName"},
		{"bad title and target", []string{"pr", "check", "3"},
			`{"title":"Fix stuff","body":"nope","baseRefName":"main","headRefName":"develop/feat/x"}`, 2,
			[]string{"GH005", "GH008", "BR005"}, "gh pr view 3"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := newPRWorld(t, "")
			w.run.on("gh pr view", tc.json)
			if code := w.main(tc.args...); code != tc.code {
				t.Fatalf("exit %d, want %d\n%s%s", code, tc.code, w.out, w.errb)
			}
			for _, want := range tc.wants {
				if !strings.Contains(w.out.String(), want) {
					t.Errorf("lacks %q:\n%s", want, w.out)
				}
			}
			if !w.run.ran(tc.call) {
				t.Errorf("did not run %q; calls: %v", tc.call, w.run.calls)
			}
			for _, c := range w.run.calls {
				if strings.Contains(c, "push") || strings.HasPrefix(c, "gh pr create") {
					t.Errorf("check wrote: %s", c)
				}
			}
		})
	}
}

func TestPRCheckJSONAndErrors(t *testing.T) {
	w := newPRWorld(t, "")
	w.run.on("gh pr view", `{"title":"Fix stuff","body":"","baseRefName":"main","headRefName":"develop/feat/x"}`)
	if code := w.main("pr", "check", "5", "--json"); code != 2 {
		t.Fatalf("exit %d", code)
	}
	var got struct {
		Decision string
		Findings []struct{ RuleID string }
		PR       struct{ Title string }
	}
	if err := json.Unmarshal(w.out.Bytes(), &got); err != nil || got.Decision != "deny" || got.PR.Title != "Fix stuff" || len(got.Findings) == 0 {
		t.Errorf("json %v: %+v\n%s", err, got, w.out)
	}
	if code := w.main("pr", "check", "abc"); code != 1 {
		t.Errorf("bad number exit %d", code)
	}
	w.run.fail("gh pr view", 1, "no pull requests found")
	if code := w.main("pr", "check"); code != 1 || !strings.Contains(w.errb.String(), "no pull requests found") {
		t.Errorf("gh failure exit %d: %s", code, w.errb)
	}
	w.run.on("gh auth status --active", `{"hosts":{}}`)
	if code := w.main("pr", "check", "5"); code != 2 {
		t.Errorf("ID005 exit %d", code)
	}
}

func TestPRTemplate(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "template"); code != 0 {
		t.Fatalf("exit %d\n%s", code, w.errb)
	}
	out := w.out.String()
	for _, s := range []string{"## Summary", "## Detailed changes", "## Explanation", "## Test results", "`<command>`"} {
		if !strings.Contains(out, s) {
			t.Errorf("template lacks %q:\n%s", s, out)
		}
	}
	if code := w.main("pr", "template", "--kind", "release"); code != 0 {
		t.Fatalf("release exit %d\n%s", code, w.errb)
	}
	if out := w.out.String(); !strings.Contains(out, "## Included PRs") || !strings.Contains(out, "promote develop/base to main") {
		t.Errorf("release template:\n%s", out)
	}
	if code := w.main("pr", "template", "--kind", "x"); code != 1 {
		t.Errorf("bad kind exit %d", code)
	}
	if code := w.main("pr", "template", "--json"); code != 0 {
		t.Fatal(code)
	}
	var j struct {
		Sections []string
		Body     string
	}
	if err := json.Unmarshal(w.out.Bytes(), &j); err != nil || len(j.Sections) != 4 {
		t.Errorf("json: %v %+v", err, j)
	}
}

func TestPRManualModeScript(t *testing.T) {
	w := newPRWorld(t, "")
	if code := w.main("pr", "create", "--mode", "manual", "--shell", "bash"); code != 0 {
		t.Fatalf("exit %d\n%s%s", code, w.out, w.errb)
	}
	out := prFlat(w.out.String())
	if !strings.Contains(out, "push -u origin develop/feat/login") || !strings.Contains(out, "gh pr create --base develop/base") {
		t.Errorf("script:\n%s", out)
	}
	if w.run.ran("gh pr create") {
		t.Error("manual mode ran gh pr create")
	}
}
