package app

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
)

func testEnv(t *testing.T, f *execx.Fake) *Env {
	t.Helper()
	home := t.TempDir()
	repo := t.TempDir()
	f.OnOut("git rev-parse --show-toplevel", repo+"\n")
	f.OnOut("git remote get-url origin", "git@github.com:octo/repo.git\n")
	f.OnOut("git rev-parse -q --verify HEAD^{commit}", "deadbeef\n")
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "refs/heads/main deadbeef\n")
	e, err := Load(context.Background(), Deps{
		Runner: f,
		Cwd:    repo,
		Home:   home,
		GOOS:   "linux",
		Getenv: func(k string) string {
			if k == "XDG_STATE_HOME" {
				return filepath.Join(home, "state")
			}
			return ""
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestEnvAndSession(t *testing.T) {
	if _, err := Load(context.Background(), Deps{}); err == nil {
		t.Error("expected error with nil Runner")
	}

	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	e, err := Load(context.Background(), Deps{Runner: f, Cwd: "/nowhere", Home: "/h", GOOS: "linux"})
	if err != nil {
		t.Fatal(err)
	}
	if e.InRepo() || e.RequireRepo() == nil {
		t.Error("expected not in repo")
	}
	if _, err := e.ScopePath(config.ScopeProject); err == nil {
		t.Error("ScopePath(project) should fail outside repo")
	}
	if _, err := e.ScopePath("bogus"); err == nil {
		t.Error("ScopePath(bogus) should fail")
	}
	if p, err := e.ScopePath(config.ScopeGlobal); err != nil || p == "" {
		t.Errorf("ScopePath(global) = %q, %v", p, err)
	}

	// Session memoizes Load.
	calls := 0
	s := NewSession(func() Deps {
		calls++
		return Deps{Runner: f, Cwd: "/nowhere", Home: "/h", GOOS: "linux"}
	})
	_, _ = s.Env(context.Background())
	_, _ = s.Env(context.Background())
	if calls != 1 {
		t.Errorf("Session called deps %d times, want 1", calls)
	}
}

func TestStoreDir(t *testing.T) {
	if got := StoreDir("linux", func(k string) string {
		if k == "XDG_STATE_HOME" {
			return "/xdg"
		}
		return ""
	}, "/h"); got != filepath.Join("/xdg", "mygit", "plans") {
		t.Errorf("XDG = %q", got)
	}
	if got := StoreDir("windows", func(k string) string {
		if k == "LOCALAPPDATA" {
			return `C:\Local`
		}
		return ""
	}, `C:\Users\o`); got != filepath.Join(`C:\Local`, "mygit", "plans") {
		t.Errorf("windows LOCALAPPDATA = %q", got)
	}
	if got := StoreDir("windows", nil, `C:\Users\o`); got != filepath.Join(`C:\Users\o`, "AppData", "Local", "mygit", "plans") {
		t.Errorf("windows fallback = %q", got)
	}
	if got := StoreDir("linux", nil, "/h"); got != filepath.Join("/h", ".local", "state", "mygit", "plans") {
		t.Errorf("linux fallback = %q", got)
	}
}

func TestFilesAndAtomicWrite(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "src.txt")
	dst := filepath.Join(dir, "sub", "dst.txt")
	if err := WriteFileAtomic(src, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	fr := FileRunner{Next: execx.NewFake()}
	res, err := fr.Run(context.Background(), fileWriteCmd(src, dst))
	if err != nil || !res.OK() {
		t.Fatalf("FileRunner = %+v, %v", res, err)
	}
	got, err := os.ReadFile(dst)
	if err != nil || string(got) != "hello" {
		t.Errorf("dst = %q, %v", got, err)
	}
	// Preserve existing permissions.
	if err := os.Chmod(dst, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := WriteFileAtomic(dst, []byte("updated")); err != nil {
		t.Fatal(err)
	}
	if fi, err := os.Stat(dst); err != nil || fi.Mode().Perm() != 0o755 {
		t.Errorf("mode = %v, %v", fi.Mode().Perm(), err)
	}
}

func TestPlannerAndExecutor(t *testing.T) {
	f := execx.NewFake()
	e := testEnv(t, f)

	p := e.NewPlanner([]string{"config", "set", "k", "v"})
	target := filepath.Join(e.RepoRoot, "out.txt")
	p.WriteFile(target, "one\n", "write first")
	p.WriteFile(target, "two\n", "write second")
	p.Add([]Action{
		{Kind: ActGit, Args: []string{"config", "--local", "user.name", "Octo"}},
		{Kind: ActGh, Args: []string{"auth", "switch", "--user", "octo"}, Description: "switch gh"},
	})
	if p.Writes() != 4 {
		t.Fatalf("Writes = %d, want 4", p.Writes())
	}
	f.OnOut("git config --local user.name Octo", "")
	f.OnOut("gh auth switch --user octo", "")

	built, err := p.Build(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	// 1. Dry-run saves plan and prints hint.
	var out, errb bytes.Buffer
	x := &Executor{Env: e, In: strings.NewReader(""), Out: &out, Err: &errb}
	oc, err := x.Run(context.Background(), built, RunOptions{TempDir: t.TempDir()})
	if err != nil || oc.Status != StatusPlanned || !strings.Contains(out.String(), "--apply "+built.ID) {
		t.Fatalf("dry-run: %+v, %v\n%s", oc, err, out.String())
	}

	// 2. Manual mode with --out saves script.
	out.Reset()
	scriptOut := filepath.Join(t.TempDir(), "run.sh")
	oc, err = x.Run(context.Background(), built, RunOptions{Mode: "manual", OutFile: scriptOut, TempDir: t.TempDir()})
	if err != nil || oc.Status != StatusManual || oc.ScriptPath != scriptOut {
		t.Fatalf("manual: %+v, %v", oc, err)
	}

	// 3. Interactive prompt declined.
	out.Reset()
	x.In = strings.NewReader("n\n")
	oc, err = x.Run(context.Background(), built, RunOptions{Interactive: true, TempDir: t.TempDir()})
	if err != nil || oc.Status != StatusDeclined {
		t.Fatalf("declined: %+v, %v", oc, err)
	}

	// 4. Apply by ID executes writes and After callback.
	out.Reset()
	afterCalled := false
	x.After = func(w io.Writer) { afterCalled = true; io.WriteString(w, "after!\n") }
	oc, err = x.Run(context.Background(), built, RunOptions{ApplyID: built.ID, TempDir: t.TempDir()})
	if err != nil || oc.Status != StatusApplied || !afterCalled {
		t.Fatalf("apply: %+v, %v\n%s", oc, err, out.String())
	}
	if b, err := os.ReadFile(target); err != nil || string(b) != "two\n" {
		t.Errorf("written file = %q, %v", b, err)
	}

	// 5. JSON noop plan.
	out.Reset()
	pNoop := e.NewPlanner([]string{"noop"})
	bNoop, _ := pNoop.Build(context.Background())
	oc, err = x.Run(context.Background(), bNoop, RunOptions{JSON: true, TempDir: t.TempDir()})
	if err != nil || oc.Status != StatusNoop || !strings.Contains(out.String(), `"status": "noop"`) {
		t.Errorf("noop json: %+v, %v\n%s", oc, err, out.String())
	}

	// 6. ScriptPath extensions.
	for _, tc := range []struct {
		in   string
		sh   shellfmt.Shell
		path string
		want shellfmt.Shell
	}{
		{"a.ps1", shellfmt.Bash, "a.ps1", shellfmt.PowerShell},
		{"a.cmd", shellfmt.Bash, "a.cmd", shellfmt.Cmd},
		{"a.sh", shellfmt.PowerShell, "a.sh", shellfmt.Bash},
		{"a", shellfmt.Bash, "a.sh", shellfmt.Bash},
	} {
		gotP, gotSh := ScriptPath(tc.in, tc.sh)
		if gotP != tc.path || gotSh != tc.want {
			t.Errorf("ScriptPath(%q, %s) = (%q, %s), want (%q, %s)", tc.in, tc.sh, gotP, gotSh, tc.path, tc.want)
		}
	}
}

func TestProfilesAndCatalog(t *testing.T) {
	if got := SSHKeyFromCommand(`ssh -i "/k/id" -o IdentitiesOnly=yes`); got != "/k/id" {
		t.Errorf("SSHKeyFromCommand = %q", got)
	}
	if got := SSHKeyFromCommand(`ssh -i/k/id2`); got != "/k/id2" {
		t.Errorf("SSHKeyFromCommand(-i...) = %q", got)
	}

	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	f.OnOut("git config --get user.name", "Octo\n")
	f.OnOut("git config --get user.email", "o@x.io\n")
	f.OnOut("git config --get core.sshCommand", `ssh -i ~/.ssh/id -o IdentitiesOnly=yes`)
	f.OnOut("git config --get user.signingkey", "~/.ssh/id.pub\n")
	f.OnOut("git config --get gpg.format", "ssh\n")
	f.OnOut("gh api user --jq .login", "octo\n")

	prof, notes := DetectProfile(context.Background(), gitx.New(f, "/repo"), ghx.New(f, "/repo"))
	if prof.Git.Name != "Octo" || prof.GitHub.User != "octo" || prof.GitHub.Transport != "ssh" || len(notes) == 0 {
		t.Errorf("DetectProfile = %+v, notes=%v", prof, notes)
	}

	legacy := `{"github_user":"octo","github_orgs":"org1, org2","git_name":"Octo","git_email":["o@x.io"],"git_transport":"ssh","ssh_key":"~/.ssh/id","signing_key":"~/.ssh/id.pub","extra_key":1}`
	lp, unk, err := FromLegacy([]byte(legacy))
	if err != nil || !reflect.DeepEqual(unk, []string{"extra_key"}) || lp.Git.Signing.Format != "ssh" || len(lp.GitHub.Orgs) != 2 {
		t.Errorf("FromLegacy = %+v, unk=%v, err=%v", lp, unk, err)
	}

	merged := MergeProfiles(lp, profile.Profile{GitHub: profile.GitHub{Transport: "gh"}})
	if merged.GitHub.SSHKey != "" {
		t.Errorf("MergeProfiles to gh should clear SSHKey: %+v", merged)
	}
	if v, err := ProfileValue(merged); err != nil || v == nil {
		t.Errorf("ProfileValue = %v, %v", v, err)
	}
	if allowed, known := OwnerCheck(lp, "ORG1"); !allowed || !known {
		t.Errorf("OwnerCheck = %v, %v", allowed, known)
	}
	uPath, pPath := LegacyPaths("/h", "/r")
	if !strings.Contains(uPath, ".claude") || !strings.Contains(pPath, ".claude") {
		t.Errorf("LegacyPaths = %q, %q", uPath, pPath)
	}

	// Catalog
	e := testEnv(t, f)
	wfs, err := e.Workflows()
	if err != nil || len(wfs) != 1 || wfs[0].Name != "default" || !wfs[0].Active {
		t.Errorf("Workflows = %+v, %v", wfs, err)
	}
	if len(FromSignSteps([]sign.Step{{Kind: sign.KindGitConfig, Args: []string{"config", "k", "v"}}})) != 1 {
		t.Error("FromSignSteps")
	}
	if len(FromProfileSteps([]profile.Step{{Kind: profile.KindGitConfig, Args: []string{"config", "k", "v"}}})) != 1 {
		t.Error("FromProfileSteps")
	}
}

func TestDoctorDiagnose(t *testing.T) {
	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	e := testEnv(t, f)

	// Write a global config with an active profile.
	if err := os.MkdirAll(filepath.Dir(e.Paths.Global), 0o755); err != nil {
		t.Fatal(err)
	}
	cfg := map[string]any{
		"defaultProfile": "work",
		"profiles": map[string]any{
			"work": map[string]any{
				"git": map[string]any{
					"name":    "Octo",
					"email":   []string{"o@x.io"},
					"signing": map[string]any{"format": "ssh", "key": "/k/id.pub"},
				},
				"github": map[string]any{"user": "octo"},
			},
		},
	}
	b, _ := json.Marshal(cfg)
	if err := os.WriteFile(e.Paths.Global, b, 0o644); err != nil {
		t.Fatal(err)
	}

	f.OnOut("git version", "git version 2.45.0\n")
	f.OnOut("gh --version", "gh version 2.50.0 (2024-01-01)\n")
	f.OnOut("git config --get user.name", "Octo\n")
	f.OnOut("git config --get user.email", "o@x.io\n")
	f.OnOut("git config --get gpg.format", "ssh\n")
	f.OnOut("git config --get user.signingkey", "/k/id.pub\n")
	f.OnOut("git config --get commit.gpgsign", "true\n")
	f.OnOut("git config --get gpg.ssh.allowedSignersFile", "/cfg/allowed\n")
	f.OnOut("git config --get core.hooksPath", ".githooks\n")
	f.OnOut("gh api user --jq .login", "octo\n")

	e, err := Load(context.Background(), e.Deps)
	if err != nil {
		t.Fatal(err)
	}
	checks := Diagnose(context.Background(), e, func(p string) bool { return p == "/k/id.pub" })
	for _, c := range checks {
		if c.Level == LevelFail {
			t.Errorf("unexpected fail check: %+v (%s)", c, c.Mark())
		}
	}
}
