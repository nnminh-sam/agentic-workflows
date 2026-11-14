package agent

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

var update = flag.Bool("update", false, "rewrite golden files in testdata/")

func claudePayload(tool, command string) string {
	b, _ := json.Marshal(map[string]any{
		"tool_name": tool, "tool_input": map[string]any{"command": command}, "cwd": testCwd,
		"hook_event_name": "PreToolUse", "session_id": "s1",
	})
	return string(b)
}

// decodeClaude parses one Claude hook output line ("" → nil).
func decodeClaude(t *testing.T, out []byte) map[string]any {
	t.Helper()
	if len(out) == 0 {
		return nil
	}
	if !bytes.HasSuffix(out, []byte("\n")) || bytes.Count(out, []byte("\n")) != 1 {
		t.Errorf("output must be exactly one JSON line, got %q", out)
	}
	var m map[string]any
	if err := json.Unmarshal(out, &m); err != nil {
		t.Fatalf("output is not JSON: %v\n%s", err, out)
	}
	return m
}

func hso(m map[string]any) map[string]any {
	h, _ := m["hookSpecificOutput"].(map[string]any)
	return h
}

var ruleIDRe = regexp.MustCompile(`\[([A-Z]{2}\d{3}|agentApply)\]`)

// reasonIDs extracts the sorted unique rule ids quoted as [XX000] in text.
func reasonIDs(text string) []string {
	seen := map[string]bool{}
	var out []string
	for _, m := range ruleIDRe.FindAllStringSubmatch(text, -1) {
		if !seen[m[1]] {
			seen[m[1]] = true
			out = append(out, m[1])
		}
	}
	sort.Strings(out)
	return out
}

// TestClaudePythonCases ports every PreToolUse case of ../tests/test_hooks.py.
func TestClaudePythonCases(t *testing.T) {
	c := Claude{}
	t.Run("clean commands produce no output", func(t *testing.T) {
		for _, cmd := range []string{"ls -la", "git commit -m 'feat: add x'", "echo hello"} {
			out, code := guard(t, c, claudePayload("Bash", cmd), nil, nil, "")
			if len(out) != 0 || code != 0 {
				t.Errorf("%q: got %q exit %d, want no output exit 0", cmd, out, code)
			}
		}
	})
	t.Run("non-shell tools ignored", func(t *testing.T) {
		out, code := guard(t, c, `{"tool_name":"Read","tool_input":{"file_path":"x"}}`, nil, nil, "")
		if len(out) != 0 || code != 0 {
			t.Errorf("got %q exit %d", out, code)
		}
	})
	t.Run("deny shape", func(t *testing.T) {
		out, code := guard(t, c, claudePayload("Bash", "git commit -m 'bad'"), nil, nil, "")
		h := hso(decodeClaude(t, out))
		if code != 0 || h["hookEventName"] != "PreToolUse" || h["permissionDecision"] != "deny" {
			t.Fatalf("got %s exit %d", out, code)
		}
		r, _ := h["permissionDecisionReason"].(string)
		if !strings.Contains(r, "[CM003]") {
			t.Errorf("reason lacks [CM003]: %q", r)
		}
		// the Python "rules.html" pointer became a `mygit rules explain` hint
		if !strings.Contains(r, "Rule details: mygit rules explain CM") {
			t.Errorf("reason lacks the explain hint: %q", r)
		}
	})
	t.Run("ask shape (PowerShell)", func(t *testing.T) {
		out, _ := guard(t, c, claudePayload("PowerShell", "gh pr merge 1"), nil, nil, "")
		if d := hso(decodeClaude(t, out))["permissionDecision"]; d != "ask" {
			t.Errorf("decision = %v, want ask (%s)", d, out)
		}
	})
	t.Run("warn shape", func(t *testing.T) {
		out, code := guard(t, c, claudePayload("Bash", "git commit -m 'feat: Add x'"), nil, nil, "")
		m := decodeClaude(t, out)
		sm, _ := m["systemMessage"].(string)
		if code != 0 || !strings.Contains(sm, "CM009") || !strings.HasPrefix(sm, "mygit warnings: ") {
			t.Errorf("systemMessage = %q", sm)
		}
		h := hso(m)
		if _, ok := h["permissionDecision"]; ok {
			t.Errorf("warn must not carry permissionDecision: %s", out)
		}
		if h["additionalContext"] != sm || h["hookEventName"] != "PreToolUse" {
			t.Errorf("additionalContext must repeat the systemMessage: %s", out)
		}
	})
	t.Run("broken input asks", func(t *testing.T) {
		out, code := guard(t, c, "{broken", nil, nil, "")
		h := hso(decodeClaude(t, out))
		r, _ := h["permissionDecisionReason"].(string)
		if code != 0 || h["permissionDecision"] != "ask" || !strings.HasPrefix(r, "mygit could not check this command: bad hook input") {
			t.Errorf("got %s exit %d", out, code)
		}
		if strings.Contains(r, "Rule details") {
			t.Errorf("fail-safe reason must not carry a rule hint: %q", r)
		}
	})
	t.Run("contract error fails safe", func(t *testing.T) {
		// the CLI turns an invalid config into FailSafe for git/gh commands
		if !MayInvolveGit("git push") {
			t.Fatal("git push must count as a git command")
		}
		out, code := c.Encode(FailSafe("boom"))
		h := hso(decodeClaude(t, out))
		if code != 0 || h["permissionDecision"] != "ask" || !strings.Contains(h["permissionDecisionReason"].(string), "boom") {
			t.Errorf("got %s", out)
		}
	})
}

