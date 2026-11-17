// Package shellfmt renders a list of commands as copy-paste text (or a
// script) for one shell: PowerShell, cmd, Git Bash, bash or zsh. It is a set
// of pure string functions used by manual mode; nothing here runs a process.
package shellfmt

import (
	"fmt"
	"strings"
)

// Shell is a target shell for rendered commands.
type Shell int

// Supported shells.
const (
	Bash Shell = iota
	Zsh
	GitBash
	PowerShell
	Cmd
)

var shellNames = [...]string{"bash", "zsh", "gitbash", "powershell", "cmd"}

// String returns the canonical name used by --shell and config.
func (s Shell) String() string {
	if s < Bash || s > Cmd {
		return fmt.Sprintf("Shell(%d)", int(s))
	}
	return shellNames[s]
}

// ParseShell parses a shell name. Accepted (case-insensitive): bash, sh,
// zsh, gitbash, git-bash, powershell, pwsh, ps, cmd.
func ParseShell(name string) (Shell, error) {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "bash", "sh":
		return Bash, nil
	case "zsh":
		return Zsh, nil
	case "gitbash", "git-bash", "git_bash":
		return GitBash, nil
	case "powershell", "pwsh", "ps":
		return PowerShell, nil
	case "cmd", "cmd.exe":
		return Cmd, nil
	}
	return Bash, fmt.Errorf("unknown shell %q (want powershell|cmd|gitbash|bash|zsh)", name)
}

// MarshalText implements encoding.TextMarshaler.
func (s Shell) MarshalText() ([]byte, error) { return []byte(s.String()), nil }

// UnmarshalText implements encoding.TextUnmarshaler.
func (s *Shell) UnmarshalText(b []byte) error {
	v, err := ParseShell(string(b))
	if err != nil {
		return err
	}
	*s = v
	return nil
}

// Posix reports whether the shell is a POSIX-style shell (bash, zsh, Git Bash).
func (s Shell) Posix() bool { return s == Bash || s == Zsh || s == GitBash }

// ScriptExt is the file extension for a saved script: .ps1, .cmd or .sh.
func ScriptExt(s Shell) string {
	switch s {
	case PowerShell:
		return ".ps1"
	case Cmd:
		return ".cmd"
	default:
		return ".sh"
	}
}

// Continuation is the line-continuation character of the shell:
// a backtick for PowerShell, ^ for cmd and \ for POSIX shells.
func Continuation(s Shell) string {
	switch s {
	case PowerShell:
		return "`"
	case Cmd:
		return "^"
	default:
		return `\`
	}
}

// CommentPrefix starts a comment line: "REM " for cmd, "# " otherwise.
func CommentPrefix(s Shell) string {
	if s == Cmd {
		return "REM "
	}
	return "# "
}

// Comment renders text as one or more comment lines (no trailing newline).
func Comment(s Shell, text string) string {
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(CommentPrefix(s)+l, " ")
	}
	return strings.Join(lines, "\n")
}

// HeaderLine is the first comment of a manual plan, e.g.
// "# plan 7f3a2c · manual · shell powershell · run these, then: mygit plan verify 7f3a2c".
func HeaderLine(s Shell, id string) string {
	return Comment(s, fmt.Sprintf("plan %s · manual · shell %s · run these, then: mygit plan verify %s", id, s, id))
}
