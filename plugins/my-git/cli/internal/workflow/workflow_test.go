package workflow

import (
	"encoding/json"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
)

// releaseTrain is the plan's example, verbatim.
const releaseTrain = `{
  "name": "release-train",
  "extends": "default",
  "branches": {
    "staging": { "name": "develop" },
    "release": { "name": "release/{version}", "protected": true, "pattern": true }
  },
  "work": { "pattern": "{type}/{ticket}-{name}" },
  "transitions": [
    { "id": "merge",   "from": "work",    "to": "staging",    "via": "pr", "mergeMethod": "squash" },
    { "id": "cut",     "from": "staging", "to": "release",    "via": "branch" },
    { "id": "release", "from": "release", "to": "production", "via": "pr", "mergeMethod": "merge", "tag": "v{version}" },
    { "id": "backport","from": "release", "to": "staging",    "via": "pr" },
    { "id": "hotfix",  "from": "production", "to": ["production","staging"], "via": "pr", "work": "hotfix/{name}" }
  ],
  "protection": { "approvals": 1, "requireSigned": true }
}`

func builtins(t *testing.T) *Catalog {
	t.Helper()
	cat, err := Builtins(defaults.FS)
	if err != nil {
		t.Fatal(err)
	}
	return cat
}

func compileName(t *testing.T, cat *Catalog, name string, opts CompileOptions) *Compiled {
	t.Helper()
	r, err := Resolve(name, cat)
	if err != nil {
		t.Fatal(err)
	}
	c, err := Compile(r, opts)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// Exit criterion: the built-in default compiles to exactly the shipped
// branching contract (settings + BR003 patterns), both the embedded copy
// and the repository's ../contracts/branching.json.
func TestDefaultMatchesBranchingContract(t *testing.T) {
	c := compileName(t, builtins(t), "default", CompileOptions{})
	for _, src := range []string{"embedded", "repo"} {
		var raw []byte
		var err error
		if src == "embedded" {
			raw, err = defaults.FS.ReadFile("contracts/branching.json")
		} else {
			raw, err = readRepoFile("../../../contracts/branching.json")
		}
		if err != nil {
			t.Fatal(err)
		}
		var br struct {
			Settings map[string]any
			Rules    []struct {
				ID     string
				Params map[string]any
			}
		}
		if err := json.Unmarshal(raw, &br); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(br.Settings, c.BranchingSettings()) {
			t.Errorf("%s settings %v vs compiled %v", src, br.Settings, c.BranchingSettings())
		}
		found := false
		for _, r := range br.Rules {
			if r.ID == "BR003" {
				found = true
				if !reflect.DeepEqual(r.Params["patterns"], c.RuleParams()["BR003"]["patterns"]) {
					t.Errorf("%s BR003 %v vs compiled %v", src, r.Params["patterns"], c.RuleParams()["BR003"]["patterns"])
				}
			}
		}
		if !found {
			t.Errorf("%s: no BR003", src)
		}
	}
	if c.Production != "main" || c.Staging != "develop/base" || c.WorkFrom != "develop/base" || c.Remote != "origin" {
		t.Errorf("compiled %+v", c)
	}
	if !reflect.DeepEqual(c.Protected, []string{"main", "develop/base"}) {
		t.Errorf("protected %v", c.Protected)
	}
	if c.Origin != Builtin || len(c.Chain) != 1 {
		t.Errorf("origin %q chain %v", c.Origin, c.Chain)
	}
	p := c.Template.Protection
	if !p.RequirePR || p.Approvals != 0 || p.ForcePush || p.Deletion || p.RequireSigned || p.LinearHistory {
		t.Errorf("protection %+v", p)
	}
}

func TestDefaultRoutesAndProtection(t *testing.T) {
	c := compileName(t, builtins(t), "default", CompileOptions{})
	tests := []struct {
		head  string
		bases []string
		id    string
	}{
		{"develop/feat/x", []string{"develop/base"}, "merge"},
		{"develop/base", []string{"main"}, "release"},
		{"main", nil, ""},
		{"random", nil, ""},
		{"develop/nope/x", nil, ""},
	}
	for _, tt := range tests {
		rt, ok := c.PRRoute(tt.head)
		if ok != (tt.bases != nil) || !reflect.DeepEqual(rt.Bases, tt.bases) || rt.Transition.ID != tt.id {
			t.Errorf("PRRoute(%q) = %v %v, want %v %s", tt.head, rt, ok, tt.bases, tt.id)
		}
	}
	for name, want := range map[string]bool{"main": true, "develop/base": true, "develop/feat/x": false, "other": false} {
		if c.IsProtected(name) != want {
			t.Errorf("IsProtected(%q) = %v", name, !want)
		}
	}
	for name, want := range map[string]string{"main": Production, "develop/base": Staging, "develop/fix/a": WorkKey, "x": ""} {
		if got := c.BranchKey(name); got != want {
			t.Errorf("BranchKey(%q) = %q want %q", name, got, want)
		}
	}
	if got := c.AllowedBases("develop/feat/x"); !reflect.DeepEqual(got, []string{"develop/base"}) {
		t.Errorf("AllowedBases %v", got)
	}
}

func releaseTrainCatalog(t *testing.T) *Catalog {
	t.Helper()
	cat := builtins(t)
	if err := cat.AddJSON([]byte(releaseTrain), ".mygit/workflows/release-train.json", "ignored"); err != nil {
		t.Fatal(err)
	}
	return cat
}

func TestReleaseTrainExample(t *testing.T) {
	cat := releaseTrainCatalog(t)
	c := compileName(t, cat, "release-train", CompileOptions{})
	tm := c.Template
	if c.Staging != "develop" || c.Production != "main" || c.WorkFrom != "develop" {
		t.Errorf("branches: prod %q staging %q from %q", c.Production, c.Staging, c.WorkFrom)
	}
	// staging deep-merged: name replaced, protected/githubDefault inherited.
	if s := tm.Branches[Staging]; !s.Protected || !s.GithubDefault {
		t.Errorf("staging lost inherited fields: %+v", s)
	}
	// work.types and work.from inherited from default.
	if !reflect.DeepEqual(c.WorkTypes, []string{"feat", "fix", "chore", "docs", "refactor", "perf", "test", "build", "ci"}) || tm.Work.From != Staging {
		t.Errorf("work %+v types %v", tm.Work, c.WorkTypes)
	}
	var ids []string
	for _, tr := range tm.Transitions {
		ids = append(ids, tr.ID)
	}
	if !reflect.DeepEqual(ids, []string{"merge", "release", "cut", "backport", "hotfix"}) {
		t.Errorf("transition ids %v", ids)
	}
	if !reflect.DeepEqual(c.Protected, []string{"main", "develop", "release/{version}"}) {
		t.Errorf("protected %v", c.Protected)
	}
	wantPatterns := []string{
		`^(feat|fix|chore|docs|refactor|perf|test|build|ci)/[A-Za-z][A-Za-z0-9]*-[0-9]+-[a-z0-9][a-z0-9._-]*$`,
		`^hotfix/[a-z0-9][a-z0-9._-]*$`,
		`^release/v?[0-9]+(\.[0-9]+)*(-[0-9A-Za-z.-]+)?$`,
	}
	if !reflect.DeepEqual(c.WorkPatterns, wantPatterns) {
		t.Errorf("patterns\n%v\nwant\n%v", c.WorkPatterns, wantPatterns)
	}
	if !reflect.DeepEqual(c.Chain, []string{"release-train (.mygit/workflows/release-train.json)", "default (built-in)"}) {
		t.Errorf("chain %v", c.Chain)
	}
	routes := []struct {
		head  string
		bases []string
		id    string
	}{
		{"feat/ABC-12-login", []string{"develop"}, "merge"},
		{"release/1.2.0", []string{"main"}, "release"},
		{"hotfix/x", []string{"main", "develop"}, "hotfix"},
		{"develop", nil, ""}, // staging → release is a branch transition, not a PR
		{"main", nil, ""},
		{"feat/login", nil, ""},
	}
	for _, tt := range routes {
		rt, ok := c.PRRoute(tt.head)
		if ok != (tt.bases != nil) || !reflect.DeepEqual(rt.Bases, tt.bases) || rt.Transition.ID != tt.id {
			t.Errorf("PRRoute(%q) = %+v %v, want %v %s", tt.head, rt.Bases, ok, tt.bases, tt.id)
		}
	}
	if got := c.AllowedBases("release/1.2.0"); !reflect.DeepEqual(got, []string{"main", "develop"}) {
		t.Errorf("AllowedBases(release) = %v", got)
	}
	// hotfix/* also classifies as a generic work branch, so "merge" applies
	// too; the set of bases is what matters.
	if got := c.AllowedBases("hotfix/a"); len(got) != 2 || !contains(got, "main") || !contains(got, "develop") {
		t.Errorf("AllowedBases(hotfix) = %v", got)
	}
	if !c.IsProtected("release/1.2.0") || c.IsProtected("feat/ABC-1-x") || c.BranchKey("release/2.0") != "release" {
		t.Error("release branch classification")
	}
	if c.BranchKey("hotfix/a") != WorkKey {
		t.Error("hotfix is a work branch")
	}
}

// The plan's "workflow show" output for release-train lists the merge
// transition as "PR · squash · delete head" even though the override does
// not repeat deleteHead. The text "same id replaces" says the opposite;
// this test pins the implemented (replace) behaviour.
// Plan: release-train's "merge" overrides only some fields; `workflow show`
// still lists "delete head", so same-id transitions deep-merge.
func TestTransitionSameIDMerges(t *testing.T) {
	c := compileName(t, releaseTrainCatalog(t), "release-train", CompileOptions{})
	m := c.Template.Transitions[0]
	if m.ID != "merge" || !m.DeleteHead || m.PRTemplate != "default" || m.MergeMethod != "squash" {
		t.Errorf("merge transition %+v", m)
	}
	cat := builtins(t)
	if err := cat.AddJSON([]byte(`{"name":"r","extends":"default",
	  "transitions":[{"id":"release","mergeMethod":"rebase"}]}`), "proj", "r"); err != nil {
		t.Fatal(err)
	}
	c = compileName(t, cat, "r", CompileOptions{})
	var rel Transition
	for _, tr := range c.Template.Transitions {
		if tr.ID == "release" {
			rel = tr
		}
	}
	want := Transition{ID: "release", From: "staging", To: Targets{"production"}, Via: "pr",
		MergeMethod: "rebase", PRTemplate: "release", Tag: "v{version}"}
	if !reflect.DeepEqual(rel, want) {
		t.Errorf("release = %+v, want %+v", rel, want)
	}
}

// A transition removed by a parent can be re-added by a child.
func TestTransitionReAddAfterRemove(t *testing.T) {
	cat := builtins(t)
	add := func(doc string) {
		if err := cat.AddJSON([]byte(doc), "proj", ""); err != nil {
			t.Fatal(err)
		}
	}
	add(`{"name":"a","extends":"default","transitions":[{"id":"merge","remove":true},
	  {"id":"direct","from":"work","to":"production","via":"pr"}]}`)
	add(`{"name":"b","extends":"a","transitions":[{"id":"merge","from":"work","to":"staging","via":"pr"}]}`)
	c := compileName(t, cat, "b", CompileOptions{})
	found := false
	for _, tr := range c.Template.Transitions {
		if tr.ID == "merge" {
			found = true
			if tr.Remove || tr.DeleteHead || tr.MergeMethod != "" {
				t.Errorf("re-added merge carries old fields: %+v", tr)
			}
		}
	}
	if !found {
		t.Error("re-added merge transition missing")
	}
}

// Plan: "objects deep-merge" also for extends; a partial protection must
// inherit the rest (requirePr from default).
func TestReleaseTrainProtectionInherits(t *testing.T) {
	c := compileName(t, releaseTrainCatalog(t), "release-train", CompileOptions{})
	p := c.Template.Protection
	want := Protection{RequirePR: true, Approvals: 1, ForcePush: false, Deletion: false, RequireSigned: true, LinearHistory: false}
	if p != want {
		t.Errorf("protection = %+v, want %+v", p, want)
	}
	// Same through the explicit "only approvals" override.
	cat := builtins(t)
	cat.Add(Source{Name: "p", Origin: "x", Doc: map[string]any{"extends": "default", "protection": map[string]any{"approvals": float64(1)}}})
	c = compileName(t, cat, "p", CompileOptions{})
	if !c.Template.Protection.RequirePR || c.Template.Protection.Approvals != 1 {
		t.Errorf("partial protection override reset fields: %+v", c.Template.Protection)
	}
	// prTemplates and description inherited.
	if len(c.Template.PRTemplates) != 2 || c.Template.Description == "" {
		t.Errorf("inherited prTemplates/description: %+v", c.Template)
	}
}

func TestRemoveTransition(t *testing.T) {
	cat := builtins(t)
	add := func(name, doc string) {
		if err := cat.AddJSON([]byte(doc), "proj", name); err != nil {
			t.Fatal(err)
		}
	}
	add("trunk", `{"name":"trunk","extends":"default",
	  "branches":{"staging":{"name":"next"}},
	  "transitions":[{"id":"merge","remove":true},{"id":"direct","from":"work","to":"production","via":"pr","mergeMethod":"rebase"}]}`)
	c := compileName(t, cat, "trunk", CompileOptions{})
	var ids []string
	for _, tr := range c.Template.Transitions {
		ids = append(ids, tr.ID)
	}
	if !reflect.DeepEqual(ids, []string{"release", "direct"}) {
		t.Errorf("ids = %v", ids)
	}
	if rt, _ := c.PRRoute("develop/feat/a"); rt.Transition.ID != "direct" || rt.Bases[0] != "main" {
		t.Errorf("route %+v", rt)
	}
	// A removal marker in a middle layer is still visible to MergeDocs callers.
	merged := MergeDocs(map[string]any{"transitions": []any{map[string]any{"id": "a"}}},
		map[string]any{"transitions": []any{map[string]any{"id": "a", "remove": true}}})
	if l := merged["transitions"].([]any); len(l) != 1 || l[0].(map[string]any)["remove"] != true {
		t.Errorf("merged = %v", merged)
	}
}

func TestMergeDocs(t *testing.T) {
	base := map[string]any{"a": map[string]any{"x": 1.0, "y": 2.0}, "l": []any{1.0, 2.0}, "s": "b", "transitions": []any{map[string]any{"id": "t1", "via": "pr"}}}
	over := map[string]any{"a": map[string]any{"y": 3.0}, "l": []any{9.0}, "s": "o", "transitions": []any{map[string]any{"id": "t2"}, map[string]any{"via": "x"}}}
	got := MergeDocs(base, over)
	want := map[string]any{
		"a": map[string]any{"x": 1.0, "y": 3.0}, "l": []any{9.0}, "s": "o",
		"transitions": []any{map[string]any{"id": "t1", "via": "pr"}, map[string]any{"id": "t2"}, map[string]any{"via": "x"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("MergeDocs = %v", got)
	}
	// base not mutated
	if base["a"].(map[string]any)["y"] != 2.0 {
		t.Error("base mutated")
	}
	// non-list transitions are passed through for decode to reject.
	if got := MergeDocs(base, map[string]any{"transitions": "x"}); got["transitions"] != "x" {
		t.Errorf("non-list transitions = %v", got["transitions"])
	}
}

func TestExtendsErrors(t *testing.T) {
	cyc := NewCatalog()
	_ = cyc.AddJSON([]byte(`{"name":"a","extends":"b"}`), "x", "")
	_ = cyc.AddJSON([]byte(`{"name":"b","extends":"a"}`), "x", "")
	if _, err := Resolve("a", cyc); err == nil || !strings.Contains(err.Error(), "cycle") || !strings.Contains(err.Error(), "a → b → a") {
		t.Errorf("cycle err = %v", err)
	}
	self := NewCatalog()
	self.Add(Source{Name: "s", Origin: "x", Doc: map[string]any{"extends": "s"}})
	if _, err := Resolve("s", self); err == nil || !strings.Contains(err.Error(), "extends unknown workflow") {
		t.Errorf("self extends with no lower scope: %v", err)
	}
	unk := NewCatalog()
	unk.Add(Source{Name: "u", Origin: "x", Doc: map[string]any{"extends": "nope"}})
	if _, err := Resolve("u", unk); err == nil || !strings.Contains(err.Error(), `unknown workflow "nope"`) {
		t.Errorf("unknown parent: %v", err)
	}
	if _, err := Resolve("missing", builtins(t)); err == nil || !strings.Contains(err.Error(), "known: default") {
		t.Errorf("unknown name: %v", err)
	}
	bad := builtins(t)
	bad.Add(Source{Name: "b", Origin: "f.json", Doc: map[string]any{"extends": "default", "bogus": 1.0}})
	if _, err := Resolve("b", bad); err == nil || !strings.Contains(err.Error(), "f.json") || !strings.Contains(err.Error(), "bogus") {
		t.Errorf("unknown field: %v", err)
	}
}

// A more specific scope shadows a less specific definition of the same name,
// and may extend its own name to inherit the shadowed one.
func TestNameClashShadowing(t *testing.T) {
	cat := builtins(t)
	cat.Add(Source{Name: "default", Origin: "global", Doc: map[string]any{"extends": "default", "branches": map[string]any{"production": map[string]any{"name": "prod"}}}})
	cat.Add(Source{Name: "default", Origin: "project", Doc: map[string]any{"extends": "default", "remote": "upstream"}})
	c := compileName(t, cat, "default", CompileOptions{})
	if c.Production != "prod" || c.Remote != "upstream" || c.Staging != "develop/base" || c.Origin != "project" {
		t.Errorf("shadowing: prod %q remote %q staging %q origin %q", c.Production, c.Remote, c.Staging, c.Origin)
	}
	if !reflect.DeepEqual(c.Chain, []string{"default (project)", "default (global)", "default (built-in)"}) {
		t.Errorf("chain %v", c.Chain)
	}
	if s, _ := cat.Lookup("default"); s.Origin != "project" {
		t.Errorf("Lookup %q", s.Origin)
	}
	if _, ok := cat.Lookup("nope"); ok {
		t.Error("lookup missing")
	}
	if !reflect.DeepEqual(cat.Names(), []string{"default"}) {
		t.Error(cat.Names())
	}
}

func TestAddJSONErrors(t *testing.T) {
	c := NewCatalog()
	if err := c.AddJSON([]byte("{oops"), "f", "x"); err == nil || !strings.Contains(err.Error(), "f:") {
		t.Errorf("bad json: %v", err)
	}
	if err := c.AddJSON([]byte("null"), "f", "x"); err == nil {
		t.Error("null accepted")
	}
	if err := c.AddJSON([]byte("{}"), "f", ""); err == nil {
		t.Error("nameless accepted")
	}
	if err := c.AddJSON([]byte("{}"), "f", "base"); err != nil {
		t.Error(err)
	} else if _, ok := c.Lookup("base"); !ok {
		t.Error("fallback name")
	}
}

func TestBuiltinsErrors(t *testing.T) {
	if _, err := Builtins(fstest.MapFS{"workflows/a.json": {Data: []byte("{x")}}); err == nil {
		t.Error("bad built-in accepted")
	}
	c, err := Builtins(fstest.MapFS{})
	if err != nil || len(c.Names()) != 0 {
		t.Errorf("empty fs %v %v", c, err)
	}
}

func valid() Template {
	return Template{
		Name:     "t",
		Branches: map[string]Branch{Production: {Name: "main"}, Staging: {Name: "dev"}},
		Work:     Work{Pattern: "w/{name}"},
		Transitions: []Transition{
			{ID: "m", From: WorkKey, To: Targets{Staging}, Via: "pr"},
			{ID: "r", From: Staging, To: Targets{Production}, Via: "pr"},
		},
	}
}

func TestValidate(t *testing.T) {
	if err := Validate(valid()); err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name   string
		mutate func(*Template)
		want   string
	}{
		{"missing production", func(t *Template) { delete(t.Branches, Production) }, "branches.production.name is required"},
		{"same names", func(t *Template) { t.Branches[Staging] = Branch{Name: "main"} }, "must differ"},
		{"reserved work key", func(t *Template) { t.Branches[WorkKey] = Branch{Name: "w"} }, "reserved"},
		{"pattern flag mismatch", func(t *Template) { t.Branches["rel"] = Branch{Name: "rel/{version}"} }, `"pattern" must be true`},
		{"bad placeholder in branch", func(t *Template) { t.Branches["rel"] = Branch{Name: "rel/{nope}", Pattern: true} }, "unknown placeholder {nope}"},
		{"no work pattern", func(t *Template) { t.Work.Pattern = "" }, "work.pattern is required"},
		{"work pattern without name", func(t *Template) { t.Work.Pattern = "w/{ticket}" }, "must contain {name}"},
		{"unclosed brace", func(t *Template) { t.Work.Pattern = "w/{name" }, "unclosed"},
		{"type without types", func(t *Template) { t.Work.Pattern = "{type}/{name}" }, "work.types is required"},
		{"unknown from", func(t *Template) { t.Work.From = "nope" }, `work.from: unknown branch "nope"`},
		{"missing id", func(t *Template) { t.Transitions[0].ID = "" }, "transitions[0]: id is required"},
		{"duplicate id", func(t *Template) { t.Transitions[1].ID = "m" }, "duplicate id"},
		{"bad from", func(t *Template) { t.Transitions[0].From = "x" }, `from "x" is not a branch key`},
		{"no to", func(t *Template) { t.Transitions[0].To = nil }, "to is required"},
		{"bad to", func(t *Template) { t.Transitions[0].To = Targets{"x"} }, `to "x" is not a branch key`},
		{"bad via", func(t *Template) { t.Transitions[0].Via = "push" }, "via must be"},
		{"bad merge method", func(t *Template) { t.Transitions[0].MergeMethod = "ff" }, "mergeMethod must be"},
		{"bad pr template", func(t *Template) { t.Transitions[0].PRTemplate = "x" }, `unknown prTemplate "x"`},
		{"bad transition work", func(t *Template) { t.Transitions[0].Work = "h/{zzz}" }, "work: unknown placeholder"},
		{"no work transition", func(t *Template) { t.Transitions = t.Transitions[1:] }, "nowhere to go"},
		{"staging dead end", func(t *Template) { t.Transitions = t.Transitions[:1] }, `"staging" never reaches production`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tm := valid()
			tm.Branches = map[string]Branch{Production: {Name: "main"}, Staging: {Name: "dev"}}
			tm.Transitions = append([]Transition(nil), tm.Transitions...)
			tt.mutate(&tm)
			err := Validate(tm)
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Errorf("err = %v, want %q", err, tt.want)
			}
		})
	}
	// Compile reports validation errors.
	tm := valid()
	tm.Work.Pattern = ""
	if _, err := Compile(&Resolved{Template: tm}, CompileOptions{}); err == nil {
		t.Error("Compile accepted invalid template")
	}
}

