package config

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Get reads a dotted key from one config file's JSON. ok is false when the
// key is absent. Empty data counts as an empty file.
func Get(data []byte, key string) (value any, ok bool, err error) {
	doc, err := decodeDoc(data)
	if err != nil {
		return nil, false, err
	}
	v, ok := getPath(doc, key)
	return v, ok, nil
}

// Set returns data with the dotted key set to value, re-validated for scope
// (for example "profiles" in the project file is an error). The result is
// indented JSON with sorted keys; the caller writes it. Use ParseValue to
// turn command-line text into a value.
func Set(data []byte, scope Scope, key string, value any) ([]byte, error) {
	return edit(data, scope, key, func(parent map[string]any, last string) error {
		parent[last] = value
		return nil
	})
}

// Unset returns data with the dotted key removed. When the key (or any
// parent object on its path) is absent, data is returned unchanged.
func Unset(data []byte, scope Scope, key string) ([]byte, error) {
	doc, err := decodeDoc(data)
	if err != nil {
		return nil, err
	}
	if _, ok := getPath(doc, key); !ok {
		return append([]byte(nil), data...), nil
	}
	return edit(data, scope, key, func(parent map[string]any, last string) error {
		delete(parent, last)
		return nil
	})
}

func edit(data []byte, scope Scope, key string, fn func(map[string]any, string) error) ([]byte, error) {
	if strings.TrimSpace(key) == "" {
		return nil, fmt.Errorf("empty config key")
	}
	doc, err := decodeDoc(data)
	if err != nil {
		return nil, err
	}
	parts := strings.Split(key, ".")
	cur := doc
	for i, p := range parts[:len(parts)-1] {
		next, ok := cur[p]
		if !ok || next == nil {
			m := map[string]any{}
			cur[p] = m
			cur = m
			continue
		}
		m, ok := next.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s is not an object", strings.Join(parts[:i+1], "."))
		}
		cur = m
	}
	if err := fn(cur, parts[len(parts)-1]); err != nil {
		return nil, err
	}
	out, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return nil, err
	}
	out = append(out, '\n')
	if _, err := Parse(out, string(scope)+" config", scope); err != nil {
		return nil, err
	}
	return out, nil
}

func decodeDoc(data []byte) (map[string]any, error) {
	if len(strings.TrimSpace(string(data))) == 0 {
		return map[string]any{}, nil
	}
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("invalid JSON: %w", err)
	}
	if doc == nil {
		doc = map[string]any{}
	}
	return doc, nil
}

// ParseValue interprets command-line text: valid JSON (numbers, true/false,
// null, arrays, objects, quoted strings) is decoded, anything else is a
// plain string.
func ParseValue(s string) any {
	var v any
	if err := json.Unmarshal([]byte(s), &v); err == nil {
		return v
	}
	return s
}
