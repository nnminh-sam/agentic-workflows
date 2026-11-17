package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// Scope is a config layer.
type Scope string

// Layers, from least to most specific.
const (
	ScopeBuiltin Scope = "built-in"
	ScopeGlobal  Scope = "global"
	ScopeProject Scope = "project"
	ScopeLocal   Scope = "local"
	ScopeEnv     Scope = "env"
	ScopeFlag    Scope = "flag"
)

// ParseScope parses "global", "project" or "local".
func ParseScope(s string) (Scope, error) {
	switch Scope(s) {
	case ScopeGlobal, ScopeProject, ScopeLocal:
		return Scope(s), nil
	}
	return "", fmt.Errorf("unknown config scope %q (want global, project or local)", s)
}

// forbidden lists the top-level keys each file scope may not set, with the
// reason shown in the error.
func forbidden(scope Scope) map[string]string {
	const onlyGlobal = "only allowed in the global config"
	switch scope {
	case ScopeGlobal:
		return map[string]string{
			"profile":  "the global config selects with defaultProfile or match",
			"ruleset":  "the global config selects with defaultRuleset or match",
			"workflow": "the global config selects with defaultWorkflow or match",
		}
	case ScopeProject:
		return map[string]string{
			"profiles":        "profiles name people and are not allowed in the committed project config; put them in the global or local config",
			"profile":         "the profile is personal; set it in .mygit.local.json",
			"match":           onlyGlobal,
			"locked":          onlyGlobal,
			"defaultProfile":  onlyGlobal,
			"defaultRuleset":  onlyGlobal,
			"defaultWorkflow": onlyGlobal,
		}
	case ScopeLocal:
		return map[string]string{
			"match":           onlyGlobal,
			"locked":          onlyGlobal,
			"defaultProfile":  onlyGlobal,
			"defaultRuleset":  onlyGlobal,
			"defaultWorkflow": onlyGlobal,
		}
	}
	return nil
}

// Layer is one parsed config file (or the built-in / env / flag layer).
type Layer struct {
	Scope Scope
	// Path is the file path ("" for built-in, env and flag layers).
	Path string
	// Origin labels values from this layer: "built-in", a display path,
	// "env MYGIT_X" or "flag --x".
	Origin string
	// Raw is the decoded JSON with rule shorthands normalised to objects.
	Raw  map[string]any
	File File
}

// Parse decodes and validates one config file. name is used in errors (a
// path). Unknown keys, wrong types and keys the scope may not set are
// errors carrying the file, field and line.
func Parse(data []byte, name string, scope Scope) (*Layer, error) {
	var raw any
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, syntaxError(data, name, err)
	}
	doc, ok := raw.(map[string]any)
	if !ok {
		return nil, &Error{File: name, Line: 1, Msg: "config must be a JSON object"}
	}
	if err := checkKeys(doc, reflect.TypeOf(File{}), ""); err != nil {
		var e *Error
		if errors.As(err, &e) {
			e.File, e.Line = name, lineOfField(data, e.Field)
		}
		return nil, err
	}
	if err := checkScope(doc, scope); err != nil {
		err.File, err.Line = name, lineOfField(data, err.Field)
		return nil, err
	}
	if err := checkValues(doc); err != nil {
		err.File, err.Line = name, lineOfField(data, err.Field)
		return nil, err
	}
	var f File
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, decodeError(data, name, err)
	}
	normalizeRules(doc)
	return &Layer{Scope: scope, Path: name, Origin: name, Raw: doc, File: f}, nil
}

func syntaxError(data []byte, name string, err error) error {
	var se *json.SyntaxError
	if errors.As(err, &se) {
		return &Error{File: name, Line: lineAt(data, se.Offset), Msg: "invalid JSON: " + se.Error()}
	}
	return &Error{File: name, Msg: "invalid JSON: " + err.Error()}
}

func decodeError(data []byte, name string, err error) error {
	var te *json.UnmarshalTypeError
	if errors.As(err, &te) {
		return &Error{File: name, Field: te.Field, Line: lineAt(data, te.Offset),
			Msg: fmt.Sprintf("expected %s, got JSON %s", te.Type, te.Value)}
	}
	// Errors from custom UnmarshalJSON (rules, types, to) carry no position.
	return &Error{File: name, Msg: err.Error()}
}

func checkScope(doc map[string]any, scope Scope) *Error {
	bad := forbidden(scope)
	keys := sortedKeys(doc)
	for _, k := range keys {
		if why, ok := bad[k]; ok {
			return &Error{Field: k, Msg: fmt.Sprintf("not allowed in the %s config: %s", scope, why)}
		}
	}
	return nil
}

