package shell

import (
	"regexp"
	"strings"
)

// lexer is a faithful port of shellparse._Lexer: a small, forgiving lexer
// for bash and PowerShell. It splits on && || ; | & newline and parens,
// removes quotes, resolves the common static message forms and marks words
// that depend on runtime expansion as dynamic.
type lexer struct {
	t   string
	n   int
	ps  bool
	esc byte
}

func newLexer(t string, d Dialect) *lexer {
	l := &lexer{t: t, n: len(t), ps: d == PowerShell, esc: '\\'}
	if l.ps {
		l.esc = '`'
	}
	return l
}

var hereStringDynamic = regexp.MustCompile(`\$[A-Za-z_({]`)

// sub returns t[a:b] clamped to the text like a Python slice.
func (l *lexer) sub(a, b int) string {
	if a < 0 {
		a = 0
	}
	if b > l.n {
		b = l.n
	}
	if a >= b {
		return ""
	}
	return l.t[a:b]
}

func (l *lexer) has(i int, prefix string) bool {
	return i >= 0 && i <= l.n && strings.HasPrefix(l.t[i:], prefix)
}

func (l *lexer) find(s string, from int) int {
	if from > l.n {
		return -1
	}
	if k := strings.Index(l.t[from:], s); k >= 0 {
		return from + k
	}
	return -1
}

func isVarStart(c byte) bool {
	return c == '_' || c == '{' || isAlnumASCII(c) || strings.IndexByte("@*#?$!", c) >= 0
}

func isAlnumASCII(c byte) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9'
}

// isAlnum approximates Python's str.isalnum: non-ASCII bytes (parts of
// UTF-8 letters) count as alphanumeric.
func isAlnum(c byte) bool { return isAlnumASCII(c) || c >= 0x80 }

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

// ---------- skipping helpers (used inside $( ... )) ----------

// skipSq: i at the opening quote; returns the index after the closing quote.
func (l *lexer) skipSq(i int) int {
	i++
	for i < l.n {
		if l.t[i] == '\'' {
			if l.ps && i+1 < l.n && l.t[i+1] == '\'' {
				i += 2
				continue
			}
			return i + 1
		}
		i++
	}
	return l.n
}

func (l *lexer) skipDq(i int) int {
	i++
	for i < l.n {
		c := l.t[i]
		if c == l.esc {
			i += 2
			continue
		}
		if l.has(i, "$(") {
			i = l.skipSubst(i + 2)
			continue
		}
		if c == '"' {
			if l.ps && i+1 < l.n && l.t[i+1] == '"' {
				i += 2
				continue
			}
			return i + 1
		}
		i++
	}
	return l.n
}

// readDelim reads a heredoc delimiter word starting at i.
func (l *lexer) readDelim(i int) (string, int) {
	var out strings.Builder
	for i < l.n && strings.IndexByte(" \t\r\n;&|()<>", l.t[i]) < 0 {
		c := l.t[i]
		switch {
		case c == '\'' || c == '"':
			j := l.find(string(c), i+1)
			if j < 0 {
				j = l.n
			}
			out.WriteString(l.sub(i+1, j))
			i = j + 1
		case c == '\\':
			out.WriteString(l.sub(i+1, i+2))
			i += 2
		default:
			out.WriteByte(c)
			i++
		}
	}
	return out.String(), i
}

type heredoc struct {
	delim     string
	stripTabs bool
}

// readHeredocBodies: i at the first char after the newline; consumes each
// pending heredoc body.
func (l *lexer) readHeredocBodies(i int, pending []heredoc) ([]string, int) {
	var bodies []string
	for _, hd := range pending {
		var lines []string
		for i < l.n {
			j := l.find("\n", i)
			end := l.n
			if j >= 0 {
				end = j
			}
			line := strings.TrimRight(l.sub(i, end), "\r")
			if j >= 0 {
				i = end + 1
			} else {
				i = l.n
			}
			check := line
			if hd.stripTabs {
				check = strings.TrimLeft(line, "\t")
			}
			if check == hd.delim {
				break
			}
			lines = append(lines, check)
		}
		bodies = append(bodies, strings.Join(lines, "\n"))
	}
	return bodies, i
}