func TestPatternRegex(t *testing.T) {
	tests := []struct {
		pattern string
		types   []string
		want    string
		match   []string
		reject  []string
	}{
		{"develop/{type}/{name}", []string{"feat", "fix"}, `^develop/(feat|fix)/[a-z0-9][a-z0-9._-]*$`,
			[]string{"develop/feat/x", "develop/fix/a.b-c_d"}, []string{"develop/feat/X", "develop/chore/x", "develop/feat/"}},
		{"{type}/{ticket}-{name}", []string{"feat"}, `^(feat)/[A-Za-z][A-Za-z0-9]*-[0-9]+-[a-z0-9][a-z0-9._-]*$`,
			[]string{"feat/ABC-12-login", "feat/x1-9-a"}, []string{"feat/12-login", "feat/ABC-login"}},
		{"release/{version}", nil, `^release/v?[0-9]+(\.[0-9]+)*(-[0-9A-Za-z.-]+)?$`,
			[]string{"release/1.2.0", "release/v2", "release/1.0.0-rc.1"}, []string{"release/x", "release/"}},
		{"a.b+/{name}", nil, `^a\.b\+/[a-z0-9][a-z0-9._-]*$`, []string{"a.b+/x"}, []string{"aXb+/x"}},
		{"c++/{type}", []string{"c++"}, `^c\+\+/(c\+\+)$`, []string{"c++/c++"}, nil},
	}
	for _, tt := range tests {
		got, err := PatternRegex(tt.pattern, tt.types)
		if err != nil || got != tt.want {
			t.Errorf("PatternRegex(%q) = %q, %v; want %q", tt.pattern, got, err, tt.want)
			continue
		}
		re := regexp.MustCompile(got)
		for _, m := range tt.match {
			if !re.MatchString(m) {
				t.Errorf("%s should match %q", got, m)
			}
		}
		for _, m := range tt.reject {
			if re.MatchString(m) {
				t.Errorf("%s should reject %q", got, m)
			}
		}
	}
	for _, bad := range []struct {
		p     string
		types []string
	}{{"{type}/{name}", nil}, {"{x}", nil}, {"a{", nil}} {
		if _, err := PatternRegex(bad.p, bad.types); err == nil {
			t.Errorf("PatternRegex(%q) accepted", bad.p)
		}
	}
}

