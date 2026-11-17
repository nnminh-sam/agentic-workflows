package policy

import (
	"reflect"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// prof mirrors the Python test fixture in tests/test_profile.py.
func prof() map[string]any {
	return map[string]any{"u": "a.b", "orgs": []string{"o1", "o2"}}
}

func sub(t *testing.T, v any, regex bool) (any, bool) {
	t.Helper()
	return Substitute(v, prof(), regex)
}

// Ports tests/test_profile.py Substitution.test_rules case by case.
func TestSubstitutePythonParity(t *testing.T) {
	tests := []struct {
		name   string
		in     any
		regex  bool
		want   any
		wantOK bool
	}{
		{"whole string", "${u}", false, "a.b", true},
		{"whole unset is dropped", "${none}", false, nil, false},
		{"list splice and drop", []any{"${u}", "${orgs}", "${none}", "fixed"}, false, []any{"a.b", "o1", "o2", "fixed"}, true},
		{"embedded regex escape", "^${u}@x$", true, `^a\.b@x$`, true},
		{"embedded unset drops list item", []any{"^${none}$", "keep"}, true, []any{"keep"}, true},
		{"map recurses", map[string]any{"k": []any{"${orgs}"}}, false, map[string]any{"k": []any{"o1", "o2"}}, true},
		{"embedded no regex", "pre-${u}-post", false, "pre-a.b-post", true},
		{"embedded list joins with comma", "x=${orgs}", false, "x=o1,o2", true},
		{"embedded list regex", "${u}|${orgs}", true, `a\.b|o1,o2`, true},
		{"plain string untouched", "plain", false, "plain", true},
		{"dollar without brace untouched", "$u", false, "$u", true},
		{"non-string scalar", float64(3), false, float64(3), true},
		{"bool", true, false, true, true},
		{"nil", nil, false, nil, true},
		{"[]string input", []string{"${u}", "z"}, false, []any{"a.b", "z"}, true},
		{"nested list flattens one level", []any{[]any{"${u}"}}, false, []any{"a.b"}, true},
		{"whole list var", "${orgs}", false, []string{"o1", "o2"}, true},
		{"whole string not regex-escaped", "${u}", true, "a.b", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := sub(t, tt.in, tt.regex)
			if ok != tt.wantOK || !reflect.DeepEqual(got, tt.want) {
				t.Errorf("Substitute(%#v, regex=%v) = %#v, %v; want %#v, %v", tt.in, tt.regex, got, ok, tt.want, tt.wantOK)
			}
		})
	}
}

func TestSubstituteMapKeepsDroppedAsNil(t *testing.T) {
	got, ok := Substitute(map[string]any{"a": "${none}", "b": "x"}, prof(), false)
	if !ok {
		t.Fatal("map must not be dropped")
	}
	want := map[string]any{"a": nil, "b": "x"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %#v want %#v", got, want)
	}
}

func TestSubstituteEmptyValuesCountAsUnset(t *testing.T) {
	vars := map[string]any{"s": "", "l": []string{}, "a": []any{}, "n": nil}
	for _, name := range []string{"s", "l", "a", "n"} {
		if _, ok := Substitute("${"+name+"}", vars, false); ok {
			t.Errorf("${%s}: want dropped", name)
		}
		if _, ok := Substitute("x${"+name+"}", vars, false); ok {
			t.Errorf("x${%s}: want dropped", name)
		}
	}
	// []any list values work as whole and embedded values.
	vars = map[string]any{"l": []any{"p", "q", 3}}
	if got, _ := Substitute("${l}", vars, false); !reflect.DeepEqual(got, []any{"p", "q", 3}) {
		t.Errorf("whole []any = %#v", got)
	}
	if got, _ := Substitute("<${l}>", vars, false); got != "<p,q>" {
		t.Errorf("embedded []any = %#v", got)
	}
	if got, _ := Substitute([]any{"${l}"}, vars, false); !reflect.DeepEqual(got, []any{"p", "q", 3}) {
		t.Errorf("spliced []any = %#v", got)
	}
	// A non-string, non-list var embeds as "".
	vars = map[string]any{"n": 5}
	if got, ok := Substitute("v${n}", vars, false); !ok || got != "v" {
		t.Errorf("embedded number = %#v %v", got, ok)
	}
}

func TestSubstituteDottedAliases(t *testing.T) {
	vars := map[string]any{"github.user": "octo"}
	got, ok := Substitute([]any{"${github.user}"}, vars, false)
	if !ok || !reflect.DeepEqual(got, []any{"octo"}) {
		t.Errorf("got %#v", got)
	}
}

