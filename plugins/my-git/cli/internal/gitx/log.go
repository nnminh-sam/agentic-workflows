package gitx

import (
	"context"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	fieldSep  = "\x1f"
	recordSep = "\x1e"
)

// Track is a local branch with its upstream and ahead/behind counts.
type Track struct {
	Branch   string
	Upstream string // "" when none is set
	Ahead    int
	Behind   int
	Gone     bool // upstream is configured but no longer exists
}

// RefTrack lists local branches with %(upstream:track) parsed.
func (g *Git) RefTrack(ctx context.Context) ([]Track, error) {
	out, err := g.Query(ctx, "for-each-ref",
		"--format=%(refname:short)%1f%(upstream:short)%1f%(upstream:track)", "refs/heads")
	if err != nil {
		return nil, err
	}
	return ParseRefTrack(out), nil
}

// ParseRefTrack parses lines of "branch\x1fupstream\x1f[ahead N, behind M]".
func ParseRefTrack(out string) []Track {
	var tracks []Track
	for _, line := range strings.Split(out, "\n") {
		f := strings.Split(strings.TrimRight(line, "\r"), fieldSep)
		if len(f) < 3 || f[0] == "" {
			continue
		}
		t := Track{Branch: f[0], Upstream: f[1]}
		t.Ahead, t.Behind, t.Gone = ParseTrack(f[2])
		tracks = append(tracks, t)
	}
	return tracks
}

var trackRe = regexp.MustCompile(`(ahead|behind) (\d+)`)

// ParseTrack parses "[ahead 1, behind 2]", "[gone]" or "".
func ParseTrack(s string) (ahead, behind int, gone bool) {
	if strings.Contains(s, "gone") {
		return 0, 0, true
	}
	for _, m := range trackRe.FindAllStringSubmatch(s, -1) {
		n, _ := strconv.Atoi(m[2])
		if m[1] == "ahead" {
			ahead = n
		} else {
			behind = n
		}
	}
	return ahead, behind, false
}

// Commit is one entry of Log.
type Commit struct {
	SHA         string
	Parents     []string
	AuthorName  string
	AuthorEmail string
	Time        time.Time
	Subject     string
	Body        string
}

// logFormat yields SHA, parents, author name, email, unix time, subject, body.
const logFormat = "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%s%x1f%b%x1e"

// Log runs git log with extra args (a range, -n 20, paths after --) and
// returns structured commits.
func (g *Git) Log(ctx context.Context, args ...string) ([]Commit, error) {
	res, err := g.Run(ctx, append([]string{"log", logFormat}, args...)...)
	if err != nil {
		return nil, err
	}
	return ParseLog(res.Stdout), nil
}

// ParseLog parses output produced with logFormat.
func ParseLog(out string) []Commit {
	var commits []Commit
	for _, rec := range strings.Split(out, recordSep) {
		f := strings.Split(strings.TrimLeft(rec, "\r\n"), fieldSep)
		if len(f) < 7 || f[0] == "" {
			continue
		}
		sec, _ := strconv.ParseInt(f[4], 10, 64)
		commits = append(commits, Commit{
			SHA:         f[0],
			Parents:     strings.Fields(f[1]),
			AuthorName:  f[2],
			AuthorEmail: f[3],
			Time:        time.Unix(sec, 0).UTC(),
			Subject:     f[5],
			Body:        strings.TrimRight(f[6], "\r\n"),
		})
	}
	return commits
}