// checkKeys walks raw against the Go type t and reports the first unknown
// object key (sorted order, so errors are stable).
func checkKeys(raw any, t reflect.Type, field string) error {
	u := reflect.TypeOf((*json.Unmarshaler)(nil)).Elem()
	if reflect.PointerTo(t).Implements(u) || t.Implements(u) {
		return nil // validated by its own UnmarshalJSON
	}
	switch t.Kind() {
	case reflect.Pointer:
		return checkKeys(raw, t.Elem(), field)
	case reflect.Struct:
		obj, ok := raw.(map[string]any)
		if !ok {
			return nil // a type error, reported by json.Unmarshal
		}
		fields := jsonFields(t)
		for _, k := range sortedKeys(obj) {
			ft, ok := fields[k]
			if !ok {
				return &Error{Field: joinField(field, k), Msg: fmt.Sprintf("unknown field %q%s", k, suggest(k, fields))}
			}
			if err := checkKeys(obj[k], ft, joinField(field, k)); err != nil {
				return err
			}
		}
	case reflect.Map:
		obj, ok := raw.(map[string]any)
		if !ok {
			return nil
		}
		for _, k := range sortedKeys(obj) {
			if err := checkKeys(obj[k], t.Elem(), joinField(field, k)); err != nil {
				return err
			}
		}
	case reflect.Slice:
		list, ok := raw.([]any)
		if !ok {
			return nil
		}
		for i, x := range list {
			if err := checkKeys(x, t.Elem(), fmt.Sprintf("%s[%d]", field, i)); err != nil {
				return err
			}
		}
	}
	return nil
}

func jsonFields(t reflect.Type) map[string]reflect.Type {
	out := map[string]reflect.Type{}
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		name := strings.Split(f.Tag.Get("json"), ",")[0]
		if name == "-" || !f.IsExported() {
			continue
		}
		if name == "" {
			name = f.Name
		}
		out[name] = f.Type
	}
	return out
}

// suggest returns ` (did you mean "x"?)` for a case-insensitive near match.
func suggest(k string, fields map[string]reflect.Type) string {
	for name := range fields {
		if strings.EqualFold(name, k) {
			return fmt.Sprintf(" (did you mean %q?)", name)
		}
	}
	return ""
}

func joinField(prefix, k string) string {
	if prefix == "" {
		return k
	}
	return prefix + "." + k
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// normalizeRules rewrites "ID": "sev" as {"severity": "sev"} in the top-level
// rules map and in every rule set, so layers merge per rule id.
func normalizeRules(doc map[string]any) {
	normalizeRuleMap(doc["rules"])
	if sets, ok := doc["rulesets"].(map[string]any); ok {
		for _, s := range sets {
			if m, ok := s.(map[string]any); ok {
				normalizeRuleMap(m["rules"])
			}
		}
	}
}

func normalizeRuleMap(v any) {
	rules, ok := v.(map[string]any)
	if !ok {
		return
	}
	for id, r := range rules {
		if s, ok := r.(string); ok {
			rules[id] = map[string]any{"severity": s}
		}
	}
}

// checkValues decodes the values with custom JSON forms one by one, so a
// bad rule override, profile or workflow template is reported with its field.
func checkValues(doc map[string]any) *Error {
	if err := checkRuleMap(doc["rules"], "rules"); err != nil {
		return err
	}
	if sets, ok := doc["rulesets"].(map[string]any); ok {
		for _, name := range sortedKeys(sets) {
			if m, ok := sets[name].(map[string]any); ok {
				if err := checkRuleMap(m["rules"], "rulesets."+name+".rules"); err != nil {
					return err
				}
			}
		}
	}
	if profs, ok := doc["profiles"].(map[string]any); ok {
		for _, name := range sortedKeys(profs) {
			var p profile.Profile
			field := "profiles." + name
			if err := redecode(profs[name], &p); err != nil {
				return &Error{Field: field, Msg: err.Error()}
			}
			if err := p.Validate(); err != nil {
				return &Error{Field: field, Msg: err.Error()}
			}
		}
	}
	if wfs, ok := doc["workflows"].(map[string]any); ok {
		for _, name := range sortedKeys(wfs) {
			var t workflow.Template
			if err := redecode(wfs[name], &t); err != nil {
				return &Error{Field: "workflows." + name, Msg: err.Error()}
			}
		}
	}
	return nil
}

func checkRuleMap(v any, field string) *Error {
	rules, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	for _, id := range sortedKeys(rules) {
		var r RuleOverride
		if err := redecode(rules[id], &r); err != nil {
			return &Error{Field: field + "." + id, Msg: err.Error()}
		}
	}
	return nil
}

// redecode converts a decoded JSON value into v.
func redecode(raw any, v any) error {
	data, err := json.Marshal(raw)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}
