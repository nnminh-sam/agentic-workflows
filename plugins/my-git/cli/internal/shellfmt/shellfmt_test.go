package shellfmt

import (
	"encoding/json"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

var update = flag.Bool("update", false, "rewrite testdata/*.golden files")

var allShells = []Shell{Bash, Zsh, GitBash, PowerShell, Cmd}

func TestParseShell(t *testing.T) {
	tests := map[string]Shell{
		"bash": Bash, "sh": Bash, "zsh": Zsh, "gitbash": GitBash, "git-bash": GitBash, "Git_Bash": GitBash,
		"powershell": PowerShell, "PWSH": PowerShell, "ps": PowerShell, "cmd": Cmd, " cmd.exe ": Cmd,
	}
	for in, want := range tests {
		got, err := ParseShell(in)
		if err != nil || got != want {
			t.Errorf("ParseShell(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	if _, err := ParseShell("fish"); err == nil {
		t.Error("ParseShell(fish) should fail")
	}
	for _, s := range allShells {
		back, err := ParseShell(s.String())
		if err != nil || back != s {
			t.Errorf("round trip %v: %v %v", s, back, err)
		}
	}
	if got := Shell(42).String(); got != "Shell(42)" {
		t.Errorf("Shell(42).String() = %q", got)
	}
}

func TestShellText(t *testing.T) {
	type wrap struct {
		S Shell `json:"s"`
	}
	b, err := json.Marshal(wrap{PowerShell})
	if err != nil || string(b) != `{"s":"powershell"}` {
		t.Fatalf("marshal = %s %v", b, err)
	}
	var w wrap
	if err := json.Unmarshal([]byte(`{"s":"gitbash"}`), &w); err != nil || w.S != GitBash {
		t.Errorf("unmarshal = %v %v", w.S, err)
	}
	if err := json.Unmarshal([]byte(`{"s":"tcsh"}`), &w); err == nil {
		t.Error("unmarshal tcsh should fail")
	}
}

func TestShellProperties(t *testing.T) {
	tests := []struct {
		s           Shell
		posix       bool
		ext, cont   string
		commentPref string
	}{
		{Bash, true, ".sh", `\`, "# "},
		{Zsh, true, ".sh", `\`, "# "},
		{GitBash, true, ".sh", `\`, "# "},
		{PowerShell, false, ".ps1", "`", "# "},
		{Cmd, false, ".cmd", "^", "REM "},
	}
	for _, tt := range tests {
		if tt.s.Posix() != tt.posix || ScriptExt(tt.s) != tt.ext || Continuation(tt.s) != tt.cont || CommentPrefix(tt.s) != tt.commentPref {
			t.Errorf("%v: posix=%v ext=%q cont=%q comment=%q", tt.s, tt.s.Posix(), ScriptExt(tt.s), Continuation(tt.s), CommentPrefix(tt.s))
		}
	}
}

func TestComment(t *testing.T) {
	if got := Comment(Bash, "one\ntwo\n"); got != "# one\n# two" {
		t.Errorf("bash = %q", got)
	}
	if got := Comment(Cmd, "a\n\nb"); got != "REM a\nREM\nREM b" {
		t.Errorf("cmd = %q", got)
	}
	want := "# plan 7f3a2c · manual · shell powershell · run these, then: mygit plan verify 7f3a2c"
	if got := HeaderLine(PowerShell, "7f3a2c"); got != want {
		t.Errorf("HeaderLine = %q", got)
	}
	if got := HeaderLine(Cmd, "abc123"); !strings.HasPrefix(got, "REM plan abc123 · manual · shell cmd") {
		t.Errorf("cmd HeaderLine = %q", got)
	}
}

func TestQuote(t *testing.T) {
	tests := []struct {
		in                     string
		posix, ps, cmd, script string // script: cmd in a saved .cmd
	}{
		{"abc", "abc", "abc", "abc", "abc"},
		{"--porcelain=v2", "--porcelain=v2", "--porcelain=v2", "--porcelain=v2", "--porcelain=v2"},
		{"", "''", "''", `""`, `""`},
		{"a b", "'a b'", "'a b'", `"a b"`, `"a b"`},
		{"it's", `'it'\''s'`, "'it''s'", `"it's"`, `"it's"`},
		{"user.name=Octo Cat", "user.name='Octo Cat'", "user.name='Octo Cat'", `user.name="Octo Cat"`, `user.name="Octo Cat"`},
		{"user.email=octo@acme.example", "user.email=octo@acme.example", "user.email='octo@acme.example'", "user.email=octo@acme.example", "user.email=octo@acme.example"},
		{"100%", "100%", "'100%'", `"100%"`, `"100%%"`},
		{"%PATH%", "%PATH%", "'%PATH%'", `"%PATH%"`, `"%%PATH%%"`},
		{"a^b", "'a^b'", "'a^b'", `"a^b"`, `"a^b"`},
		{"a&b", "'a&b'", "'a&b'", `"a&b"`, `"a&b"`},
		{"a|b", "'a|b'", "'a|b'", `"a|b"`, `"a|b"`},
		{"a<b>c", "'a<b>c'", "'a<b>c'", `"a<b>c"`, `"a<b>c"`},
		{`say "hi"`, `'say "hi"'`, `'say "hi"'`, `"say \"hi\""`, `"say \"hi\""`},
		{`a"&b`, `'a"&b'`, `'a"&b'`, `"a\"^&b"`, `"a\"^&b"`},
		{`C:\dir\`, `'C:\dir\'`, `'C:\dir\'`, `"C:\dir\\"`, `"C:\dir\\"`},
		{`a\"b`, `'a\"b'`, `'a\"b'`, `"a\\\"b"`, `"a\\\"b"`},
		{"$HOME", "'$HOME'", "'$HOME'", `"$HOME"`, `"$HOME"`},
		{"`x`", "'`x`'", "'`x`'", "\"`x`\"", "\"`x`\""},
		{"café", "'café'", "'café'", `"café"`, `"café"`},
		{"a\nb", "'a\nb'", "'a\nb'", "\"a\nb\"", "\"a\nb\""},
		{"a,b", "a,b", "'a,b'", `"a,b"`, `"a,b"`},
		{"~/x", "'~/x'", "'~/x'", `"~/x"`, `"~/x"`},
		{"*.go", "'*.go'", "'*.go'", `"*.go"`, `"*.go"`},
		{"x=", "x=", "x=", "x=", "x="},
		{"x=a b=c", "x='a b=c'", "x='a b=c'", `x="a b=c"`, `x="a b=c"`},
		{"a b=c", "'a b=c'", "'a b=c'", `"a b=c"`, `"a b=c"`},
	}
	for _, tt := range tests {
		r := func(s Shell, script bool) string {
			rr := renderer{shell: s, opts: Options{Script: script}}
			return rr.arg(tt.in)
		}
		if got := Quote(Bash, tt.in); got != tt.posix {
			t.Errorf("Quote(bash, %q) = %s, want %s", tt.in, got, tt.posix)
		}
		if got := Quote(Zsh, tt.in); got != tt.posix {
			t.Errorf("Quote(zsh, %q) = %s, want %s", tt.in, got, tt.posix)
		}
		if got := Quote(PowerShell, tt.in); got != tt.ps {
			t.Errorf("Quote(powershell, %q) = %s, want %s", tt.in, got, tt.ps)
		}
		if got := Quote(Cmd, tt.in); got != tt.cmd {
			t.Errorf("Quote(cmd, %q) = %s, want %s", tt.in, got, tt.cmd)
		}
		if got := r(Cmd, true); got != tt.script {
			t.Errorf("cmd script arg(%q) = %s, want %s", tt.in, got, tt.script)
		}
	}
}

// PowerShell treats the typographic quotes U+2018..U+201B as single quotes,
// so they must be doubled inside a single-quoted string too.
func TestQuotePowerShellSmartQuotes(t *testing.T) {
	if got, want := Quote(PowerShell, "it’s"), "'it’’s'"; got != want {
		t.Errorf("Quote(powershell, it’s) = %s, want %s", got, want)
	}
}

// zsh expands a word that starts with "=" (EQUALS option) to a command path.
func TestQuoteZshLeadingEquals(t *testing.T) {
	if got := Quote(Zsh, "=foo"); got != "'=foo'" {
		t.Errorf("Quote(zsh, =foo) = %s", got)
	}
}

func TestGitBashPath(t *testing.T) {
	tests := map[string]string{
		`C:\src\api`:  "/c/src/api",
		`d:/work/x`:   "/d/work/x",
		`C:\`:         "/c/",
		`/already/ok`: "/already/ok",
		`relative\x`:  `relative\x`,
		`C:`:          "C:",
		`1:\x`:        `1:\x`,
		``:            ``,
	}
	for in, want := range tests {
		if got := GitBashPath(in); got != want {
			t.Errorf("GitBashPath(%q) = %q, want %q", in, got, want)
		}
	}
}

// The three spec examples ("Manual mode on Windows: one plan, three shells").
func TestRenderSpecExamples(t *testing.T) {
	cmds := []Command{{
		Args: []string{"git", "-c", "user.name=Octo Cat", "commit", "-S", "-F", TempPath("mygit-7f3a2c.msg")},
		Dir:  `C:\src\api`,
	}}
	tests := []struct {
		s    Shell
		want string
	}{
		{PowerShell, "git -C 'C:\\src\\api' -c user.name='Octo Cat' commit -S -F \"$env:TEMP\\mygit-7f3a2c.msg\"\nif (-not $?) { exit 1 }\n"},
		{Cmd, `git -C "C:\src\api" -c user.name="Octo Cat" commit -S -F "%TEMP%\mygit-7f3a2c.msg" || exit /b 1` + "\n"},
		{GitBash, "set -e\ngit -C '/c/src/api' -c user.name='Octo Cat' commit -S -F \"$TEMP/mygit-7f3a2c.msg\"\n"},
		{Bash, "set -e\ngit -C 'C:\\src\\api' -c user.name='Octo Cat' commit -S -F \"${TMPDIR:-/tmp}/mygit-7f3a2c.msg\"\n"},
	}
	for _, tt := range tests {
		if got := Render(tt.s, cmds, Options{}); got != tt.want {
			t.Errorf("%v:\n got %q\nwant %q", tt.s, got, tt.want)
		}
	}
}

func TestRenderEnv(t *testing.T) {
	cmds := []Command{{
		Args: []string{"git", "push", "-u", "origin", "HEAD"},
		Env:  map[string]string{"GIT_SSH_COMMAND": "ssh -i ~/.ssh/id_acme -o IdentitiesOnly=yes", "A": "1"},
	}}
	tests := []struct {
		s    Shell
		want string
	}{
		{PowerShell, "$env:A='1'\n$env:GIT_SSH_COMMAND='ssh -i ~/.ssh/id_acme -o IdentitiesOnly=yes'\ngit push -u origin HEAD\nif (-not $?) { exit 1 }\n"},
		{Cmd, "set \"A=1\"\nset \"GIT_SSH_COMMAND=ssh -i ~/.ssh/id_acme -o IdentitiesOnly=yes\"\ngit push -u origin HEAD || exit /b 1\n"},
		{Bash, "set -e\nA='1' GIT_SSH_COMMAND='ssh -i ~/.ssh/id_acme -o IdentitiesOnly=yes' git push -u origin HEAD\n"},
	}
	for _, tt := range tests {
		if got := Render(tt.s, cmds, Options{}); got != tt.want {
			t.Errorf("%v:\n got %q\nwant %q", tt.s, got, tt.want)
		}
	}
	// Git Bash converts a Windows path in an env value.
	got := Render(GitBash, []Command{{Args: []string{"git", "status"}, Env: map[string]string{"GIT_DIR": `C:\x\.git`}}}, Options{})
	if want := "set -e\nGIT_DIR='/c/x/.git' git status\n"; got != want {
		t.Errorf("gitbash env = %q, want %q", got, want)
	}
	// cmd scripts double % in env values.
	got = Render(Cmd, []Command{{Args: []string{"x"}, Env: map[string]string{"P": "50%"}}}, Options{Script: true})
	if want := "@chcp 65001 >nul\n@echo off\nset \"P=50%%\"\nx || exit /b 1\n"; got != want {
		t.Errorf("cmd script env = %q, want %q", got, want)
	}
}

func TestRenderTemp(t *testing.T) {
	tests := []struct {
		s      Shell
		script bool
		arg    string
		want   string
	}{
		{PowerShell, false, TempPath("a/b c.msg"), `"$env:TEMP\a\b c.msg"`},
		{PowerShell, false, TempPath("x$y`z\".msg"), "\"$env:TEMP\\x`$y``z`\".msg\""},
		{Cmd, false, TempPath("a/b.msg"), `"%TEMP%\a\b.msg"`},
		{Cmd, true, TempPath("50%.msg"), `"%TEMP%\50%%.msg"`},
		{Cmd, false, TempPath(`q"x.msg`), `"%TEMP%\qx.msg"`},
		{GitBash, false, TempPath("a b.msg"), `"$TEMP/a b.msg"`},
		{Bash, false, TempPath(`$x"y.msg`), `"${TMPDIR:-/tmp}/\$x\"y.msg"`},
		{Zsh, false, TempPath("m.msg"), `"${TMPDIR:-/tmp}/m.msg"`},
		{Bash, false, "--file=" + TempPath("m.msg"), `--file="${TMPDIR:-/tmp}/m.msg"`},
		{PowerShell, false, "--file=" + TempPath("m.msg"), `--file="$env:TEMP\m.msg"`},
		{Cmd, true, "--file=" + TempPath("m.msg"), `--file="%TEMP%\m.msg"`},
		// key with unsafe characters: placeholder not recognised, quoted literally
		{Bash, false, "a b=" + TempPath("m"), `'a b={{TEMP}}/m'`},
		// placeholder in the middle is not a temp path
		{Bash, false, "x" + TempPath("m"), `'x{{TEMP}}/m'`},
	}
	for _, tt := range tests {
		r := renderer{shell: tt.s, opts: Options{Script: tt.script}}
		if got := r.arg(tt.arg); got != tt.want {
			t.Errorf("%v script=%v arg(%q) = %s, want %s", tt.s, tt.script, tt.arg, got, tt.want)
		}
	}
}

func TestRenderStopOnFailure(t *testing.T) {
	cmds := []Command{{Args: []string{"git", "add", "-A"}}, {Args: []string{"git", "commit", "-F", "m"}}}
	got := Render(PowerShell, cmds, Options{})
	if strings.Count(got, "if (-not $?) { exit 1 }") != 2 {
		t.Errorf("powershell: want a check after every step:\n%s", got)
	}
	got = Render(Cmd, cmds, Options{})
	if strings.Count(got, " || exit /b 1\n") != 2 {
		t.Errorf("cmd: want || exit /b 1 on every step:\n%s", got)
	}
	for _, s := range []Shell{Bash, Zsh, GitBash} {
		got = Render(s, cmds, Options{})
		if !strings.HasPrefix(got, "set -e\n") || strings.Count(got, "set -e") != 1 {
			t.Errorf("%v: want set -e once at the top:\n%s", s, got)
		}
	}
}

func TestRenderSkipsEmptyAndDedupesCd(t *testing.T) {
	cmds := []Command{
		{},
		{Args: []string{"make", "test"}, Dir: "/repo"},
		{Args: []string{"make", "lint"}, Dir: "/repo"},
		{Args: []string{"make", "x"}, Dir: "/other"},
	}
	got := Render(Bash, cmds, Options{})
	want := "set -e\ncd '/repo'\nmake test\nmake lint\ncd '/other'\nmake x\n"
	if got != want {
		t.Errorf("got %q\nwant %q", got, want)
	}
}

func TestWrap(t *testing.T) {
	words := []string{"git", "-c", "user.name='Octo Cat'", "-c", "user.email=octo@acme.example", "commit", "-S", "-F", "m"}
	got := Wrap(words, 40, "    ", `\`)
	want := []string{
		`git -c user.name='Octo Cat' \`,
		`    -c user.email=octo@acme.example \`,
		`    commit -S -F m`,
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Wrap =\n%q\nwant\n%q", got, want)
	}
	if got := Wrap(words, 0, "  ", `\`); len(got) != 1 || got[0] != "git -c user.name='Octo Cat' -c user.email=octo@acme.example commit -S -F m" {
		t.Errorf("width 0 = %q", got)
	}
	// a word longer than the width still goes on its own line, never split
	got = Wrap([]string{"a", strings.Repeat("x", 30), "b"}, 10, "  ", "")
	if !reflect.DeepEqual(got, []string{"a", "  " + strings.Repeat("x", 30), "  b"}) {
		t.Errorf("long word = %q", got)
	}
	// "-S -F": two short flags are not glued; -F keeps its value
	if g := groupFlags([]string{"commit", "-S", "-F", "m"}); !reflect.DeepEqual(g, []string{"commit", "-S", "-F m"}) {
		t.Errorf("groupFlags = %q", g)
	}
	// first word is never glued, trailing flag stays alone
	if g := groupFlags([]string{"-C", "x", "-q"}); !reflect.DeepEqual(g, []string{"-C", "x", "-q"}) {
		t.Errorf("groupFlags = %q", g)
	}
}

// goldenCases are rendered for every shell into testdata/<name>.<shell>.golden.
var goldenCases = []struct {
	name string
	cmds []Command
	opts Options
}{
	{
		name: "commit",
		opts: Options{ID: "7f3a2c"},
		cmds: []Command{
			{Args: []string{"git", "add", "-A"}, Dir: `C:\src\api`},
			{
				Args: []string{"git", "-c", "user.name=Octo Cat", "-c", "user.email=octo@acme.example",
					"-c", "gpg.format=ssh", "-c", `user.signingkey=C:\Users\octo\.ssh\id_sign.pub`,
					"commit", "-S", "-F", TempPath("mygit-7f3a2c.msg")},
				Dir: `C:\src\api`,
			},
		},
	},
	{
		name: "commit-wrapped",
		opts: Options{ID: "7f3a2c", Width: 60},
		cmds: []Command{{
			Args: []string{"git", "-c", "user.name=Octo Cat", "-c", "user.email=octo@acme.example",
				"-c", "gpg.format=ssh", "commit", "-S", "-F", TempPath("mygit-7f3a2c.msg")},
		}},
	},
	{
		name: "script",
		opts: Options{ID: "1c9e04", Script: true},
		cmds: []Command{
			{Args: []string{"git", "config", "user.name", "Octo O'Cat"}, Comment: "identity\nfrom profile work"},
			{Args: []string{"git", "config", "core.sshCommand", "ssh -i ~/.ssh/id_acme -o IdentitiesOnly=yes"}},
			{Args: []string{"gh", "auth", "switch", "--user", "octo-acme"}},
			{Args: []string{"git", "commit", "-m", "100% & done | a^b <x>"}},
			{Args: []string{"git", "push"}, Env: map[string]string{"GIT_SSH_COMMAND": "ssh -i C:\\keys\\id"}},
			{Args: []string{"make", "test"}, Dir: `D:\work dir`},
		},
	},
	{
		name: "edge",
		cmds: []Command{
			{Args: []string{"git", "commit", "--allow-empty", "-m", ""}},
			{Args: []string{"git", "tag", "-a", "v1", "-m", "café ünïcødé — 日本"}},
			{Args: []string{"git", "commit", "-m", "line1\nline2"}},
			{Args: []string{"git", "log", "--format=%H %s", "--", "dir with space/file.go"}},
		},
	},
}

func TestRenderGolden(t *testing.T) {
	for _, gc := range goldenCases {
		for _, s := range allShells {
			name := gc.name + "." + s.String()
			t.Run(name, func(t *testing.T) {
				got := Render(s, gc.cmds, gc.opts)
				path := filepath.Join("testdata", name+".golden")
				if *update {
					if err := os.MkdirAll("testdata", 0o755); err != nil {
						t.Fatal(err)
					}
					if err := os.WriteFile(path, []byte(got), 0o644); err != nil {
						t.Fatal(err)
					}
					return
				}
				want, err := os.ReadFile(path)
				if err != nil {
					t.Fatalf("%v (run with -update)", err)
				}
				if got != string(want) {
					t.Errorf("mismatch %s:\n--- got\n%s--- want\n%s", path, got, want)
				}
			})
		}
	}
}

// TestRenderRoundTripPosix runs the rendered text in a real bash/zsh with a
// fake git that prints its argv, and checks every argument arrives intact.
func TestRenderRoundTripPosix(t *testing.T) {
	if testing.Short() {
		t.Skip("runs real shells")
	}
	bin := t.TempDir()
	out := filepath.Join(t.TempDir(), "args")
	fake := "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\0' \"$a\" >> \"$ARGS_OUT\"; done\nprintf '\\n\\0' >> \"$ARGS_OUT\"\n"
	if err := os.WriteFile(filepath.Join(bin, "git"), []byte(fake), 0o755); err != nil {
		t.Fatal(err)
	}
	tmp := t.TempDir()
	args := [][]string{
		{"git", "commit", "-m", "it's a \"test\" $HOME `id` \\ ! * ? ~ & | ; < > ( ) { } [ ] # % ^"},
		{"git", "-c", "user.name=Octo O'Cat", "commit", "-m", ""},
		{"git", "commit", "-m", "multi\nline\n\tmessage", "--", "café ünï 日本"},
		{"git", "commit", "-F", TempPath("mygit-abc.msg")},
		{"git", "x", "a,b", "100%", "@{u}", "HEAD~1", "a=b c", "--opt=v w"},
	}
	want := [][]string{
		args[0][1:], args[1][1:], args[2][1:],
		{"commit", "-F", filepath.Join(tmp, "mygit-abc.msg")},
		args[4][1:],
	}
	for _, sh := range []struct {
		s    Shell
		prog string
	}{{Bash, "bash"}, {Zsh, "zsh"}} {
		if _, err := exec.LookPath(sh.prog); err != nil {
			continue
		}
		for _, width := range []int{0, 30} {
			os.Remove(out)
			var cmds []Command
			for _, a := range args {
				cmds = append(cmds, Command{Args: a})
			}
			text := Render(sh.s, cmds, Options{ID: "abc123", Width: width})
			flags := []string{"-c", text}
			if sh.s == Zsh {
				flags = []string{"-f", "-c", text} // skip ~/.zshenv (it may reset PATH)
			}
			c := exec.Command(sh.prog, flags...)
			c.Env = append(os.Environ(), "PATH="+bin+":/usr/bin:/bin", "ARGS_OUT="+out, "TMPDIR="+tmp)
			if b, err := c.CombinedOutput(); err != nil {
				t.Fatalf("%s width %d: %v\n%s\nscript:\n%s", sh.prog, width, err, b, text)
			}
			raw, err := os.ReadFile(out)
			if err != nil {
				t.Fatal(err)
			}
			var got [][]string
			var cur []string
			for _, a := range strings.Split(strings.TrimSuffix(string(raw), "\x00"), "\x00") {
				if a == "\n" {
					got = append(got, cur)
					cur = nil
					continue
				}
				cur = append(cur, a)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("%s width %d argv mismatch:\n got %q\nwant %q\nscript:\n%s", sh.prog, width, got, want, text)
			}
		}
	}
}

// TestRenderPosixStopsOnFailure checks that set -e stops at the first
// failing step in a real shell.
func TestRenderPosixStopsOnFailure(t *testing.T) {
	if testing.Short() {
		t.Skip("runs a real shell")
	}
	marker := filepath.Join(t.TempDir(), "ran")
	text := Render(Bash, []Command{{Args: []string{"false"}}, {Args: []string{"touch", marker}}}, Options{})
	err := exec.Command("bash", "-c", text).Run()
	if err == nil {
		t.Fatal("script should fail")
	}
	if _, serr := os.Stat(marker); serr == nil {
		t.Error("second step ran after a failure")
	}
}
