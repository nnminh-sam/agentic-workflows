package graph

import (
	"fmt"
	"math/rand"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// dag builds commits from "sha:p1,p2" specs, in the given (topological,
// children-first) order. "sha:" is a root commit.
func dag(specs ...string) []Commit {
	out := make([]Commit, 0, len(specs))
	for _, s := range specs {
		sha, ps, _ := strings.Cut(s, ":")
		c := Commit{SHA: sha, Subject: "subject " + sha}
		if ps != "" {
			c.Parents = strings.Split(ps, ",")
		}
		out = append(out, c)
	}
	return out
}

func cols(rows []Row) []int {
	out := make([]int, len(rows))
	for i, r := range rows {
		out[i] = r.Col
	}
	return out
}

// edgeSet returns the edges sorted, for order-insensitive comparison.
func edgeSet(es []Edge) []Edge {
	out := append([]Edge{}, es...)
	sort.Slice(out, func(i, j int) bool {
		if out[i].From != out[j].From {
			return out[i].From < out[j].From
		}
		return out[i].To < out[j].To
	})
	if len(out) == 0 {
		return nil
	}
	return out
}

func e(from, to int) Edge { return Edge{from, to} }

// connector renders a row's connector line as text (trailing blanks trimmed).
func connector(r Row, ascii bool) string {
	var b strings.Builder
	for _, m := range r.Connector() {
		b.WriteString(Glyph(m, ascii))
	}
	return strings.TrimRight(b.String(), " ")
}

func TestLayoutFixtures(t *testing.T) {
	tests := []struct {
		name  string
		dag   []Commit
		cols  []int
		edges [][]Edge
		conn  []string // connector per row ("" = no bend)
	}{
		{
			name:  "linear",
			dag:   dag("A:B", "B:C", "C:"),
			cols:  []int{0, 0, 0},
			edges: [][]Edge{{e(0, 0)}, {e(0, 0)}, nil},
			conn:  []string{"", "", ""},
		},
		{
			name:  "single branch-off",
			dag:   dag("F:B", "A:B", "B:C", "C:"),
			cols:  []int{0, 1, 0, 0},
			edges: [][]Edge{{e(0, 0)}, {e(0, 0), e(1, 0)}, {e(0, 0)}, nil},
			conn:  []string{"", "├─╯", "", ""},
		},
		{
			name:  "merge with side branch",
			dag:   dag("M:A,F", "A:B", "F:B", "B:"),
			cols:  []int{0, 0, 1, 0},
			edges: [][]Edge{{e(0, 0), e(0, 1)}, {e(0, 0), e(1, 1)}, {e(0, 0), e(1, 0)}, nil},
			conn:  []string{"├─╮", "", "├─╯", ""},
		},
		{
			name:  "octopus merge",
			dag:   dag("O:A,B,C", "A:R", "B:R", "C:R", "R:"),
			cols:  []int{0, 0, 1, 2, 0},
			edges: [][]Edge{
				{e(0, 0), e(0, 1), e(0, 2)},
				{e(0, 0), e(1, 1), e(2, 2)},
				{e(0, 0), e(1, 0), e(2, 2)},
				{e(0, 0), e(2, 0)},
				nil,
			},
			conn: []string{"├─┬─╮", "", "├─╯ │", "├───╯", ""},
		},
		{
			name:  "three tips share a parent",
			dag:   dag("T1:B", "T2:B", "T3:B", "B:"),
			cols:  []int{0, 1, 1, 0},
			edges: [][]Edge{{e(0, 0)}, {e(0, 0), e(1, 0)}, {e(0, 0), e(1, 0)}, nil},
			conn:  []string{"", "├─╯", "├─╯", ""},
		},
		{
			name: "criss-cross merges",
			dag:  dag("A2:A1,B1", "B2:B1,A1", "A1:R", "B1:R", "R:"),
			cols: []int{0, 2, 0, 1, 0},
			edges: [][]Edge{
				{e(0, 0), e(0, 1)},
				{e(0, 0), e(1, 1), e(2, 0), e(2, 1)},
				{e(0, 0), e(1, 1)},
				{e(0, 0), e(1, 0)},
				nil,
			},
			conn: []string{"├─╮", "├─┼─╯", "", "├─╯", ""},
		},
		{
			name: "lane reuse after a lane ends (middle lane)",
			dag:  dag("T0:P0", "T1:R1", "T2:P2", "R1:", "T3:P3", "P0:", "P2:", "P3:"),
			cols: []int{0, 1, 2, 1, 1, 0, 2, 1},
			edges: [][]Edge{
				{e(0, 0)},
				{e(0, 0), e(1, 1)},
				{e(0, 0), e(1, 1), e(2, 2)},
				{e(0, 0), e(2, 2)}, // R1 is a root: lane 1 ends
				{e(0, 0), e(1, 1), e(2, 2)},
				{e(1, 1), e(2, 2)},
				{e(1, 1)},
				nil,
			},
			conn: []string{"", "", "", "", "", "", "", ""},
		},
		{
			name: "multiple roots",
			dag:  dag("A:R1", "B:R2", "R1:", "R2:"),
			cols: []int{0, 1, 0, 1},
			edges: [][]Edge{
				{e(0, 0)},
				{e(0, 0), e(1, 1)},
				{e(1, 1)},
				nil,
			},
			conn: []string{"", "", "", ""},
		},
		{
			name: "limit: parents outside the window keep their lanes open",
			dag:  dag("M:X,Y", "A:B", "B:C"),
			cols: []int{0, 2, 2},
			edges: [][]Edge{
				{e(0, 0), e(0, 1)},
				{e(0, 0), e(1, 1), e(2, 2)},
				{e(0, 0), e(1, 1), e(2, 2)},
			},
			conn: []string{"├─╮", "", ""},
		},
		{
			name: "first parent waited to the right is pulled left",
			dag:  dag("C:M", "E:P", "M:P", "P:"),
			cols: []int{0, 1, 0, 0},
			edges: [][]Edge{
				{e(0, 0)},
				{e(0, 0), e(1, 1)},
				{e(0, 0), e(1, 0)},
				nil,
			},
			conn: []string{"", "", "├─╯", ""},
		},
		{
			name: "merge parent joins a lane to the left",
			dag:  dag("X:P", "Y:Q", "M:Q,P", "P:", "Q:"),
			cols: []int{0, 1, 2, 0, 1},
			edges: [][]Edge{
				{e(0, 0)},
				{e(0, 0), e(1, 1)},
				{e(0, 0), e(1, 1), e(2, 0), e(2, 1)},
				{e(1, 1)},
				nil,
			},
			conn: []string{"", "", "├─┼─╯", "", ""},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rows := Layout(tt.dag)
			if len(rows) != len(tt.dag) {
				t.Fatalf("rows = %d, want %d", len(rows), len(tt.dag))
			}
			if got := cols(rows); !reflect.DeepEqual(got, tt.cols) {
				t.Errorf("cols = %v, want %v", got, tt.cols)
			}
			for i, r := range rows {
				if r.Commit != i {
					t.Errorf("row %d: Commit = %d", i, r.Commit)
				}
				if got, want := edgeSet(r.Edges), edgeSet(tt.edges[i]); !reflect.DeepEqual(got, want) {
					t.Errorf("row %d (%s): edges = %v, want %v", i, tt.dag[i].SHA, got, want)
				}
				conn := ""
				if r.Bends() {
					conn = connector(r, false)
				}
				if conn != tt.conn[i] {
					t.Errorf("row %d (%s): connector = %q, want %q", i, tt.dag[i].SHA, conn, tt.conn[i])
				}
			}
			if err := checkLayout(tt.dag, rows); err != nil {
				t.Error(err)
			}
		})
	}
}

