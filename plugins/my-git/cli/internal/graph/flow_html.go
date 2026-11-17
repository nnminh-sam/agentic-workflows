package graph

import (
	"fmt"
	"io"
	"strings"
)

// Geometry of the flow diagram, in pixels.
const (
	boxW    = 270
	boxH    = 80
	colGap  = 110
	rowGap  = 18
	margin  = 20
	headerH = 28
)

// flowBox is one positioned box of the diagram.
type flowBox struct {
	x, y               int
	title, status, cmd string
	class              string // work, long, empty
}

// arrow classes: dashed = no PR, open = PR open, merged = merged.
const (
	arrowDashed = "dashed"
	arrowOpen   = "open"
	arrowMerged = "merged"
)

// RenderFlowHTML writes the flow view as a self-contained offline page:
// one column per template branch, a box per branch, and arrows along the
// transitions (dashed = no PR yet, solid = PR open, green = merged).
func RenderFlowHTML(w io.Writer, f Flow, title string) error {
	if title == "" {
		title = "mygit flow"
	}
	sym := SymbolsFor(false)
	colX := map[string]int{}
	for i, c := range f.Columns {
		colX[c.Key] = margin + i*(boxW+colGap)
	}
	longBox := map[string]*flowBox{}
	var boxes []*flowBox
	for _, l := range f.Long {
		bx := &flowBox{x: colX[l.Key], y: margin + headerH, title: longTitle(l, sym),
			status: LongStatus(l, sym), cmd: l.Next, class: "long"}
		longBox[l.Key] = bx
		boxes = append(boxes, bx)
	}
	colRows := map[string]int{}
	var arrows strings.Builder
	for _, it := range f.Work {
		key := it.Key
		if _, ok := colX[key]; !ok {
			key = "work"
		}
		bx := &flowBox{x: colX[key], y: margin + headerH + colRows[key]*(boxH+rowGap),
			title: it.Name, status: WorkStatus(it, sym), cmd: "next: " + it.Next, class: "work"}
		colRows[key]++
		boxes = append(boxes, bx)
		if target := boxForBranch(f, longBox, it.Base); target != nil {
			writeArrow(&arrows, bx, target, workArrow(it), "")
		}
	}
	if colRows["work"] == 0 {
		bx := &flowBox{x: colX["work"], y: margin + headerH, title: "no work branches",
			status: "start one with mygit branch new", class: "empty"}
		boxes = append(boxes, bx)
		colRows["work"] = 1
	}
	for _, tr := range f.Transitions {
		from := longBox[tr.From]
		for _, to := range tr.To {
			if from == nil || longBox[to] == nil {
				continue
			}
			style := arrowDashed
			if l := findLong(f, tr.From); l != nil && l.OpenPR != nil {
				style = arrowOpen
			}
			writeArrow(&arrows, from, longBox[to], style, tr.ID)
		}
	}
	height := margin*2 + headerH
	for _, n := range colRows {
		height = max(height, margin*2+headerH+n*(boxH+rowGap))
	}
	height = max(height, margin*2+headerH+boxH+rowGap)
	width := margin*2 + len(f.Columns)*(boxW+colGap) - colGap

	var b strings.Builder
	writeHead(&b, title, flowCSS)
	fmt.Fprintf(&b, "<h1>%s</h1>\n<p class=\"sub\">workflow %s · remote %s</p>\n", esc(title), esc(f.Workflow), esc(f.Remote))
	fmt.Fprintf(&b, "<div class=\"graph\"><svg width=\"%d\" height=\"%d\" role=\"img\" aria-label=\"workflow\">\n", width, height)
	b.WriteString(arrowDefs)
	for _, c := range f.Columns {
		name := c.Name
		if name == "" {
			name = "work branches"
		}
		fmt.Fprintf(&b, "<text class=\"colhead\" x=\"%d\" y=\"%d\">%s</text>\n", colX[c.Key], margin+12, esc(strings.ToUpper(c.Key)+" · "+name))
	}
	b.WriteString(arrows.String())
	for _, bx := range boxes {
		writeBox(&b, bx)
	}
	b.WriteString("</svg></div>\n")
	for _, n := range f.Next {
		fmt.Fprintf(&b, "<p><strong>next:</strong> <code class=\"mono\">%s</code></p>\n", esc(n))
	}
	if len(f.Ignored) > 0 {
		fmt.Fprintf(&b, "<p class=\"legend\">outside the workflow: %s</p>\n", esc(strings.Join(f.Ignored, ", ")))
	}
	b.WriteString("<p class=\"legend\">dashed arrow = no PR yet · solid = PR open · green = merged · ⛨ protected</p>\n")
	for _, warn := range f.Warnings {
		fmt.Fprintf(&b, "<p class=\"legend\">note: %s</p>\n", esc(warn))
	}
	b.WriteString("</body></html>\n")
	_, err := io.WriteString(w, b.String())
	return err
}

