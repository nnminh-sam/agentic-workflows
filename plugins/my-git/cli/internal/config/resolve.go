package config

import (
	"errors"
	"fmt"
	"io/fs"
	"path"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// Flags are the per-run command-line overrides (the top layer).
type Flags struct {
	Profile  string
	Ruleset  string
	Workflow string
	Rules    []string // ID=severity
	Mode     string
	Shell    string
}

// Inputs is everything Resolve reads. Nothing else is consulted.
type Inputs struct {
	Cwd         string
	RepoRoot    string // "" outside a repository
	RemoteOwner string // owner of the origin remote, computed by the caller
	GOOS        string
	Getenv      func(string) string
	Home        string
	// ReadFile reads a file; a missing file must yield an error wrapping
	// fs.ErrNotExist (os.ReadFile does).
	ReadFile func(path string) ([]byte, error)
	// Glob lists files matching a pattern (filepath.Glob); nil skips the
	// workflow template directories.
	Glob  func(pattern string) ([]string, error)
	Flags Flags
	// Defaults overrides the embedded defaults (tests); nil means defaults.FS.
	Defaults fs.FS
}

// ExecutionSettings is the resolved execution block.
type ExecutionSettings struct {
	Mode       string // auto | manual
	Shell      string // powershell | cmd | gitbash | bash | zsh (never "auto")
	AgentApply string // ask | allow
}

// Resolved is the fully resolved configuration.
type Resolved struct {
	// File is the merged config; Raw is the same as decoded JSON.
	File    File
	Raw     map[string]any
	Origins Origins
	Layers  []*Layer
	Paths   Paths

	ProfileName   string
	ProfileReason string
	Profile       profile.Profile
	// ProfileOrigins records environment overrides of profile fields.
	ProfileOrigins map[string]string

	RulesetName   string
	RulesetReason string

	WorkflowName   string
	WorkflowReason string
	Workflow       *workflow.Compiled

	Policy    *policy.Policy
	Execution ExecutionSettings
	Warnings  []string
}

// Get returns the merged value at a dotted key and the layer it came from.
func (r *Resolved) Get(key string) (any, string, bool) {
	v, ok := getPath(r.Raw, key)
	if !ok {
		return nil, "", false
	}
	return v, r.Origins.Of(key), true
}

// Resolve loads every layer and resolves profile, rule set, workflow and policy.
func Resolve(in Inputs) (*Resolved, error) {
	in = withDefaults(in)
	contracts, err := LoadContracts(in.Defaults)
	if err != nil {
		return nil, err
	}
	r := &Resolved{Paths: ResolvePaths(in.GOOS, in.Getenv, in.Home, in.RepoRoot)}
	if err := r.loadLayers(in, contracts); err != nil {
		return nil, err
	}
	if err := r.merge(); err != nil {
		return nil, err
	}
	if err := r.validateMerged(contracts); err != nil {
		return nil, err
	}
	where := profile.Where{RemoteOwner: in.RemoteOwner, Dir: in.RepoRoot, Home: in.Home, GOOS: in.GOOS}
	if where.Dir == "" {
		where.Dir = in.Cwd
	}
	steps := []func() error{
		func() error { return r.selectProfile(in, where) },
		func() error { return r.selectRuleset(in, where) },
		func() error { return r.selectWorkflow(in, where) },
		func() error { return r.buildPolicy(in, contracts) },
	}
	for _, step := range steps {
		if err := step(); err != nil {
			return nil, err
		}
	}
	r.Execution = resolveExecution(r.File.Execution, in)
	return r, nil
}

func withDefaults(in Inputs) Inputs {
	if in.Getenv == nil {
		in.Getenv = func(string) string { return "" }
	}
	if in.Defaults == nil {
		in.Defaults = defaults.FS
	}
	if in.ReadFile == nil {
		in.ReadFile = func(string) ([]byte, error) { return nil, fs.ErrNotExist }
	}
	return in
}

// layer returns the parsed layer of a scope, or nil.
func (r *Resolved) layer(s Scope) *Layer {
	for _, l := range r.Layers {
		if l.Scope == s {
			return l
		}
	}
	return nil
}

// file returns the File of a scope (zero File when the layer is absent).
func (r *Resolved) file(s Scope) File {
	if l := r.layer(s); l != nil {
		return l.File
	}
	return File{}
}

func (r *Resolved) loadLayers(in Inputs, contracts map[string]*policy.Contract) error {
	r.Layers = append(r.Layers, builtinLayer(contracts))
	files := []struct {
		scope Scope
		path  string
	}{{ScopeGlobal, r.Paths.Global}, {ScopeProject, r.Paths.Project}, {ScopeLocal, r.Paths.Local}}
	for _, f := range files {
		if f.path == "" {
			continue
		}
		data, err := in.ReadFile(f.path)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return &Error{File: f.path, Msg: err.Error()}
		}
		l, err := Parse(data, f.path, f.scope)
		if err != nil {
			return err
		}
		l.Origin = Display(f.path, in.RepoRoot, in.Home)
		r.Layers = append(r.Layers, l)
	}
	env, err := envLayers(in.Getenv)
	if err != nil {
		return err
	}
	flags, err := flagLayers(in.Flags)
	if err != nil {
		return err
	}
	r.Layers = append(append(r.Layers, env...), flags...)
	return nil
}

