// Package shell turns the command lines an agent or a user runs into the
// simple commands they would execute: program, words (quotes removed), env
// prefixes, the directory they run in and any heredoc fed to stdin.
//
// It is a port of scripts/gitguard/shellparse.py and commands.py. Bash is
// parsed with mvdan.cc/sh (falling back to the hand-written lexer when the
// command does not parse); PowerShell always uses the hand-written lexer.
// The parser is forgiving: it never fails and never panics, and words whose
// value depends on runtime expansion ($VAR, $(cmd), `cmd`) are marked Dynamic.
package shell

import "strings"

// Dialect selects the shell grammar.
type Dialect int

const (
	// Bash covers bash, sh, zsh, dash and Git Bash.
	Bash Dialect = iota
	// PowerShell covers Windows PowerShell and pwsh.
	PowerShell
)

func (d Dialect) String() string {
	if d == PowerShell {
		return "powershell"
	}
	return "bash"
}

// Word is one shell word with quotes removed.
type Word struct {
	Value   string // the word as the program would see it (static parts resolved)
	Raw     string // the source text of the word
	Dynamic bool   // depends on runtime expansion ($VAR, $(cmd), `cmd`)
}

// Segment is one simple command: its words and the heredoc / here-string
// body redirected to its stdin, if any.
type Segment struct {
	Words    []Word
	Stdin    string
	HasStdin bool
}

// Values returns the word values.
func (s Segment) Values() []string { return values(s.Words) }

func values(ws []Word) []string {
	out := make([]string, len(ws))
	for i, w := range ws {
		out[i] = w.Value
	}
	return out
}

// Lex splits a command line into simple-command segments of words. For Bash
// it uses mvdan.cc/sh and falls back to the forgiving hand lexer when the
// text does not parse (unterminated quotes, odd syntax).
func Lex(text string, d Dialect) []Segment {
	if d == Bash {
		if segs, ok := lexBash(text); ok {
			return segs
		}
	}
	return LexFallback(text, d)
}

// LexFallback runs the hand-written lexer ported from shellparse.py. It is
// exported so tests can compare it with the mvdan-based Bash path.
func LexFallback(text string, d Dialect) (segs []Segment) {
	defer func() {
		if recover() != nil { // the lexer is index-heavy; never take the caller down
			segs = nil
		}
	}()
	return newLexer(text, d).lex()
}

// isSpace reports Python's " \t\r" word separators.
func isSpace(c byte) bool { return c == ' ' || c == '\t' || c == '\r' }

func joinRaw(ws []Word) string {
	parts := make([]string, len(ws))
	for i, w := range ws {
		parts[i] = w.Raw
	}
	return strings.Join(parts, " ")
}
