package tmpl

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"testing/fstest"
)

func TestRenderCommit(t *testing.T) {
	tests := []struct {
		name string
		d    CommitData
		want string
	}{
		{"minimal", CommitData{Type: "feat", Subject: "add login endpoint"}, "feat: add login endpoint\n"},
		{"scope", CommitData{Type: "feat", Scope: "auth", Subject: "add login endpoint"}, "feat(auth): add login endpoint\n"},
		{"breaking", CommitData{Type: "feat", Breaking: true, Subject: "drop v1"}, "feat!: drop v1\n"},
		{"scope+breaking", CommitData{Type: "fix", Scope: "api", Breaking: true, Subject: "x"}, "fix(api)!: x\n"},
		{"body", CommitData{Type: "fix", Subject: "x", Body: "Line one.\n\nLine two."}, "fix: x\n\nLine one.\n\nLine two.\n"},
		{"trailers", CommitData{Type: "fix", Subject: "x", Trailers: []string{"Refs: #12", "Reviewed-by: A"}}, "fix: x\n\nRefs: #12\nReviewed-by: A\n"},
		{"body+trailers", CommitData{Type: "fix", Subject: "x", Body: "Why.\n", Trailers: []string{"Refs: #1"}}, "fix: x\n\nWhy.\n\nRefs: #1\n"},
		{"tidy body", CommitData{Type: "docs", Subject: "y", Body: "a   \n\n\n\nb\t\n\n"}, "docs: y\n\na\n\nb\n"},
	}
	s := Default()
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := s.RenderCommit(tt.d, "")
			if err != nil {
				t.Fatal(err)
			}
			if got != tt.want {
				t.Errorf("got %q\nwant %q", got, tt.want)
			}
			if strings.Contains(got, "\n\n\n") || strings.HasPrefix(got, "\n") || strings.HasSuffix(got, "\n\n") {
				t.Errorf("stray blank lines: %q", got)
			}
		})
	}
}

func TestRenderCommitCustom(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "commit.tmpl")
	os.WriteFile(path, []byte("{{.Type | lower}}: {{trim .Subject}}\r\n\r\n{{join .Trailers \", \"}}\r\n"), 0o644)
	s := Default()
	got, err := s.RenderCommit(CommitData{Type: "FEAT", Subject: "  x  ", Trailers: []string{"a", "b"}}, path)
	if err != nil || got != "feat: x\n\na, b\n" {
		t.Errorf("custom = %q %v", got, err)
	}
	if _, err := s.RenderCommit(CommitData{}, filepath.Join(dir, "missing")); err == nil || !strings.Contains(err.Error(), "missing") {
		t.Errorf("missing file err = %v", err)
	}
	bad := filepath.Join(dir, "bad.tmpl")
	os.WriteFile(bad, []byte("{{.Type"), 0o644)
	if _, err := s.RenderCommit(CommitData{}, bad); err == nil {
		t.Error("parse error expected")
	}
	unknown := filepath.Join(dir, "unknown.tmpl")
	os.WriteFile(unknown, []byte("{{.Nope}}"), 0o644)
	if _, err := s.RenderCommit(CommitData{}, unknown); err == nil {
		t.Error("unknown field should error")
	}
	// injected ReadFile is used for custom paths
	s2 := New(fstest.MapFS{}, func(p string) ([]byte, error) {
		if p == "x" {
			return []byte("{{.Subject}}"), nil
		}
		return nil, errors.New("nope")
	})
	if got, err := s2.RenderCommit(CommitData{Subject: "s"}, "x"); got != "s\n" || err != nil {
		t.Errorf("injected = %q %v", got, err)
	}
	if _, err := s2.RenderCommit(CommitData{}, ""); err == nil {
		t.Error("missing built-in should error")
	}
}

func TestSections(t *testing.T) {
	got := Sections(DefaultSections(), map[string]string{"test RESULTS": "  ok 12 passed \n", "other": "x"})
	if len(got) != 4 || got[3].Content != "ok 12 passed" || got[0].Content != "" || got[0].Name != "Summary" {
		t.Errorf("Sections = %+v", got)
	}
}

