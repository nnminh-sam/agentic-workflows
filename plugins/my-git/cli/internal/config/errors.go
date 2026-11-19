package config

import (
	"bytes"
	"fmt"
	"strings"
)

// Error is an invalid config value or file. It names the file, the field
// (dotted path, e.g. "rules.CM007.params.max") and, when known, the line.
type Error struct {
	File  string
	Field string
	Line  int // 1-based; 0 when unknown
	Msg   string
}

func (e *Error) Error() string {
	var b strings.Builder
	b.WriteString(e.File)
	if e.Line > 0 {
		fmt.Fprintf(&b, ":%d", e.Line)
	}
	if e.Field != "" {
		b.WriteString(": " + e.Field)
	}
	b.WriteString(": " + e.Msg)
	return b.String()
}

// lineAt returns the 1-based line of byte offset off in data.
func lineAt(data []byte, off int64) int {
	if off < 0 {
		return 0
	}
	if off > int64(len(data)) {
		off = int64(len(data))
	}
	return bytes.Count(data[:off], []byte("\n")) + 1
}

// lineOfField finds the line where the dotted field path is defined by
// searching for each key in turn after the previous one. It is a heuristic
// good enough for error messages; 0 when not found.
func lineOfField(data []byte, field string) int {
	pos := 0
	found := false
	for _, seg := range splitField(field) {
		if seg == "" || seg[0] == '[' {
			continue
		}
		needle := []byte(`"` + seg + `"`)
		i := bytes.Index(data[pos:], needle)
		if i < 0 {
			break
		}
		pos += i + len(needle)
		found = true
	}
	if !found {
		return 0
	}
	return lineAt(data, int64(pos))
}

// splitField splits "match[0].path" into ["match", "[0]", "path"].
func splitField(field string) []string {
	var out []string
	for _, part := range strings.Split(field, ".") {
		for {
			i := strings.IndexByte(part, '[')
			if i < 0 {
				break
			}
			if i > 0 {
				out = append(out, part[:i])
			}
			j := strings.IndexByte(part[i:], ']')
			if j < 0 {
				break
			}
			out = append(out, part[i:i+j+1])
			part = part[i+j+1:]
		}
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}
