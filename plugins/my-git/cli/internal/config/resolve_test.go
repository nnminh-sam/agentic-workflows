package config

import (
	"reflect"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const gOrigin = "~/.config/mygit/config.json"

func TestResolveNoConfig(t *testing.T) {
	w := newWorld()
	r := w.resolve(t)
	if r.ProfileName != "" || r.RulesetName != "default" || r.WorkflowName != "default" || r.WorkflowReason != "built-in" {
		t.Errorf("selection %q %q %q", r.ProfileName, r.RulesetName, r.WorkflowName)
	}
	wantRule(t, r, "CM007", verdict.Deny, "default")
	if got := rule(t, r, "CM007").Params["max"]; got != 72.0 {
		t.Errorf("CM007 max %v", got)
	}
	// Unset profile vars are dropped from lists, become nil as whole values.
	if got := rule(t, r, "ID005").Params["expected_logins"]; !reflect.DeepEqual(got, []any{}) {
		t.Errorf("ID005 expected_logins = %#v", got)
	}
	if got, ok := rule(t, r, "ID003").Params["key_path"]; !ok || got != nil {
		t.Errorf("ID003 key_path = %#v", got)
	}
	// BR003 unchanged from the contract (exit criterion end-to-end).
	if got := rule(t, r, "BR003").Params["patterns"]; !reflect.DeepEqual(got, []any{"^develop/(feat|fix|chore|docs|refactor|perf|test|build|ci)/[a-z0-9][a-z0-9._-]*$"}) {
		t.Errorf("BR003 %v", got)
	}
	if got := r.Policy.Contract(policy.Branching).Settings; got["production_branch"] != "main" || got["staging_branch"] != "develop/base" {
		t.Errorf("settings %v", got)
	}
	if v, o, ok := r.Get("commit.headerMax"); !ok || v != 72.0 || o != "built-in" {
		t.Errorf("Get headerMax = %v %q", v, o)
	}
	if _, _, ok := r.Get("nope.x"); ok {
		t.Error("Get missing")
	}
}

func TestMergeScalarsObjectsArraysAndOrigins(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{"execution":{"mode":"manual","agentApply":"allow"},"commit":{"types":["a","b","c"],"headerMax":80},
	  "rules":{"CM007":{"params":{"max":80}},"CM012":"ask"}}`
	w.files[ppath] = `{"commit":{"types":["feat","fix"],"headerMax":100},"execution":{"shell":"cmd"},
	  "rules":{"CM007":"warn","CM012":{"params":{"max":90}}}}`
	w.files[lpath] = `{"commit":{"headerMax":120}}`
	w.env["MYGIT_MODE"] = "auto"
	w.flags = Flags{Shell: "gitbash"}
	r := w.resolve(t)
	checks := []struct {
		key    string
		val    any
		origin string
	}{
		{"commit.headerMax", 120.0, ".mygit.local.json"},
		{"commit.types", []any{"feat", "fix"}, ".mygit.json"}, // arrays replace
		{"commit.convention", "conventional", "built-in"},     // object deep-merge keeps built-in
		{"execution.agentApply", "allow", gOrigin},
		{"execution.mode", "auto", "env MYGIT_MODE"},
		{"execution.shell", "gitbash", "flag --shell"},
		{"rules.CM007.severity", "warn", ".mygit.json"},
		{"rules.CM007.params.max", 80.0, gOrigin}, // per-id merge: shorthand severity + object params
		{"rules.CM012.severity", "ask", gOrigin},
		{"rules.CM012.params.max", 90.0, ".mygit.json"},
	}
	for _, c := range checks {
		v, o, ok := r.Get(c.key)
		if !ok || !reflect.DeepEqual(v, c.val) || o != c.origin {
			t.Errorf("%s = %#v ← %q; want %#v ← %q", c.key, v, o, c.val, c.origin)
		}
	}
	if r.Origins.Of("rules.CM007") != ".mygit.json" && r.Origins.Of("rules.CM007") != gOrigin {
		t.Errorf("Of(object) = %q", r.Origins.Of("rules.CM007"))
	}
	if r.Origins.Of("rules.CM007.severity.x") != ".mygit.json" {
		t.Error("Of(ancestor)")
	}
	if r.Execution != (ExecutionSettings{Mode: "auto", Shell: "gitbash", AgentApply: "allow"}) {
		t.Errorf("execution %+v", r.Execution)
	}
	// Policy: per-rule merge global → project.
	cm7 := wantRule(t, r, "CM007", verdict.Warn, "")
	// Later layer wins across forms: the local commit.headerMax=120 beats
	// the global rules.CM007.params.max=80.
	if cm7.Params["max"] != 120.0 || cm7.Origin != "commit.headerMax (.mygit.local.json)" {
		t.Errorf("CM007 max %v ← %q", cm7.Params["max"], cm7.Origin)
	}
	cm12 := wantRule(t, r, "CM012", verdict.Ask, "override rules.CM012 (.mygit.json)")
	if cm12.Params["max"] != 90.0 {
		t.Errorf("CM012 max %v", cm12.Params["max"])
	}
}

func TestOverrideOrderAllLayers(t *testing.T) {
	steps := []struct {
		name   string
		setup  func(*world)
		sev    verdict.Severity
		origin string
	}{
		{"global", func(w *world) { w.files[gpath] = `{"rules":{"CM009":"ask"}}` }, verdict.Ask, "override rules.CM009 (" + gOrigin + ")"},
		{"project", func(w *world) { w.files[ppath] = `{"rules":{"CM009":"deny"}}` }, verdict.Deny, "override rules.CM009 (.mygit.json)"},
		{"local", func(w *world) { w.files[lpath] = `{"rules":{"CM009":"off"}}` }, verdict.Off, "override rules.CM009 (.mygit.local.json)"},
		{"env", func(w *world) { w.env["MYGIT_RULES"] = "cm009=warn, ,GH001=ask" }, verdict.Warn, "override rules.CM009 (env MYGIT_RULES)"},
		{"flag", func(w *world) { w.flags.Rules = []string{"CM009=deny"} }, verdict.Deny, "override rules.CM009 (flag --rule)"},
	}
	w := newWorld()
	for _, s := range steps {
		s.setup(w)
		r := w.resolve(t)
		if ru := rule(t, r, "CM009"); ru.Severity != s.sev || ru.Origin != s.origin {
			t.Errorf("after %s: CM009 = %s ← %q; want %s ← %q", s.name, ru.Severity, ru.Origin, s.sev, s.origin)
		}
	}
}

func TestRuleFlagErrors(t *testing.T) {
	for _, bad := range []string{"CM009", "=off", "CM009=", "CM009=meh"} {
		if _, _, err := ParseRuleFlag(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
	id, ov, err := ParseRuleFlag(" cm009 = OFF ")
	if err != nil || id != "CM009" || ov.Severity != "off" {
		t.Errorf("%q %+v %v", id, ov, err)
	}
	w := newWorld()
	w.flags.Rules = []string{"ZZ999=off"}
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), `unknown rule id "ZZ999"`) {
		t.Errorf("unknown id: %v", err)
	}
	w = newWorld()
	w.flags.Rules = []string{"x"}
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "flag --rule") {
		t.Errorf("bad flag: %v", err)
	}
	w = newWorld()
	w.env["MYGIT_RULES"] = "CM009"
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "MYGIT_RULES") {
		t.Errorf("bad env: %v", err)
	}
	w = newWorld()
	w.files[ppath] = `{"rules":{"XX001":"off"}}`
	err = func() error { _, e := Resolve(w.inputs()); return e }()
	if e := asErr(t, err); e.File != ppath || e.Field != "rules.XX001" {
		t.Errorf("unknown id in project: %+v", e)
	}
}

const rulesetGlobal = `{
  "rulesets": {
    "solo":   { "extends": "default", "rules": { "BR005": "warn", "GH008": "ask" } },
    "strict": { "extends": "default", "rules": { "ID005": "deny", "CM009": "deny", "CM010": "deny" } }
  },
  "defaultRuleset": "solo",
  "match": [ { "remoteOwner": "acme", "ruleset": "strict" }, { "path": "~/oss/**", "ruleset": "solo" } ]
}`

func TestRulesetSelectionOrder(t *testing.T) {
	type tc struct {
		name, want, reason string
		setup              func(*world)
	}
	base := func(w *world) {
		w.files[gpath] = rulesetGlobal
		w.files[ppath] = `{"rulesets":{"team":{"extends":"strict"},"proj":{}},"ruleset":"team"}`
		w.files[lpath] = `{"ruleset":"proj"}`
		w.env["MYGIT_RULESET"] = "solo"
		w.flags.Ruleset = "strict"
		w.owner = "acme"
	}
	cases := []tc{
		{"flag", "strict", "--ruleset flag", func(w *world) {}},
		{"env", "solo", "env MYGIT_RULESET", func(w *world) { w.flags.Ruleset = "" }},
		{"local", "proj", "local .mygit.local.json", func(w *world) { w.flags.Ruleset = ""; delete(w.env, "MYGIT_RULESET") }},
		{"project", "team", "project .mygit.json", func(w *world) {
			w.flags.Ruleset = ""
			delete(w.env, "MYGIT_RULESET")
			w.files[lpath] = `{}`
		}},
		{"global match", "strict", "match remoteOwner=acme", func(w *world) {
			w.flags.Ruleset = ""
			delete(w.env, "MYGIT_RULESET")
			w.files[lpath] = `{}`
			w.files[ppath] = `{}`
		}},
		{"defaultRuleset", "solo", "defaultRuleset", func(w *world) {
			w.flags.Ruleset = ""
			delete(w.env, "MYGIT_RULESET")
			delete(w.files, lpath)
			delete(w.files, ppath)
			w.owner = "other"
		}},
		{"built-in", "default", "built-in", func(w *world) {
			w.flags.Ruleset = ""
			delete(w.env, "MYGIT_RULESET")
			w.files = map[string]string{}
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld()
			base(w)
			c.setup(w)
			r := w.resolve(t)
			if r.RulesetName != c.want || r.RulesetReason != c.reason {
				t.Errorf("ruleset = %q (%s); want %q (%s)", r.RulesetName, r.RulesetReason, c.want, c.reason)
			}
		})
	}
}

func TestRulesetExtendsChainAndErrors(t *testing.T) {
	w := newWorld()
	w.files[gpath] = rulesetGlobal
	w.files[ppath] = `{"rulesets":{"team":{"extends":"strict","rules":{"CM010":"warn"}}},"ruleset":"team"}`
	r := w.resolve(t)
	wantRule(t, r, "CM010", verdict.Warn, "ruleset team (.mygit.json) ← extends strict ← default")
	wantRule(t, r, "CM009", verdict.Deny, "ruleset strict ("+gOrigin+")")
	wantRule(t, r, "BR005", verdict.Deny, "default") // solo not applied

	// Cycle.
	w.files[ppath] = `{"rulesets":{"a":{"extends":"b"},"b":{"extends":"a"}},"ruleset":"a"}`
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Errorf("cycle: %v", err)
	}
	// Unknown parent and unknown name.
	w.files[ppath] = `{"rulesets":{"a":{"extends":"zzz"}},"ruleset":"a"}`
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), `unknown rule set "zzz"`) {
		t.Errorf("unknown parent: %v", err)
	}
	w.files[ppath] = `{"ruleset":"nope"}`
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), `unknown rule set "nope"`) || !strings.Contains(err.Error(), "project") {
		t.Errorf("unknown name: %v", err)
	}
	// A project set extending its own name inherits the global definition.
	w.files[ppath] = `{"rulesets":{"strict":{"extends":"strict","rules":{"CM010":"off"}}},"ruleset":"strict"}`
	r = w.resolve(t)
	wantRule(t, r, "CM009", verdict.Deny, "")
	wantRule(t, r, "CM010", verdict.Off, "")
	// Sets without extends implicitly extend default; bad id in a set names the file.
	w.files[ppath] = `{"rulesets":{"x":{"rules":{"QQ1":"off"}}},"ruleset":"x"}`
	_, err := Resolve(w.inputs())
	if e := asErr(t, err); e.File != ppath || e.Field != "rulesets.x.rules.QQ1" {
		t.Errorf("bad id in set: %+v", e)
	}
	// "default" may be redefined in a scope.
	w.files[ppath] = `{"rulesets":{"default":{"rules":{"CM010":"off"}}}}`
	r = w.resolve(t)
	_ = r
}

func TestLockedRules(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{"locked":["CM016","CM009","BR002"],"rules":{"CM009":"off"},
	  "rulesets":{"lax":{"rules":{"CM016":"warn","BR002":"deny"}}}}`
	w.files[ppath] = `{"ruleset":"lax","rules":{"CM016":"off","BR002":"warn"}}`
	w.files[lpath] = `{"rules":{"CM009":"deny"}}`
	w.flags.Rules = []string{"CM009=warn"}
	r := w.resolve(t)
	wantRule(t, r, "CM016", verdict.Deny, "default")                                  // weaker from ruleset and project ignored
	wantRule(t, r, "BR002", verdict.Deny, "ruleset lax ("+gOrigin+")")                // stricter-or-equal allowed, project warn ignored
	wantRule(t, r, "CM009", verdict.Deny, "override rules.CM009 (.mygit.local.json)") // global may weaken; local stricter ok; flag weaker ignored
	if len(r.Warnings) != 4 {
		t.Errorf("warnings = %d: %v", len(r.Warnings), r.Warnings)
	}
	for _, wn := range r.Warnings {
		if !strings.Contains(wn, "locked in "+gOrigin) || !strings.Contains(wn, "(ignored)") {
			t.Errorf("warning %q", wn)
		}
	}
	// forbidAiAttribution:false in the project must not weaken a locked CM016.
	w = newWorld()
	w.files[gpath] = `{"locked":["CM016"]}`
	w.files[ppath] = `{"commit":{"forbidAiAttribution":false}}`
	r = w.resolve(t)
	wantRule(t, r, "CM016", verdict.Deny, "default")
	// Unknown locked id.
	w.files[gpath] = `{"locked":["NOPE1"]}`
	_, err := Resolve(w.inputs())
	if e := asErr(t, err); e.Field != "locked[0]" || e.File != gpath {
		t.Errorf("locked unknown: %+v", e)
	}
}

// The plan's `mygit rules show --origin CM007 ID006 CM009` example, verbatim.
func TestPlanRulesShowExample(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{
  "rulesets": {
    "solo":   { "extends": "default", "rules": { "BR005": "warn", "GH008": "ask" } },
    "strict": { "extends": "default", "rules": { "ID006": "deny", "CM009": "deny", "CM010": "deny" } }
  },
  "defaultRuleset": "solo",
  "match": [ { "remoteOwner": "acme", "profile": "work", "ruleset": "strict" } ],
  "profiles": { "work": { "github": { "user": "octo-acme", "orgs": ["acme"] } } },
  "rules": { "CM009": "off" }
}`
	w.files[ppath] = `{ "rulesets": { "team": { "extends": "strict", "rules": { "CM007": { "params": { "max": 100 } } } } }, "ruleset": "team" }`
	w.files[lpath] = `{ "rules": { "CM009": "off" } }`
	w.owner = "acme"
	r := w.resolve(t)
	if r.RulesetName != "team" || r.ProfileName != "work" {
		t.Fatalf("ruleset %q profile %q", r.RulesetName, r.ProfileName)
	}
	cm7 := wantRule(t, r, "CM007", verdict.Deny, "ruleset team (.mygit.json) ← extends strict ← default")
	if cm7.Params["max"] != 100.0 {
		t.Errorf("CM007 max = %v", cm7.Params["max"])
	}
	wantRule(t, r, "ID006", verdict.Deny, "ruleset strict ("+gOrigin+")")
	wantRule(t, r, "CM009", verdict.Off, "override rules.CM009 (.mygit.local.json)")
}

func TestProfileVarsSubstitutedIntoPolicy(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{"defaultProfile":"p","profiles":{"p":{"git":{"name":"Octo","email":"o@x"},"github":{"user":"octo.cat","orgs":["acme"],"transport":"ssh","sshKey":"~/.ssh/k"}}},
	  "rules":{"ID002":{"params":{"patterns":["^${github.user}@users\\.noreply$"]}}}}`
	w.env["MYGIT_GITHUB_ORGS"] = "acme,beta"
	r := w.resolve(t)
	if got := rule(t, r, "ID005").Params["expected_logins"]; !reflect.DeepEqual(got, []any{"octo.cat"}) {
		t.Errorf("ID005 expected_logins = %#v", got)
	}
	if got := rule(t, r, "ID004").Params["allowed_owners"]; !reflect.DeepEqual(got, []any{"octo.cat", "acme", "beta"}) {
		t.Errorf("ID004 allowed_owners = %#v", got)
	}
	if got := rule(t, r, "ID003").Params["key_path"]; got != "~/.ssh/k" {
		t.Errorf("ID003 key_path = %#v", got)
	}
	if got := rule(t, r, "ID002").Params["patterns"]; !reflect.DeepEqual(got, []any{`^octo\.cat@users\.noreply$`}) {
		t.Errorf("ID002 patterns (regex-escaped) = %#v", got)
	}
	if r.ProfileOrigins["github.orgs"] != "env MYGIT_GITHUB_ORGS" || r.Policy.Vars["github_user"] != "octo.cat" {
		t.Errorf("profile origins %v vars %v", r.ProfileOrigins, r.Policy.Vars)
	}
	// unknown profile name is an error naming the reason.
	w.env["MYGIT_PROFILE"] = "ghost"
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "env MYGIT_PROFILE") {
		t.Errorf("unknown profile: %v", err)
	}
	delete(w.env, "MYGIT_PROFILE")
	w.env["MYGIT_GIT_TRANSPORT"] = "https"
	if _, err := Resolve(w.inputs()); err == nil {
		t.Error("bad env transport accepted")
	}
}

