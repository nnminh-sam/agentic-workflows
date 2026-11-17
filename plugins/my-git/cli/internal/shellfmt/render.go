package shellfmt

import (
	"sort"
	"strings"
)

// TempDir is a placeholder for the user's temporary directory. An argument
// (or the value part of key=value) that starts with TempDir+"/" is rendered
// as a path in the shell's temp dir: "$env:TEMP\…" (PowerShell),
// "%TEMP%\…" (cmd), "$TEMP/…" (Git Bash) or "${TMPDIR:-/tmp}/…" (bash, zsh).
const TempDir = "{{TEMP}}"

// TempPath returns the placeholder path for a file in the temp dir.
func TempPath(name string) string { return TempDir + "/" + name }

// Command is one step to render. Args[0] is the program.
type Command struct {
	Args    []string          // program and arguments, never shell-interpreted
	Env     map[string]string // extra environment for this step only
	Dir     string            // working directory; git gets -C, others a cd line
	Comment string            // optional comment line printed above the step
}

// Options controls Render.
type Options struct {
	ID string // plan id; when set, a header comment line is printed
	// Script renders a file to save (--out): a shebang for POSIX shells;
	// "@chcp 65001 >nul" + "@echo off" and %% doubling for cmd; a UTF-8
	// byte order mark for PowerShell (so 5.1 reads non-ASCII text correctly).
	Script bool
	Width  int // wrap command lines longer than this (0 = never wrap)
}

// UTF8BOM starts a saved PowerShell script.
const UTF8BOM = "\ufeff"

// Render renders cmds for shell s, one step per line, stopping at the first
// failure: PowerShell checks $? after each step, cmd appends || exit /b 1 and
// POSIX shells start with set -e. The result ends with a newline.
func Render(s Shell, cmds []Command, opts Options) string {
	r := renderer{shell: s, opts: opts}
	r.prologue()
	for _, c := range cmds {
		r.step(c)
	}
	out := strings.Join(r.lines, "\n") + "\n"
	if opts.Script && s == PowerShell {
		out = UTF8BOM + out
	}
	return out
}

type renderer struct {
	shell Shell
	opts  Options
	lines []string
	cwd   string
}

func (r *renderer) add(lines ...string) { r.lines = append(r.lines, lines...) }

func (r *renderer) prologue() {
	if r.opts.Script {
		switch r.shell {
		case Cmd:
			r.add("@chcp 65001 >nul", "@echo off")
		case Zsh:
			r.add("#!/usr/bin/env zsh")
		case Bash, GitBash:
			r.add("#!/usr/bin/env bash")
		}
	}
	if r.opts.ID != "" {
		r.add(HeaderLine(r.shell, r.opts.ID))
	}
	if r.shell.Posix() {
		r.add("set -e")
	}
}

func (r *renderer) step(c Command) {
	if len(c.Args) == 0 {
		return
	}
	if c.Comment != "" {
		r.add(Comment(r.shell, c.Comment))
	}
	if r.shell == Cmd && hasLineBreak(c) {
		// cmd cannot pass a line break inside an argument: fail the step.
		r.add(Comment(Cmd, "mygit: this step has an argument with a line break, which cmd cannot pass;"),
			Comment(Cmd, "  render the plan with --shell powershell or --shell gitbash instead"),
			"exit /b 1")
		return
	}
	args := c.Args
	if c.Dir != "" {
		if isGit(args[0]) {
			args = append([]string{args[0], "-C", c.Dir}, args[1:]...)
		} else {
			r.chdir(c.Dir)
		}
	}
	words := r.words(args, c.Dir)
	prefix := r.env(c.Env)
	if len(prefix) > 0 {
		words = append(prefix, words...)
	}
	r.command(words)
}

// command emits one command (wrapped) followed by the failure check.
func (r *renderer) command(words []string) {
	lines := Wrap(words, r.opts.Width, "    ", Continuation(r.shell))
	switch r.shell {
	case PowerShell:
		r.add(lines...)
		r.add("if (-not $?) { exit 1 }")
	case Cmd:
		lines[len(lines)-1] += " || exit /b 1"
		r.add(lines...)
	default:
		r.add(lines...)
	}
}

func (r *renderer) chdir(dir string) {
	if dir == r.cwd {
		return
	}
	r.cwd = dir
	q := r.path(dir)
	switch r.shell {
	case PowerShell:
		r.command([]string{"Set-Location", "-LiteralPath", q})
	case Cmd:
		r.command([]string{"cd", "/d", q})
	default:
		r.command([]string{"cd", q})
	}
}

// path renders a directory: always quoted, converted for Git Bash.
func (r *renderer) path(p string) string {
	if r.shell == GitBash {
		p = GitBashPath(p)
	}
	return r.percent(QuoteAlways(r.shell, p))
}

// words renders each argument; the value of -C is treated as a path.
func (r *renderer) words(args []string, dir string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		if i > 0 && args[i-1] == "-C" && a == dir {
			out[i] = r.path(a)
			continue
		}
		out[i] = r.arg(a)
	}
	return out
}

