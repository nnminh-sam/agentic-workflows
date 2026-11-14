package graph

import (
	"fmt"
	"html"
	"io"
	"strings"
)

// HTMLOptions control RenderHTML.
type HTMLOptions struct {
	Title string
	// Protected reports whether a branch name is protected; nil = none.
	Protected func(name string) bool
}

// Geometry of the SVG lanes, in pixels.
const (
	rowH  = 30
	laneW = 18
	padX  = 14
)

// pageCSS is shared by the graph and flow pages: light and dark palettes via
// prefers-color-scheme, no external resources.
const pageCSS = `
:root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--card:#f6f8fa;
--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700;--link:#0969da;
--l0:#0969da;--l1:#1a7f37;--l2:#bf3989;--l3:#9a6700;--l4:#8250df;--l5:#cf222e}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8d96a0;
--line:#30363d;--card:#161b22;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--link:#4493f8;
--l0:#4493f8;--l1:#3fb950;--l2:#db61a2;--l3:#d29922;--l4:#a371f7;--l5:#f85149}}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font:14px/1.4 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
h1{font-size:18px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 16px}
.mono,.sha{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.graph{display:flex;align-items:flex-start;overflow-x:auto}
.graph svg{flex:none}
.rows{flex:1;min-width:0}
.row{height:30px;display:flex;align-items:center;gap:10px;white-space:nowrap}
.sha{color:var(--warn)}
.ref{border:1px solid var(--line);border-radius:10px;padding:0 7px;font-size:12px;background:var(--card)}
.ref.local{color:var(--ok)}.ref.remote{color:var(--bad)}.ref.tag{color:var(--warn)}
.ref.head{font-weight:600;color:var(--link)}
.subj{overflow:hidden;text-overflow:ellipsis}
.ok{color:var(--ok)}.bad{color:var(--bad)}.muted{color:var(--muted)}.warn{color:var(--warn)}
.legend{color:var(--muted);margin-top:16px;font-size:13px}
`

// RenderHTML writes the graph as one self-contained offline HTML page.
func RenderHTML(w io.Writer, repo Repo, rows []Row, opt HTMLOptions) error {
	title := opt.Title
	if title == "" {
		title = "mygit graph"
	}
	var b strings.Builder
	writeHead(&b, title, "")
	fmt.Fprintf(&b, "<h1>%s</h1>\n<p class=\"sub\">%d commits · remote %s</p>\n",
		esc(title), len(rows), esc(repo.Remote))
	b.WriteString("<div class=\"graph\">\n")
	writeLanesSVG(&b, rows)
	b.WriteString("<div class=\"rows\">\n")
	sym := SymbolsFor(false)
	for _, r := range rows {
		writeHTMLRow(&b, repo, repo.Commits[r.Commit], opt, sym)
	}
	b.WriteString("</div></div>\n")
	if repo.Limited {
		b.WriteString("<p class=\"legend\">More history not shown (raise --limit).</p>\n")
	}
	fmt.Fprintf(&b, "<p class=\"legend\">%s</p>\n", esc(Legend(sym)))
	for _, warn := range repo.Warnings {
		fmt.Fprintf(&b, "<p class=\"legend\">note: %s</p>\n", esc(warn))
	}
	b.WriteString("</body></html>\n")
	_, err := io.WriteString(w, b.String())
	return err
}

func writeHead(b *strings.Builder, title, extraCSS string) {
	fmt.Fprintf(b, "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"+
		"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"+
		"<meta name=\"color-scheme\" content=\"light dark\">\n<title>%s</title>\n<style>%s%s</style></head><body>\n",
		esc(title), pageCSS, extraCSS)
}

func laneX(i int) int { return padX + i*laneW }
func rowY(i int) int  { return i*rowH + rowH/2 }

// writeLanesSVG draws edges as lines or curves between row centers and a
// node per commit. Merge commits get a hollow node.
func writeLanesSVG(b *strings.Builder, rows []Row) {
	width := 0
	for _, r := range rows {
		width = max(width, r.Width)
	}
	fmt.Fprintf(b, "<svg width=\"%d\" height=\"%d\" role=\"img\" aria-label=\"commit lanes\">\n",
		laneX(width)+padX/2, len(rows)*rowH)
	for i, r := range rows {
		for _, e := range r.Edges {
			x1, y1, x2, y2 := laneX(e.From), rowY(i), laneX(e.To), rowY(i+1)
			lane := max(e.From, e.To)
			if x1 == x2 {
				fmt.Fprintf(b, "<line x1=\"%d\" y1=\"%d\" x2=\"%d\" y2=\"%d\" stroke=\"var(--l%d)\" stroke-width=\"2\"/>\n",
					x1, y1, x2, y2, lane%6)
				continue
			}
			mid := (y1 + y2) / 2
			fmt.Fprintf(b, "<path d=\"M%d %d C%d %d %d %d %d %d\" fill=\"none\" stroke=\"var(--l%d)\" stroke-width=\"2\"/>\n",
				x1, y1, x1, mid, x2, mid, x2, y2, lane%6)
		}
	}
	for i, r := range rows {
		fill := fmt.Sprintf("var(--l%d)", r.Col%6)
		if r.mergeEdges() > 1 {
			fill = "var(--bg)"
		}
		fmt.Fprintf(b, "<circle cx=\"%d\" cy=\"%d\" r=\"5\" fill=\"%s\" stroke=\"var(--l%d)\" stroke-width=\"2\"/>\n",
			laneX(r.Col), rowY(i), fill, r.Col%6)
	}
	b.WriteString("</svg>\n")
}

// mergeEdges counts the edges leaving the row's node (its parents shown).
func (r Row) mergeEdges() int {
	n := 0
	for _, e := range r.Edges {
		if e.From == r.Col {
			n++
		}
	}
	return n
}

func writeHTMLRow(b *strings.Builder, repo Repo, c Commit, opt HTMLOptions, sym Symbols) {
	fmt.Fprintf(b, "<div class=\"row\"><span class=\"sha\" title=\"%s\">%s</span>", esc(c.SHA), esc(c.Short()))
	for _, r := range c.Refs {
		cls, text := "local", r.Name
		switch {
		case r.Head:
			cls, text = "head", "HEAD "+sym.Arrow+" "+r.Name
		case r.Kind == RefHead:
			cls = "head"
		case r.Kind == RefTag:
			cls, text = "tag", "tag: "+r.Name
		case r.Kind == RefRemote, r.Kind == RefOther:
			cls = "remote"
		}
		fmt.Fprintf(b, "<span class=\"ref %s\">%s</span>", cls, esc(text))
	}
	if protectedCommit(c, opt.Protected) {
		fmt.Fprintf(b, "<span class=\"warn\" title=\"protected\">%s</span>", sym.Shield)
	}
	fmt.Fprintf(b, "<span class=\"subj\" title=\"%s\">%s</span>", esc(c.Subject), esc(c.Subject))
	for i, x := range extras(repo, c, sym) {
		cls := "muted"
		switch {
		case i == 0 && c.Sig.Status == SigOK:
			cls = "ok"
		case i == 0 && c.Sig.Status == SigBad:
			cls = "bad"
		case i > 0:
			cls = "warn"
		}
		fmt.Fprintf(b, "<span class=\"%s\">%s</span>", cls, esc(plain(x)))
	}
	b.WriteString("</div>\n")
}

func esc(s string) string { return html.EscapeString(s) }