func TestProfileSelectionThroughResolve(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{"defaultProfile":"d","profiles":{"d":{},"o":{},"p":{},"l":{},"f":{}},
	  "match":[{"path":"~/work/**","profile":"p"},{"remoteOwner":"acme","profile":"o"}]}`
	if r := w.resolve(t); r.ProfileName != "p" {
		t.Errorf("path match: %q", r.ProfileName)
	}
	w.owner = "acme"
	if r := w.resolve(t); r.ProfileName != "o" {
		t.Errorf("owner match: %q", r.ProfileName)
	}
	w.files[lpath] = `{"profile":"l","profiles":{"l":{"github":{"user":"local"}}}}`
	if r := w.resolve(t); r.ProfileName != "l" || r.Profile.GitHub.User != "local" {
		t.Errorf("local: %q %+v", r.ProfileName, r.Profile)
	}
	w.flags.Profile = "f"
	if r := w.resolve(t); r.ProfileName != "f" || r.ProfileReason != "--profile flag" {
		t.Errorf("flag: %q", r.ProfileName)
	}
}

func TestCommitAndPRConventions(t *testing.T) {
	w := newWorld()
	w.files[ppath] = `{"commit":{"types":["feat","fix"],"scopes":["api","cli"],"headerMax":100,"forbidAiAttribution":false},
	  "pr":{"sections":["Summary","Risks"]}}`
	r := w.resolve(t)
	cm7 := wantRule(t, r, "CM007", verdict.Deny, "commit.headerMax (.mygit.json)")
	if cm7.Params["max"] != 100.0 {
		t.Errorf("CM007 max %v", cm7.Params["max"])
	}
	cm5 := rule(t, r, "CM005")
	if !reflect.DeepEqual(cm5.Params["allowed_scopes"], []any{"api", "cli"}) || cm5.Params["scope_pattern"] == nil {
		t.Errorf("CM005 %v", cm5.Params)
	}
	wantRule(t, r, "CM016", verdict.Off, "commit.forbidAiAttribution (.mygit.json)")
	if got := r.Policy.Contract(policy.Commit).Settings["types"]; !reflect.DeepEqual(got, []any{"feat", "fix"}) {
		t.Errorf("types setting %v", got)
	}
	secs, _ := rule(t, r, "GH008").Params["sections"].([]any)
	if len(secs) != 2 {
		t.Fatalf("GH008 sections %v", secs)
	}
	if s := secs[0].(map[string]any); s["name"] != "Summary" || s["heading"] != `^#{1,4}\s*summary\b` {
		t.Errorf("known section %v", s)
	}
	if s := secs[1].(map[string]any); s["name"] != "Risks" || s["heading"] != `^#{1,4}\s*risks\b` {
		t.Errorf("new section %v", s)
	}
	// forbidAiAttribution true leaves CM016 alone; built-in values are not re-applied.
	w.files[ppath] = `{"commit":{"forbidAiAttribution":true}}`
	r = w.resolve(t)
	wantRule(t, r, "CM016", verdict.Deny, "default")
	wantRule(t, r, "CM007", verdict.Deny, "default")
	// Invalid enums and negative headerMax name the layer.
	for _, bad := range []struct{ doc, field string }{
		{`{"execution":{"mode":"sometimes"}}`, "execution.mode"},
		{`{"execution":{"shell":"fish"}}`, "execution.shell"},
		{`{"execution":{"agentApply":"yes"}}`, "execution.agentApply"},
		{`{"commit":{"convention":"angular"}}`, "commit.convention"},
		{`{"commit":{"headerMax":-1}}`, "commit.headerMax"},
	} {
		w.files[ppath] = bad.doc
		_, err := Resolve(w.inputs())
		if e := asErr(t, err); e.Field != bad.field || e.File != ".mygit.json" {
			t.Errorf("%s: %+v", bad.doc, e)
		}
	}
}