// builtinLayer is the configuration implied by the shipped contracts.
func builtinLayer(contracts map[string]*policy.Contract) *Layer {
	commit := map[string]any{"convention": "conventional", "forbidAiAttribution": true}
	if t, ok := contracts[policy.Commit].Settings["types"]; ok {
		commit["types"] = cloneValue(t)
	}
	if max := policy.Int(contracts[policy.Commit].Rule("CM007").Params, "max", 0); max > 0 {
		commit["headerMax"] = float64(max)
	}
	var sections []any
	for _, s := range sectionList(contracts[policy.GitHubCLI].Rule("GH008").Params) {
		sections = append(sections, s["name"])
	}
	doc := map[string]any{
		"version":   float64(1),
		"execution": map[string]any{"mode": "auto", "shell": "auto", "agentApply": "ask"},
		"commit":    commit,
		"pr":        map[string]any{"base": "auto", "sections": sections},
	}
	return &Layer{Scope: ScopeBuiltin, Origin: string(ScopeBuiltin), Raw: doc}
}

// envLayers builds one tiny layer per set MYGIT_* variable so each value
// keeps its own origin.
func envLayers(getenv func(string) string) ([]*Layer, error) {
	keys := []struct{ env, key string }{
		{"MYGIT_PROFILE", "profile"},
		{"MYGIT_RULESET", "ruleset"},
		{"MYGIT_WORKFLOW", "workflow"},
		{"MYGIT_MODE", "execution.mode"},
		{"MYGIT_SHELL", "execution.shell"},
		{"MYGIT_AGENT_APPLY", "execution.agentApply"},
	}
	var out []*Layer
	for _, k := range keys {
		if v := strings.TrimSpace(getenv(k.env)); v != "" {
			out = append(out, valueLayer(ScopeEnv, "env "+k.env, map[string]any{k.key: v}))
		}
	}
	if v := strings.TrimSpace(getenv("MYGIT_RULES")); v != "" {
		rules, err := parseRuleList(strings.Split(v, ","))
		if err != nil {
			return nil, &Error{File: "env MYGIT_RULES", Msg: err.Error()}
		}
		out = append(out, rulesLayer(ScopeEnv, "env MYGIT_RULES", rules))
	}
	return out, nil
}

// flagLayers builds the per-run flag layers.
func flagLayers(f Flags) ([]*Layer, error) {
	vals := []struct{ flag, key, val string }{
		{"--profile", "profile", f.Profile},
		{"--ruleset", "ruleset", f.Ruleset},
		{"--workflow", "workflow", f.Workflow},
		{"--mode", "execution.mode", f.Mode},
		{"--shell", "execution.shell", f.Shell},
	}
	var out []*Layer
	for _, v := range vals {
		if v.val != "" {
			out = append(out, valueLayer(ScopeFlag, "flag "+v.flag, map[string]any{v.key: v.val}))
		}
	}
	if len(f.Rules) > 0 {
		rules, err := parseRuleList(f.Rules)
		if err != nil {
			return nil, &Error{File: "flag --rule", Msg: err.Error()}
		}
		out = append(out, rulesLayer(ScopeFlag, "flag --rule", rules))
	}
	return out, nil
}

func valueLayer(scope Scope, origin string, kv map[string]any) *Layer {
	doc := map[string]any{}
	for k, v := range kv {
		setPath(doc, k, v)
	}
	l := &Layer{Scope: scope, Origin: origin, Raw: doc}
	_ = redecode(doc, &l.File)
	return l
}