const flowCSS = `
.box{fill:var(--card);stroke:var(--line);stroke-width:1.5}
.box.long{stroke:var(--fg)}.box.empty{stroke-dasharray:4 4}
.t{fill:var(--fg);font-weight:600;font-size:13px}
.s{fill:var(--muted);font-size:12px}
.c{fill:var(--link);font-size:12px;font-family:ui-monospace,Menlo,Consolas,monospace}
.colhead{fill:var(--muted);font-size:11px;letter-spacing:.06em}
.a{fill:none;stroke-width:2}
.a.dashed{stroke:var(--muted);stroke-dasharray:6 5}
.a.open{stroke:var(--link)}
.a.merged{stroke:var(--ok)}
.al{fill:var(--muted);font-size:11px}
`

const arrowDefs = `<defs>
<marker id="m-dashed" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--muted)"/></marker>
<marker id="m-open" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--link)"/></marker>
<marker id="m-merged" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--ok)"/></marker>
</defs>
`

func workArrow(it WorkItem) string {
	switch it.State {
	case StateMerged:
		return arrowMerged
	case StatePROpen:
		return arrowOpen
	}
	return arrowDashed
}

// boxForBranch finds the long box whose branch name is name.
func boxForBranch(f Flow, boxes map[string]*flowBox, name string) *flowBox {
	for _, l := range f.Long {
		if l.Name == name {
			return boxes[l.Key]
		}
	}
	return nil
}

func findLong(f Flow, key string) *LongBranch {
	for i := range f.Long {
		if f.Long[i].Key == key {
			return &f.Long[i]
		}
	}
	return nil
}

// writeArrow draws a curve from one box's side to the other's facing side.
func writeArrow(b *strings.Builder, from, to *flowBox, style, label string) {
	x1, x2 := from.x+boxW, to.x
	if to.x < from.x {
		x1, x2 = from.x, to.x+boxW
	}
	y1, y2 := from.y+boxH/2, to.y+boxH/2
	mx := (x1 + x2) / 2
	fmt.Fprintf(b, "<path class=\"a %s\" d=\"M%d %d C%d %d %d %d %d %d\" marker-end=\"url(#m-%s)\"/>\n",
		style, x1, y1, mx, y1, mx, y2, x2, y2, style)
	if label != "" {
		fmt.Fprintf(b, "<text class=\"al\" x=\"%d\" y=\"%d\" text-anchor=\"middle\">%s</text>\n", mx, (y1+y2)/2-6, esc(label))
	}
}

func writeBox(b *strings.Builder, bx *flowBox) {
	fmt.Fprintf(b, "<g><title>%s</title><rect class=\"box %s\" x=\"%d\" y=\"%d\" width=\"%d\" height=\"%d\" rx=\"8\"/>\n",
		esc(bx.title+"\n"+bx.status+"\n"+bx.cmd), bx.class, bx.x, bx.y, boxW, boxH)
	tx := bx.x + 12
	fmt.Fprintf(b, "<text class=\"t\" x=\"%d\" y=\"%d\">%s</text>\n", tx, bx.y+22, esc(truncate(bx.title, 34, "…")))
	fmt.Fprintf(b, "<text class=\"s\" x=\"%d\" y=\"%d\">%s</text>\n", tx, bx.y+44, esc(truncate(bx.status, 40, "…")))
	if bx.cmd != "" {
		fmt.Fprintf(b, "<text class=\"c\" x=\"%d\" y=\"%d\">%s</text>\n", tx, bx.y+66, esc(truncate(bx.cmd, 38, "…")))
	}
	b.WriteString("</g>\n")
}