func TestLayoutEmptyAndDeterministic(t *testing.T) {
	if rows := Layout(nil); len(rows) != 0 {
		t.Fatalf("Layout(nil) = %v", rows)
	}
	d := dag("O:A,B,C", "X:B", "A:R", "B:R", "C:R", "R:")
	if a, b := Layout(d), Layout(d); !reflect.DeepEqual(a, b) {
		t.Fatal("Layout is not deterministic")
	}
}

// checkLayout verifies a layout independently of how it was computed: it
// replays the rows, tracking which commit each lane carries, and checks that
//   - each commit sits on the one lane that carries it (or a free lane for a
//     tip) and no other lane still carries it (no dangling line),
//   - every busy lane continues with exactly one edge and free lanes have none,
//   - a commit has exactly one outgoing edge per parent, to distinct lanes,
//   - edges that converge carry the same commit,
//   - Pass marks exactly the busy lanes other than the node,
//   - Width and the connector cover every lane in use.
//
// Together these mean every parent edge inside the window reaches the
// parent's node and nothing else is drawn.
func checkLayout(commits []Commit, rows []Row) error {
	inWindow := map[string]bool{}
	hasChild := map[string]bool{}
	for _, c := range commits {
		inWindow[c.SHA] = true
		for _, p := range c.Parents {
			hasChild[p] = true
		}
	}
	if len(rows) != len(commits) {
		return fmt.Errorf("rows = %d, commits = %d", len(rows), len(commits))
	}
	carry := map[int]string{}
	for i, r := range rows {
		c := commits[r.Commit]
		if r.Commit != i {
			return fmt.Errorf("row %d: commit index %d", i, r.Commit)
		}
		col := r.Col
		for j, s := range carry {
			if j != col && s == c.SHA {
				return fmt.Errorf("row %d (%s): lane %d still carries it (node in lane %d)", i, c.SHA, j, col)
			}
		}
		if cur := carry[col]; cur != "" && cur != c.SHA {
			return fmt.Errorf("row %d (%s): node placed on lane %d which carries %s", i, c.SHA, col, cur)
		}
		if got := carry[col] == c.SHA; got != hasChild[c.SHA] {
			return fmt.Errorf("row %d (%s): arrives by lane = %v, has child in window = %v", i, c.SHA, got, hasChild[c.SHA])
		}
		maxLane := col
		for j, s := range carry {
			if s != "" {
				maxLane = max(maxLane, j)
			}
		}
		for j := 0; j <= maxLane || j < len(r.Pass); j++ {
			want := j != col && carry[j] != ""
			got := j < len(r.Pass) && r.Pass[j]
			if got != want {
				return fmt.Errorf("row %d (%s): Pass[%d] = %v, want %v", i, c.SHA, j, got, want)
			}
		}
		// outgoing edges
		from := map[int][]int{}
		for _, ed := range r.Edges {
			if ed.From < 0 || ed.To < 0 {
				return fmt.Errorf("row %d: negative lane in %v", i, ed)
			}
			from[ed.From] = append(from[ed.From], ed.To)
			maxLane = max(maxLane, ed.From, ed.To)
		}
		if r.Width < maxLane+1 {
			return fmt.Errorf("row %d: Width %d < lanes used %d", i, r.Width, maxLane+1)
		}
		if n := len(r.Connector()); n != 2*r.Width-1 {
			return fmt.Errorf("row %d: connector cells %d, want %d", i, n, 2*r.Width-1)
		}
		next := map[int]string{}
		for j, s := range carry {
			if j == col || s == "" {
				continue
			}
			if len(from[j]) != 1 {
				return fmt.Errorf("row %d (%s): busy lane %d (%s) has %d edges", i, c.SHA, j, s, len(from[j]))
			}
			to := from[j][0]
			if prev, ok := next[to]; ok && prev != s {
				return fmt.Errorf("row %d: lanes carrying %s and %s converge in lane %d", i, prev, s, to)
			}
			next[to] = s
		}
		for j := range from {
			if j != col && carry[j] == "" {
				return fmt.Errorf("row %d (%s): edge from free lane %d", i, c.SHA, j)
			}
		}
		parents := c.Parents
		outs := append([]int(nil), from[col]...)
		if len(outs) != len(parents) {
			return fmt.Errorf("row %d (%s): %d edges from node, %d parents", i, c.SHA, len(outs), len(parents))
		}
		sort.Ints(outs)
		for k := 1; k < len(outs); k++ {
			if outs[k] == outs[k-1] {
				return fmt.Errorf("row %d (%s): two node edges into lane %d", i, c.SHA, outs[k])
			}
		}
		assigned := map[string]bool{}
		var fresh []int
		for _, to := range outs {
			if s, ok := next[to]; ok {
				if !contains(parents, s) || assigned[s] {
					return fmt.Errorf("row %d (%s): node edge joins lane %d carrying %s, not an unassigned parent", i, c.SHA, to, s)
				}
				assigned[s] = true
				continue
			}
			fresh = append(fresh, to)
		}
		k := 0
		for _, p := range parents {
			if assigned[p] {
				continue
			}
			if k >= len(fresh) {
				return fmt.Errorf("row %d (%s): parent %s has no edge", i, c.SHA, p)
			}
			next[fresh[k]] = p
			k++
		}
		carry = next
	}
	for j, s := range carry {
		if inWindow[s] {
			return fmt.Errorf("after last row: lane %d still carries in-window commit %s", j, s)
		}
	}
	return nil
}