func TestExecution(t *testing.T) {
	tests := []struct {
		goos, shell, cfg, want, mode string
	}{
		{"windows", "/bin/zsh", "", "powershell", "auto"},
		{"darwin", "/bin/zsh", "", "zsh", "auto"},
		{"linux", "/usr/bin/bash", "", "bash", "auto"},
		{"linux", "/usr/bin/fish", "", "bash", "auto"},
		{"linux", "/usr/bin/pwsh", "", "powershell", "auto"},
		{"linux", "", "", "bash", "auto"},
		{"linux", "/bin/zsh", `{"execution":{"shell":"cmd","mode":"manual"}}`, "cmd", "manual"},
	}
	for _, tt := range tests {
		w := newWorld()
		w.goos = tt.goos
		w.env["SHELL"] = tt.shell
		if tt.cfg != "" {
			w.files[ppath] = tt.cfg
		}
		r := w.resolve(t)
		if r.Execution.Shell != tt.want || r.Execution.Mode != tt.mode || r.Execution.AgentApply != "ask" {
			t.Errorf("%s %s %s: %+v", tt.goos, tt.shell, tt.cfg, r.Execution)
		}
	}
	// global auto, project forces manual, --mode for one run.
	w := newWorld()
	w.files[gpath] = `{"execution":{"mode":"auto"}}`
	w.files[ppath] = `{"execution":{"mode":"manual"}}`
	if r := w.resolve(t); r.Execution.Mode != "manual" {
		t.Errorf("project manual: %+v", r.Execution)
	}
	w.flags.Mode = "auto"
	if r := w.resolve(t); r.Execution.Mode != "auto" {
		t.Errorf("--mode: %+v", r.Execution)
	}
	if got := resolveExecution(Execution{}, withDefaults(Inputs{GOOS: "linux"})); got != (ExecutionSettings{Mode: "auto", Shell: "bash", AgentApply: "ask"}) {
		t.Errorf("empty execution %+v", got)
	}
}

