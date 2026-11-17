package config

import (
	"sort"
	"strings"
)

// Origins maps a dotted key path of the merged config ("commit.headerMax",
// "rules.CM009.severity") to the layer that set it ("built-in", a display
// path, "env MYGIT_X", "flag --x"). Only leaves are recorded: scalars,
// arrays and empty objects.
type Origins map[string]string

// Of returns the origin of key, or of its closest recorded ancestor or
// descendant (for an object key, the origin of its first leaf).
func (o Origins) Of(key string) string {
	if v, ok := o[key]; ok {
		return v
	}
	for k := key; strings.Contains(k, "."); {
		k = k[:strings.LastIndex(k, ".")]
		if v, ok := o[k]; ok {
			return v
		}
	}
	var keys []string
	for k := range o {
		if strings.HasPrefix(k, key+".") {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	if len(keys) > 0 {
		return o[keys[0]]
	}
	return ""
}

// replaced reports whether the object at path replaces instead of merging:
// named rule sets and workflow templates are whole definitions, and the
// most specific scope wins on a name clash.
func replaced(path string) bool {
	parts := strings.Split(path, ".")
	return len(parts) == 2 && (parts[0] == "rulesets" || parts[0] == "workflows")
}

// mergeLayer deep-merges src into dst (objects merge key by key, arrays and
// scalars replace) and records the origin of every leaf it writes.
func mergeLayer(dst, src map[string]any, origin string, origins Origins) {
	mergeAt(dst, src, "", origin, origins)
}

func mergeAt(dst, src map[string]any, prefix, origin string, origins Origins) {
	for _, k := range sortedKeys(src) {
		v := src[k]
		p := joinField(prefix, k)
		sm, sok := v.(map[string]any)
		dm, dok := dst[k].(map[string]any)
		if sok && dok && !replaced(p) {
			mergeAt(dm, sm, p, origin, origins)
			continue
		}
		clearOrigins(origins, p)
		dst[k] = cloneValue(v)
		recordLeaves(origins, p, v, origin)
	}
}

func clearOrigins(origins Origins, p string) {
	delete(origins, p)
	for k := range origins {
		if strings.HasPrefix(k, p+".") {
			delete(origins, k)
		}
	}
}

func recordLeaves(origins Origins, p string, v any, origin string) {
	if m, ok := v.(map[string]any); ok && len(m) > 0 {
		for k, x := range m {
			recordLeaves(origins, p+"."+k, x, origin)
		}
		return
	}
	origins[p] = origin
}

func cloneValue(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			out[k] = cloneValue(x)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = cloneValue(x)
		}
		return out
	}
	return v
}

// deepMerge returns base with over merged in (objects merge, rest replaces).
func deepMerge(base, over map[string]any) map[string]any {
	out, _ := cloneValue(base).(map[string]any)
	if out == nil {
		out = map[string]any{}
	}
	for k, v := range over {
		bm, bok := out[k].(map[string]any)
		om, ook := v.(map[string]any)
		if bok && ook {
			out[k] = deepMerge(bm, om)
		} else {
			out[k] = cloneValue(v)
		}
	}
	return out
}

// setPath sets a dotted key in doc, creating objects on the way.
func setPath(doc map[string]any, key string, v any) {
	parts := strings.Split(key, ".")
	cur := doc
	for _, p := range parts[:len(parts)-1] {
		next, ok := cur[p].(map[string]any)
		if !ok {
			next = map[string]any{}
			cur[p] = next
		}
		cur = next
	}
	cur[parts[len(parts)-1]] = v
}

// getPath reads a dotted key from doc.
func getPath(doc map[string]any, key string) (any, bool) {
	var cur any = doc
	for _, p := range strings.Split(key, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, false
		}
		if cur, ok = m[p]; !ok {
			return nil, false
		}
	}
	return cur, true
}
