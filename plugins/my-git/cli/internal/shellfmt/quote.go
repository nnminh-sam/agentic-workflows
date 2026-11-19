package shellfmt

import "strings"

// Quote renders one literal argument for the shell, adding quotes only when
// the value contains characters that are not safe bare. A "key=value"
// argument whose key is safe quotes only the value (user.name='Octo Cat').
func Quote(s Shell, arg string) string {
	if arg == "" {
		return QuoteAlways(s, "")
	}
	if isSafe(s, arg) {
		return arg
	}
	if i := strings.IndexByte(arg, '='); i > 0 && isSafe(s, arg[:i+1]) {
		return arg[:i+1] + QuoteAlways(s, arg[i+1:])
	}
	return QuoteAlways(s, arg)
}

// QuoteAlways renders arg as a quoted literal for the shell:
// PowerShell single quotes (an inner quote is doubled), cmd double quotes
// (MSVCRT-escaped, with ^ escapes outside quotes), POSIX single quotes (an
// inner quote is written as quote-backslash-quote-quote).
func QuoteAlways(s Shell, arg string) string {
	switch s {
	case PowerShell:
		return "'" + psSingle.Replace(arg) + "'"
	case Cmd:
		return cmdCaret(windowsQuote(arg))
	default:
		return "'" + strings.ReplaceAll(arg, "'", `'\''`) + "'"
	}
}

// psSingle doubles every character PowerShell treats as a single quote:
// ' and the typographic quotes U+2018..U+201B.
var psSingle = strings.NewReplacer("'", "''", "\u2018", "\u2018\u2018", "\u2019", "\u2019\u2019", "\u201a", "\u201a\u201a", "\u201b", "\u201b\u201b")

// safe bare characters per shell family.
const (
	posixSafe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./:=@%+,"
	psSafe    = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./=+"
	cmdSafe   = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./=+@"
)

func isSafe(s Shell, arg string) bool {
	// zsh expands a leading = (EQUALS) and every POSIX shell a leading ~.
	if strings.HasPrefix(arg, "=") || strings.HasPrefix(arg, "~") {
		return false
	}
	set := posixSafe
	switch s {
	case PowerShell:
		set = psSafe
	case Cmd:
		set = cmdSafe
	}
	for _, r := range arg {
		if !strings.ContainsRune(set, r) {
			return false
		}
	}
	return true
}

// windowsQuote wraps arg in double quotes following the MSVCRT argv rules:
// backslashes before a quote (or the closing quote) are doubled and inner
// quotes become \".
func windowsQuote(arg string) string {
	var b strings.Builder
	b.WriteByte('"')
	slashes := 0
	for i := 0; i < len(arg); i++ {
		c := arg[i]
		switch c {
		case '\\':
			slashes++
			b.WriteByte(c)
			continue
		case '"':
			b.WriteString(strings.Repeat(`\`, slashes+1))
		}
		slashes = 0
		b.WriteByte(c)
	}
	b.WriteString(strings.Repeat(`\`, slashes))
	b.WriteByte('"')
	return b.String()
}

// cmdCaret escapes cmd metacharacters that end up outside double quotes
// (cmd toggles its quote state on every "): & | < > ^ ( ) get a ^.
func cmdCaret(s string) string {
	var b strings.Builder
	inQuote := false
	for _, r := range s {
		if r == '"' {
			inQuote = !inQuote
		} else if !inQuote && strings.ContainsRune("&|<>^()", r) {
			b.WriteByte('^')
		}
		b.WriteRune(r)
	}
	return b.String()
}

// cmdPercent doubles % so a .cmd script passes it through literally.
func cmdPercent(s string) string { return strings.ReplaceAll(s, "%", "%%") }

// GitBashPath converts a Windows path (C:\src\api) to its Git Bash form
// (/c/src/api). Other paths are returned with backslashes turned to slashes
// only when they are drive paths; anything else is unchanged.
func GitBashPath(p string) string {
	if !IsWindowsAbs(p) {
		return p
	}
	rest := strings.ReplaceAll(p[2:], `\`, "/")
	return "/" + strings.ToLower(p[:1]) + rest
}

// IsWindowsAbs reports whether p starts with a drive letter and a separator.
func IsWindowsAbs(p string) bool {
	if len(p) < 3 || p[1] != ':' || (p[2] != '\\' && p[2] != '/') {
		return false
	}
	c := p[0]
	return ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z')
}