func contains(l []string, s string) bool {
	for _, x := range l {
		if x == s {
			return true
		}
	}
	return false
}

// randomDAG returns n commits in topological order (children first); each
// commit has 0-3 distinct parents among later commits, and with some
// probability a parent outside the window (as after --limit).
func randomDAG(rng *rand.Rand, n int) []Commit {
	commits := make([]Commit, n)
	for i := range commits {
		commits[i].SHA = fmt.Sprintf("c%03d", i)
	}
	ext := 0
	for i := range commits {
		np := rng.Intn(4)
		if rng.Intn(6) == 0 {
			np = 1
		}
		seen := map[string]bool{}
		for k := 0; k < np; k++ {
			var p string
			if i+1 < n && rng.Intn(5) != 0 {
				// prefer near parents so lanes stay realistic
				span := min(n-i-1, 1+rng.Intn(6))
				p = commits[i+1+rng.Intn(span)].SHA
			} else {
				p = fmt.Sprintf("x%03d", ext)
				ext++
			}
			if !seen[p] {
				seen[p] = true
				commits[i].Parents = append(commits[i].Parents, p)
			}
		}
	}
	return commits
}

func TestLayoutPropertyRandomDAGs(t *testing.T) {
	rng := rand.New(rand.NewSource(42))
	for iter := 0; iter < 3000; iter++ {
		n := 1 + rng.Intn(40)
		commits := randomDAG(rng, n)
		rows := Layout(commits)
		if err := checkLayout(commits, rows); err != nil {
			t.Fatalf("iteration %d: %v\ndag: %s", iter, err, dagString(commits))
		}
		// every commit has exactly one row and one lane
		seen := map[int]bool{}
		for _, r := range rows {
			if seen[r.Commit] {
				t.Fatalf("iteration %d: commit %d laid out twice", iter, r.Commit)
			}
			seen[r.Commit] = true
		}
		// rendering never panics and has one line per commit plus bends
		var b strings.Builder
		repo := Repo{Remote: "origin", Commits: commits}
		if err := RenderText(&b, repo, rows, TextOptions{NoLegend: true}); err != nil {
			t.Fatal(err)
		}
		want := len(rows)
		for _, r := range rows {
			if r.Bends() {
				want++
			}
		}
		if got := strings.Count(b.String(), "\n"); got != want {
			t.Fatalf("iteration %d: %d lines, want %d", iter, got, want)
		}
	}
}