func TestSubstituteParams(t *testing.T) {
	vars := map[string]any{"github_user": "a.b", "github_orgs": []string{"o"}}
	in := map[string]any{
		"expected_logins": []any{"${github_user}"},
		"key_path":        "${ssh_key}",
		"extra_patterns":  []any{"^${github_user}$"},
		"placeholders":    []any{"${github_user}+"},
		"other":           "${github_user}.x",
	}
	got := SubstituteParams(in, vars)
	want := map[string]any{
		"expected_logins": []any{"a.b"},
		"key_path":        nil,
		"extra_patterns":  []any{`^a\.b$`},
		"placeholders":    []any{`a\.b+`},
		"other":           "a.b.x",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %#v\nwant %#v", got, want)
	}
	if m := SubstituteParams(nil, vars); m == nil || len(m) != 0 {
		t.Errorf("nil params → %#v", m)
	}
}

func TestIsRegexParam(t *testing.T) {
	for k, want := range map[string]bool{
		"patterns": true, "extra_patterns": true, "scope_pattern": true, "Coauthor_Patterns": true,
		"placeholders": true, "expected": false, "max": false,
	} {
		if IsRegexParam(k) != want {
			t.Errorf("IsRegexParam(%q) = %v", k, !want)
		}
	}
}

func TestReferences(t *testing.T) {
	got := References(map[string]any{"a": []any{"${x}", "y"}, "b": "pre-${z}", "c": []string{"${w}"}, "d": 1})
	if !reflect.DeepEqual(got, []string{"w", "x", "z"}) {
		t.Errorf("got %v", got)
	}
	if got := References("none"); len(got) != 0 {
		t.Errorf("got %v", got)
	}
}

func TestAccessors(t *testing.T) {
	p := map[string]any{
		"s": "x", "f": float64(7), "i": 9, "b": true, "l": []any{"a", 1, "b"}, "ls": []string{"c"},
		"sev": "ask", "badsev": "nope",
	}
	if Str(p, "s", "d") != "x" || Str(p, "f", "d") != "d" || Str(p, "missing", "d") != "d" {
		t.Error("Str")
	}
	if Int(p, "f", 0) != 7 || Int(p, "i", 0) != 9 || Int(p, "s", 4) != 4 || Int(nil, "x", 2) != 2 {
		t.Error("Int")
	}
	if !Bool(p, "b", false) || Bool(p, "s", false) || !Bool(p, "missing", true) {
		t.Error("Bool")
	}
	if !reflect.DeepEqual(Strings(p, "l"), []string{"a", "b"}) || !reflect.DeepEqual(Strings(p, "ls"), []string{"c"}) {
		t.Error("Strings lists")
	}
	if Strings(p, "s") != nil || Strings(p, "missing") != nil {
		t.Error("Strings non-list")
	}
	if Sev(p, "sev", verdict.Off) != verdict.Ask || Sev(p, "badsev", verdict.Warn) != verdict.Warn ||
		Sev(p, "f", verdict.Deny) != verdict.Deny {
		t.Error("Sev")
	}
}

func TestPolicyLookups(t *testing.T) {
	var nilP *Policy
	if nilP.Contract("x") != nil {
		t.Error("nil policy contract")
	}
	if _, ok := nilP.Rule("A"); ok {
		t.Error("nil policy rule")
	}
	p := &Policy{Contracts: map[string]*Contract{
		Commit:    {Name: Commit, Rules: []Rule{{ID: "CM2", Severity: verdict.Warn}, {ID: "CM1", Severity: verdict.Deny}}},
		Branching: {Name: Branching, Rules: []Rule{{ID: "BR1"}}},
	}}
	if p.Contract(Commit).Name != Commit || p.Contract("nope") != nil {
		t.Error("Contract")
	}
	if r, ok := p.Rule("CM1"); !ok || r.Severity != verdict.Deny {
		t.Error("Rule found")
	}
	if _, ok := p.Rule("ZZ"); ok {
		t.Error("Rule missing")
	}
	var ids []string
	for _, r := range p.AllRules() {
		ids = append(ids, r.ID)
	}
	if !reflect.DeepEqual(ids, []string{"BR1", "CM1", "CM2"}) {
		t.Errorf("AllRules order %v", ids)
	}
	c := p.Contract(Commit)
	if c.Rule("CM2").Severity != verdict.Warn {
		t.Error("Contract.Rule")
	}
	z := c.Rule("missing")
	if z.Severity != verdict.Off || z.Params == nil || z.ID != "missing" {
		t.Errorf("Contract.Rule missing = %#v", z)
	}
	var nc *Contract
	if nc.Rule("x").Severity != verdict.Off {
		t.Error("nil contract")
	}
	if ErrUnknownContract("x").Error() != `unknown contract "x"` {
		t.Error("ErrUnknownContract")
	}
}
