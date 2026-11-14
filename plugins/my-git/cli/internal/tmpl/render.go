package tmpl

import (
	"encoding/json"
	"fmt"
	"strings"
)

// CommitData fills the commit message template.
type CommitData struct {
	Type     string   // feat, fix, …
	Scope    string   // optional
	Breaking bool     // adds "!" after type(scope)
	Subject  string   // imperative summary
	Body     string   // optional, free text
	Trailers []string // "Key: value" lines, e.g. "Refs: #12"
}

// RenderCommit renders a commit message from the built-in template, or
// from customPath when it is non-empty. The result is tidied (no runs of
// blank lines, one trailing newline); callers still validate it with the
// commit-message rules.
func (s *Set) RenderCommit(d CommitData, customPath string) (string, error) {
	out, err := s.render(CommitTemplate, customPath, d)
	if err != nil {
		return "", err
	}
	return tidy(out), nil
}

// PR body kinds.
const (
	PRDefault = "default"
	PRRelease = "release"
)

// PRSection is one "## Name" section of a PR body. Content is pre-filled
// text (e.g. real test output); empty content renders a placeholder hint
// that rule GH008 rejects until someone fills it in.
type PRSection struct {
	Name    string
	Content string
}

// PRData fills the PR body template.
type PRData struct {
	Head     string // head branch
	Base     string // base branch
	Version  string // release version or date (release kind)
	Sections []PRSection
}

// DefaultSections returns the built-in section names, the ones rule GH008
// requires by default (the same for "default" and "release").
func DefaultSections() []string {
	return []string{"Summary", "Detailed changes", "Explanation", "Test results"}
}

// Sections builds PRSections from names, taking content from filled
// (keyed by section name, case-insensitive) when present.
func Sections(names []string, filled map[string]string) []PRSection {
	out := make([]PRSection, len(names))
	for i, n := range names {
		out[i] = PRSection{Name: n}
		for k, v := range filled {
			if strings.EqualFold(k, n) {
				out[i].Content = strings.TrimSpace(v)
			}
		}
	}
	return out
}

// RenderPRBody renders a PR body of kind PRDefault or PRRelease, or from
// customPath when it is non-empty.
func (s *Set) RenderPRBody(kind string, d PRData, customPath string) (string, error) {
	builtin := PRDefaultTemplate
	switch kind {
	case PRDefault, "":
	case PRRelease:
		builtin = PRReleaseTemplate
	default:
		return "", fmt.Errorf("unknown pr body kind %q (want default|release)", kind)
	}
	out, err := s.render(builtin, customPath, d)
	if err != nil {
		return "", err
	}
	return tidy(out), nil
}

// HookNames are the git hooks mygit installs as shims.
func HookNames() []string { return []string{"commit-msg", "pre-commit", "pre-push"} }

// DefaultInstallHint is printed by a shim when mygit is not on PATH.
const DefaultInstallHint = "go install github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/cmd/mygit@latest"

// HookData fills the hook shim template.
type HookData struct {
	Name        string // commit-msg | pre-commit | pre-push
	InstallHint string // "" = DefaultInstallHint
}

// RenderHook renders the sh shim for one git hook. The shim forwards to
// `mygit hook <name>` and fails open (prints an install hint, exits 0)
// when mygit is not on PATH. Output always uses LF line endings.
func (s *Set) RenderHook(d HookData) (string, error) {
	if !isHook(d.Name) {
		return "", fmt.Errorf("unknown hook %q (want %s)", d.Name, strings.Join(HookNames(), "|"))
	}
	if d.InstallHint == "" {
		d.InstallHint = DefaultInstallHint
	}
	return s.render(HookShimTemplate, "", d)
}

func isHook(name string) bool {
	for _, h := range HookNames() {
		if h == name {
			return true
		}
	}
	return false
}

// RenderAgentsMD renders the AGENTS.md block (between mygit:begin/end
// markers) telling agents to use mygit commit and mygit pr create.
func (s *Set) RenderAgentsMD() (string, error) {
	return s.render(AgentsMDTemplate, "", nil)
}

// ClaudeHooksData fills the Claude Code hooks.json template.
type ClaudeHooksData struct {
	Command string // mygit executable ("" = "mygit"); double-quoted if it has spaces
	Timeout int    // seconds (0 = 30)
}

// RenderClaudeHooks renders hooks.json entries: PreToolUse (matcher
// "Bash|PowerShell") runs `mygit guard --agent claude`, SessionStart
// (matcher "startup|resume|clear") adds --session-start. The output is
// checked to be valid JSON.
func (s *Set) RenderClaudeHooks(d ClaudeHooksData) (string, error) {
	if d.Command == "" {
		d.Command = "mygit"
	}
	if strings.ContainsAny(d.Command, " \t") && !strings.HasPrefix(d.Command, `"`) {
		d.Command = `"` + d.Command + `"` // a path with spaces must stay one word
	}
	if d.Timeout <= 0 {
		d.Timeout = 30
	}
	out, err := s.render(ClaudeHooksJSON, "", d)
	if err != nil {
		return "", err
	}
	if !json.Valid([]byte(out)) {
		return "", fmt.Errorf("template %s: rendered invalid json", ClaudeHooksJSON)
	}
	return out, nil
}
