package agent

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

var verdictlessPolicy = policy.Policy{}

type jsonResult struct {
	Decision string            `json:"decision"`
	Findings []verdict.Finding `json:"findings"`
	Reason   string            `json:"reason"`
	Context  []string          `json:"context"`
}

func decodeJSONOut(t *testing.T, out []byte) (jsonResult, map[string]any) {
	t.Helper()
	var r jsonResult
	var raw map[string]any
	if err := json.Unmarshal(out, &r); err != nil {
		t.Fatalf("not JSON: %v: %s", err, out)
	}
	_ = json.Unmarshal(out, &raw)
	return r, raw
}

func jsonPayload(cmd string) string {
	b, _ := json.Marshal(map[string]string{"command": cmd, "cwd": testCwd})
	return string(b)
}

func TestJSONAdapterDecisions(t *testing.T) {
	for _, name := range []string{"json", "codex", "agy"} {
		ad, err := New(name)
		if err != nil {
			t.Fatal(err)
		}
		if ad.Name() != name {
			t.Errorf("Name() = %q, want %q", ad.Name(), name)
		}
		tests := []struct {
			payload  string
			decision string
			code     int
			ids      []string
			reason   bool
		}{
			{jsonPayload("ls -la"), "allow", 0, nil, false},
			{jsonPayload("git commit -m 'feat: add x'"), "allow", 0, nil, false},
			{jsonPayload("git commit -m 'feat: Add x'"), "warn", 0, []string{"CM009"}, true},
			{`{"command":"gh pr merge 1","dialect":"powershell"}`, "ask", 1, []string{"GH"}, true},
			{jsonPayload("git commit -m 'bad'"), "deny", 2, []string{"CM003"}, true},
			{`{broken`, "ask", 1, nil, true},
			{`{"command":"git status","dialect":"fish"}`, "ask", 1, nil, true},
			{``, "allow", 0, nil, false},
		}
		for _, tt := range tests {
			out, code := guard(t, ad, tt.payload, nil, nil, "")
			r, raw := decodeJSONOut(t, out)
			if r.Decision != tt.decision || code != tt.code {
				t.Errorf("%s %s: decision %q exit %d, want %q %d (%s)", name, tt.payload, r.Decision, code, tt.decision, tt.code, out)
			}
			if _, ok := raw["findings"].([]any); !ok {
				t.Errorf("%s: findings must always be an array: %s", tt.payload, out)
			}
			if (r.Reason != "") != tt.reason {
				t.Errorf("%s: reason = %q", tt.payload, r.Reason)
			}
			for _, id := range tt.ids {
				found := false
				for _, f := range r.Findings {
					found = found || strings.HasPrefix(f.RuleID, id)
				}
				if !found {
					t.Errorf("%s: no %s finding in %s", tt.payload, id, out)
				}
			}
			if _, ok := raw["context"]; ok {
				t.Errorf("pre-command output must not carry context: %s", out)
			}
		}
	}
}

func TestJSONFindingShape(t *testing.T) {
	out, _ := guard(t, JSON{}, jsonPayload("git commit -m 'bad'"), nil, nil, "")
	var raw struct {
		Findings []map[string]any `json:"findings"`
	}
	if err := json.Unmarshal(out, &raw); err != nil || len(raw.Findings) == 0 {
		t.Fatalf("%s %v", out, err)
	}
	f := raw.Findings[0]
	for _, k := range []string{"rule", "severity", "message"} {
		if _, ok := f[k]; !ok {
			t.Errorf("finding lacks %q (stable field names): %v", k, f)
		}
	}
}

func TestJSONDecode(t *testing.T) {
	j := JSON{}
	tests := []struct {
		in      string
		dialect shell.Dialect
		tool    string
		wantErr bool
	}{
		{`{"command":"x"}`, shell.Bash, "", false},
		{`{"command":"x","dialect":"bash"}`, shell.Bash, "", false},
		{`{"command":"x","dialect":"zsh"}`, shell.Bash, "", false},
		{`{"command":"x","dialect":"gitbash"}`, shell.Bash, "", false},
		{`{"command":"x","dialect":" PowerShell "}`, shell.PowerShell, "", false},
		{`{"command":"x","dialect":"pwsh"}`, shell.PowerShell, "", false},
		{`{"command":"x","tool":"PowerShell"}`, shell.PowerShell, "PowerShell", false},
		{`{"command":"x","tool":"PowerShell","dialect":"bash"}`, shell.Bash, "PowerShell", false},
		{`{"command":"x","dialect":"cmd"}`, shell.Bash, "", true},
		{`[1,2]`, shell.Bash, "", true},
	}
	for _, tt := range tests {
		req, err := j.Decode([]byte(tt.in), PreCommand)
		if (err != nil) != tt.wantErr {
			t.Errorf("%s: err = %v", tt.in, err)
			continue
		}
		if err == nil && (req.Dialect != tt.dialect || req.Tool != tt.tool || req.Command != "x" || req.Skip) {
			t.Errorf("%s: %+v", tt.in, req)
		}
	}
	if (JSON{}).Name() != "json" {
		t.Error("zero JSON name")
	}
}