func rulesLayer(scope Scope, origin string, rules map[string]RuleOverride) *Layer {
	raw := map[string]any{}
	for id, ov := range rules {
		raw[id] = map[string]any{"severity": ov.Severity}
	}
	return &Layer{Scope: scope, Origin: origin, Raw: map[string]any{"rules": raw},
		File: File{Rules: rules}}
}

func parseRuleList(items []string) (map[string]RuleOverride, error) {
	out := map[string]RuleOverride{}
	for _, it := range items {
		if strings.TrimSpace(it) == "" {
			continue
		}
		id, ov, err := ParseRuleFlag(it)
		if err != nil {
			return nil, err
		}
		out[id] = ov
	}
	return out, nil
}

func (r *Resolved) merge() error {
	r.Raw, r.Origins = map[string]any{}, Origins{}
	for _, l := range r.Layers {
		mergeLayer(r.Raw, l.Raw, l.Origin, r.Origins)
	}
	if err := redecode(r.Raw, &r.File); err != nil {
		return fmt.Errorf("merged config: %w", err)
	}
	return nil
}

// validateMerged checks enumerations and cross references on the merged
// config; errors name the layer that set the bad value.
func (r *Resolved) validateMerged(contracts map[string]*policy.Contract) error {
	f := r.File
	enums := []struct {
		key, val string
		allowed  []string
	}{
		{"execution.mode", f.Execution.Mode, []string{"auto", "manual"}},
		{"execution.shell", f.Execution.Shell, []string{"auto", "powershell", "cmd", "gitbash", "bash", "zsh"}},
		{"execution.agentApply", f.Execution.AgentApply, []string{"ask", "allow"}},
		{"commit.convention", f.Commit.Convention, []string{"conventional", "none"}},
	}
	for _, e := range enums {
		if e.val != "" && !contains(e.allowed, e.val) {
			return &Error{File: r.Origins.Of(e.key), Field: e.key,
				Msg: fmt.Sprintf("%q is not one of %s", e.val, strings.Join(e.allowed, ", "))}
		}
	}
	idx := ruleIndex(contracts)
	for i, id := range r.file(ScopeGlobal).Locked {
		if idx[id] == nil {
			return &Error{File: r.layer(ScopeGlobal).Path, Field: fmt.Sprintf("locked[%d]", i),
				Msg: fmt.Sprintf("unknown rule id %q", id)}
		}
	}
	if f.Commit.HeaderMax < 0 {
		return &Error{File: r.Origins.Of("commit.headerMax"), Field: "commit.headerMax", Msg: "must not be negative"}
	}
	return nil
}

func (r *Resolved) selectProfile(in Inputs, where profile.Where) error {
	g := r.file(ScopeGlobal)
	r.ProfileName, r.ProfileReason = profile.Select(profile.SelectInput{
		Flag: in.Flags.Profile, Getenv: in.Getenv, Local: r.file(ScopeLocal).Profile,
		Match: g.Match, Default: g.DefaultProfile, Where: where,
	})
	p, err := profile.Lookup(r.File.Profiles, r.ProfileName, r.ProfileReason)
	if err != nil {
		return err
	}
	p, origins, err := profile.ApplyEnv(p, in.Getenv)
	if err != nil {
		return err
	}
	r.Profile, r.ProfileOrigins = p, origins
	return nil
}

func (r *Resolved) selectRuleset(in Inputs, where profile.Where) error {
	g := r.file(ScopeGlobal)
	mv, mr := profile.FindMatch(g.Match, where, func(m profile.Match) string { return m.Ruleset })
	c, _ := profile.First(
		profile.Choice{Value: in.Flags.Ruleset, Reason: "--ruleset flag"},
		profile.Choice{Value: strings.TrimSpace(in.Getenv("MYGIT_RULESET")), Reason: "env MYGIT_RULESET"},
		profile.Choice{Value: r.file(ScopeLocal).Ruleset, Reason: "local " + r.layerOrigin(ScopeLocal)},
		profile.Choice{Value: r.file(ScopeProject).Ruleset, Reason: "project " + r.layerOrigin(ScopeProject)},
		profile.Choice{Value: mv, Reason: mr},
		profile.Choice{Value: g.DefaultRuleset, Reason: "defaultRuleset"},
		profile.Choice{Value: DefaultRuleset, Reason: "built-in"},
	)
	r.RulesetName, r.RulesetReason = c.Value, c.Reason
	return nil
}

