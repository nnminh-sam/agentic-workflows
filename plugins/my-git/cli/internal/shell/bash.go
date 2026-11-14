package shell

import (
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

// lexBash parses text with mvdan.cc/sh and flattens it into segments the
// same way the hand lexer does: one segment per simple command, in source
// order, with env prefix assignments kept as leading NAME=value words and
// the last heredoc / here-string of the command as its stdin. Command
// substitutions are not descended into (they are dynamic words), matching
// the Python parser. ok is false when the text does not parse.
func lexBash(text string) (segs []Segment, ok bool) {
	defer func() {
		if recover() != nil {
			segs, ok = nil, false
		}
	}()
	f, err := syntax.NewParser(syntax.Variant(syntax.LangBash)).Parse(strings.NewReader(text), "")
	if err != nil {
		return nil, false
	}
	b := &bashWalker{src: text}
	b.stmts(f.Stmts)
	return b.segs, true
}

type bashWalker struct {
	src  string
	segs []Segment
}

func (b *bashWalker) slice(n syntax.Node) string {
	if n == nil || !n.Pos().IsValid() || !n.End().IsValid() {
		return ""
	}
	start, end := int(n.Pos().Offset()), int(n.End().Offset())
	if start < 0 || end > len(b.src) || start > end {
		return ""
	}
	return b.src[start:end]
}

func (b *bashWalker) stmts(list []*syntax.Stmt) {
	for _, s := range list {
		b.stmt(s)
	}
}

func (b *bashWalker) stmt(s *syntax.Stmt) {
	if s == nil {
		return
	}
	switch c := s.Cmd.(type) {
	case *syntax.CallExpr:
		b.call(c, s.Redirs)
	case *syntax.DeclClause:
		b.decl(c, s.Redirs)
	case *syntax.BinaryCmd:
		b.stmt(c.X)
		b.stmt(c.Y)
	case *syntax.Subshell:
		b.stmts(c.Stmts)
	case *syntax.Block:
		b.stmts(c.Stmts)
	case *syntax.IfClause:
		for ic := c; ic != nil; ic = ic.Else {
			b.stmts(ic.Cond)
			b.stmts(ic.Then)
		}
	case *syntax.WhileClause:
		b.stmts(c.Cond)
		b.stmts(c.Do)
	case *syntax.ForClause:
		b.stmts(c.Do)
	case *syntax.CaseClause:
		for _, it := range c.Items {
			b.stmts(it.Stmts)
		}
	case *syntax.FuncDecl:
		b.stmt(c.Body)
	case *syntax.TimeClause:
		b.stmt(c.Stmt)
	case *syntax.CoprocClause:
		b.stmt(c.Stmt)
	}
}

func (b *bashWalker) call(c *syntax.CallExpr, redirs []*syntax.Redirect) {
	var seg Segment
	for _, a := range c.Assigns {
		seg.Words = append(seg.Words, b.assign(a))
	}
	for _, w := range c.Args {
		seg.Words = append(seg.Words, b.word(w))
	}
	b.stdin(&seg, redirs)
	b.emit(seg)
}

func (b *bashWalker) decl(d *syntax.DeclClause, redirs []*syntax.Redirect) {
	seg := Segment{Words: []Word{{Value: d.Variant.Value, Raw: d.Variant.Value}}}
	for _, a := range d.Args {
		seg.Words = append(seg.Words, b.assign(a))
	}
	b.stdin(&seg, redirs)
	b.emit(seg)
}

func (b *bashWalker) emit(seg Segment) {
	if len(seg.Words) > 0 || seg.HasStdin {
		b.segs = append(b.segs, seg)
	}
}

// assign renders NAME=value (or a bare word for naked declare flags).
func (b *bashWalker) assign(a *syntax.Assign) Word {
	raw := b.slice(a)
	if a.Name == nil {
		if a.Value != nil {
			return b.word(a.Value)
		}
		return Word{Value: raw, Raw: raw}
	}
	op := "="
	if a.Append {
		op = "+="
	}
	if a.Naked {
		return Word{Value: a.Name.Value, Raw: raw}
	}
	if a.Array != nil {
		return Word{Value: a.Name.Value + op + b.slice(a.Array), Raw: raw, Dynamic: true}
	}
	w := Word{Value: a.Name.Value + op, Raw: raw}
	if a.Value != nil {
		v := b.word(a.Value)
		w.Value += v.Value
		w.Dynamic = v.Dynamic
	}
	return w
}

// stdin records the last heredoc or here-string body, as the Python lexer does.
func (b *bashWalker) stdin(seg *Segment, redirs []*syntax.Redirect) {
	for _, r := range redirs {
		switch r.Op {
		case syntax.Hdoc, syntax.DashHdoc:
			body := b.hdocBody(r)
			if r.Op == syntax.DashHdoc {
				body = stripLeadingTabs(body)
			}
			seg.Stdin, seg.HasStdin = body, true
		case syntax.WordHdoc:
			if r.Word != nil {
				seg.Stdin, seg.HasStdin = b.word(r.Word).Value+"\n", true
			}
		}
	}
}

// hdocBody returns the raw heredoc text with exactly one trailing newline.
func (b *bashWalker) hdocBody(r *syntax.Redirect) string {
	body := ""
	if r.Hdoc != nil {
		body = strings.TrimSuffix(b.slice(r.Hdoc), "\n")
	}
	// The node may extend over the closing delimiter line; drop it.
	delim := ""
	if r.Word != nil {
		delim = b.word(r.Word).Value
	}
	head, last := "", body
	if k := strings.LastIndexByte(body, '\n'); k >= 0 {
		head, last = body[:k], body[k+1:]
	}
	if strings.TrimRight(strings.TrimLeft(last, "\t"), "\r") == delim {
		if head == "" {
			return "\n"
		}
		body = head
	}
	return body + "\n"
}

func stripLeadingTabs(s string) string {
	lines := strings.Split(s, "\n")
	for i, ln := range lines {
		lines[i] = strings.TrimLeft(ln, "\t")
	}
	return strings.Join(lines, "\n")
}

// word resolves a word: quotes removed, escapes applied, expansions kept as
// their source text and flagged dynamic.
func (b *bashWalker) word(w *syntax.Word) Word {
	var out strings.Builder
	dyn := false
	for _, p := range w.Parts {
		text, d := b.part(p, false)
		out.WriteString(text)
		dyn = dyn || d
	}
	return Word{Value: out.String(), Raw: b.slice(w), Dynamic: dyn}
}

func (b *bashWalker) part(p syntax.WordPart, inDq bool) (string, bool) {
	switch x := p.(type) {
	case *syntax.Lit:
		if inDq {
			return unescapeDq(x.Value), false
		}
		return unescapeBare(x.Value), false
	case *syntax.SglQuoted:
		if x.Dollar {
			return decodeANSIC(x.Value), false
		}
		return x.Value, false
	case *syntax.DblQuoted:
		var out strings.Builder
		dyn := false
		for _, q := range x.Parts {
			text, d := b.part(q, true)
			out.WriteString(text)
			dyn = dyn || d
		}
		return out.String(), dyn
	case *syntax.CmdSubst:
		if body, ok := b.catHeredoc(x); ok {
			return body, false
		}
		return b.slice(x), true
	default: // ParamExp, ArithmExp, ProcSubst, ExtGlob, BraceExp
		return b.slice(p), true
	}
}

// catHeredoc resolves $(cat <<'EOF' ... EOF) to the heredoc text.
func (b *bashWalker) catHeredoc(cs *syntax.CmdSubst) (string, bool) {
	if cs.Backquotes || len(cs.Stmts) != 1 {
		return "", false
	}
	s := cs.Stmts[0]
	call, ok := s.Cmd.(*syntax.CallExpr)
	if !ok || s.Negated || s.Background || len(call.Assigns) > 0 || len(call.Args) != 1 || len(s.Redirs) != 1 {
		return "", false
	}
	if lit := call.Args[0].Lit(); lit != "cat" {
		return "", false
	}
	r := s.Redirs[0]
	if r.Op != syntax.Hdoc && r.Op != syntax.DashHdoc {
		return "", false
	}
	return strings.TrimSuffix(strings.TrimSuffix(b.hdocBody(r), "\n"), "\r"), true
}

// unescapeBare applies backslash escapes of unquoted text.
func unescapeBare(s string) string {
	if !strings.Contains(s, "\\") {
		return s
	}
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			i++
			if s[i] != '\n' {
				out.WriteByte(s[i])
			}
			continue
		}
		out.WriteByte(s[i])
	}
	return out.String()
}

// unescapeDq applies the backslash escapes bash honours inside "...".
func unescapeDq(s string) string {
	if !strings.Contains(s, "\\") {
		return s
	}
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			switch nxt := s[i+1]; {
			case strings.IndexByte("\"\\$`", nxt) >= 0:
				out.WriteByte(nxt)
			case nxt == '\n':
			default:
				out.WriteByte('\\')
				out.WriteByte(nxt)
			}
			i++
			continue
		}
		out.WriteByte(s[i])
	}
	return out.String()
}

// decodeANSIC decodes the body of $'...' with the escapes the Python lexer knows.
func decodeANSIC(s string) string {
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			if r, ok := ansiCEscapes[s[i+1]]; ok {
				out.WriteString(r)
			} else {
				out.WriteByte('\\')
				out.WriteByte(s[i+1])
			}
			i++
			continue
		}
		out.WriteByte(s[i])
	}
	return out.String()
}
