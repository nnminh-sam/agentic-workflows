// Package tmpl renders mygit's text/template files: the commit message, the
// PR body, git hook shims and agent snippets. Built-in templates come from
// defaults.FS; a project may point commit.template / pr.template at its own
// file, read through an injected ReadFile.
package tmpl

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"regexp"
	"strings"
	"text/template"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
)

// Built-in template paths inside defaults.FS.
const (
	CommitTemplate    = "templates/commit.tmpl"
	PRDefaultTemplate = "templates/pr-default.md.tmpl"
	PRReleaseTemplate = "templates/pr-release.md.tmpl"
	HookShimTemplate  = "templates/hooks/shim.sh.tmpl"
	AgentsMDTemplate  = "templates/agents/AGENTS.md.tmpl"
	ClaudeHooksJSON   = "templates/agents/claude-hooks.json.tmpl"
)

// Set renders templates from a built-in FS and, for custom paths, from disk.
type Set struct {
	FS       fs.FS                        // built-in templates (defaults.FS)
	ReadFile func(string) ([]byte, error) // reads custom template files
}

// New returns a Set over fsys and readFile.
func New(fsys fs.FS, readFile func(string) ([]byte, error)) *Set {
	return &Set{FS: fsys, ReadFile: readFile}
}

// Default returns a Set over the embedded defaults and os.ReadFile.
func Default() *Set { return New(defaults.FS, os.ReadFile) }

// funcMap holds the helpers available to every template.
func funcMap() template.FuncMap {
	return template.FuncMap{
		"json": func(v any) (string, error) {
			b, err := json.Marshal(v)
			return string(b), err
		},
		"shq":   shellQuote,
		"join":  strings.Join,
		"trim":  strings.TrimSpace,
		"lower": strings.ToLower,
	}
}

// load parses the custom template at customPath, or the built-in one.
func (s *Set) load(builtin, customPath string) (*template.Template, error) {
	var (
		src  []byte
		err  error
		name = builtin
	)
	if customPath != "" {
		name = customPath
		src, err = s.ReadFile(customPath)
	} else {
		src, err = fs.ReadFile(s.FS, builtin)
	}
	if err != nil {
		return nil, fmt.Errorf("template %s: %w", name, err)
	}
	t, err := template.New(name).Funcs(funcMap()).Option("missingkey=error").Parse(string(src))
	if err != nil {
		return nil, fmt.Errorf("template %s: %w", name, err)
	}
	return t, nil
}

// render executes the template and returns its output.
func (s *Set) render(builtin, customPath string, data any) (string, error) {
	t, err := s.load(builtin, customPath)
	if err != nil {
		return "", err
	}
	var b bytes.Buffer
	if err := t.Execute(&b, data); err != nil {
		return "", fmt.Errorf("template %s: %w", t.Name(), err)
	}
	return b.String(), nil
}

var blankRuns = regexp.MustCompile(`\n{3,}`)

// tidy trims trailing spaces on each line, collapses runs of blank lines to
// one, and ends the text with exactly one newline.
func tidy(s string) string {
	lines := strings.Split(strings.ReplaceAll(s, "\r\n", "\n"), "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " \t")
	}
	s = blankRuns.ReplaceAllString(strings.Join(lines, "\n"), "\n\n")
	return strings.Trim(s, "\n") + "\n"
}

// shellQuote quotes s for a POSIX sh script as one single-quoted word.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
