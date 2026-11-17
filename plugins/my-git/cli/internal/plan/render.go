package plan

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
)

const (
	textWidth     = 92 // wrap long commands in Text
	summaryColumn = 46 // command column width before a read summary
	stepPrefixLen = 12 // "  1  write  "
)

// Text renders the plan for a terminal:
//
//	plan 7f3a2c   dry-run · mode auto · profile work · ruleset team · shell powershell
//	  1  read   git status --porcelain=v2                     3 files changed
//	  2  write  git add -A
//	  checks  ✓ CM003 ✓ ID001
func Text(p *Plan) string {
	var b strings.Builder
	b.WriteString(header(p) + "\n")
	for i, s := range p.Steps {
		b.WriteString(stepLine(p, i+1, s) + "\n")
	}
	b.WriteString(checkLines(p.Checks))
	for _, n := range p.Notes {
		b.WriteString("  note    " + n + "\n")
	}
	return b.String()
}

func header(p *Plan) string {
	parts := []string{"dry-run"}
	for _, kv := range [][2]string{{"mode", p.Mode}, {"profile", p.Profile}, {"ruleset", p.Ruleset}, {"workflow", p.Workflow}, {"shell", p.Shell}} {
		if kv[1] != "" {
			parts = append(parts, kv[0]+" "+kv[1])
		}
	}
	return fmt.Sprintf("plan %s   %s", p.ID, strings.Join(parts, " · "))
}

func stepLine(p *Plan, n int, s Step) string {
	prefix := fmt.Sprintf("%3d  %-5s  ", n, s.Kind)
	words := displayWords(p, s.Cmd)
	words[0] = prefix + words[0]
	lines := shellfmt.Wrap(words, textWidth, strings.Repeat(" ", stepPrefixLen+4), "")
	if sum := stepSummary(s); sum != "" {
		last := &lines[len(lines)-1]
		pad := summaryColumn + stepPrefixLen - len([]rune(*last))
		if pad < 2 {
			pad = 2
		}
		*last += strings.Repeat(" ", pad) + sum
	}
	return strings.Join(lines, "\n")
}

func stepSummary(s Step) string {
	if s.Summary != "" {
		return s.Summary
	}
	if s.Result != nil && !s.Result.OK() {
		return fmt.Sprintf("exit %d", s.Result.ExitCode)
	}
	return ""
}

// displayWords renders a command for reading (not for pasting): planned
// files show as <name>, values with spaces get double quotes.
func displayWords(p *Plan, c execx.Cmd) []string {
	files := map[string]string{}
	for _, f := range p.Files {
		files[f.Path] = "<" + f.Name + ">"
	}
	var words []string
	for _, e := range c.Env {
		words = append(words, displayArg(e))
	}
	words = append(words, c.Name)
	for _, a := range c.Args {
		if name, ok := files[a]; ok {
			words = append(words, name)
			continue
		}
		words = append(words, displayArg(p.ResolvePath(a, "$TEMP")))
	}
	return words
}

var plainArg = regexp.MustCompile(`^[A-Za-z0-9_@%+=:,./~<>$^{}\[\]-]+$`)

func displayArg(a string) string {
	if plainArg.MatchString(a) {
		return a
	}
	dq := func(s string) string { return `"` + strings.ReplaceAll(s, `"`, `\"`) + `"` }
	if i := strings.IndexByte(a, '='); i > 0 && plainArg.MatchString(a[:i+1]) {
		return a[:i+1] + dq(a[i+1:])
	}
	return dq(a)
}

// checkLines renders passed checks on one line and each failure on its own.
func checkLines(checks []Check) string {
	if len(checks) == 0 {
		return ""
	}
	var ok []string
	var bad []string
	for _, c := range checks {
		if c.OK {
			ok = append(ok, strings.TrimSpace("✓ "+c.RuleID+" "+c.Message))
			continue
		}
		bad = append(bad, fmt.Sprintf("          ✗ %-4s  %s  %s", c.Severity, c.RuleID, c.Message))
	}
	out := "  checks  " + strings.Join(ok, " ")
	if len(ok) == 0 {
		out = "  checks"
	}
	out = strings.TrimRight(out, " ") + "\n"
	for _, l := range bad {
		out += l + "\n"
	}
	return out
}

// JSON renders the plan as one indented JSON object.
func JSON(p *Plan) ([]byte, error) {
	return json.MarshalIndent(p, "", "  ")
}

// Manual renders the plan's write steps as commands for shell sh, with the
// "# plan <id> · manual …" header. The plan's files must already exist in
// the user's temp dir (call WriteFiles(os.TempDir()) first). script adds a
// shebang / @echo off and cmd %-doubling for a saved --out file.
func Manual(p *Plan, sh shellfmt.Shell, script bool) string {
	var cmds []shellfmt.Command
	for _, s := range p.Writes() {
		c := p.Resolve(s.Cmd, "")
		cmds = append(cmds, shellfmt.Command{
			Args: append([]string{c.Name}, c.Args...),
			Env:  envMap(c.Env),
			Dir:  c.Dir,
		})
	}
	return shellfmt.Render(sh, cmds, shellfmt.Options{ID: p.ID, Script: script, Width: 80})
}

func envMap(env []string) map[string]string {
	if len(env) == 0 {
		return nil
	}
	m := make(map[string]string, len(env))
	for _, kv := range env {
		k, v, _ := strings.Cut(kv, "=")
		m[k] = v
	}
	return m
}

// Confirm prints "apply? [y/N] " to out and reads one line from in.
// Only y or yes (any case) approves; EOF means no.
func Confirm(in io.Reader, out io.Writer) (bool, error) {
	if _, err := fmt.Fprint(out, "apply? [y/N] "); err != nil {
		return false, err
	}
	line, err := bufio.NewReader(in).ReadString('\n')
	if err != nil && err != io.EOF {
		return false, err
	}
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return true, nil
	}
	return false, nil
}

func firstLine(s string) string {
	l, _, _ := strings.Cut(strings.TrimSpace(s), "\n")
	return l
}
