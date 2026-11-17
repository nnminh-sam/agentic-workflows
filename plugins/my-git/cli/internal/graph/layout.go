package graph

// Lane layout
//
// Layout walks the commits in topological order (children before parents,
// as `git log --topo-order` prints them) and keeps a list of lanes. Each
// lane holds the SHA of the commit it is waiting for ("" = free lane):
//
//  1. A commit goes into the lane that waits for it; a commit nobody waits
//     for (a branch tip) takes the first free lane, or a new one.
//  2. Its lane now waits for its first parent. If another lane already
//     waits for that parent, the two converge: the right-hand one ends and
//     an edge joins it into the left-hand one (drawn ├─╯).
//  3. Every further parent (a merge) joins the lane already waiting for it,
//     or opens a new lane to the right of the commit (drawn ├─╮).
//
// Lanes never shift sideways, so a lane keeps its column for its whole
// life; freed columns are reused by later tips. The result is a list of
// rows, each with the commit's column and the edges that connect this row
// to the next one. Renderers turn edges into glyphs (text) or curves (SVG).

// Edge connects lane From in a commit row to lane To in the next row.
// From == To is a straight vertical line.
type Edge struct {
	From int `json:"from"`
	To   int `json:"to"`
}

// Row is one laid-out commit.
type Row struct {
	Commit int `json:"commit"` // index into the commits passed to Layout
	Col    int `json:"col"`    // lane of the commit's node
	// Pass marks the lanes (other than Col) that have a line passing
	// through the commit row.
	Pass []bool `json:"pass"`
	// Edges connect this row to the next one, verticals included.
	Edges []Edge `json:"edges"`
	// Width is the number of lanes this row and its connector use.
	Width int `json:"width"`
}

// Bends reports whether the row's edges include anything but verticals,
// i.e. whether a text renderer needs a connector line below the commit.
func (r Row) Bends() bool {
	for _, e := range r.Edges {
		if e.From != e.To {
			return true
		}
	}
	return false
}

// Layout assigns each commit a lane and computes the edges between rows.
// It is pure and deterministic: the same commits give the same rows.
func Layout(commits []Commit) []Row {
	rows := make([]Row, 0, len(commits))
	var lanes []string
	for i, c := range commits {
		col := indexOf(lanes, c.SHA)
		if col < 0 {
			col = firstFree(lanes, 0)
			lanes = grow(lanes, col)
		}
		row := Row{Commit: i, Col: col, Pass: passing(lanes, col)}
		next := append([]string(nil), lanes...)
		next[col] = ""
		row.Edges = verticals(lanes, col)
		next, row.Edges = placeFirstParent(next, row.Edges, col, c.Parents)
		if len(c.Parents) > 1 {
			next, row.Edges = placeMergeParents(next, row.Edges, col, c.Parents[1:])
		}
		next = trim(next)
		row.Width = max(len(lanes), len(next), maxEdgeLane(row.Edges)+1)
		rows = append(rows, row)
		lanes = next
	}
	return rows
}

// placeFirstParent continues the commit's lane to its first parent. When
// another lane already waits for that parent the lanes converge: a lane to
// the left absorbs the commit's lane; a lane to the right is pulled into the
// commit's column so the graph leans left.
func placeFirstParent(next []string, edges []Edge, col int, parents []string) ([]string, []Edge) {
	if len(parents) == 0 {
		return next, edges // root commit: the lane ends here
	}
	p := parents[0]
	k := indexOf(next, p)
	switch {
	case k < 0:
		next[col] = p
		edges = append(edges, Edge{col, col})
	case k < col:
		edges = append(edges, Edge{col, k})
	default: // k > col: pull lane k into col
		next[k], next[col] = "", p
		edges = redirect(edges, k, col)
		edges = append(edges, Edge{col, col})
	}
	return next, edges
}

// placeMergeParents joins each further parent to the lane already waiting
// for it, or opens a new lane to the right of col.
func placeMergeParents(next []string, edges []Edge, col int, parents []string) ([]string, []Edge) {
	for _, p := range parents {
		k := indexOf(next, p)
		if k < 0 && next[col] == "" {
			k = col // the first parent left this column: reuse it
			next[k] = p
		} else if k < 0 {
			k = firstFree(next, col+1)
			next = grow(next, k)
			next[k] = p
		}
		edges = append(edges, Edge{col, k})
	}
	return next, edges
}

// verticals returns a straight edge for every busy lane except col.
func verticals(lanes []string, col int) []Edge {
	var edges []Edge
	for j, s := range lanes {
		if s != "" && j != col {
			edges = append(edges, Edge{j, j})
		}
	}
	return edges
}

// redirect retargets the vertical edge of lane from so it ends in lane to.
func redirect(edges []Edge, from, to int) []Edge {
	for i, e := range edges {
		if e.From == from && e.To == from {
			edges[i].To = to
		}
	}
	return edges
}

func passing(lanes []string, col int) []bool {
	pass := make([]bool, len(lanes))
	for j, s := range lanes {
		pass[j] = s != "" && j != col
	}
	return pass
}

func indexOf(lanes []string, sha string) int {
	for i, s := range lanes {
		if s == sha {
			return i
		}
	}
	return -1
}

// firstFree returns the first free lane at or after from (possibly
// len(lanes), meaning "append a lane").
func firstFree(lanes []string, from int) int {
	for i := from; i < len(lanes); i++ {
		if lanes[i] == "" {
			return i
		}
	}
	return max(from, len(lanes))
}

// grow extends lanes so index i exists.
func grow(lanes []string, i int) []string {
	for len(lanes) <= i {
		lanes = append(lanes, "")
	}
	return lanes
}

// trim drops free lanes at the right edge.
func trim(lanes []string) []string {
	for len(lanes) > 0 && lanes[len(lanes)-1] == "" {
		lanes = lanes[:len(lanes)-1]
	}
	return lanes
}

func maxEdgeLane(edges []Edge) int {
	m := -1
	for _, e := range edges {
		m = max(m, e.From, e.To)
	}
	return m
}

// Connection bits of one connector cell.
const (
	up    = 1
	down  = 2
	left  = 4
	right = 8
)

// Connector returns the connector line below a row as a mask per cell.
// Cell 2*i is lane i; odd cells are the gaps between lanes.
func (r Row) Connector() []uint8 {
	cells := make([]uint8, 2*r.Width-1)
	for _, e := range r.Edges {
		a, b := 2*e.From, 2*e.To
		switch {
		case a == b:
			cells[a] |= up | down
		case a < b:
			cells[a] |= up | right
			for x := a + 1; x < b; x++ {
				cells[x] |= left | right
			}
			cells[b] |= left | down
		default:
			cells[a] |= up | left
			for x := b + 1; x < a; x++ {
				cells[x] |= left | right
			}
			cells[b] |= right | down
		}
	}
	return cells
}
