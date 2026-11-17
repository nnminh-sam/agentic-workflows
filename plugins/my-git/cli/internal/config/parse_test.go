package config

import (
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestGlobalDir(t *testing.T) {
	tests := []struct {
		name, goos string
		env        map[string]string
		home, want string
	}{
		{"xdg", "linux", map[string]string{"XDG_CONFIG_HOME": "/xdg/"}, "/h", "/xdg/mygit"},
		{"xdg on darwin", "darwin", map[string]string{"XDG_CONFIG_HOME": "/x"}, "/h", "/x/mygit"},
		{"blank xdg falls back", "linux", map[string]string{"XDG_CONFIG_HOME": "  "}, "/h", "/h/.config/mygit"},
		{"fallback linux", "linux", nil, "/h", "/h/.config/mygit"},
		{"fallback darwin", "darwin", nil, "/Users/u", "/Users/u/.config/mygit"},
		{"windows appdata", "windows", map[string]string{"APPDATA": `C:\Users\u\AppData\Roaming\`}, `C:\Users\u`, `C:\Users\u\AppData\Roaming\mygit`},
		{"windows no appdata", "windows", nil, `C:\Users\u`, `C:\Users\u\AppData\Roaming\mygit`},
		{"windows xdg wins", "windows", map[string]string{"XDG_CONFIG_HOME": `D:\cfg`, "APPDATA": `C:\a`}, `C:\Users\u`, `D:\cfg\mygit`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := GlobalDir(tt.goos, func(k string) string { return tt.env[k] }, tt.home)
			if got != tt.want {
				t.Errorf("GlobalDir = %q, want %q", got, tt.want)
			}
		})
	}
	if got := GlobalDir("linux", nil, "/h"); got != "/h/.config/mygit" {
		t.Errorf("nil getenv = %q", got)
	}
}

func TestResolvePaths(t *testing.T) {
	p := ResolvePaths("windows", func(k string) string { return map[string]string{"APPDATA": `C:\R`}[k] }, `C:\U`, `C:\src\api`)
	want := Paths{GlobalDir: `C:\R\mygit`, Global: `C:\R\mygit\config.json`, GlobalWorkflows: `C:\R\mygit\workflows`,
		Project: `C:\src\api\.mygit.json`, Local: `C:\src\api\.mygit.local.json`, ProjectWorkflows: `C:\src\api\.mygit\workflows`}
	if p != want {
		t.Errorf("windows paths = %+v", p)
	}
	p = ResolvePaths("linux", nil, "/h", "")
	if p.Project != "" || p.Local != "" || p.ProjectWorkflows != "" || p.Global != "/h/.config/mygit/config.json" {
		t.Errorf("no repo = %+v", p)
	}
}

func TestDisplay(t *testing.T) {
	for _, c := range []struct{ p, repo, home, want string }{
		{"/h/r/.mygit.json", "/h/r", "/h", ".mygit.json"},
		{"/h/.config/mygit/config.json", "/h/r", "/h", "~/.config/mygit/config.json"},
		{"/etc/x", "/h/r", "/h", "/etc/x"},
		{"", "/h/r", "/h", ""},
		{"/h", "", "/h", "/h"},
	} {
		if got := Display(c.p, c.repo, c.home); got != c.want {
			t.Errorf("Display(%q) = %q, want %q", c.p, got, c.want)
		}
	}
}

func asErr(t *testing.T, err error) *Error {
	t.Helper()
	var e *Error
	if !errors.As(err, &e) {
		t.Fatalf("want *config.Error, got %T %v", err, err)
	}
	return e
}

func TestParseUnknownKey(t *testing.T) {
	data := "{\n  \"commit\": {\n    \"headrMax\": 3\n  }\n}"
	_, err := Parse([]byte(data), "/g.json", ScopeGlobal)
	e := asErr(t, err)
	if e.File != "/g.json" || e.Field != "commit.headrMax" || e.Line != 3 {
		t.Errorf("err = %+v", e)
	}
	if !strings.Contains(err.Error(), "/g.json:3: commit.headrMax") {
		t.Errorf("message %q", err)
	}
	// near-match suggestion
	_, err = Parse([]byte(`{"commit":{"headermax":3}}`), "g", ScopeGlobal)
	if err == nil || !strings.Contains(err.Error(), `did you mean "headerMax"`) {
		t.Errorf("suggestion: %v", err)
	}
	// nested unknown keys in profiles / match list / workflows.
	for _, c := range []struct{ data, field string }{
		{`{"profiles":{"p":{"git":{"nam":"x"}}}}`, "profiles.p.git.nam"},
		{`{"match":[{"owner":"x"}]}`, "match[0].owner"},
		{`{"top":1}`, "top"},
		{`{"execution":{"modes":"x"}}`, "execution.modes"},
		{`{"rulesets":{"s":{"rule":{}}}}`, "rulesets.s.rule"},
		{`{"workflows":{"w":{"branchez":{}}}}`, "workflows.w.branchez"},
	} {
		_, err := Parse([]byte(c.data), "g", ScopeGlobal)
		if e := asErr(t, err); e.Field != c.field {
			t.Errorf("%s: field %q want %q", c.data, e.Field, c.field)
		}
	}
}

func TestParseInvalidJSON(t *testing.T) {
	_, err := Parse([]byte("{\n  \"commit\": {,}\n}"), "/p/.mygit.json", ScopeProject)
	e := asErr(t, err)
	if e.File != "/p/.mygit.json" || e.Line != 2 || !strings.Contains(e.Msg, "invalid JSON") {
		t.Errorf("err = %+v", e)
	}
	_, err = Parse([]byte(`[1]`), "f", ScopeGlobal)
	if e := asErr(t, err); !strings.Contains(e.Msg, "JSON object") {
		t.Errorf("array: %v", err)
	}
	_, err = Parse([]byte(`{"a":1`), "f", ScopeGlobal) // unexpected EOF
	if e := asErr(t, err); e.File != "f" || !strings.Contains(e.Msg, "invalid JSON") {
		t.Errorf("eof: %v", err)
	}
}

func TestParseTypeError(t *testing.T) {
	_, err := Parse([]byte("{\n  \"commit\": {\"headerMax\": \"x\"}\n}"), "f", ScopeGlobal)
	e := asErr(t, err)
	if e.Field != "commit.headerMax" || e.Line != 2 {
		t.Errorf("err = %+v", e)
	}
}

func TestParseValueErrors(t *testing.T) {
	tests := []struct{ data, field, msg string }{
		{`{"rules":{"CM001":"nope"}}`, "rules.CM001", "unknown severity"},
		{`{"rules":{"CM001":{"severity":"nah"}}}`, "rules.CM001", "unknown severity"},
		{`{"rules":{"CM001":{"sev":"off"}}}`, "rules.CM001", "unknown field"},
		{`{"rules":{"CM001":5}}`, "rules.CM001", "want a severity string"},
		{`{"rulesets":{"s":{"rules":{"CM001":"bad"}}}}`, "rulesets.s.rules.CM001", "unknown severity"},
		{`{"profiles":{"p":{"git":{"signing":{"format":"pgp"}}}}}`, "profiles.p", "git.signing.format"},
		{`{"profiles":{"p":{"github":{"transport":"https"}}}}`, "profiles.p", "github.transport"},
		{`{"profiles":{"p":{"git":{"email":5}}}}`, "profiles.p", "string"},
		{`{"workflows":{"w":{"work":{"types":"{x}"}}}}`, "workflows.w", "unknown reference"},
	}
	for _, tt := range tests {
		_, err := Parse([]byte(tt.data), "g", ScopeGlobal)
		if err == nil {
			t.Errorf("%s accepted", tt.data)
			continue
		}
		e := asErr(t, err)
		if e.Field != tt.field || !strings.Contains(e.Msg, tt.msg) {
			t.Errorf("%s: %+v, want field %q msg ~%q", tt.data, e, tt.field, tt.msg)
		}
	}
}

func TestParseScopeRules(t *testing.T) {
	type c struct {
		key   string
		val   string
		scope Scope
		ok    bool
	}
	cases := []c{
		{"profiles", `{}`, ScopeProject, false},
		{"profile", `"w"`, ScopeProject, false},
		{"match", `[]`, ScopeProject, false},
		{"locked", `[]`, ScopeProject, false},
		{"defaultProfile", `"p"`, ScopeProject, false},
		{"defaultRuleset", `"p"`, ScopeProject, false},
		{"defaultWorkflow", `"p"`, ScopeProject, false},
		{"match", `[]`, ScopeLocal, false},
		{"locked", `[]`, ScopeLocal, false},
		{"defaultProfile", `"p"`, ScopeLocal, false},
		{"defaultRuleset", `"p"`, ScopeLocal, false},
		{"defaultWorkflow", `"p"`, ScopeLocal, false},
		{"profile", `"w"`, ScopeGlobal, false},
		{"ruleset", `"w"`, ScopeGlobal, false},
		{"workflow", `"w"`, ScopeGlobal, false},
		// allowed
		{"profiles", `{}`, ScopeGlobal, true},
		{"profiles", `{}`, ScopeLocal, true},
		{"profile", `"w"`, ScopeLocal, true},
		{"ruleset", `"w"`, ScopeProject, true},
		{"ruleset", `"w"`, ScopeLocal, true},
		{"rulesets", `{}`, ScopeProject, true},
		{"rulesets", `{}`, ScopeLocal, true},
		{"workflow", `"w"`, ScopeProject, true},
		{"workflows", `{}`, ScopeLocal, true},
		{"match", `[]`, ScopeGlobal, true},
		{"locked", `[]`, ScopeGlobal, true},
		{"execution", `{"mode":"manual"}`, ScopeProject, true},
	}
	for _, tt := range cases {
		data := `{"` + tt.key + `": ` + tt.val + `}`
		_, err := Parse([]byte(data), "f.json", tt.scope)
		if tt.ok && err != nil {
			t.Errorf("%s in %s: unexpected %v", tt.key, tt.scope, err)
		}
		if !tt.ok {
			if err == nil {
				t.Errorf("%s in %s: accepted", tt.key, tt.scope)
				continue
			}
			e := asErr(t, err)
			if e.File != "f.json" || e.Field != tt.key || e.Line != 1 || !strings.Contains(e.Msg, string(tt.scope)) {
				t.Errorf("%s in %s: %+v", tt.key, tt.scope, e)
			}
		}
	}
	// profiles error explains why
	_, err := Parse([]byte("{\n\"version\":1,\n\"profiles\": {}}"), ".mygit.json", ScopeProject)
	if e := asErr(t, err); e.Line != 3 || !strings.Contains(e.Msg, "people") {
		t.Errorf("profiles: %+v", e)
	}
}

func TestParseNormalizesRules(t *testing.T) {
	l, err := Parse([]byte(`{"rules":{"CM009":"off","CM007":{"params":{"max":9}}},"rulesets":{"s":{"rules":{"BR005":"warn"}}}}`), "g", ScopeGlobal)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(l.Raw["rules"], map[string]any{"CM009": map[string]any{"severity": "off"}, "CM007": map[string]any{"params": map[string]any{"max": 9.0}}}) {
		t.Errorf("raw rules = %v", l.Raw["rules"])
	}
	if got := l.Raw["rulesets"].(map[string]any)["s"].(map[string]any)["rules"]; !reflect.DeepEqual(got, map[string]any{"BR005": map[string]any{"severity": "warn"}}) {
		t.Errorf("raw ruleset rules = %v", got)
	}
	if l.File.Rules["CM009"].Severity != "off" || l.File.Rules["CM007"].Params["max"] != 9.0 {
		t.Errorf("file rules %+v", l.File.Rules)
	}
}

func TestParseScope(t *testing.T) {
	for _, s := range []string{"global", "project", "local"} {
		if got, err := ParseScope(s); err != nil || string(got) != s {
			t.Errorf("%s: %v", s, err)
		}
	}
	if _, err := ParseScope("env"); err == nil {
		t.Error("env accepted")
	}
}

func TestSplitFieldAndLine(t *testing.T) {
	if got := splitField("match[0].path"); !reflect.DeepEqual(got, []string{"match", "[0]", "path"}) {
		t.Error(got)
	}
	if got := splitField("a[1][2]"); !reflect.DeepEqual(got, []string{"a", "[1]", "[2]"}) {
		t.Error(got)
	}
	if lineOfField([]byte(`{"a":1}`), "zzz") != 0 {
		t.Error("missing field line")
	}
	if lineAt([]byte("a\nb"), -1) != 0 || lineAt([]byte("a\nb"), 99) != 2 {
		t.Error("lineAt bounds")
	}
	if (&Error{File: "f", Msg: "m"}).Error() != "f: m" {
		t.Error("Error format")
	}
}

func TestGetSetUnset(t *testing.T) {
	out, err := Set(nil, ScopeLocal, "rules.CM009", "off")
	if err != nil {
		t.Fatal(err)
	}
	if v, ok, _ := Get(out, "rules.CM009"); !ok || v != "off" {
		t.Errorf("Get after Set = %v %v", v, ok)
	}
	out, err = Set(out, ScopeLocal, "commit.headerMax", ParseValue("100"))
	if err != nil {
		t.Fatal(err)
	}
	if v, _, _ := Get(out, "commit.headerMax"); v != 100.0 {
		t.Errorf("headerMax = %#v", v)
	}
	out, err = Set(out, ScopeLocal, "commit.types", ParseValue(`["feat","fix"]`))
	if err != nil {
		t.Fatal(err)
	}
	if v, _, _ := Get(out, "commit.types"); !reflect.DeepEqual(v, []any{"feat", "fix"}) {
		t.Errorf("types = %#v", v)
	}
	out, err = Unset(out, ScopeLocal, "rules.CM009")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := Get(out, "rules.CM009"); ok {
		t.Error("Unset left the key")
	}
	if !strings.HasSuffix(string(out), "\n") || !strings.Contains(string(out), "\n  \"commit\"") {
		t.Errorf("not indented: %s", out)
	}
	// Scope validation on Set.
	if _, err := Set(nil, ScopeProject, "profiles.x.git.name", "a"); err == nil || !strings.Contains(err.Error(), "profiles") {
		t.Errorf("profiles in project: %v", err)
	}
	if _, err := Set(nil, ScopeGlobal, "commit.nope", 1.0); err == nil {
		t.Error("unknown key accepted")
	}
	if _, err := Set([]byte(`{"commit":"x"}`), ScopeGlobal, "commit.headerMax", 1.0); err == nil || !strings.Contains(err.Error(), "not an object") {
		t.Errorf("through scalar: %v", err)
	}
	if _, err := Set(nil, ScopeGlobal, " ", 1.0); err == nil {
		t.Error("empty key accepted")
	}
	if _, err := Set([]byte("{oops"), ScopeGlobal, "a", 1.0); err == nil {
		t.Error("bad json accepted")
	}
	if _, _, err := Get([]byte("{oops"), "a"); err == nil {
		t.Error("Get bad json")
	}
	if v, ok, err := Get([]byte("  "), "a"); v != nil || ok || err != nil {
		t.Error("Get empty")
	}
	if _, ok, _ := Get([]byte("null"), "a"); ok {
		t.Error("Get null")
	}
	if out, err := Set([]byte(`{"commit":null}`), ScopeGlobal, "commit.headerMax", 5.0); err != nil || !strings.Contains(string(out), `"headerMax": 5`) {
		t.Errorf("through null: %s %v", out, err)
	}
}

func TestParseValue(t *testing.T) {
	for in, want := range map[string]any{
		"100": 100.0, "true": true, "null": nil, `"q"`: "q", "plain text": "plain text", "off": "off",
		`["a"]`: []any{"a"}, `{"max":1}`: map[string]any{"max": 1.0},
	} {
		if got := ParseValue(in); !reflect.DeepEqual(got, want) {
			t.Errorf("ParseValue(%q) = %#v, want %#v", in, got, want)
		}
	}
}

func TestSchemaIsValidJSON(t *testing.T) {
	s := Schema()
	var doc map[string]any
	if err := json.Unmarshal(s, &doc); err != nil {
		t.Fatalf("schema: %v", err)
	}
	if !strings.Contains(doc["$schema"].(string), "2020-12") {
		t.Error("draft")
	}
	props, _ := doc["properties"].(map[string]any)
	// Every top-level File field is described by the schema.
	for name := range jsonFields(reflectFileType()) {
		if _, ok := props[name]; !ok {
			t.Errorf("schema lacks property %q", name)
		}
	}
	for name := range props {
		if _, ok := jsonFields(reflectFileType())[name]; !ok {
			t.Errorf("schema property %q is not a File field", name)
		}
	}
	s[0] = 'X'
	if Schema()[0] == 'X' {
		t.Error("Schema returns shared bytes")
	}
}

// Unset is documented as "a no-op when absent".
func TestUnsetAbsentIsNoop(t *testing.T) {
	out, err := Unset([]byte(`{"version": 1}`), ScopeLocal, "commit.headerMax")
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != `{"version": 1}` {
		t.Errorf("Unset of an absent key changed the file: %s", out)
	}
	for _, key := range []string{"commit.foo.bar", "version.x", "nope"} {
		out, err := Unset([]byte(`{"version": 1}`), ScopeLocal, key)
		if err != nil || string(out) != `{"version": 1}` {
			t.Errorf("Unset(%q) = %s, %v; want unchanged", key, out, err)
		}
	}
}
