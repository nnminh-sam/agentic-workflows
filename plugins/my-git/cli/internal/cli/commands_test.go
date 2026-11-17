package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

type cliTestEnv struct {
	t      *testing.T
	home   string
	repo   string
	fake   *execx.Fake
	stdin  string
	out    bytes.Buffer
	errOut bytes.Buffer
}

func newCLITestEnv(t *testing.T) *cliTestEnv {
	t.Helper()
	home := t.TempDir()
	repo := t.TempDir()
	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	f.OnOut("git rev-parse --show-toplevel", repo+"\n")
	f.OnOut("git remote get-url origin", "git@github.com:octo/repo.git\n")
	f.OnOut("git rev-parse -q --verify HEAD^{commit}", "deadbeef\n")
	f.OnOut("git status --porcelain=v2", "")
	f.OnOut("git for-each-ref --format=%(refname) %(objectname)", "refs/heads/main deadbeef\n")
	f.OnOut("git symbolic-ref --short -q HEAD", "develop/feat/login\n")
	f.OnOut("git -C "+repo+" symbolic-ref --short -q HEAD", "develop/feat/login\n")
	f.OnOut("git -C "+repo+" rev-parse --verify -q HEAD", "deadbeef\n")
	f.OnOut("git version", "git version 2.45.0\n")
	f.OnOut("gh --version", "gh version 2.50.0 (2024-01-01)\n")
	f.OnOut("gh api user --jq .login", "octo\n")
	f.OnOut("gh auth status --active --hostname github.com --json hosts",
		`{"hosts":{"github.com":[{"active":true,"state":"success","login":"octo"}]}}`)
	return &cliTestEnv{t: t, home: home, repo: repo, fake: f}
}

func (e *cliTestEnv) run(args ...string) int {
	e.t.Helper()
	e.out.Reset()
	e.errOut.Reset()
	io := IO{
		In: strings.NewReader(e.stdin), Out: &e.out, Err: &e.errOut,
		Getenv: func(k string) string {
			switch k {
			case "HOME":
				return e.home
			case "XDG_CONFIG_HOME":
				return filepath.Join(e.home, ".config")
			case "XDG_STATE_HOME":
				return filepath.Join(e.home, ".local", "state")
			}
			return ""
		},
		Getwd:      func() (string, error) { return e.repo, nil },
		Home:       func() (string, error) { return e.home, nil },
		Now:        time.Now,
		Runner:     e.fake,
		IsTerminal: func() bool { return false },
		GOOS:       "linux",
	}
	return Main("test", args, io)
}

