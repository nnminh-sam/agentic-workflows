package shell

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

// Invocation is one program a command line would run.
type Invocation struct {
	Program  string            // normalised: "git", "gh", "curl", ... or the bare lower-case name
	Args     []Word            // words after the program
	Env      map[string]string // env overrides set by the command line itself (prefixes, export, $env:)
	Cwd      string            // directory the command would run in (tracks cd / Set-Location)
	Stdin    string            // heredoc / here-string body fed to stdin
	HasStdin bool              // whether Stdin is set
	Raw      string            // source text of the program and its words, space-joined

	home string // for resolving ~ in git -C
}

// Argv returns the argument values (without the program).
func (inv Invocation) Argv() []string { return values(inv.Args) }

// Dynamic reports whether any argument depends on runtime expansion.
func (inv Invocation) Dynamic() bool {
	for _, w := range inv.Args {
		if w.Dynamic {
			return true
		}
	}
	return false
}

// Options tune Parse.
type Options struct {
	Home string            // home directory for ~ and a bare cd; "" leaves ~ unresolved
	Env  map[string]string // env already set before the command line runs (not the process env)
}

var (
	assignRe = regexp.MustCompile(`(?s)^([A-Za-z_][A-Za-z0-9_]*)=(.*)$`)
	psEnvRe  = regexp.MustCompile(`(?i)^\$env:([A-Za-z_][A-Za-z0-9_]*)$`)
	bashCRe  = regexp.MustCompile(`^-[a-z]*c$`)
	gitBash  = regexp.MustCompile(`^/[a-zA-Z]/`)
	pathSep  = regexp.MustCompile(`[\\/]`)
)

var (
	prefixes   = set("env", "command", "sudo", "exec", "time", "nohup", "&", "builtin")
	cdCommands = set("cd", "set-location", "sl", "pushd", "push-location", "chdir")
	bashShells = set("bash", "sh", "zsh", "dash")
	psShells   = set("powershell", "pwsh")
	httpClient = set("curl", "wget", "http", "https", "xh", "invoke-webrequest", "invoke-restmethod", "iwr", "irm")
)

func set(items ...string) map[string]bool {
	m := make(map[string]bool, len(items))
	for _, s := range items {
		m[s] = true
	}
	return m
}

// IsHTTPClient reports whether a normalised program name is an HTTP client
// (curl, wget, httpie, xh, Invoke-WebRequest, Invoke-RestMethod, iwr, irm).
func IsHTTPClient(program string) bool { return httpClient[program] }

// ProgramName normalises a program word: last path element, lower-case,
// without .exe/.cmd/.bat/.ps1.
func ProgramName(word string) string {
	parts := pathSep.Split(word, -1)
	base := strings.ToLower(parts[len(parts)-1])
	for _, ext := range []string{".exe", ".cmd", ".bat", ".ps1"} {
		if strings.HasSuffix(base, ext) {
			base = strings.TrimSuffix(base, ext)
		}
	}
	return base
}