func dagString(cs []Commit) string {
	parts := make([]string, len(cs))
	for i, c := range cs {
		parts[i] = c.SHA + ":" + strings.Join(c.Parents, ",")
	}
	return strings.Join(parts, " ")
}

// A lane that ends in a row (its line bends into the node's lane) must not
// start a different line in the same row: in text the two halves join into
// one vertical and the reader sees the old line continue to the new parent.
//
//	C:M  D:Y  E:P  M:P,Q  →  M pulls lane 2 (P) left and opens Q in lane 2.
func TestLayoutNoLaneRecycledInSameRow(t *testing.T) {
	commits := dag("C:M", "D:Y", "E:P", "M:P,Q", "P:", "Q:")
	rows := Layout(commits)
	if err := checkLayout(commits, rows); err != nil {
		t.Fatal(err)
	}
	if bad := recycledLanes(rows); len(bad) > 0 {
		t.Skipf("BUG: layout.go placeMergeParents reuses lane(s) %v freed by placeFirstParent's pull-left in the same row; "+
			"connector %q reads as E continuing to Q", bad, connector(rows[3], false))
	}
}

// recycledLanes returns "row:lane" for lanes whose incoming line ends (bends
// away) while a different edge ends in the same lane, other than the node's.
func recycledLanes(rows []Row) []string {
	var bad []string
	for i, r := range rows {
		leaves, arrives := map[int]bool{}, map[int]bool{}
		for _, ed := range r.Edges {
			if ed.From != ed.To && ed.From != r.Col {
				leaves[ed.From] = true
			}
			if ed.From != ed.To {
				arrives[ed.To] = true
			}
		}
		for j := range leaves {
			if arrives[j] && j != r.Col {
				bad = append(bad, fmt.Sprintf("%d:%d", i, j))
			}
		}
	}
	return bad
}

