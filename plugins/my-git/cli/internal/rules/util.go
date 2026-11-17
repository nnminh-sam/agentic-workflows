// Package rules holds the four rule groups ported from scripts/gitguard:
// identity (ID001–ID006), commit-message (CM001–CM016), branching
// (BR001–BR005) and github-cli (GH001–GH009). Rules are pure: they read
// severities and params from a resolved *policy.Contract, repository facts
// through the Repo interface, message/body files through a ReadFile func and
// the process environment through a getenv func.
package rules

import (
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
)

// ReadFile reads a message or PR body file. A nil ReadFile makes every file
// unreadable (no I/O happens behind the caller's back).
type ReadFile func(path string) ([]byte, error)

func (rf ReadFile) read(path string) (string, bool) {
	if rf == nil {
		return "", false
	}
	b, err := rf(path)
	if err != nil {
		return "", false
	}
	return string(b), true
}

// --- Python re semantics on top of RE2. User patterns that RE2 cannot
// compile (lookarounds, backreferences) never match.

func compile(pat string, icase bool) *regexp.Regexp {
	if icase {
		pat = "(?i)" + pat
	}
	re, err := regexp.Compile(pat)
	if err != nil {
		return nil
	}
	return re
}

// reSearch is re.search.
func reSearch(pat, s string, icase bool) bool {
	re := compile(pat, icase)
	return re != nil && re.MatchString(s)
}

// reMatch is re.match (anchored at the start).
func reMatch(pat, s string, icase bool) bool { return reSearch(`^(?:`+pat+`)`, s, icase) }

// reFullmatch is re.fullmatch.
func reFullmatch(pat, s string, icase bool) bool { return reSearch(`^(?:`+pat+`)$`, s, icase) }

func anySearch(pats []string, s string, icase bool) bool {
	for _, p := range pats {
		if reSearch(p, s, icase) {
			return true
		}
	}
	return false
}

// pyList renders a list like Python's repr: ['a', 'b'].
func pyList(items []string) string {
	parts := make([]string, len(items))
	for i, s := range items {
		parts[i] = pyStr(s)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

func pyStr(s string) string {
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		return `"` + strings.ReplaceAll(s, `\`, `\\`) + `"`
	}
	return "'" + strings.ReplaceAll(strings.ReplaceAll(s, `\`, `\\`), "'", `\'`) + "'"
}

// orNone renders an optional value like Python's f"{x}" for None.
func orNone(s string, ok bool) string {
	if !ok {
		return "None"
	}
	return s
}

func runeLen(s string) int { return utf8.RuneCountInString(s) }

// runePrefix is s[:n] in code points.
func runePrefix(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func pyStrip(s string) string { return strings.TrimFunc(s, unicode.IsSpace) }

func lowerAll(xs []string) []string {
	out := make([]string, len(xs))
	for i, x := range xs {
		out[i] = strings.ToLower(x)
	}
	return out
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

// nonEmpty drops empty strings (unset profile variables substituted into lists).
func nonEmpty(xs []string) []string {
	var out []string
	for _, x := range xs {
		if x != "" {
			out = append(out, x)
		}
	}
	return out
}

func settings(c *policy.Contract) map[string]any {
	if c == nil {
		return nil
	}
	return c.Settings
}

// lookup merges command-line env overrides over the process env, like
// Python's {**process_env, **call.env}.get(key).
func lookup(cmdEnv map[string]string, getenv func(string) string, key string) string {
	if v, ok := cmdEnv[key]; ok {
		return v
	}
	if getenv == nil {
		return ""
	}
	return getenv(key)
}

// HomeDir returns the home directory from the environment (HOME, then USERPROFILE).
func HomeDir(getenv func(string) string) string {
	if getenv == nil {
		return ""
	}
	if h := getenv("HOME"); h != "" {
		return h
	}
	return getenv("USERPROFILE")
}

// expandUser is os.path.expanduser for ~ and ~/... .
func expandUser(p, home string) string {
	if home == "" || !strings.HasPrefix(p, "~") {
		return p
	}
	if p == "~" {
		return home
	}
	if p[1] == '/' || p[1] == '\\' {
		return home + p[1:]
	}
	return p
}

// absUnder joins a relative path to cwd.
func absUnder(cwd, p string) string {
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(cwd, p)
}

// shortOptValue handles clustered short options like -am or -mfoo:
// (has letter, inline value, has inline value).
func shortOptValue(arg string, letter byte) (bool, string, bool) {
	if !strings.HasPrefix(arg, "-") || strings.HasPrefix(arg, "--") || len(arg) < 2 {
		return false, "", false
	}
	body := arg[1:]
	idx := strings.IndexByte(body, letter)
	if idx < 0 {
		return false, "", false
	}
	for _, ch := range body[:idx] {
		if !unicode.IsLetter(ch) {
			return false, "", false
		}
	}
	tail := body[idx+1:]
	return true, tail, tail != ""
}

// hasFlag is _has: any arg equal to a flag or starting with flag=.
func hasFlag(argv []string, flags ...string) bool {
	for _, a := range argv {
		for _, f := range flags {
			if a == f || strings.HasPrefix(a, f+"=") {
				return true
			}
		}
	}
	return false
}