func TestJSONEncode(t *testing.T) {
	var deny verdict.Verdict
	deny.Add("CM003", verdict.Deny, "header bad")
	tests := []struct {
		res      Result
		decision string
		code     int
	}{
		{Allow(PreCommand), "allow", 0},
		{Result{Kind: PreCommand, Verdict: deny}, "deny", 2},
		{FailSafe("boom"), "ask", 1},
		{Result{Kind: SessionStart, Verdict: deny, Context: []string{"a", "b"}}, "allow", 0},
	}
	for _, tt := range tests {
		out, code := JSON{}.Encode(tt.res)
		r, _ := decodeJSONOut(t, out)
		if r.Decision != tt.decision || code != tt.code {
			t.Errorf("%+v: %s exit %d", tt.res, out, code)
		}
	}
	out, _ := JSON{}.Encode(FailSafe("boom"))
	r, _ := decodeJSONOut(t, out)
	if r.Reason != "mygit could not check this command: boom" || len(r.Findings) != 0 {
		t.Errorf("fail safe: %s", out)
	}
	out, _ = JSON{}.Encode(Result{Kind: SessionStart, Context: []string{"a", "b"}})
	r, _ = decodeJSONOut(t, out)
	if !reflect.DeepEqual(r.Context, []string{"a", "b"}) || r.Reason != "" {
		t.Errorf("session start: %s", out)
	}
}

func TestNewAndNames(t *testing.T) {
	if got := Names(); !reflect.DeepEqual(got, []string{"agy", "claude", "codex", "json"}) {
		t.Errorf("Names() = %v", got)
	}
	if ad, err := New(" Claude "); err != nil || ad.Name() != "claude" {
		t.Errorf("New(Claude) = %v %v", ad, err)
	}
	if _, err := New("cursor"); err == nil || !strings.Contains(err.Error(), "agy|claude|codex|json") {
		t.Errorf("New(cursor) err = %v", err)
	}
}

func TestKindAndResult(t *testing.T) {
	if PreCommand.String() != "pre-command" || SessionStart.String() != "session-start" {
		t.Error("Kind.String")
	}
	var w verdict.Verdict
	w.Add("CM009", verdict.Warn, "w")
	r := Result{Kind: PreCommand, Verdict: w}
	if r.Decision() != verdict.DecWarn || r.Reason() != w.Reason() {
		t.Errorf("%v %q", r.Decision(), r.Reason())
	}
	fs := FailSafe("x")
	fs.Verdict = w
	if fs.Decision() != verdict.DecAsk || !strings.HasSuffix(fs.Reason(), ": x") {
		t.Errorf("fail-safe overrides the verdict: %v %q", fs.Decision(), fs.Reason())
	}
}

// TestFailSafeInvalidConfig: an invalid config asks for git/gh, allows the rest.
func TestFailSafeInvalidConfig(t *testing.T) {
	tests := map[string]bool{
		"git push":                    true,
		"gh pr create":                true,
		"curl https://api.github.com": true,
		"git-lfs push":                true,
		"ls -la":                      false,
		"echo hello":                  false,
		"npm test":                    false,
		"python3 -m http.server":      false,
		"cd /x && make && git status": true,
		"Get-ChildItem | Select -F 1": false,
	}
	for cmd, want := range tests {
		if got := MayInvolveGit(cmd); got != want {
			t.Errorf("MayInvolveGit(%q) = %v, want %v", cmd, got, want)
		}
		// what the guard returns for this command when the config is invalid
		res := Allow(PreCommand)
		if MayInvolveGit(cmd) {
			res = FailSafe("config: bad")
		}
		out, code := Claude{}.Encode(res)
		if want {
			if !strings.Contains(string(out), `"permissionDecision":"ask"`) || code != 0 {
				t.Errorf("%q: %s", cmd, out)
			}
		} else if out != nil {
			t.Errorf("%q: want no output, got %s", cmd, out)
		}
	}
}