// ResolveDir resolves target against cwd like a shell cd would.
func ResolveDir(cwd, target, home string) string {
	if target == "" || target == "~" {
		if home == "" {
			return cwd
		}
		return home
	}
	if home != "" && (strings.HasPrefix(target, "~/") || strings.HasPrefix(target, `~\`)) {
		target = home + target[1:]
	}
	if runtime.GOOS == "windows" && gitBash.MatchString(target) { // git-bash /d/path
		target = target[1:2] + ":" + target[2:]
	}
	if filepath.IsAbs(target) {
		return filepath.Clean(target)
	}
	return filepath.Join(cwd, target)
}

// Parse turns a command line into the invocations it would run, resolving ~
// against the current user's home directory.
func Parse(command string, d Dialect, cwd string) []Invocation {
	home, _ := os.UserHomeDir()
	return ParseWith(command, d, cwd, Options{Home: home})
}

// ParseWith is Parse with explicit options.
func ParseWith(command string, d Dialect, cwd string, opt Options) []Invocation {
	return parse(command, d, cwd, opt.Home, opt.Env, 0)
}

type parseState struct {
	cwd  string
	env  map[string]string
	home string
}

func parse(command string, d Dialect, cwd, home string, env map[string]string, depth int) []Invocation {
	st := &parseState{cwd: cwd, env: copyEnv(env), home: home}
	var out []Invocation
	for _, seg := range Lex(command, d) {
		out = append(out, fromSegment(seg, st, d, depth)...)
	}
	return out
}

func copyEnv(env map[string]string) map[string]string {
	out := make(map[string]string, len(env))
	for k, v := range env {
		out[k] = v
	}
	return out
}

func merged(a, b map[string]string) map[string]string {
	out := copyEnv(a)
	for k, v := range b {
		out[k] = v
	}
	return out
}

func fromSegment(seg Segment, st *parseState, d Dialect, depth int) []Invocation {
	words := seg.Words
	if len(words) == 0 {
		return nil
	}
	// PowerShell: $env:NAME = value
	if m := psEnvRe.FindStringSubmatch(words[0].Raw); m != nil && len(words) >= 3 && words[1].Value == "=" {
		st.env[m[1]] = words[2].Value
		return nil
	}
	// bash: export NAME=value ...
	if words[0].Value == "export" {
		for _, w := range words[1:] {
			if a := assignRe.FindStringSubmatch(w.Value); a != nil {
				st.env[a[1]] = a[2]
			}
		}
		return nil
	}
	local := map[string]string{}
	words = stripPrefixes(words, d, local)
	if len(words) == 0 {
		for k, v := range local { // bare assignment sets a shell variable
			st.env[k] = v
		}
		return nil
	}
	prog := ProgramName(words[0].Value)
	rest := words[1:]
	if cdCommands[prog] {
		target := "~"
		for _, w := range rest {
			if !strings.HasPrefix(w.Value, "-") {
				target = w.Value
				break
			}
		}
		st.cwd = ResolveDir(st.cwd, target, st.home)
		return nil
	}
	if inner, ok := nestedShell(prog, rest, st, local, depth); ok {
		return inner
	}
	return []Invocation{{
		Program: prog, Args: rest, Env: merged(st.env, local), Cwd: st.cwd,
		Stdin: seg.Stdin, HasStdin: seg.HasStdin, Raw: joinRaw(words), home: st.home,
	}}
}

// stripPrefixes removes leading NAME=value assignments (into local) and
// wrappers such as env, sudo, command and PowerShell's & call operator.
func stripPrefixes(words []Word, d Dialect, local map[string]string) []Word {
	for len(words) > 0 {
		if d == Bash {
			if a := assignRe.FindStringSubmatch(words[0].Value); a != nil {
				local[a[1]] = a[2]
				words = words[1:]
				continue
			}
		}
		if !prefixes[words[0].Value] && ProgramName(words[0].Value) != "env" {
			return words
		}
		words = words[1:]
		for len(words) > 0 && strings.HasPrefix(words[0].Value, "-") && words[0].Value != "-" {
			words = words[1:]
		}
	}
	return words
}

// nestedShell parses bash -c '…' / powershell -Command "…" recursively.
func nestedShell(prog string, rest []Word, st *parseState, local map[string]string, depth int) ([]Invocation, bool) {
	isBash, isPS := bashShells[prog], psShells[prog]
	if depth >= 3 || !(isBash || isPS) || len(rest) == 0 {
		return nil, false
	}
	for k, w := range rest[:len(rest)-1] {
		flag := strings.ToLower(w.Value)
		match := flag == "-c" || (isPS && (flag == "-command" || flag == "/c")) || (isBash && bashCRe.MatchString(w.Value))
		if !match {
			continue
		}
		inner := Bash
		if isPS {
			inner = PowerShell
		}
		return parse(rest[k+1].Value, inner, st.cwd, st.home, merged(st.env, local), depth+1), true
	}
	return nil, false
}

// ---------------------------------------------------------------- git

// GitCall is a git invocation split into global options and subcommand.
type GitCall struct {
	Sub        string            // subcommand, e.g. "commit"
	Args       []Word            // words after the subcommand
	Config     map[string]string // -c key=value overrides (keys lower-cased)
	GlobalOpts []string          // global option words before the subcommand, e.g. ["-C", "sub", "--no-pager"]
	Cwd        string            // directory after -C
	Env        map[string]string // env overrides from the command line
	Stdin      string
	HasStdin   bool
}

// Argv returns the argument values after the subcommand.
func (g *GitCall) Argv() []string { return values(g.Args) }

var gitGlobalWithValue = set("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
	"--config-env", "--super-prefix", "--list-cmds")

// AsGit interprets a git invocation. ok is false when there is no subcommand
// (e.g. "git --version").
func AsGit(inv Invocation) (*GitCall, bool) {
	words := inv.Args
	cwd, config := inv.Cwd, map[string]string{}
	var globals []string
	for i := 0; i < len(words); {
		v := words[i].Value
		switch {
		case v == "-C" && i+1 < len(words):
			cwd = ResolveDir(cwd, words[i+1].Value, inv.home)
			globals = append(globals, v, words[i+1].Value)
			i += 2
		case v == "-c" && i+1 < len(words):
			k, val, _ := strings.Cut(words[i+1].Value, "=")
			config[strings.ToLower(k)] = val
			globals = append(globals, v, words[i+1].Value)
			i += 2
		case gitGlobalWithValue[v] && i+1 < len(words):
			globals = append(globals, v, words[i+1].Value)
			i += 2
		case strings.HasPrefix(v, "-"):
			globals = append(globals, v)
			i++
		default:
			return &GitCall{
				Sub: v, Args: words[i+1:], Config: config, GlobalOpts: globals, Cwd: cwd,
				Env: inv.Env, Stdin: inv.Stdin, HasStdin: inv.HasStdin,
			}, true
		}
	}
	return nil, false
}

// GhPath returns the leading command words of a gh call, e.g. ["pr", "create"].
func GhPath(inv Invocation) []string {
	var path []string
	for _, v := range inv.Argv() {
		if strings.HasPrefix(v, "-") {
			break
		}
		path = append(path, v)
		if len(path) == 2 {
			break
		}
	}
	return path
}
