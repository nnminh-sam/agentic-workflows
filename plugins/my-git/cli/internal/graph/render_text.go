package graph

import (
	"fmt"
	"io"
	"strings"
	"unicode/utf8"
)

// TextOptions control RenderText.
type TextOptions struct {
	ASCII bool // use * | / \ - and plain-ASCII symbols
	Color bool // ANSI colors
	// Protected reports whether a branch name is protected (⛨); nil = none.
	Protected func(name string) bool
	// NoLegend omits the legend line.
	NoLegend bool
}

// Symbols is the glyph set for annotations.
type Symbols struct {
	Node, Arrow, Shield, Up, Down, OK, Bad, Unknown, Check, Cross, Pending, Dot, Ellipsis string
}

var (
	unicodeSymbols = Symbols{"*", "→", "⛨", "↑", "↓", "✔", "✗", "?", "✓", "✗", "…", "·", "…"}
	asciiSymbols   = Symbols{"*", "->", "[P]", "^", "v", "ok", "x", "?", "ok", "x", "...", "|", "..."}
)

// SymbolsFor returns the ASCII or Unicode symbol set.
func SymbolsFor(ascii bool) Symbols {
	if ascii {
		return asciiSymbols
	}
	return unicodeSymbols
}

// ANSI color codes.
const (
	cReset  = "\x1b[0m"
	cBold   = "\x1b[1m"
	cDim    = "\x1b[2m"
	cRed    = "\x1b[31m"
	cGreen  = "\x1b[32m"
	cYellow = "\x1b[33m"
	cCyan   = "\x1b[36m"
)

// laneColors cycle by lane index.
var laneColors = []string{"\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[34m", "\x1b[35m", "\x1b[36m"}

// Column width caps.
const (
	maxRefsWidth    = 48
	maxSubjectWidth = 52
)

// segment is a piece of text with an optional ANSI color.
type segment struct{ text, color string }

// line is a list of segments.
type line []segment

func (l line) width() int {
	n := 0
	for _, s := range l {
		n += utf8.RuneCountInString(s.text)
	}
	return n
}

func (l line) render(color bool) string {
	var b strings.Builder
	for _, s := range l {
		if color && s.color != "" && s.text != "" {
			b.WriteString(s.color + s.text + cReset)
		} else {
			b.WriteString(s.text)
		}
	}
	return b.String()
}

// pad returns l padded with spaces to width w.
func (l line) pad(w int) line {
	if n := l.width(); n < w {
		return append(l, segment{text: strings.Repeat(" ", w-n)})
	}
	return l
}

// RenderText writes the graph as terminal text: one line per commit, a
// connector line wherever lanes bend, and a legend.
func RenderText(w io.Writer, repo Repo, rows []Row, opt TextOptions) error {
	sym := SymbolsFor(opt.ASCII)
	cols := buildColumns(repo, rows, opt, sym)
	graphW, refsW, subjW := 0, 0, 0
	for _, c := range cols {
		graphW = max(graphW, c.graph.width())
		refsW = max(refsW, c.refs.width())
		subjW = max(subjW, c.subject.width())
	}
	var b strings.Builder
	for i, c := range cols {
		l := c.graph.pad(graphW)
		l = append(l, segment{text: " "}, segment{c.sha, cYellow}, segment{text: " "})
		l = append(l, c.refs.pad(refsW)...)
		if refsW > 0 {
			l = append(l, segment{text: "  "})
		}
		l = append(l, c.subject.pad(subjW)...)
		for _, e := range c.extras {
			l = append(l, segment{text: "  "})
			l = append(l, e...)
		}
		b.WriteString(strings.TrimRight(l.render(opt.Color), " ") + "\n")
		if rows[i].Bends() {
			b.WriteString(strings.TrimRight(connectorLine(rows[i], opt).render(opt.Color), " ") + "\n")
		}
	}
	if repo.Limited {
		b.WriteString(dim(opt, fmt.Sprintf("%s more history not shown (raise --limit)", sym.Ellipsis)) + "\n")
	}
	if !opt.NoLegend {
		b.WriteString(dim(opt, Legend(sym)) + "\n")
	}
	for _, warn := range repo.Warnings {
		b.WriteString(dim(opt, "note: "+warn) + "\n")
	}
	_, err := io.WriteString(w, b.String())
	return err
}

