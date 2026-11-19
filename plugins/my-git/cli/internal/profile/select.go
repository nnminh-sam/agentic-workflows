package profile

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Match is one entry of the global "match" list. A rule matches when every
// condition it sets matches (remoteOwner and/or path). It may pick a
// profile, a rule set and a workflow at once.
type Match struct {
	RemoteOwner string `json:"remoteOwner,omitempty"`
	// Path is a glob with ~ (home), * (one path segment) and ** (any depth).
	Path     string `json:"path,omitempty"`
	Profile  string `json:"profile,omitempty"`
	Ruleset  string `json:"ruleset,omitempty"`
	Workflow string `json:"workflow,omitempty"`
}

// Where is the repository a match is evaluated against.
type Where struct {
	RemoteOwner string // owner of the origin remote, "" if unknown
	Dir         string // repo root, or cwd outside a repo
	Home        string
	GOOS        string
}

// FindMatch returns the first rule whose pick(rule) is non-empty and that
// matches: rules with a remoteOwner are tried first, then path-only rules.
// reason reads like "match remoteOwner=acme" or "match path=~/work/**".
func FindMatch(rules []Match, w Where, pick func(Match) string) (value, reason string) {
	for _, byOwner := range []bool{true, false} {
		for _, m := range rules {
			if pick(m) == "" || (m.RemoteOwner != "") != byOwner || !m.matches(w) {
				continue
			}
			return pick(m), m.reason()
		}
	}
	return "", ""
}

func (m Match) matches(w Where) bool {
	if m.RemoteOwner == "" && m.Path == "" {
		return false
	}
	if m.RemoteOwner != "" && !strings.EqualFold(m.RemoteOwner, w.RemoteOwner) {
		return false
	}
	if m.Path != "" && !PathMatch(m.Path, w.Dir, w.Home, w.GOOS) {
		return false
	}
	return true
}

func (m Match) reason() string {
	var parts []string
	if m.RemoteOwner != "" {
		parts = append(parts, "remoteOwner="+m.RemoteOwner)
	}
	if m.Path != "" {
		parts = append(parts, "path="+m.Path)
	}
	return "match " + strings.Join(parts, " ")
}

// PathMatch reports whether dir matches glob. "~" expands to home, "*"
// matches within one path segment, "**" matches any number of segments, and
// a trailing "/**" also matches the directory itself. On Windows separators
// are normalised and matching is case-insensitive.
func PathMatch(glob, dir, home, goos string) bool {
	if dir == "" {
		return false
	}
	if glob == "~" || strings.HasPrefix(glob, "~/") || strings.HasPrefix(glob, `~\`) {
		glob = home + glob[1:]
	}
	glob, dir = normPath(glob, goos), normPath(dir, goos)
	re, err := regexp.Compile(globRegex(glob, goos == "windows"))
	return err == nil && re.MatchString(dir)
}

func normPath(p, goos string) string {
	if goos == "windows" {
		p = strings.ReplaceAll(p, `\`, "/")
	}
	if len(p) > 1 {
		p = strings.TrimRight(p, "/")
	}
	return p
}

func globRegex(glob string, fold bool) string {
	var b strings.Builder
	if fold {
		b.WriteString("(?i)")
	}
	b.WriteString("^")
	for i := 0; i < len(glob); i++ {
		switch {
		case strings.HasPrefix(glob[i:], "/**") && i+3 == len(glob):
			b.WriteString("(/.*)?")
			i += 2
		case strings.HasPrefix(glob[i:], "**/"):
			b.WriteString("(.*/)?")
			i += 2
		case strings.HasPrefix(glob[i:], "**"):
			b.WriteString(".*")
			i++
		case glob[i] == '*':
			b.WriteString("[^/]*")
		case glob[i] == '?':
			b.WriteString("[^/]")
		default:
			b.WriteString(regexp.QuoteMeta(string(glob[i])))
		}
	}
	b.WriteString("$")
	return b.String()
}

// Choice is one source in a selection chain, checked in order.
type Choice struct {
	Value  string
	Reason string
}

// First returns the first choice with a non-empty value.
func First(choices ...Choice) (Choice, bool) {
	for _, c := range choices {
		if c.Value != "" {
			return c, true
		}
	}
	return Choice{}, false
}

// SelectInput is everything Select looks at.
type SelectInput struct {
	Flag    string              // --profile
	Getenv  func(string) string // for MYGIT_PROFILE
	Local   string              // "profile" in .mygit.local.json
	Match   []Match             // global match rules
	Default string              // global defaultProfile
	Where   Where
}

// Select returns the active profile name and why it was chosen. First hit
// wins: --profile → MYGIT_PROFILE → local "profile" → global match
// (remoteOwner first, then path) → defaultProfile. An empty name means no
// profile: the identity rules fall back to presence checks.
func Select(in SelectInput) (name, reason string) {
	env := ""
	if in.Getenv != nil {
		env = strings.TrimSpace(in.Getenv("MYGIT_PROFILE"))
	}
	mv, mr := FindMatch(in.Match, in.Where, func(m Match) string { return m.Profile })
	c, ok := First(
		Choice{in.Flag, "--profile flag"},
		Choice{env, "env MYGIT_PROFILE"},
		Choice{in.Local, "local .mygit.local.json"},
		Choice{mv, mr},
		Choice{in.Default, "defaultProfile"},
	)
	if !ok {
		return "", "no profile selected"
	}
	return c.Value, c.Reason
}

// Lookup returns the named profile from profiles, or an error naming the
// known ones. The empty name yields the zero profile.
func Lookup(profiles map[string]Profile, name, reason string) (Profile, error) {
	if name == "" {
		return Profile{}, nil
	}
	p, ok := profiles[name]
	if !ok {
		known := make([]string, 0, len(profiles))
		for k := range profiles {
			known = append(known, k)
		}
		return Profile{}, fmt.Errorf("profile %q (%s) is not defined; known profiles: %s",
			name, reason, strings.Join(sorted(known), ", "))
	}
	return p, nil
}

func sorted(s []string) []string {
	sort.Strings(s)
	return s
}