// TestClaudeGolden compares the full output of fixture payloads with
// testdata/claude/<name>.golden (regenerate with -update).
func TestClaudeGolden(t *testing.T) {
	cases := []struct{ name, payload string }{
		{"bash_ls", claudePayload("Bash", "ls -la")},
		{"powershell_dir", claudePayload("PowerShell", "Get-ChildItem")},
		{"bash_commit_good", claudePayload("Bash", `git commit -m "feat: add x"`)},
		{"bash_commit_bad", claudePayload("Bash", `git commit -m "Added stuff."`)},
		{"powershell_commit_bad", claudePayload("PowerShell", `git commit -m 'did stuff'`)},
		{"bash_commit_warn", claudePayload("Bash", `git commit -m "feat: Add x"`)},
		{"bash_gh_pr_create_bad_title", claudePayload("Bash", `gh pr create --title "stuff" --body "x" --base develop/base`)},
		{"powershell_gh_pr_merge", claudePayload("PowerShell", "gh pr merge 1")},
		{"bash_force_push", claudePayload("Bash", "git push --force origin develop/feat/test")},
		{"bash_mygit_apply", claudePayload("Bash", "mygit commit --apply abc123")},
		{"malformed_json", `{"tool_name": "Bash", "tool_input": {`},
		{"empty_payload", ``},
		{"empty_object", `{}`},
		{"missing_tool_name", `{"tool_input":{"command":"git push --force"}}`},
		{"missing_tool_input", `{"tool_name":"Bash"}`},
		{"tool_input_wrong_type", `{"tool_name":"Bash","tool_input":"git push"}`},
		{"read_tool", `{"tool_name":"Read","tool_input":{"file_path":"/etc/passwd"}}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			out, code := guard(t, Claude{}, tc.payload, nil, nil, "")
			if code != 0 {
				t.Errorf("claude exit code must always be 0, got %d", code)
			}
			if len(out) > 0 {
				decodeClaude(t, out)
			}
			got := []byte(string(out) + "exit=" + string(rune('0'+code)) + "\n")
			checkGolden(t, filepath.Join("testdata", "claude", tc.name+".golden"), got)
		})
	}
}

func checkGolden(t *testing.T, path string, got []byte) {
	t.Helper()
	if *update {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, got, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("missing golden %s (run go test -update): %v", path, err)
	}
	if !bytes.Equal(got, want) {
		t.Errorf("%s mismatch\n got: %s\nwant: %s", path, got, want)
	}
}

func TestClaudeDecode(t *testing.T) {
	c := Claude{}
	tests := []struct {
		payload string
		kind    Kind
		want    Request
		wantErr bool
	}{
		{claudePayload("Bash", "git status"), PreCommand, Request{Kind: PreCommand, Tool: "Bash", Command: "git status", Cwd: testCwd, Dialect: shell.Bash}, false},
		{claudePayload("PowerShell", "git status"), PreCommand, Request{Kind: PreCommand, Tool: "PowerShell", Command: "git status", Cwd: testCwd, Dialect: shell.PowerShell}, false},
		{`{"tool_name":"Edit"}`, PreCommand, Request{Kind: PreCommand, Tool: "Edit", Skip: true}, false},
		{`  `, PreCommand, Request{Kind: PreCommand, Skip: true}, false},
		{`{"hook_event_name":"SessionStart","source":"startup","cwd":"/w"}`, SessionStart, Request{Kind: SessionStart, Cwd: "/w"}, false},
		{`nope`, PreCommand, Request{Kind: PreCommand}, true},
	}
	for _, tt := range tests {
		got, err := c.Decode([]byte(tt.payload), tt.kind)
		if (err != nil) != tt.wantErr || got != tt.want {
			t.Errorf("Decode(%s) = %+v, %v; want %+v, err=%v", tt.payload, got, err, tt.want, tt.wantErr)
		}
	}
	if c.Name() != "claude" {
		t.Error("name")
	}
}

func TestClaudeSessionStart(t *testing.T) {
	pol := testPolicy(t, testProfile(), nil)
	for _, src := range []string{"startup", "resume", "clear"} {
		t.Run(src, func(t *testing.T) {
			payload := `{"hook_event_name":"SessionStart","source":"` + src + `","cwd":"` + testCwd + `"}`
			req, err := Claude{}.Decode([]byte(payload), SessionStart)
			if err != nil || req.Kind != SessionStart || req.Skip {
				t.Fatalf("decode: %+v %v", req, err)
			}
			bad := fake(func(f *fakeRepo) {
				f.config["user.name"], f.config["user.email"], f.ghLogin = "someone-else", "a@b.c", "someone-else"
			})
			notes := SessionNotes(SessionInput{Cwd: req.Cwd, Policy: pol, Repo: bad, Getenv: func(k string) string { return testEnv[k] }})
			out, code := Claude{}.Encode(Result{Kind: SessionStart, Context: SessionContext(notes)})
			h := hso(decodeClaude(t, out))
			ctx, _ := h["additionalContext"].(string)
			if code != 0 || h["hookEventName"] != "SessionStart" {
				t.Fatalf("got %s", out)
			}
			for _, want := range []string{"mygit identity check", "ID001", "ID002", "ID005", "Do not change git/gh identity yourself"} {
				if !strings.Contains(ctx, want) {
					t.Errorf("context lacks %q: %q", want, ctx)
				}
			}
		})
	}
	t.Run("clean session prints nothing", func(t *testing.T) {
		notes := SessionNotes(SessionInput{Cwd: testCwd, Policy: pol, Repo: newFake(), Getenv: func(k string) string { return testEnv[k] }})
		if len(notes) != 0 {
			t.Fatalf("notes = %q", notes)
		}
		if out, code := (Claude{}).Encode(Result{Kind: SessionStart, Context: SessionContext(notes)}); out != nil || code != 0 {
			t.Errorf("got %q %d", out, code)
		}
	})
}

// TestSessionNotes ports test_hooks.SessionStart.
func TestSessionNotes(t *testing.T) {
	getenv := func(k string) string { return testEnv[k] }
	pol := testPolicy(t, testProfile(), nil)
	bad := fake(func(f *fakeRepo) {
		f.config["user.name"], f.config["user.email"], f.ghLogin = "someone-else", "a@b.c", "someone-else"
	})
	notes := SessionNotes(SessionInput{Cwd: testCwd, Policy: pol, Repo: bad, Getenv: getenv})
	if len(notes) != 3 {
		t.Errorf("want 3 notes (python parity), got %d: %q", len(notes), notes)
	}
	joined := strings.Join(notes, "\n")
	for _, id := range []string{"(ID001).", "(ID002).", "(ID005)."} {
		if !strings.Contains(joined, id) {
			t.Errorf("notes lack %s: %q", id, notes)
		}
	}
	if n := SessionNotes(SessionInput{Cwd: testCwd, Policy: pol, Repo: newFake(), Getenv: getenv}); len(n) != 0 {
		t.Errorf("clean: %q", n)
	}
	if n := SessionNotes(SessionInput{Cwd: testCwd, Policy: &verdictlessPolicy, Repo: newFake(), Getenv: getenv}); n != nil {
		t.Errorf("no identity contract: %q", n)
	}

	t.Run("ID003 gh transport without gh helper", func(t *testing.T) {
		vars := testProfile()
		delete(vars, "ssh_key")
		vars["git_transport"] = "gh"
		gpol := testPolicy(t, vars, nil)
		n := SessionNotes(SessionInput{Cwd: testCwd, Policy: gpol, Repo: newFake(), Getenv: getenv})
		j := strings.Join(n, "\n")
		if !strings.Contains(j, "(ID003).") || !strings.Contains(j, "is not set") || !strings.Contains(j, "gh auth setup-git --hostname github.com") {
			t.Errorf("notes = %q", n)
		}
		withHelper := fake(func(f *fakeRepo) { f.config["credential.helper"] = "store" })
		j = strings.Join(SessionNotes(SessionInput{Cwd: testCwd, Policy: gpol, Repo: withHelper, Getenv: getenv}), "\n")
		if !strings.Contains(j, "is 'store'") {
			t.Errorf("notes = %q", j)
		}
		ok := fake(func(f *fakeRepo) { f.config["credential.helper"] = "!/opt/homebrew/bin/gh auth git-credential" })
		for _, s := range SessionNotes(SessionInput{Cwd: testCwd, Policy: gpol, Repo: ok, Getenv: getenv}) {
			if strings.Contains(s, "ID003") {
				t.Errorf("gh helper set but got %q", s)
			}
		}
		off := testPolicy(t, vars, setSeverity("ID003", verdict.Off))
		for _, s := range SessionNotes(SessionInput{Cwd: testCwd, Policy: off, Repo: newFake(), Getenv: getenv}) {
			if strings.Contains(s, "ID003") {
				t.Errorf("ID003 off but got %q", s)
			}
		}
	})
}

func TestSessionContextAndInvalidConfig(t *testing.T) {
	if SessionContext(nil) != nil {
		t.Error("no notes → nil")
	}
	got := SessionContext([]string{"a (ID001)."})
	if len(got) != 3 || got[1] != "- a (ID001)." {
		t.Errorf("got %q", got)
	}
	ic := InvalidConfigContext(os.ErrInvalid)
	if len(ic) != 1 || !strings.Contains(ic[0], "configuration is invalid") || !strings.Contains(ic[0], "mygit doctor") {
		t.Errorf("got %q", ic)
	}
}

func TestClaudeEncodeHintSkipsApplyRule(t *testing.T) {
	var v verdict.Verdict
	v.Add(ApplyRuleID, verdict.Ask, "confirm")
	out, _ := Claude{}.Encode(Result{Kind: PreCommand, Verdict: v})
	r := hso(decodeClaude(t, out))["permissionDecisionReason"].(string)
	if !strings.HasSuffix(r, "Rule details: mygit rules list") {
		t.Errorf("reason = %q", r)
	}
	// no HTML escaping of <, >, &
	v = verdict.Verdict{}
	v.Add("CM003", verdict.Deny, "header must be '<type>(<scope>)!: <subject>' & more")
	out, _ = Claude{}.Encode(Result{Kind: PreCommand, Verdict: v})
	if !bytes.Contains(out, []byte("<type>")) || !bytes.Contains(out, []byte("& more")) {
		t.Errorf("output is HTML-escaped: %s", out)
	}
}