// arg renders one literal argument for the shell.
func (r *renderer) arg(a string) string {
	if t, ok := r.temp(a); ok {
		return t
	}
	if r.shell == GitBash {
		a = gitBashArg(a)
	}
	return r.percent(Quote(r.shell, a))
}

// percent doubles % for cmd scripts.
func (r *renderer) percent(s string) string {
	if r.shell == Cmd && r.opts.Script {
		return cmdPercent(s)
	}
	return s
}

// temp renders an argument that contains the TempDir placeholder, either at
// the start or right after "key=".
func (r *renderer) temp(a string) (string, bool) {
	key, rest := "", a
	if !strings.HasPrefix(a, TempDir) {
		i := strings.Index(a, "="+TempDir)
		if i < 0 {
			return "", false
		}
		key, rest = a[:i+1], a[i+1:]
		if !isSafe(r.shell, key) {
			return "", false
		}
	}
	name := strings.TrimPrefix(strings.TrimPrefix(rest, TempDir), "/")
	return r.percent(key) + tempPath(r.shell, name, r.opts.Script), true
}

// tempPath renders "<shell temp dir>/name" inside double quotes.
func tempPath(s Shell, name string, script bool) string {
	switch s {
	case PowerShell:
		return `"$env:TEMP\` + psDQ(strings.ReplaceAll(name, "/", `\`)) + `"`
	case Cmd:
		name = strings.ReplaceAll(strings.ReplaceAll(name, "/", `\`), `"`, "")
		if script {
			name = cmdPercent(name)
		}
		return `"%TEMP%\` + name + `"`
	case GitBash:
		return `"$TEMP/` + posixDQ(name) + `"`
	default:
		return `"${TMPDIR:-/tmp}/` + posixDQ(name) + `"`
	}
}

// psDQ escapes text for a PowerShell double-quoted string.
func psDQ(s string) string {
	return strings.NewReplacer("`", "``", "$", "`$", `"`, "`\"").Replace(s)
}

// posixDQ escapes text for a POSIX double-quoted string.
func posixDQ(s string) string {
	return strings.NewReplacer(`\`, `\\`, "$", `\$`, `"`, `\"`, "`", "\\`").Replace(s)
}

// env renders per-step environment. PowerShell and cmd get their own lines
// before the step; POSIX shells get a KEY='value' prefix on the command.
func (r *renderer) env(env map[string]string) []string {
	keys := make([]string, 0, len(env))
	for k := range env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var prefix []string
	for _, k := range keys {
		v := env[k]
		switch r.shell {
		case PowerShell:
			r.add("$env:" + k + "=" + QuoteAlways(PowerShell, v))
		case Cmd:
			r.add(r.percent(cmdCaret(`set "` + k + "=" + v + `"`)))
		default:
			if r.shell == GitBash {
				v = gitBashArg(v)
			}
			prefix = append(prefix, k+"="+QuoteAlways(r.shell, v))
		}
	}
	return prefix
}

// gitBashArg converts a Windows path argument, or the path value of a
// key=value argument, to its Git Bash form.
func gitBashArg(a string) string {
	if IsWindowsAbs(a) {
		return GitBashPath(a)
	}
	if i := strings.IndexByte(a, '='); i > 0 && IsWindowsAbs(a[i+1:]) {
		return a[:i+1] + GitBashPath(a[i+1:])
	}
	return a
}

// hasLineBreak reports whether any argument, env value or dir of c
// contains a CR or LF.
func hasLineBreak(c Command) bool {
	vals := append([]string{c.Dir}, c.Args...)
	for _, v := range c.Env {
		vals = append(vals, v)
	}
	for _, v := range vals {
		if strings.ContainsAny(v, "\r\n") {
			return true
		}
	}
	return false
}

func isGit(prog string) bool {
	p := strings.ToLower(prog)
	return p == "git" || p == "git.exe"
}

// Wrap joins words with spaces, breaking lines longer than width. Every
// line but the last ends with " "+cont (if cont is non-empty) and every line
// but the first starts with indent. A short flag ("-c", "-C", "-F") is kept
// on the same line as its value. width <= 0 means a single line.
func Wrap(words []string, width int, indent, cont string) []string {
	groups := groupFlags(words)
	if width <= 0 {
		return []string{strings.Join(groups, " ")}
	}
	suffix := ""
	if cont != "" {
		suffix = " " + cont
	}
	var lines []string
	cur := ""
	for _, g := range groups {
		switch {
		case cur == "":
			cur = g
		case len(cur)+1+len(g)+len(suffix) > width && strings.TrimSpace(cur) != "":
			lines = append(lines, cur+suffix)
			cur = indent + g
		default:
			cur += " " + g
		}
	}
	return append(lines, cur)
}

func isShortFlag(w string) bool { return len(w) == 2 && w[0] == '-' && w[1] != '-' }

// groupFlags glues a two-character flag to the word after it.
func groupFlags(words []string) []string {
	var out []string
	for i := 0; i < len(words); i++ {
		w := words[i]
		if i > 0 && isShortFlag(w) && i+1 < len(words) && !isShortFlag(words[i+1]) {
			w += " " + words[i+1]
			i++
		}
		out = append(out, w)
	}
	return out
}
