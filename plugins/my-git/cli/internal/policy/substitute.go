package policy

import (
	"regexp"
	"sort"
	"strings"
)

// varRef matches a ${var} reference. Unlike the Python original, dots are
// allowed so the dotted aliases (${github.user}) resolve too.
var varRef = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_.]*)\}`)

// Substitute replaces ${var} references in a params value with the profile
// variables in vars, with the semantics of the Python profile.substitute:
//
//   - a string that is exactly "${var}" becomes the value (string or list),
//     or is dropped (ok=false) when the variable is unset;
//   - a list item "${var}" is spliced in (lists flatten) and dropped when unset;
//   - "${var}" embedded in a longer string is substituted (regexp.QuoteMeta'd
//     when regex is true; lists join with ","), and the whole string is
//     dropped when any referenced variable is unset;
//   - maps recurse; a dropped map value is kept as nil (Python None).
//
// The returned bool is false when v itself was dropped.
func Substitute(v any, vars map[string]any, regex bool) (any, bool) {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			r, ok := Substitute(x, vars, regex)
			if !ok {
				r = nil
			}
			out[k] = r
		}
		return out, true
	case []any:
		return substituteList(t, vars, regex), true
	case []string:
		items := make([]any, len(t))
		for i, s := range t {
			items[i] = s
		}
		return substituteList(items, vars, regex), true
	case string:
		return substituteString(t, vars, regex)
	default:
		return v, true
	}
}

func substituteList(items []any, vars map[string]any, regex bool) []any {
	out := make([]any, 0, len(items))
	for _, item := range items {
		r, ok := Substitute(item, vars, regex)
		if !ok || r == nil {
			continue
		}
		switch l := r.(type) {
		case []any:
			out = append(out, l...)
		case []string:
			for _, s := range l {
				out = append(out, s)
			}
		default:
			out = append(out, r)
		}
	}
	return out
}

func substituteString(s string, vars map[string]any, regex bool) (any, bool) {
	if !strings.Contains(s, "${") {
		return s, true
	}
	if m := varRef.FindStringSubmatch(s); m != nil && m[0] == s {
		val, ok := lookupVar(vars, m[1])
		if !ok {
			return nil, false
		}
		return val, true
	}
	missing := false
	out := varRef.ReplaceAllStringFunc(s, func(ref string) string {
		name := varRef.FindStringSubmatch(ref)[1]
		val, ok := lookupVar(vars, name)
		if !ok {
			missing = true
			return ""
		}
		str := joinValue(val)
		if regex {
			return regexp.QuoteMeta(str)
		}
		return str
	})
	if missing {
		return nil, false
	}
	return out, true
}

// lookupVar returns the variable's value; nil, "" and empty lists count as unset.
func lookupVar(vars map[string]any, name string) (any, bool) {
	val, ok := vars[name]
	if !ok || val == nil {
		return nil, false
	}
	switch t := val.(type) {
	case string:
		return t, t != ""
	case []string:
		return t, len(t) > 0
	case []any:
		return t, len(t) > 0
	}
	return val, true
}

func joinValue(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case []string:
		return strings.Join(t, ",")
	case []any:
		parts := make([]string, 0, len(t))
		for _, x := range t {
			if s, ok := x.(string); ok {
				parts = append(parts, s)
			}
		}
		return strings.Join(parts, ",")
	}
	return ""
}

// IsRegexParam reports whether a rule param holds regular expressions, so
// substituted values must be escaped. It matches the Python REGEX_PARAMS set:
// every key containing "pattern", plus GH008's "placeholders".
func IsRegexParam(key string) bool {
	return strings.Contains(strings.ToLower(key), "pattern") || key == "placeholders"
}

// SubstituteParams applies Substitute to every value of a rule's params
// (regex escaping for IsRegexParam keys). Dropped values become nil, as in
// the Python implementation. A nil params map yields an empty map.
func SubstituteParams(params map[string]any, vars map[string]any) map[string]any {
	out := make(map[string]any, len(params))
	for k, v := range params {
		r, ok := Substitute(v, vars, IsRegexParam(k))
		if !ok {
			r = nil
		}
		out[k] = r
	}
	return out
}

// References returns every ${var} name used anywhere inside v, sorted.
func References(v any) []string {
	seen := map[string]bool{}
	var walk func(any)
	walk = func(x any) {
		switch t := x.(type) {
		case map[string]any:
			for _, y := range t {
				walk(y)
			}
		case []any:
			for _, y := range t {
				walk(y)
			}
		case []string:
			for _, y := range t {
				walk(y)
			}
		case string:
			for _, m := range varRef.FindAllStringSubmatch(t, -1) {
				seen[m[1]] = true
			}
		}
	}
	walk(v)
	out := make([]string, 0, len(seen))
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