// Legend returns the legend line.
func Legend(sym Symbols) string {
	return fmt.Sprintf("legend  %s protected (BR001/BR002) %s %s ahead / %s behind origin %s %s signature ok %s %s unsigned/bad",
		sym.Shield, sym.Dot, sym.Up, sym.Down, sym.Dot, sym.OK, sym.Dot, sym.Bad)
}

func dim(opt TextOptions, s string) string {
	if opt.Color {
		return cDim + s + cReset
	}
	return s
}

// textColumns are the rendered pieces of one commit line.
type textColumns struct {
	graph, refs, subject line
	sha                  string
	extras               []line
}

func buildColumns(repo Repo, rows []Row, opt TextOptions, sym Symbols) []textColumns {
	cols := make([]textColumns, len(rows))
	for i, r := range rows {
		c := repo.Commits[r.Commit]
		cols[i] = textColumns{
			graph:   commitLine(r, opt, sym),
			sha:     c.Short(),
			refs:    refsLine(c, opt, sym),
			subject: line{{text: truncate(c.Subject, maxSubjectWidth, sym.Ellipsis)}},
			extras:  extras(repo, c, sym),
		}
	}
	return cols
}

// commitLine draws the lanes of a commit row: the node and pass-through lines.
func commitLine(r Row, opt TextOptions, sym Symbols) line {
	var l line
	vbar := "│"
	if opt.ASCII {
		vbar = "|"
	}
	for j := 0; j < r.Width; j++ {
		if j > 0 {
			l = append(l, segment{text: " "})
		}
		switch {
		case j == r.Col:
			l = append(l, segment{sym.Node, cBold})
		case j < len(r.Pass) && r.Pass[j]:
			l = append(l, segment{vbar, laneColor(j)})
		default:
			l = append(l, segment{text: " "})
		}
	}
	return l
}

// connectorLine draws the bends below a row from Row.Connector masks.
func connectorLine(r Row, opt TextOptions) line {
	cells := r.Connector()
	l := make(line, len(cells))
	for x, m := range cells {
		l[x] = segment{Glyph(m, opt.ASCII), laneColor((x + 1) / 2)}
	}
	return l
}

// Glyph maps a connector cell mask to a box-drawing (or ASCII) character.
func Glyph(m uint8, ascii bool) string {
	u, a := glyphFor(m)
	if ascii {
		return a
	}
	return u
}

func glyphFor(m uint8) (unicode, ascii string) {
	switch m {
	case 0:
		return " ", " "
	case up, down, up | down:
		return "│", "|"
	case left, right, left | right:
		return "─", "-"
	case up | down | right:
		return "├", "|"
	case up | down | left:
		return "┤", "|"
	case left | down:
		return "╮", "\\"
	case right | down:
		return "╭", "/"
	case up | left:
		return "╯", "/"
	case up | right:
		return "╰", "\\"
	case left | right | down:
		return "┬", "-"
	case left | right | up:
		return "┴", "-"
	default:
		return "┼", "+"
	}
}

func laneColor(i int) string { return laneColors[i%len(laneColors)] }

// refsLine renders "(HEAD → main, origin/main, tag: v1) ⛨".
func refsLine(c Commit, opt TextOptions, sym Symbols) line {
	if len(c.Refs) == 0 {
		return nil
	}
	l := line{{text: "("}}
	for i, r := range c.Refs {
		if i > 0 {
			l = append(l, segment{text: ", "})
		}
		l = append(l, refSegments(r, sym)...)
	}
	l = append(l, segment{text: ")"})
	if l.width() > maxRefsWidth {
		l = line{{text: truncate(plain(l), maxRefsWidth, sym.Ellipsis)}}
	}
	if protectedCommit(c, opt.Protected) {
		l = append(l, segment{text: " "}, segment{sym.Shield, cYellow})
	}
	return l
}

func refSegments(r Ref, sym Symbols) line {
	switch {
	case r.Head:
		return line{{"HEAD " + sym.Arrow + " ", cCyan + cBold}, {r.Name, cGreen + cBold}}
	case r.Kind == RefHead:
		return line{{"HEAD", cCyan + cBold}}
	case r.Kind == RefTag:
		return line{{"tag: " + r.Name, cYellow + cBold}}
	case r.Kind == RefBranch:
		return line{{r.Name, cGreen}}
	default:
		return line{{r.Name, cRed}}
	}
}