const rtTemplate = `{"name":"release-train","extends":"default",
 "branches":{"staging":{"name":"develop"},"release":{"name":"release/{version}","protected":true,"pattern":true}},
 "work":{"pattern":"{type}/{ticket}-{name}"},
 "transitions":[{"id":"cut","from":"staging","to":"release","via":"branch"},
   {"id":"release","from":"release","to":"production","via":"pr","mergeMethod":"merge","tag":"v{version}"}],
 "protection":{"approvals":1}}`

func TestWorkflowSelectionOrder(t *testing.T) {
	wf := func(name, prod string) string {
		return `{"name":"` + name + `","extends":"default","branches":{"production":{"name":"` + prod + `"}}}`
	}
	base := func(w *world) {
		w.files[gwfdir+"/g.json"] = wf("gm", "gm-main")
		w.files[gpath] = `{"workflows":{"gd":` + wf("gd", "gd-main") + `},"defaultWorkflow":"gd",
		  "match":[{"remoteOwner":"acme","workflow":"gm"}]}`
		w.files[ppath] = `{"workflows":{"pw":` + wf("pw", "pw-main") + `},"workflow":"pw"}`
		w.files[lpath] = `{"workflows":{"lw":` + wf("lw", "lw-main") + `},"workflow":"lw"}`
		w.env["MYGIT_WORKFLOW"] = "gd"
		w.flags.Workflow = "pw"
		w.owner = "acme"
	}
	cases := []struct {
		name, want, reason, prod string
		setup                    func(*world)
	}{
		{"flag", "pw", "--workflow flag", "pw-main", func(w *world) {}},
		{"env", "gd", "env MYGIT_WORKFLOW", "gd-main", func(w *world) { w.flags.Workflow = "" }},
		{"local", "lw", "local .mygit.local.json", "lw-main", func(w *world) { w.flags.Workflow = ""; delete(w.env, "MYGIT_WORKFLOW") }},
		{"project", "pw", "project .mygit.json", "pw-main", func(w *world) {
			w.flags.Workflow = ""
			delete(w.env, "MYGIT_WORKFLOW")
			delete(w.files, lpath)
		}},
		{"global match", "gm", "match remoteOwner=acme", "gm-main", func(w *world) {
			w.flags.Workflow = ""
			delete(w.env, "MYGIT_WORKFLOW")
			delete(w.files, lpath)
			delete(w.files, ppath)
		}},
		{"defaultWorkflow", "gd", "defaultWorkflow", "gd-main", func(w *world) {
			w.flags.Workflow = ""
			delete(w.env, "MYGIT_WORKFLOW")
			delete(w.files, lpath)
			delete(w.files, ppath)
			w.owner = ""
		}},
		{"built-in", "default", "built-in", "main", func(w *world) {
			w.flags.Workflow = ""
			delete(w.env, "MYGIT_WORKFLOW")
			w.files = map[string]string{}
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld()
			base(w)
			c.setup(w)
			r := w.resolve(t)
			if r.WorkflowName != c.want || r.WorkflowReason != c.reason || r.Workflow.Production != c.prod {
				t.Errorf("workflow = %q (%s) prod %q; want %q (%s) %q", r.WorkflowName, r.WorkflowReason, r.Workflow.Production, c.want, c.reason, c.prod)
			}
			if got := r.Policy.Contract(policy.Branching).Settings["production_branch"]; got != c.prod {
				t.Errorf("branching settings production_branch = %v", got)
			}
		})
	}
}