func TestExpand(t *testing.T) {
	if got := Expand("v{version}", map[string]string{"version": "1.2"}); got != "v1.2" {
		t.Error(got)
	}
	if got := Expand("{type}/{name}", map[string]string{"name": "x"}); got != "{type}/x" {
		t.Error(got)
	}
	if got := Expand("bad{", nil); got != "bad{" {
		t.Error(got)
	}
}

func TestTypesAndTargetsJSON(t *testing.T) {
	var w Work
	if err := json.Unmarshal([]byte(`{"pattern":"p","types":"{commit.types}"}`), &w); err != nil || w.Types.Ref != CommitTypesRef {
		t.Fatalf("%+v %v", w, err)
	}
	if got := w.Types.Expand([]string{"a"}); !reflect.DeepEqual(got, []string{"a"}) {
		t.Error(got)
	}
	if err := json.Unmarshal([]byte(`{"types":"{other}"}`), &w); err == nil {
		t.Error("unknown ref accepted")
	}
	if err := json.Unmarshal([]byte(`{"types":5}`), &w); err == nil {
		t.Error("number types accepted")
	}
	for _, c := range []struct {
		t    Types
		want string
	}{{Types{Ref: CommitTypesRef}, `"{commit.types}"`}, {Types{}, "null"}, {Types{List: []string{"a"}}, `["a"]`}} {
		b, _ := json.Marshal(c.t)
		if string(b) != c.want {
			t.Errorf("marshal %+v = %s", c.t, b)
		}
	}
	var tr Transition
	if err := json.Unmarshal([]byte(`{"to":["a","b"]}`), &tr); err != nil || len(tr.To) != 2 {
		t.Error(tr, err)
	}
	if err := json.Unmarshal([]byte(`{"to":3}`), &tr); err == nil {
		t.Error("number to accepted")
	}
	b, _ := json.Marshal(Targets{"a"})
	b2, _ := json.Marshal(Targets{"a", "b"})
	if string(b) != `"a"` || string(b2) != `["a","b"]` {
		t.Error(string(b), string(b2))
	}
}