func (r *Resolved) selectWorkflow(in Inputs, where profile.Where) error {
	g := r.file(ScopeGlobal)
	mv, mr := profile.FindMatch(g.Match, where, func(m profile.Match) string { return m.Workflow })
	c, _ := profile.First(
		profile.Choice{Value: in.Flags.Workflow, Reason: "--workflow flag"},
		profile.Choice{Value: strings.TrimSpace(in.Getenv("MYGIT_WORKFLOW")), Reason: "env MYGIT_WORKFLOW"},
		profile.Choice{Value: r.file(ScopeLocal).Workflow, Reason: "local " + r.layerOrigin(ScopeLocal)},
		profile.Choice{Value: r.file(ScopeProject).Workflow, Reason: "project " + r.layerOrigin(ScopeProject)},
		profile.Choice{Value: mv, Reason: mr},
		profile.Choice{Value: g.DefaultWorkflow, Reason: "defaultWorkflow"},
		profile.Choice{Value: "default", Reason: "built-in"},
	)
	r.WorkflowName, r.WorkflowReason = c.Value, c.Reason
	cat, err := r.workflowCatalog(in)
	if err != nil {
		return err
	}
	res, err := workflow.Resolve(c.Value, cat)
	if err != nil {
		return fmt.Errorf("workflow (%s): %w", c.Reason, err)
	}
	r.Workflow, err = workflow.Compile(res, workflow.CompileOptions{CommitTypes: r.File.Commit.Types})
	return err
}

func (r *Resolved) layerOrigin(s Scope) string {
	if l := r.layer(s); l != nil {
		return l.Origin
	}
	return string(s)
}

// workflowCatalog collects templates from least to most specific scope:
// built-in, global files, global config, project files, project config, local.
func (r *Resolved) workflowCatalog(in Inputs) (*workflow.Catalog, error) {
	cat, err := workflow.Builtins(in.Defaults)
	if err != nil {
		return nil, err
	}
	addDir := func(dir string) error {
		if dir == "" || in.Glob == nil {
			return nil
		}
		files, err := in.Glob(dir + sep(in.GOOS) + "*.json")
		if err != nil {
			return err
		}
		sort.Strings(files)
		for _, f := range files {
			data, err := in.ReadFile(f)
			if err != nil {
				return &Error{File: f, Msg: err.Error()}
			}
			base := strings.TrimSuffix(path.Base(strings.ReplaceAll(f, `\`, "/")), ".json")
			if err := cat.AddJSON(data, Display(f, in.RepoRoot, in.Home), base); err != nil {
				return err
			}
		}
		return nil
	}
	addLayer := func(s Scope) {
		l := r.layer(s)
		if l == nil {
			return
		}
		wfs, _ := l.Raw["workflows"].(map[string]any)
		for _, name := range sortedKeys(wfs) {
			if doc, ok := wfs[name].(map[string]any); ok {
				cat.Add(workflow.Source{Name: name, Origin: l.Origin, Doc: doc})
			}
		}
	}
	if err := addDir(r.Paths.GlobalWorkflows); err != nil {
		return nil, err
	}
	addLayer(ScopeGlobal)
	if err := addDir(r.Paths.ProjectWorkflows); err != nil {
		return nil, err
	}
	addLayer(ScopeProject)
	addLayer(ScopeLocal)
	return cat, nil
}

func sep(goos string) string {
	if goos == "windows" {
		return `\`
	}
	return "/"
}

func contains(l []string, s string) bool {
	for _, x := range l {
		if x == s {
			return true
		}
	}
	return false
}

// resolveExecution fills defaults and turns shell "auto" into a concrete
// shell: powershell on Windows, else the base name of $SHELL when it is a
// supported shell, else bash.
func resolveExecution(e Execution, in Inputs) ExecutionSettings {
	out := ExecutionSettings{Mode: e.Mode, Shell: e.Shell, AgentApply: e.AgentApply}
	if out.Mode == "" {
		out.Mode = "auto"
	}
	if out.AgentApply == "" {
		out.AgentApply = "ask"
	}
	if out.Shell == "" || out.Shell == "auto" {
		out.Shell = autoShell(in.GOOS, in.Getenv("SHELL"))
	}
	return out
}

func autoShell(goos, shellEnv string) string {
	if goos == "windows" {
		return "powershell"
	}
	base := path.Base(strings.ReplaceAll(strings.TrimSpace(shellEnv), `\`, "/"))
	switch base {
	case "bash", "zsh":
		return base
	case "pwsh", "powershell":
		return "powershell"
	}
	return "bash"
}