// heredocStart reports a bash heredoc operator at i.
func (l *lexer) heredocStart(i int) (heredoc, int, bool) {
	if l.ps || !l.has(i, "<<") || l.has(i, "<<<") {
		return heredoc{}, 0, false
	}
	j := i + 2
	strip := j < l.n && l.t[j] == '-'
	if strip {
		j++
	}
	for j < l.n && (l.t[j] == ' ' || l.t[j] == '\t') {
		j++
	}
	delim, j := l.readDelim(j)
	if delim == "" {
		return heredoc{}, 0, false
	}
	return heredoc{delim, strip}, j, true
}

// skipSubst: i just after '$(' (or '('); returns the index after the
// matching ')'.
func (l *lexer) skipSubst(i int) int {
	depth := 1
	var pending []heredoc
	for i < l.n {
		c := l.t[i]
		if c == l.esc {
			i += 2
			continue
		}
		if c == '\'' {
			i = l.skipSq(i)
			continue
		}
		if c == '"' {
			i = l.skipDq(i)
			continue
		}
		if hd, next, ok := l.heredocStart(i); ok {
			pending = append(pending, hd)
			i = next
			continue
		}
		if c == '\n' && len(pending) > 0 {
			_, i = l.readHeredocBodies(i+1, pending)
			pending = nil
			continue
		}
		if c == '(' {
			depth++
		} else if c == ')' {
			depth--
			if depth == 0 {
				return i + 1
			}
		}
		i++
	}
	return l.n
}

// ---------- word pieces ----------

// substPiece: i at '$('; resolves $(cat <<EOF ... EOF).
func (l *lexer) substPiece(i int) (string, bool, int) {
	end := l.skipSubst(i + 2)
	raw := l.sub(i, end)
	if !l.ps {
		if body, ok := matchCatHeredoc(raw); ok {
			return body, false, end
		}
	}
	return raw, true, end
}

// varPiece: i at '$' followed by a variable start.
func (l *lexer) varPiece(i int) (string, int) {
	j := i + 1
	if j < l.n && l.t[j] == '{' {
		k := l.find("}", j)
		if k < 0 {
			k = l.n - 1
		}
		return l.sub(i, k+1), k + 1
	}
	for j < l.n && (isAlnum(l.t[j]) || l.t[j] == '_' || l.t[j] == ':' ||
		(j == i+1 && strings.IndexByte("@*#?$!", l.t[j]) >= 0)) {
		j++
		if j == i+2 && strings.IndexByte("@*#?$!0123456789", l.t[i+1]) >= 0 {
			break
		}
	}
	return l.sub(i, j), j
}

var psEscapes = map[byte]string{'n': "\n", 't': "\t", 'r': "\r", '0': "\x00"}

// dqPiece: i at '"'.
func (l *lexer) dqPiece(i int) (string, bool, int) {
	var out strings.Builder
	dyn := false
	i++
	for i < l.n {
		c := l.t[i]
		if c == l.esc && i+1 < l.n {
			nxt := l.t[i+1]
			switch {
			case l.ps:
				if s, ok := psEscapes[nxt]; ok {
					out.WriteString(s)
				} else {
					out.WriteByte(nxt)
				}
			case strings.IndexByte("\"\\$`", nxt) >= 0:
				out.WriteByte(nxt)
			case nxt == '\n':
			default:
				out.WriteByte(c)
				out.WriteByte(nxt)
			}
			i += 2
			continue
		}
		if c == '"' {
			if l.ps && i+1 < l.n && l.t[i+1] == '"' {
				out.WriteByte('"')
				i += 2
				continue
			}
			return out.String(), dyn, i + 1
		}
		if l.has(i, "$(") {
			text, d, next := l.substPiece(i)
			out.WriteString(text)
			dyn = dyn || d
			i = next
			continue
		}
		if c == '$' && i+1 < l.n && isVarStart(l.t[i+1]) {
			text, next := l.varPiece(i)
			out.WriteString(text)
			dyn = true
			i = next
			continue
		}
		if c == '`' && !l.ps {
			j := l.find("`", i+1)
			if j < 0 {
				j = l.n - 1
			}
			out.WriteString(l.sub(i, j+1))
			dyn = true
			i = j + 1
			continue
		}
		out.WriteByte(c)
		i++
	}
	return out.String(), dyn, l.n
}