func TestCommitTypesReference(t *testing.T) {
	cat := builtins(t)
	cat.Add(Source{Name: "ct", Origin: "x", Doc: map[string]any{"extends": "default", "work": map[string]any{"types": "{commit.types}"}}})
	c := compileName(t, cat, "ct", CompileOptions{CommitTypes: []string{"feat", "hack"}})
	if c.WorkPatterns[0] != `^develop/(feat|hack)/[a-z0-9][a-z0-9._-]*$` {
		t.Errorf("pattern %s", c.WorkPatterns[0])
	}
	// {commit.types} with no commit types fails to compile.
	r, _ := Resolve("ct", cat)
	if _, err := Compile(r, CompileOptions{}); err == nil || !strings.Contains(err.Error(), "no types") {
		t.Errorf("err = %v", err)
	}
}

func TestCompileErrorsAndDefaults(t *testing.T) {
	tm := valid()
	tm.Transitions[0].Work = "{type}/{name}"
	tm.Work.Types = Types{List: []string{"a"}}
	tm.Work.Pattern = "w/{name}"
	c, err := Compile(&Resolved{Template: tm}, CompileOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if c.Remote != DefaultRemote || c.WorkFrom != "dev" {
		t.Errorf("defaults remote %q from %q", c.Remote, c.WorkFrom)
	}
	tm.Work.Types = Types{}
	if _, err := Compile(&Resolved{Template: tm}, CompileOptions{}); err == nil || !strings.Contains(err.Error(), "transition m work") {
		t.Errorf("transition work err %v", err)
	}
	tm = valid()
	tm.Branches["rel"] = Branch{Name: "rel/{type}", Pattern: true}
	tm.Transitions = append(tm.Transitions, Transition{ID: "x", From: "rel", To: Targets{Production}, Via: "pr"})
	if _, err := Compile(&Resolved{Template: tm}, CompileOptions{}); err == nil || !strings.Contains(err.Error(), "branches.rel") {
		t.Errorf("pattern branch err %v", err)
	}
}

func readRepoFile(p string) ([]byte, error) { return os.ReadFile(p) }

func contains(l []string, s string) bool {
	for _, x := range l {
		if x == s {
			return true
		}
	}
	return false
}