func TestWorkflowNameClashPrecedence(t *testing.T) {
	wf := func(prod string) string {
		return `{"name":"x","extends":"default","branches":{"production":{"name":"` + prod + `"}}}`
	}
	w := newWorld()
	w.files[gwfdir+"/x.json"] = wf("global-file")
	w.files[gpath] = `{"workflows":{"x":` + wf("global-cfg") + `},"defaultWorkflow":"x"}`
	w.files[pwfdir+"/x.json"] = wf("project-file")
	w.files[ppath] = `{"workflows":{"x":` + wf("project-cfg") + `}}`
	w.files[lpath] = `{"workflows":{"x":` + wf("local") + `}}`
	order := []struct{ drop, want string }{
		{"", "local"},
		{lpath, "project-cfg"},
		{ppath, "project-file"},
		{pwfdir + "/x.json", "global-cfg"},
		{gpath, "global-file"},
	}
	for _, o := range order {
		if o.drop != "" {
			delete(w.files, o.drop)
		}
		if o.drop == gpath {
			w.flags.Workflow = "x"
		}
		r := w.resolve(t)
		if r.Workflow.Production != o.want {
			t.Errorf("after dropping %q: production %q, want %q", o.drop, r.Workflow.Production, o.want)
		}
	}
	// A built-in name clash: a project "default" shadows the built-in.
	w = newWorld()
	w.files[ppath] = `{"workflows":{"default":{"extends":"default","remote":"upstream"}}}`
	r := w.resolve(t)
	if r.Workflow.Remote != "upstream" || r.Policy.Contract(policy.Branching).Settings["remote"] != "upstream" || r.Workflow.Origin != ".mygit.json" {
		t.Errorf("shadowed default: %+v", r.Workflow)
	}
}