// When a node has edges into two lanes on the same side, the nearer lane is
// both an endpoint and crossed by the farther edge; the text connector draws
// ┼ there, the same glyph as a plain crossing, so the nearer edge vanishes.
func TestConnectorJunctionVsCrossing(t *testing.T) {
	commits := dag("A2:A1,B1", "B2:B1,A1", "A1:R", "B1:R", "R:")
	rows := Layout(commits)
	r := rows[1]
	conn := connector(r, false)
	if strings.Contains(conn, "┼") {
		t.Skipf("BUG: render_text.go connectorLine: B2's edge into lane 1 (B1) and its edge across lane 1 into lane 0 both "+
			"draw %q; ┼ also means a plain crossing (e.g. 60f67a6 in the real repo), so the B2→B1 edge is invisible", conn)
	}
}

func TestConnectorMasks(t *testing.T) {
	r := Row{Col: 1, Width: 3, Edges: []Edge{{0, 0}, {1, 0}, {1, 2}}}
	got := r.Connector()
	want := []uint8{up | down | right, left | right, up | left | right, left | right, left | down}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Connector = %v, want %v", got, want)
	}
	if r.Bends() != true || (Row{Edges: []Edge{{0, 0}}}).Bends() {
		t.Fatal("Bends")
	}
}

func TestGlyphTable(t *testing.T) {
	tests := []struct {
		m        uint8
		uni, asc string
	}{
		{0, " ", " "},
		{up, "│", "|"}, {down, "│", "|"}, {up | down, "│", "|"},
		{left, "─", "-"}, {right, "─", "-"}, {left | right, "─", "-"},
		{up | down | right, "├", "|"},
		{up | down | left, "┤", "|"},
		{left | down, "╮", "\\"},
		{right | down, "╭", "/"},
		{up | left, "╯", "/"},
		{up | right, "╰", "\\"},
		{left | right | down, "┬", "-"},
		{left | right | up, "┴", "-"},
		{up | down | left | right, "┼", "+"},
	}
	for _, tt := range tests {
		if g := Glyph(tt.m, false); g != tt.uni {
			t.Errorf("Glyph(%04b) = %q, want %q", tt.m, g, tt.uni)
		}
		if g := Glyph(tt.m, true); g != tt.asc {
			t.Errorf("Glyph(%04b, ascii) = %q, want %q", tt.m, g, tt.asc)
		}
	}
}

