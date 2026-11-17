package app

import (
	"encoding/json"
	"path"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// WorkflowEntry is one known workflow template and every scope defining it.
type WorkflowEntry struct {
	Name    string   `json:"name"`
	Origin  string   `json:"origin"`            // the most specific definition
	Shadows []string `json:"shadows,omitempty"` // less specific definitions
	Active  bool     `json:"active"`
}

// WorkflowCatalog collects every workflow template with the same scope
// order config.Resolve uses: built-in, global files, global config,
// project files, project config, local config. It also returns the
// origins of every definition per name, least specific first.
func (e *Env) WorkflowCatalog() (*workflow.Catalog, map[string][]string, error) {
	cat, err := workflow.Builtins(defaults.FS)
	if err != nil {
		return nil, nil, err
	}
	origins := map[string][]string{}
	for _, n := range cat.Names() {
		origins[n] = []string{workflow.Builtin}
	}
	addDir := func(dir string) error {
		if dir == "" {
			return nil
		}
		files, err := e.Glob(dir + string(sepFor(e.GOOS)) + "*.json")
		if err != nil {
			return err
		}
		sort.Strings(files)
		for _, f := range files {
			data, err := e.ReadFile(f)
			if err != nil {
				return &config.Error{File: f, Msg: err.Error()}
			}
			base := strings.TrimSuffix(path.Base(strings.ReplaceAll(f, `\`, "/")), ".json")
			if err := cat.AddJSON(data, e.Display(f), base); err != nil {
				return err
			}
			var head struct {
				Name string `json:"name"`
			}
			_ = json.Unmarshal(data, &head) // AddJSON already validated it
			if head.Name == "" {
				head.Name = base
			}
			origins[head.Name] = append(origins[head.Name], e.Display(f))
		}
		return nil
	}
	addLayer := func(s config.Scope) {
		if e.Config == nil {
			return
		}
		for _, l := range e.Config.Layers {
			if l.Scope != s {
				continue
			}
			wfs, _ := l.Raw["workflows"].(map[string]any)
			names := make([]string, 0, len(wfs))
			for n := range wfs {
				names = append(names, n)
			}
			sort.Strings(names)
			for _, n := range names {
				if doc, ok := wfs[n].(map[string]any); ok {
					cat.Add(workflow.Source{Name: n, Origin: l.Origin, Doc: doc})
					origins[n] = append(origins[n], l.Origin)
				}
			}
		}
	}
	if err := addDir(e.Paths.GlobalWorkflows); err != nil {
		return nil, nil, err
	}
	addLayer(config.ScopeGlobal)
	if err := addDir(e.Paths.ProjectWorkflows); err != nil {
		return nil, nil, err
	}
	addLayer(config.ScopeProject)
	addLayer(config.ScopeLocal)
	return cat, origins, nil
}

// Workflows lists the known templates, marking the active one.
func (e *Env) Workflows() ([]WorkflowEntry, error) {
	cat, origins, err := e.WorkflowCatalog()
	if err != nil {
		return nil, err
	}
	active := ""
	if e.Config != nil {
		active = e.Config.WorkflowName
	}
	var out []WorkflowEntry
	for _, n := range cat.Names() {
		o := origins[n]
		ent := WorkflowEntry{Name: n, Active: n == active}
		if len(o) > 0 {
			ent.Origin = o[len(o)-1]
			for i := len(o) - 2; i >= 0; i-- {
				ent.Shadows = append(ent.Shadows, o[i])
			}
		}
		out = append(out, ent)
	}
	return out, nil
}

func sepFor(goos string) rune {
	if goos == "windows" {
		return '\\'
	}
	return '/'
}