func (l *lexer) sqPiece(i int) (string, int) {
	end := l.skipSq(i)
	var body string
	if end <= l.n && l.sub(end-1, end) == "'" && end-1 > i {
		body = l.sub(i+1, end-1)
	} else {
		body = l.sub(i+1, end)
	}
	if l.ps {
		body = strings.ReplaceAll(body, "''", "'")
	}
	return body, end
}

var ansiCEscapes = map[byte]string{
	'n': "\n", 't': "\t", 'r': "\r", '\\': "\\", '\'': "'", '"': "\"", 'a': "\a", 'e': "\x1b",
}

// ansiCPiece: i at "$'" (bash ANSI-C quoting).
func (l *lexer) ansiCPiece(i int) (string, int) {
	var out strings.Builder
	i += 2
	for i < l.n {
		c := l.t[i]
		if c == '\\' && i+1 < l.n {
			if s, ok := ansiCEscapes[l.t[i+1]]; ok {
				out.WriteString(s)
			} else {
				out.WriteByte('\\')
				out.WriteByte(l.t[i+1])
			}
			i += 2
			continue
		}
		if c == '\'' {
			return out.String(), i + 1
		}
		out.WriteByte(c)
		i++
	}
	return out.String(), l.n
}

// hereStringPiece: PowerShell @'\n...\n'@ or @"\n...\n"@ starting at i.
func (l *lexer) hereStringPiece(i int) (string, bool, int, bool) {
	if !l.ps || !(l.has(i, "@'") || l.has(i, "@\"")) {
		return "", false, 0, false
	}
	q := l.t[i+1]
	j := i + 2
	for j < l.n && (l.t[j] == ' ' || l.t[j] == '\t' || l.t[j] == '\r') {
		j++
	}
	if j >= l.n || l.t[j] != '\n' {
		return "", false, 0, false
	}
	start := j + 1
	closer := string(q) + "@"
	for k := start - 1; k < l.n; k++ {
		var mEnd int
		switch {
		case l.t[k] == '\r' && l.has(k+1, "\n"+closer):
			mEnd = k + 2 + len(closer)
		case l.t[k] == '\n' && l.has(k+1, closer):
			mEnd = k + 1 + len(closer)
		default:
			continue
		}
		body := ""
		if k >= start {
			body = l.sub(start, k)
		}
		return body, q == '"' && hereStringDynamic.MatchString(body), mEnd, true
	}
	rest := l.sub(start, l.n)
	return rest, q == '"' && strings.Contains(rest, "$"), l.n, true
}

// redirect matches Python's (\d*|&)(>>|>\||>&|>|<&|<) anchored at i and
// returns the index after the operator and the operator.
func (l *lexer) redirect(i int) (int, string, bool) {
	ops := []string{">>", ">|", ">&", ">", "<&", "<"}
	try := func(j int) (int, string, bool) {
		for _, op := range ops {
			if l.has(j, op) {
				return j + len(op), op, true
			}
		}
		return 0, "", false
	}
	j := i
	for j < l.n && isDigit(l.t[j]) {
		j++
	}
	if end, op, ok := try(j); ok {
		return end, op, true
	}
	if i < l.n && l.t[i] == '&' {
		return try(i + 1)
	}
	return 0, "", false
}

// wordEnd returns the index just past the word starting at i.
func (l *lexer) wordEnd(i int) int {
	for i < l.n {
		c := l.t[i]
		switch {
		case strings.IndexByte(" \t\r\n;|&()<>", c) >= 0:
			return i
		case c == l.esc:
			i += 2
		case c == '\'':
			i = l.skipSq(i)
		case c == '"':
			i = l.skipDq(i)
		case l.has(i, "$("):
			i = l.skipSubst(i + 2)
		default:
			i++
		}
	}
	return l.n
}