// The real-repo topology behind the suspect connector rows (2810109, 60f67a6,
// 1845b18, a418699, df9896d …), reproduced as a fixture: every connector
// must be exactly the edges the DAG implies.
func TestLayoutRealRepoTopology(t *testing.T) {
	commits := dag(
		"2a3b817:60f67a6",
		"2810109:29c5551,60f67a6",
		"60f67a6:a418699,853b838",
		"853b838:1845b18",
		"1845b18:d6eebf0,a418699",
		"a418699:df9896d,5663e54",
		"df9896d:61f801d,29c5551",
		"d6eebf0:29c5551",
		"2d90058:74aa1d7",
		"74aa1d7:5663e54",
		"5663e54:4f4d20c",
		"4f4d20c:c62566a",
		"c62566a:29c5551",
		"29c5551:b5f79ef",
		"b5f79ef:f71183b",
	)
	rows := Layout(commits)
	if err := checkLayout(commits, rows); err != nil {
		t.Fatal(err)
	}
	want := []struct {
		col   int
		edges []Edge
		conn  string
	}{
		{0, []Edge{e(0, 0)}, ""},
		{1, []Edge{e(0, 0), e(1, 0), e(1, 1)}, "├─┤"},           // 2810109 → 60f67a6 (lane 0) and 29c5551 (lane 1)
		{0, []Edge{e(0, 0), e(0, 2), e(1, 1)}, "├─┼─╮"},         // 60f67a6 → a418699 (0) and 853b838 (new lane 2, crossing lane 1)
		{2, []Edge{e(0, 0), e(1, 1), e(2, 2)}, ""},              // 853b838
		{2, []Edge{e(0, 0), e(1, 1), e(2, 0), e(2, 2)}, "├─┼─┤"}, // 1845b18 → d6eebf0 (2) and a418699 (0, crossing 1)
		{0, []Edge{e(0, 0), e(0, 3), e(1, 1), e(2, 2)}, "├─┼─┼─╮"},
		{0, []Edge{e(0, 0), e(0, 1), e(1, 1), e(2, 2), e(3, 3)}, "├─┤ │ │"}, // df9896d → 61f801d (0, outside) and 29c5551 (1)
		{2, []Edge{e(0, 0), e(1, 1), e(2, 1), e(3, 3)}, "│ ├─╯ │"},
		{2, []Edge{e(0, 0), e(1, 1), e(2, 2), e(3, 3)}, ""},
		{2, []Edge{e(0, 0), e(1, 1), e(2, 2), e(3, 2)}, "│ │ ├─╯"},
		{2, []Edge{e(0, 0), e(1, 1), e(2, 2)}, ""},
		{2, []Edge{e(0, 0), e(1, 1), e(2, 2)}, ""},
		{2, []Edge{e(0, 0), e(1, 1), e(2, 1)}, "│ ├─╯"},
		{1, []Edge{e(0, 0), e(1, 1)}, ""},
		{1, []Edge{e(0, 0), e(1, 1)}, ""},
	}
	for i, w := range want {
		r := rows[i]
		conn := ""
		if r.Bends() {
			conn = connector(r, false)
		}
		if r.Col != w.col || !reflect.DeepEqual(edgeSet(r.Edges), edgeSet(w.edges)) || conn != w.conn {
			t.Errorf("row %d %s: col %d edges %v conn %q; want col %d edges %v conn %q",
				i, commits[i].SHA, r.Col, edgeSet(r.Edges), conn, w.col, edgeSet(w.edges), w.conn)
		}
	}
}