func TestCLIInitConfigAndPlan(t *testing.T) {
	e := newCLITestEnv(t)

	// init --yes creates .mygit.json and .gitignore
	if code := e.run("init", "--yes"); code != 0 {
		t.Fatalf("init exit %d:\n%s\n%s", code, e.out.String(), e.errOut.String())
	}
	if _, err := os.Stat(filepath.Join(e.repo, ".mygit.json")); err != nil {
		t.Fatalf(".mygit.json not created: %v", err)
	}

	// config show, schema, validate
	if code := e.run("config", "show", "--origin"); code != 0 || !strings.Contains(e.out.String(), "workflow") {
		t.Errorf("config show: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("config", "schema"); code != 0 || !strings.Contains(e.out.String(), "$schema") {
		t.Errorf("config schema: exit %d", code)
	}
	if code := e.run("config", "validate"); code != 0 || !strings.Contains(e.out.String(), "config valid") {
		t.Errorf("config validate: exit %d\n%s", code, e.out.String())
	}

	// config set dry-run -> plan show -> plan verify -> config set --yes -> config get -> config unset
	if code := e.run("config", "set", "--project", "commit.headerMax", "80"); code != 0 {
		t.Fatalf("config set dry-run: exit %d\n%s", code, e.out.String())
	}
	id := planID(e.out.String())
	if id == "" {
		t.Fatalf("no plan id:\n%s", e.out.String())
	}
	if code := e.run("plan", "show", id); code != 0 || !strings.Contains(e.out.String(), "commit.headerMax") {
		t.Errorf("plan show: exit %d\n%s", code, e.out.String())
	}
	scriptOut := filepath.Join(e.t.TempDir(), "verify.sh")
	if code := e.run("plan", "verify", id, "--out", scriptOut); code != 0 || !strings.Contains(e.out.String(), "is fresh") {
		t.Errorf("plan verify: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("config", "set", "--project", "commit.headerMax", "80", "--apply", id); code != 0 {
		t.Fatalf("config set apply: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("config", "get", "commit.headerMax"); code != 0 || strings.TrimSpace(e.out.String()) != "80" {
		t.Errorf("config get: exit %d, got %q", code, e.out.String())
	}
	if code := e.run("config", "unset", "--project", "commit.headerMax", "--yes"); code != 0 {
		t.Errorf("config unset: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
}

func TestCLIProfileAndSign(t *testing.T) {
	e := newCLITestEnv(t)
	pubPath := filepath.Join(e.home, ".ssh", "id_sign.pub")
	if err := os.MkdirAll(filepath.Dir(pubPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(pubPath, []byte("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI octo@x.io\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	// profile add
	if code := e.run("profile", "add", "work", "--git-name", "Octo", "--git-email", "octo@x.io",
		"--github-user", "octo", "--ssh-key", "~/.ssh/id_work", "--signing-format", "ssh",
		"--signing-key", "~/.ssh/id_sign.pub", "--yes"); code != 0 {
		t.Fatalf("profile add: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("profile", "list"); code != 0 || !strings.Contains(e.out.String(), "* work") {
		t.Errorf("profile list: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("profile", "current"); code != 0 || !strings.Contains(e.out.String(), "work") {
		t.Errorf("profile current: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("profile", "show"); code != 0 || !strings.Contains(e.out.String(), "octo@x.io") {
		t.Errorf("profile show: exit %d\n%s", code, e.out.String())
	}

	// profile use (dry-run)
	if code := e.run("profile", "use", "work"); code != 0 || !strings.Contains(e.out.String(), "user.name") {
		t.Errorf("profile use: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// profile import
	legacyDir := filepath.Join(e.home, ".claude", "git-guard")
	if err := os.MkdirAll(legacyDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacyDir, "profile.json"), []byte(`{"github_user":"octo","git_name":"Octo","git_email":"octo@x.io"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if code := e.run("profile", "import", "--name", "imported", "--yes"); code != 0 {
		t.Errorf("profile import: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("profile", "rm", "imported", "--yes"); code != 0 {
		t.Errorf("profile rm: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// sign setup, status, verify
	if code := e.run("sign", "setup"); code != 0 || !strings.Contains(e.out.String(), "allowed_signers") {
		t.Errorf("sign setup: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	e.fake.OnOut("git config --get gpg.format", "ssh\n")
	e.fake.OnOut("git config --get user.signingkey", pubPath+"\n")
	e.fake.OnOut("git config --get commit.gpgsign", "true\n")
	e.fake.OnOut("git config --get gpg.ssh.allowedSignersFile", "/cfg/allowed_signers\n")
	if code := e.run("sign", "status"); code != 0 || !strings.Contains(e.out.String(), "commit.gpgsign") {
		t.Errorf("sign status: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	e.fake.OnOut("git log --format=%H%x1f%G?%x1f%GS%x1f%s -n 20", "abc1234\x1fG\x1fOcto\x1ffeat: signed\n")
	if code := e.run("sign", "verify"); code != 0 || !strings.Contains(e.out.String(), "1/1 signed") {
		t.Errorf("sign verify: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// doctor
	e.fake.OnOut("git config --get user.name", "Octo\n")
	e.fake.OnOut("git config --get user.email", "octo@x.io\n")
	e.fake.OnOut("git config --get core.hooksPath", ".githooks\n")
	if code := e.run("doctor"); code != 0 || !strings.Contains(e.out.String(), "profile work") {
		t.Errorf("doctor: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
}

func TestCLIRulesAndWorkflow(t *testing.T) {
	e := newCLITestEnv(t)

	// rules list, explain, show, sets, use, set
	if code := e.run("rules", "list"); code != 0 || !strings.Contains(e.out.String(), "CM003") {
		t.Errorf("rules list: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("rules", "explain", "CM003"); code != 0 || !strings.Contains(e.out.String(), "header-format") {
		t.Errorf("rules explain: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("rules", "show", "CM003", "--origin"); code != 0 || !strings.Contains(e.out.String(), "CM003") {
		t.Errorf("rules show: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("rules", "sets"); code != 0 || !strings.Contains(e.out.String(), "* default") {
		t.Errorf("rules sets: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("rules", "use", "default", "--yes"); code != 0 {
		t.Errorf("rules use: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("rules", "set", "CM009", "off", "--yes"); code != 0 {
		t.Errorf("rules set: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// workflow list, show, new, validate, use
	if code := e.run("workflow", "list"); code != 0 || !strings.Contains(e.out.String(), "* default") {
		t.Errorf("workflow list: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("workflow", "show", "--origin"); code != 0 || !strings.Contains(e.out.String(), "workflow default") {
		t.Errorf("workflow show: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("workflow", "new", "custom", "--yes"); code != 0 {
		t.Fatalf("workflow new: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("workflow", "validate"); code != 0 || !strings.Contains(e.out.String(), "✓ custom") {
		t.Errorf("workflow validate: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("workflow", "use", "custom", "--yes"); code != 0 {
		t.Errorf("workflow use: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
}

func TestCLILintHookHooksAgentGuard(t *testing.T) {
	e := newCLITestEnv(t)

	// lint msg, pr, cmd
	if code := e.run("lint", "msg", "feat(api): add retry"); code != 0 || !strings.Contains(e.out.String(), "✓ ok") {
		t.Errorf("lint msg ok: exit %d\n%s", code, e.out.String())
	}
	if code := e.run("lint", "msg", "bad header"); code != 2 || !strings.Contains(e.out.String(), "CM003") {
		t.Errorf("lint msg bad: exit %d\n%s", code, e.out.String())
	}
	e.stdin = "## Summary\nx\n## Detailed changes\ny\n## Explanation\nz\n## Test results\n- unit: pass\n- lint: pass\n"
	if code := e.run("lint", "pr", "--title", "feat: add x", "--body-file", "-", "--base", "develop/base", "--head", "develop/feat/x"); code != 0 {
		t.Errorf("lint pr: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("lint", "cmd", `git commit -m "Added stuff."`); code != 2 || !strings.Contains(e.out.String(), "CM003") {
		t.Errorf("lint cmd: exit %d\n%s", code, e.out.String())
	}

	// hook commit-msg, pre-commit, pre-push
	msgFile := filepath.Join(e.t.TempDir(), "COMMIT_EDITMSG")
	if err := os.WriteFile(msgFile, []byte("feat: valid commit\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if code := e.run("hook", "commit-msg", msgFile); code != 0 {
		t.Errorf("hook commit-msg: exit %d\n%s", code, e.errOut.String())
	}
	if code := e.run("hook", "pre-commit"); code != 0 {
		t.Errorf("hook pre-commit: exit %d\n%s", code, e.errOut.String())
	}
	e.stdin = ""
	if code := e.run("hook", "pre-push", "origin", "git@github.com:octo/repo.git"); code != 0 {
		t.Errorf("hook pre-push: exit %d\n%s", code, e.errOut.String())
	}

	// hooks install, status, uninstall
	e.fake.OnOut("chmod 0755 "+filepath.Join(e.repo, ".githooks", "commit-msg"), "")
	e.fake.OnOut("chmod 0755 "+filepath.Join(e.repo, ".githooks", "pre-commit"), "")
	e.fake.OnOut("chmod 0755 "+filepath.Join(e.repo, ".githooks", "pre-push"), "")
	e.fake.OnOut("git config core.hooksPath .githooks", "")
	if code := e.run("hooks", "install", "--yes"); code != 0 {
		t.Fatalf("hooks install: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	e.fake.OnOut("git config --get core.hooksPath", ".githooks\n")
	if code := e.run("hooks", "status", "--json"); code != 0 || !strings.Contains(e.out.String(), `"active": true`) {
		t.Errorf("hooks status: exit %d\n%s", code, e.out.String())
	}

	// agent install, status, uninstall
	if code := e.run("agent", "install", "claude", "--yes"); code != 0 {
		t.Fatalf("agent install claude: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	if code := e.run("agent", "status"); code != 0 || !strings.Contains(e.out.String(), "✓ claude") {
		t.Errorf("agent status: exit %d\n%s", code, e.out.String())
	}
	e.fake.OnOut("rm -f "+filepath.Join(e.repo, ".claude", "settings.json"), "")
	if code := e.run("agent", "uninstall", "claude", "--yes"); code != 0 {
		t.Errorf("agent uninstall claude: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// guard --agent claude & --session-start
	e.stdin = `{"tool_name":"Bash","tool_input":{"command":"git commit -m 'bad'"},"cwd":"` + e.repo + `"}`
	if code := e.run("guard", "--agent", "claude"); code != 0 || !strings.Contains(e.out.String(), "CM003") {
		t.Errorf("guard claude: exit %d\n%s", code, e.out.String())
	}
	e.stdin = `{"hook_event_name":"SessionStart","cwd":"` + e.repo + `"}`
	if code := e.run("guard", "--agent", "claude", "--session-start"); code != 0 {
		t.Errorf("guard session-start: exit %d\n%s", code, e.out.String())
	}
}

func TestCLIGraphAndFlow(t *testing.T) {
	e := newCLITestEnv(t)
	logFmt := "--format=%H%x1f%P%x1f%D%x1f%G?%x1f%GK%x1f%ce%x1f%s%x1e"
	refFmt := "--format=%(refname)%1f%(objectname)%1f%(upstream:short)%1f%(upstream:track)"
	logOut := "a1f9c020000\x1f\x1fHEAD -> develop/feat/login, origin/develop/base\x1fG\x1fSHA256:x\x1fo@x.io\x1ffeat: login\x1e"
	refOut := "refs/heads/develop/feat/login\x1fa1f9c020000\x1f\x1f\nrefs/remotes/origin/develop/base\x1fa1f9c020000\x1f\x1f\n"
	e.fake.OnOut("git log --all --topo-order "+logFmt+" -n 51", logOut)
	e.fake.OnOut("git log --all --topo-order "+logFmt+" -n 2", logOut)
	e.fake.OnOut("git for-each-ref "+refFmt+" refs/heads refs/remotes", refOut)
	e.fake.OnOut("git rev-list --left-right --count origin/develop/base...develop/feat/login", "0\t1\n")

	// graph --offline
	if code := e.run("graph", "--offline"); code != 0 || !strings.Contains(e.out.String(), "a1f9c02") {
		t.Errorf("graph: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// graph --fetch (dry-run plans git fetch --prune origin without running it)
	if code := e.run("graph", "--fetch", "--offline"); code != 0 || !strings.Contains(e.out.String(), "git fetch --prune origin") {
		t.Errorf("graph --fetch dry-run: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// graph --fetch --yes runs fetch then renders graph
	e.fake.OnOut("git fetch --prune origin", "")
	if code := e.run("graph", "--fetch", "--offline", "--yes"); code != 0 || !strings.Contains(e.out.String(), "a1f9c02") {
		t.Errorf("graph --fetch --yes: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}

	// flow --offline --json
	if code := e.run("flow", "--offline", "--json"); code != 0 {
		t.Fatalf("flow --json: exit %d\n%s%s", code, e.out.String(), e.errOut.String())
	}
	var got map[string]any
	if err := json.Unmarshal(e.out.Bytes(), &got); err != nil || got["workflow"] != "default" {
		t.Errorf("flow json = %v, %v", got, err)
	}
}