// ---------- main loop ----------

type lexRun struct {
	*lexer
	segs           []Segment
	seg            Segment
	word           strings.Builder
	inWord         bool
	rawStart       int
	dyn            bool
	pending        []heredoc
	herestringNext bool
}

func (r *lexRun) endWord(i int) {
	if r.inWord {
		w := Word{Value: r.word.String(), Raw: r.sub(r.rawStart, i), Dynamic: r.dyn}
		if r.herestringNext {
			r.seg.Stdin, r.seg.HasStdin = w.Value+"\n", true
			r.herestringNext = false
		} else {
			r.seg.Words = append(r.seg.Words, w)
		}
	}
	r.word.Reset()
	r.inWord, r.dyn = false, false
}

func (r *lexRun) endSeg() {
	if len(r.seg.Words) > 0 || r.seg.HasStdin {
		r.segs = append(r.segs, r.seg)
	}
	r.seg = Segment{}
}

func (r *lexRun) start(i int) {
	if !r.inWord {
		r.inWord, r.rawStart = true, i
	}
}

func (l *lexer) lex() []Segment {
	r := &lexRun{lexer: l}
	i := 0
	for i < l.n {
		i = r.step(i)
	}
	r.endWord(l.n)
	r.endSeg()
	return r.segs
}

// step consumes one token starting at i and returns the next index.
func (r *lexRun) step(i int) int {
	c := r.t[i]
	if isSpace(c) {
		r.endWord(i)
		return i + 1
	}
	if c == '\n' {
		return r.newline(i)
	}
	if c == '#' && !r.inWord {
		if j := r.find("\n", i); j >= 0 {
			return j
		}
		return r.n
	}
	if c == r.esc {
		if i+1 < r.n && (r.t[i+1] == '\r' || r.t[i+1] == '\n') {
			if r.t[i+1] == '\n' {
				return i + 2
			}
			return i + 3
		}
		r.start(i)
		r.word.WriteString(r.sub(i+1, i+2))
		return i + 2
	}
	if next, ok := r.operator(i); ok {
		return next
	}
	if hd, next, ok := r.heredocStart(i); ok {
		r.endWord(i)
		r.pending = append(r.pending, hd)
		return next
	}
	if !r.ps && r.has(i, "<<<") {
		r.endWord(i)
		r.herestringNext = true
		i += 3
		for i < r.n && (r.t[i] == ' ' || r.t[i] == '\t') {
			i++
		}
		return i
	}
	if !r.inWord {
		if next, ok := r.dropRedirect(i); ok {
			return next
		}
	}
	return r.piece(i)
}

func (r *lexRun) newline(i int) int {
	r.endWord(i)
	if len(r.pending) > 0 {
		var bodies []string
		bodies, i = r.readHeredocBodies(i+1, r.pending)
		if len(bodies) > 0 {
			r.seg.Stdin, r.seg.HasStdin = bodies[len(bodies)-1]+"\n", true
		}
		r.pending = nil
	} else {
		i++
	}
	r.endSeg()
	return i
}

// operator handles && || ; | & ( ).
func (r *lexRun) operator(i int) (int, bool) {
	c := r.t[i]
	if two := r.sub(i, i+2); two == "&&" || two == "||" {
		r.endWord(i)
		r.endSeg()
		return i + 2, true
	}
	isOp := c == ';' || c == '|' || (c == '&' && !r.ps && !r.has(i, "&>")) ||
		((c == '(' || c == ')') && !(r.ps && r.inWord))
	if !isOp {
		return 0, false
	}
	if c == '(' && r.ps {
		// PowerShell (expr) used as an argument: keep as a dynamic word
		r.endWord(i)
		end := r.skipSubst(i + 1)
		r.start(i)
		r.word.WriteString(r.sub(i, end))
		r.dyn = true
		r.endWord(end)
		return end, true
	}
	r.endWord(i)
	r.endSeg()
	return i + 1, true
}