func TestRenderPRBody(t *testing.T) {
	s := Default()
	d := PRData{Head: "develop/feat/x", Base: "develop/base", Sections: Sections(DefaultSections(), map[string]string{
		"Test results": "- Unit tests: `go test ./...` → 42 passed",
	})}
	got, err := s.RenderPRBody(PRDefault, d, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, h := range []string{"## Summary\n\n<!--", "## Detailed changes\n\n<!--", "## Explanation\n\n<!--", "## Test results\n\n- Unit tests: `go test ./...` → 42 passed\n"} {
		if !strings.Contains(got, h) {
			t.Errorf("missing %q in\n%s", h, got)
		}
	}
	if strings.Count(got, "## ") != 4 || !strings.HasPrefix(got, "## Summary") || strings.Contains(got, "\n\n\n") || !strings.HasSuffix(got, "passed\n") {
		t.Errorf("body shape:\n%q", got)
	}
	idx := []int{strings.Index(got, "## Summary"), strings.Index(got, "## Detailed"), strings.Index(got, "## Explanation"), strings.Index(got, "## Test")}
	for i := 1; i < len(idx); i++ {
		if idx[i] < idx[i-1] {
			t.Errorf("sections out of order: %v", idx)
		}
	}

	rel, err := s.RenderPRBody(PRRelease, PRData{Head: "develop/base", Base: "main", Version: "v1.2.0",
		Sections: Sections([]string{"Summary", "Included PRs", "Test results"}, nil)}, "")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(rel, "Release v1.2.0: promote develop/base to main") || !strings.Contains(rel, "## Included PRs\n\n<!-- Included PRs -->") ||
		!strings.Contains(rel, "Run on develop/base") {
		t.Errorf("release body:\n%s", rel)
	}
	if def, err := s.RenderPRBody("", PRData{Sections: Sections([]string{"Custom"}, nil)}, ""); err != nil || def != "## Custom\n\n<!-- Custom -->\n" {
		t.Errorf("custom section = %q %v", def, err)
	}
	if _, err := s.RenderPRBody("hotfix", d, ""); err == nil {
		t.Error("unknown kind should fail")
	}
	path := filepath.Join(t.TempDir(), "pr.md")
	os.WriteFile(path, []byte("{{range .Sections}}# {{.Name}}\n{{end}}"), 0o644)
	if got, err := s.RenderPRBody(PRDefault, d, path); err != nil || !strings.HasPrefix(got, "# Summary\n# Detailed changes") {
		t.Errorf("custom pr = %q %v", got, err)
	}
	if _, err := s.RenderPRBody(PRDefault, d, path+".missing"); err == nil {
		t.Error("missing custom pr template should fail")
	}
}

func TestRenderHook(t *testing.T) {
	s := Default()
	for _, name := range HookNames() {
		got, err := s.RenderHook(HookData{Name: name})
		if err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(got, "#!/bin/sh\n") || !strings.Contains(got, `exec mygit hook `+name+` "$@"`) ||
			!strings.Contains(got, DefaultInstallHint) || strings.Contains(got, "\r") || !strings.HasSuffix(got, "\n") {
			t.Errorf("%s shim:\n%s", name, got)
		}
	}
	if got, _ := s.RenderHook(HookData{Name: "pre-push", InstallHint: "brew install mygit"}); !strings.Contains(got, "install: brew install mygit") {
		t.Errorf("custom hint:\n%s", got)
	}
	if _, err := s.RenderHook(HookData{Name: "post-checkout"}); err == nil {
		t.Error("unknown hook should fail")
	}
}

// runShim writes the shim and runs it with /bin/sh and the given PATH.
func runShim(t *testing.T, shim, path string, args ...string) (stdout, stderr string, code int) {
	t.Helper()
	dir := t.TempDir()
	f := filepath.Join(dir, "hook")
	if err := os.WriteFile(f, []byte(shim), 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("/bin/sh", append([]string{f}, args...)...)
	cmd.Env = []string{"PATH=" + path, "HOME=" + dir}
	var o, e strings.Builder
	cmd.Stdout, cmd.Stderr = &o, &e
	err := cmd.Run()
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code = ee.ExitCode()
	} else if err != nil {
		t.Fatal(err)
	}
	return o.String(), e.String(), code
}

func TestHookShimRuns(t *testing.T) {
	if testing.Short() || runtime.GOOS == "windows" {
		t.Skip("runs /bin/sh")
	}
	shim, err := Default().RenderHook(HookData{Name: "commit-msg"})
	if err != nil {
		t.Fatal(err)
	}
	// mygit missing: fail open with a hint
	empty := t.TempDir()
	out, errOut, code := runShim(t, shim, empty+":/usr/bin:/bin", ".git/COMMIT_EDITMSG")
	if code != 0 || out != "" || !strings.Contains(errOut, "mygit: not on PATH") || !strings.Contains(errOut, DefaultInstallHint) {
		t.Errorf("missing mygit: code=%d out=%q err=%q", code, out, errOut)
	}
	// mygit present: forwards args and exit code
	bin := t.TempDir()
	fake := "#!/bin/sh\nprintf '%s|' \"$@\"\nexit 7\n"
	os.WriteFile(filepath.Join(bin, "mygit"), []byte(fake), 0o755)
	out, _, code = runShim(t, shim, bin+":/usr/bin:/bin", "msg file", "x")
	if code != 7 || out != "hook|commit-msg|msg file|x|" {
		t.Errorf("forward: code=%d out=%q", code, out)
	}
}

func TestRenderAgentsMD(t *testing.T) {
	got, err := Default().RenderAgentsMD()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, "<!-- mygit:begin") || !strings.HasSuffix(strings.TrimSpace(got), "<!-- mygit:end -->") ||
		!strings.Contains(got, "mygit commit") || !strings.Contains(got, "mygit pr create") {
		t.Errorf("AGENTS.md:\n%s", got)
	}
}