func TestWorkflowDrivesBR003AndOrigin(t *testing.T) {
	w := newWorld()
	w.files[pwfdir+"/release-train.json"] = rtTemplate
	w.files[ppath] = `{"workflow":"release-train","commit":{"types":["feat","fix"]}}`
	r := w.resolve(t)
	br3 := rule(t, r, "BR003")
	if br3.Origin != "workflow release-train (.mygit/workflows/release-train.json)" {
		t.Errorf("BR003 origin %q", br3.Origin)
	}
	pats, _ := br3.Params["patterns"].([]any)
	if len(pats) != 2 || !strings.HasPrefix(pats[0].(string), "^(feat|fix|chore") {
		t.Errorf("BR003 patterns %v", pats)
	}
	if r.Policy.Contract(policy.Branching).Settings["staging_branch"] != "develop" {
		t.Error("staging setting")
	}
	// KNOWN SUSPECT: partial protection override must inherit requirePr.
	p := r.Workflow.Template.Protection
	if !p.RequirePR || p.Approvals != 1 {
		t.Errorf("protection not deep-merged: %+v", p)
	}
	// Merged File.Workflows is decoded from raw config only; check it too.
	w.files[ppath] = `{"workflow":"rt","workflows":{"rt":{"extends":"default","protection":{"approvals":1}}}}`
	r = w.resolve(t)
	if p := r.Workflow.Template.Protection; !p.RequirePR || p.Approvals != 1 {
		t.Errorf("inline template protection not deep-merged: %+v", p)
	}
	// Invalid workflow errors carry the reason.
	w.files[ppath] = `{"workflow":"ghost"}`
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "project .mygit.json") {
		t.Errorf("unknown workflow: %v", err)
	}
	w.files[ppath] = `{"workflow":"bad","workflows":{"bad":{"extends":"default","work":{"pattern":"x"}}}}`
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "{name}") {
		t.Errorf("invalid workflow: %v", err)
	}
	w.files[ppath] = `{}`
	w.files[pwfdir+"/broken.json"] = `{oops`
	if _, err := Resolve(w.inputs()); err == nil || !strings.Contains(err.Error(), "broken.json") {
		t.Errorf("broken template file: %v", err)
	}
}