func plain(l line) string { return l.render(false) }

// protectedCommit reports whether a protected branch (local, or remote with
// its remote prefix removed) points at c.
func protectedCommit(c Commit, protected func(string) bool) bool {
	if protected == nil {
		return false
	}
	for _, r := range c.Refs {
		if r.Kind != RefBranch && r.Kind != RefRemote {
			continue
		}
		if protected(r.Name) {
			return true
		}
		if _, rest, ok := strings.Cut(r.Name, "/"); ok && protected(rest) {
			return true
		}
	}
	return false
}

// extras are the trailing annotations: signature, ahead/behind, PR.
func extras(repo Repo, c Commit, sym Symbols) []line {
	out := []line{sigLine(c.Sig, sym)}
	seenPR := map[int]bool{}
	for _, r := range c.Refs {
		if r.Kind != RefBranch && r.Kind != RefRemote {
			continue
		}
		b, ok := repo.Branch(r.Name)
		if !ok {
			continue
		}
		if t := trackLine(b, repo.Remote, sym); t != nil {
			if localRefs(c) > 1 {
				t = append(line{{b.Name + ": ", cDim}}, t...)
			}
			out = append(out, t)
		}
		name := b.Name
		if b.Remote {
			name = strings.TrimPrefix(name, repo.Remote+"/")
		}
		if pr, ok := repo.OpenPR(name); ok && !seenPR[pr.Number] {
			seenPR[pr.Number] = true
			out = append(out, prLine(pr, sym))
		}
	}
	return out
}

// sigLine renders "✔ ssh", "✔ github", "✗ unsigned", "✗ bad", "? ssh".
func sigLine(s Signature, sym Symbols) line {
	kind := s.Kind
	switch s.Status {
	case SigOK:
		if kind == "" {
			kind = "signed"
		}
		return line{{sym.OK + " " + kind, cGreen}}
	case SigBad:
		return line{{sym.Bad + " bad", cRed}}
	case SigUnknown:
		if kind == "" {
			kind = "unverified"
		}
		return line{{sym.Unknown + " " + kind, cYellow}}
	default:
		return line{{sym.Bad + " unsigned", cDim}}
	}
}

// trackLine renders a local branch's position against its upstream.
func trackLine(b Branch, remote string, sym Symbols) line {
	if b.Remote {
		return nil
	}
	var parts []string
	switch {
	case b.Gone:
		parts = append(parts, "upstream gone")
	case b.Upstream == "":
		parts = append(parts, "not on "+remote)
	default:
		if b.Ahead > 0 {
			parts = append(parts, fmt.Sprintf("%s%d not on %s", sym.Up, b.Ahead, remoteOf(b.Upstream, remote)))
		}
		if b.Behind > 0 {
			parts = append(parts, fmt.Sprintf("%s%d behind", sym.Down, b.Behind))
		}
	}
	if len(parts) == 0 {
		return nil
	}
	return line{{strings.Join(parts, " "), cYellow}}
}

// remoteOf returns the remote part of an upstream like "origin/main".
func remoteOf(upstream, fallback string) string {
	if r, _, ok := strings.Cut(upstream, "/"); ok {
		return r
	}
	return fallback
}

// prLine renders "PR #18 open · checks ✓".
func prLine(pr PR, sym Symbols) line {
	state := "open"
	if pr.Draft {
		state = "draft"
	}
	text := fmt.Sprintf("PR #%d %s", pr.Number, state)
	color := cCyan
	switch pr.Checks {
	case ChecksPass:
		text += " " + sym.Dot + " checks " + sym.Check
	case ChecksFail:
		text += " " + sym.Dot + " checks " + sym.Cross
		color = cRed
	case ChecksPending:
		text += " " + sym.Dot + " checks " + sym.Pending
	}
	return line{{text, color}}
}

// truncate shortens s to at most n runes, ending with ellipsis.
func truncate(s string, n int, ellipsis string) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	keep := n - utf8.RuneCountInString(ellipsis)
	return string([]rune(s)[:max(keep, 0)]) + ellipsis
}

// localRefs counts the local branches decorating c.
func localRefs(c Commit) int {
	n := 0
	for _, r := range c.Refs {
		if r.Kind == RefBranch {
			n++
		}
	}
	return n
}