// dropRedirect skips a redirection operator and its target word.
func (r *lexRun) dropRedirect(i int) (int, bool) {
	end, op, ok := r.redirect(i)
	if !ok {
		return 0, false
	}
	i = end
	if strings.HasSuffix(op, "&") && i < r.n && (isDigit(r.t[i]) || r.t[i] == '-') {
		return i + 1, true
	}
	for i < r.n && (r.t[i] == ' ' || r.t[i] == '\t') {
		i++
	}
	d := Bash
	if r.ps {
		d = PowerShell
	}
	return i + newLexer(r.t[i:], d).wordEnd(0), true
}

// piece reads one quoted / expanded / literal piece of a word.
func (r *lexRun) piece(i int) int {
	c := r.t[i]
	if text, d, next, ok := r.hereStringPiece(i); ok {
		r.start(i)
		r.word.WriteString(text)
		r.dyn = r.dyn || d
		return next
	}
	switch {
	case c == '\'':
		r.start(i)
		text, next := r.sqPiece(i)
		r.word.WriteString(text)
		return next
	case c == '"':
		r.start(i)
		text, d, next := r.dqPiece(i)
		r.word.WriteString(text)
		r.dyn = r.dyn || d
		return next
	case r.has(i, "$'") && !r.ps:
		r.start(i)
		text, next := r.ansiCPiece(i)
		r.word.WriteString(text)
		return next
	case r.has(i, "$("):
		r.start(i)
		text, d, next := r.substPiece(i)
		r.word.WriteString(text)
		r.dyn = r.dyn || d
		return next
	case c == '$' && i+1 < r.n && isVarStart(r.t[i+1]):
		r.start(i)
		text, next := r.varPiece(i)
		r.word.WriteString(text)
		r.dyn = true
		return next
	case c == '`' && !r.ps:
		r.start(i)
		j := r.find("`", i+1)
		if j < 0 {
			j = r.n - 1
		}
		r.word.WriteString(r.sub(i, j+1))
		r.dyn = true
		return j + 1
	}
	r.start(i)
	r.word.WriteByte(c)
	return i + 1
}

// matchCatHeredoc resolves "$(cat <<'EOF'\n...\nEOF\n)" to its body, like
// shellparse._CAT_HEREDOC (Go's regexp has no backreferences).
func matchCatHeredoc(raw string) (string, bool) {
	if !strings.HasPrefix(raw, "$(") || !strings.HasSuffix(raw, ")") || len(raw) < 3 {
		return "", false
	}
	s := strings.TrimLeft(raw[2:len(raw)-1], " \t\r\n\f\v")
	if !strings.HasPrefix(s, "cat") {
		return "", false
	}
	s = strings.TrimLeft(s[3:], " \t\r\n\f\v")
	if !strings.HasPrefix(s, "<<") {
		return "", false
	}
	s = strings.TrimPrefix(s[2:], "-")
	s = strings.TrimLeft(s, " \t\r\n\f\v")
	quote := ""
	if s != "" && (s[0] == '\'' || s[0] == '"') {
		quote, s = s[:1], s[1:]
	}
	k := 0
	for k < len(s) && (s[k] == '_' || isAlnumASCII(s[k])) {
		k++
	}
	if k == 0 || isDigit(s[0]) {
		return "", false
	}
	delim := s[:k]
	s = s[k:]
	if !strings.HasPrefix(s, quote) {
		return "", false
	}
	s = strings.TrimLeft(s[len(quote):], " \t")
	s = strings.TrimPrefix(s, "\r")
	if !strings.HasPrefix(s, "\n") {
		return "", false
	}
	after := s[1:]
	for p := 0; p < len(after); p++ {
		if after[p] != '\n' {
			continue
		}
		tail := strings.TrimLeft(after[p+1:], " \t")
		if strings.HasPrefix(tail, delim) && strings.TrimSpace(tail[len(delim):]) == "" {
			return strings.TrimSuffix(after[:p], "\r"), true
		}
	}
	return "", false
}