func TestResolveErrors(t *testing.T) {
	w := newWorld()
	w.files[gpath] = "{\n\"commit\": {\"headrMax\": 1}}"
	_, err := Resolve(w.inputs())
	if e := asErr(t, err); e.File != gpath || e.Line != 2 {
		t.Errorf("global error %+v", e)
	}
	w = newWorld()
	w.files[ppath] = `{"profiles":{}}`
	if e := asErr(t, func() error { _, e := Resolve(w.inputs()); return e }()); e.File != ppath || e.Field != "profiles" {
		t.Errorf("project profiles %+v", e)
	}
	in := newWorld().inputs()
	in.ReadFile = func(string) ([]byte, error) { return nil, errFake }
	if _, err := Resolve(in); err == nil || !strings.Contains(err.Error(), "permission") {
		t.Errorf("read error: %v", err)
	}
	in = newWorld().inputs()
	in.Defaults = fstest.MapFS{}
	if _, err := Resolve(in); err == nil || !strings.Contains(err.Error(), "built-in contract") {
		t.Errorf("missing contracts: %v", err)
	}
	in = newWorld().inputs()
	in.Glob = func(string) ([]string, error) { return nil, errFake }
	in.RepoRoot = ""
	if _, err := Resolve(in); err == nil {
		t.Error("glob error ignored")
	}
	// Outside a repository: path match uses cwd.
	w = newWorld()
	w.files[gpath] = `{"profiles":{"p":{}},"match":[{"path":"~/work/**","profile":"p"}]}`
	in = w.inputs()
	in.RepoRoot = ""
	r, err := Resolve(in)
	if err != nil || r.ProfileName != "p" || r.Paths.Project != "" {
		t.Errorf("no repo: %v %+v", err, r)
	}
}