func TestRenderClaudeHooks(t *testing.T) {
	type hook struct {
		Type    string `json:"type"`
		Command string `json:"command"`
		Timeout int    `json:"timeout"`
	}
	type entry struct {
		Matcher string `json:"matcher"`
		Hooks   []hook `json:"hooks"`
	}
	var doc struct {
		Hooks map[string][]entry `json:"hooks"`
	}
	tests := []struct {
		d       ClaudeHooksData
		cmd     string
		timeout int
	}{
		{ClaudeHooksData{}, "mygit", 30},
		{ClaudeHooksData{Command: `C:\tools\my"git.exe`, Timeout: 5}, `C:\tools\my"git.exe`, 5},
	}
	for _, tt := range tests {
		out, err := Default().RenderClaudeHooks(tt.d)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal([]byte(out), &doc); err != nil {
			t.Fatalf("invalid json: %v\n%s", err, out)
		}
		pre, ss := doc.Hooks["PreToolUse"], doc.Hooks["SessionStart"]
		if len(pre) != 1 || pre[0].Matcher != "Bash|PowerShell" || len(pre[0].Hooks) != 1 ||
			pre[0].Hooks[0] != (hook{"command", tt.cmd + " guard --agent claude", tt.timeout}) {
			t.Errorf("PreToolUse = %+v", pre)
		}
		if len(ss) != 1 || ss[0].Matcher != "startup|resume|clear" ||
			ss[0].Hooks[0] != (hook{"command", tt.cmd + " guard --agent claude --session-start", tt.timeout}) {
			t.Errorf("SessionStart = %+v", ss)
		}
	}
	// a broken template is reported as invalid json
	s := New(fstest.MapFS{ClaudeHooksJSON: {Data: []byte("{not json")}}, nil)
	if _, err := s.RenderClaudeHooks(ClaudeHooksData{}); err == nil || !strings.Contains(err.Error(), "invalid json") {
		t.Errorf("invalid json err = %v", err)
	}
	s = New(fstest.MapFS{ClaudeHooksJSON: {Data: []byte("{{.Nope}}")}}, nil)
	if _, err := s.RenderClaudeHooks(ClaudeHooksData{}); err == nil {
		t.Error("exec error expected")
	}
	s = New(fstest.MapFS{HookShimTemplate: {Data: []byte("{{.Nope}}")}}, nil)
	if _, err := s.RenderHook(HookData{Name: "pre-commit"}); err == nil {
		t.Error("exec error expected")
	}
	s = New(fstest.MapFS{}, nil)
	if _, err := s.RenderPRBody(PRDefault, PRData{}, ""); err == nil {
		t.Error("missing template expected")
	}
}

func TestTidy(t *testing.T) {
	tests := map[string]string{
		"":                   "\n",
		"a":                  "a\n",
		"\n\na  \r\nb\n\n\n": "a\nb\n",
		"a\n\n\n\n\nb":       "a\n\nb\n",
	}
	for in, want := range tests {
		if got := tidy(in); got != want {
			t.Errorf("tidy(%q) = %q, want %q", in, got, want)
		}
	}
}
