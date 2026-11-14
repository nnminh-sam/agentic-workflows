package workflow

import (
	"fmt"
	"regexp"
	"strings"
)

// Character classes for the placeholders of branch-name patterns.
const (
	nameClass    = `[a-z0-9][a-z0-9._-]*`
	ticketClass  = `[A-Za-z][A-Za-z0-9]*-[0-9]+`
	versionClass = `v?[0-9]+(\.[0-9]+)*(-[0-9A-Za-z.-]+)?`
)

// placeholderClass returns the regex for a placeholder (types fill {type}).
func placeholderClass(name string, types []string) (string, error) {
	switch name {
	case "name":
		return nameClass, nil
	case "ticket":
		return ticketClass, nil
	case "version":
		return versionClass, nil
	case "type":
		if len(types) == 0 {
			return "", fmt.Errorf("{type} used but no types are defined")
		}
		quoted := make([]string, len(types))
		for i, t := range types {
			quoted[i] = regexp.QuoteMeta(t)
		}
		return "(" + strings.Join(quoted, "|") + ")", nil
	}
	return "", fmt.Errorf("unknown placeholder {%s} (want {type}, {name}, {ticket} or {version})", name)
}

// splitPattern yields the pattern as alternating literal and placeholder parts.
// isVar[i] reports whether parts[i] is a placeholder name.
func splitPattern(p string) (parts []string, isVar []bool, err error) {
	for p != "" {
		open := strings.IndexByte(p, '{')
		if open < 0 {
			parts, isVar = append(parts, p), append(isVar, false)
			break
		}
		end := strings.IndexByte(p[open:], '}')
		if end < 0 {
			return nil, nil, fmt.Errorf("unclosed '{' in %q", p)
		}
		if open > 0 {
			parts, isVar = append(parts, p[:open]), append(isVar, false)
		}
		parts, isVar = append(parts, p[open+1:open+end]), append(isVar, true)
		p = p[open+end+1:]
	}
	return parts, isVar, nil
}

// PatternRegex converts a branch-name pattern such as develop/{type}/{name}
// into an anchored regular expression, e.g.
// ^develop/(feat|fix)/[a-z0-9][a-z0-9._-]*$.
func PatternRegex(pattern string, types []string) (string, error) {
	parts, isVar, err := splitPattern(pattern)
	if err != nil {
		return "", err
	}
	var b strings.Builder
	b.WriteString("^")
	for i, part := range parts {
		if !isVar[i] {
			b.WriteString(regexp.QuoteMeta(part))
			continue
		}
		class, err := placeholderClass(part, types)
		if err != nil {
			return "", err
		}
		b.WriteString(class)
	}
	b.WriteString("$")
	return b.String(), nil
}

// Expand fills a pattern's placeholders from values (e.g. tag "v{version}").
// Unknown placeholders are left as they are.
func Expand(pattern string, values map[string]string) string {
	parts, isVar, err := splitPattern(pattern)
	if err != nil {
		return pattern
	}
	var b strings.Builder
	for i, part := range parts {
		if v, ok := values[part]; isVar[i] && ok {
			b.WriteString(v)
		} else if isVar[i] {
			b.WriteString("{" + part + "}")
		} else {
			b.WriteString(part)
		}
	}
	return b.String()
}

func hasPlaceholder(s string) bool { return strings.Contains(s, "{") }

// checkPlaceholders reports syntax errors and unknown placeholder names.
func checkPlaceholders(p string) error {
	parts, isVar, err := splitPattern(p)
	if err != nil {
		return err
	}
	for i, part := range parts {
		if isVar[i] {
			if _, err := placeholderClass(part, []string{"x"}); err != nil {
				return err
			}
		}
	}
	return nil
}