func TestLoadContractsErrors(t *testing.T) {
	good := func() fstest.MapFS {
		m := fstest.MapFS{}
		for name, f := range contractFiles() {
			m[f] = &fstest.MapFile{Data: []byte(`{"contract":"` + name + `","version":"1.0.0","rules":[{"id":"X"}]}`)}
		}
		return m
	}
	c, err := LoadContracts(good())
	if err != nil {
		t.Fatal(err)
	}
	if c[policy.Commit].Version != "1.0.0" || c[policy.Commit].Rules[0].Origin != "default" || c[policy.Commit].Rules[0].Params == nil || c[policy.Commit].Settings == nil {
		t.Errorf("loaded %+v", c[policy.Commit])
	}
	m := good()
	m["contracts/identity.json"] = &fstest.MapFile{Data: []byte(`{oops`)}
	if _, err := LoadContracts(m); err == nil {
		t.Error("bad json")
	}
	m = good()
	m["contracts/identity.json"] = &fstest.MapFile{Data: []byte(`{"contract":"other"}`)}
	if _, err := LoadContracts(m); err == nil || !strings.Contains(err.Error(), `want "identity"`) {
		t.Errorf("wrong name: %v", err)
	}
	// Shipped contracts carry semver string versions.
	c, err = LoadContracts(defaults.FS)
	if err == nil {
		for name, ct := range c {
			if !strings.Contains(ct.Version, ".") {
				t.Errorf("%s version %q", name, ct.Version)
			}
		}
	}
}

// commit.*/pr.* conventions and rules maps obey "later layer wins" together:
// per layer, the conventions apply first and that layer's rules map second.
func TestConventionsInterleaveWithRulesPerLayer(t *testing.T) {
	cases := []struct {
		name       string
		g, p, l    string
		wantMax    float64
		wantOrigin string
	}{
		{"local headerMax beats global rules", `{"rules":{"CM007":{"params":{"max":80}}}}`, ``,
			`{"commit":{"headerMax":120}}`, 120, "commit.headerMax (.mygit.local.json)"},
		{"local rules beat local headerMax", ``, ``,
			`{"commit":{"headerMax":120},"rules":{"CM007":{"params":{"max":60}}}}`, 60, "override rules.CM007 (.mygit.local.json)"},
		{"project rules beat global headerMax", `{"commit":{"headerMax":50}}`, `{"rules":{"CM007":{"params":{"max":99}}}}`,
			``, 99, "override rules.CM007 (.mygit.json)"},
		{"project headerMax beats project-less global rules", `{"rules":{"CM007":{"params":{"max":80}}}}`, `{"commit":{"headerMax":100}}`,
			``, 100, "commit.headerMax (.mygit.json)"},
		{"global only", `{"commit":{"headerMax":90}}`, ``, ``, 90, "commit.headerMax (" + gOrigin + ")"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld()
			for path, doc := range map[string]string{gpath: c.g, ppath: c.p, lpath: c.l} {
				if doc != "" {
					w.files[path] = doc
				}
			}
			r := w.resolve(t)
			cm7, _ := r.Policy.Rule("CM007")
			if cm7.Params["max"] != c.wantMax || cm7.Origin != c.wantOrigin {
				t.Errorf("CM007 max %v ← %q; want %v ← %q", cm7.Params["max"], cm7.Origin, c.wantMax, c.wantOrigin)
			}
		})
	}
}

func TestConventionsInterleaveOtherKeys(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{"rules":{"CM005":{"params":{"allowed_scopes":["g"]}},"CM016":"deny"}}`
	w.files[ppath] = `{"commit":{"scopes":["api"],"forbidAiAttribution":false},"pr":{"sections":["Summary","Risks"]},
	  "rules":{"GH008":{"params":{"sections":[{"name":"X","heading":"^# x"}]}}}}`
	w.files[lpath] = `{"pr":{"sections":["Summary"]}}`
	r := w.resolve(t)
	cm5, _ := r.Policy.Rule("CM005")
	if !reflect.DeepEqual(cm5.Params["allowed_scopes"], []any{"api"}) || cm5.Origin != "commit.scopes (.mygit.json)" {
		t.Errorf("CM005 %v ← %q", cm5.Params["allowed_scopes"], cm5.Origin)
	}
	wantRule(t, r, "CM016", verdict.Off, "commit.forbidAiAttribution (.mygit.json)")
	gh8, _ := r.Policy.Rule("GH008")
	secs, _ := gh8.Params["sections"].([]any)
	if len(secs) != 1 || secs[0].(map[string]any)["name"] != "Summary" || gh8.Origin != "pr.sections (.mygit.local.json)" {
		t.Errorf("GH008 sections %v ← %q", secs, gh8.Origin)
	}
}

// A project commit.forbidAiAttribution=false cannot weaken a locked CM016,
// while a global one can.
func TestConventionsRespectLocked(t *testing.T) {
	w := newWorld()
	w.files[gpath] = `{"locked":["CM016"]}`
	w.files[ppath] = `{"commit":{"forbidAiAttribution":false}}`
	r := w.resolve(t)
	wantRule(t, r, "CM016", verdict.Deny, "default")
	if len(r.Warnings) != 1 || !strings.Contains(r.Warnings[0], "CM016") {
		t.Errorf("warnings %v", r.Warnings)
	}
	w = newWorld()
	w.files[gpath] = `{"locked":["CM016"],"commit":{"forbidAiAttribution":false}}`
	r = w.resolve(t)
	wantRule(t, r, "CM016", verdict.Off, "commit.forbidAiAttribution ("+gOrigin+")")
}
