package gitx

import (
	"context"
	"strings"
)

// RepoRoot returns the top-level directory of the working tree.
func (g *Git) RepoRoot(ctx context.Context) (string, error) {
	return g.Query(ctx, "rev-parse", "--show-toplevel")
}

// GitDir returns the absolute path of the .git directory.
func (g *Git) GitDir(ctx context.Context) (string, error) {
	return g.Query(ctx, "rev-parse", "--absolute-git-dir")
}

// CurrentBranch returns the checked-out branch name. It works on an unborn
// branch (a fresh repo before the first commit) and returns "" with no error
// on a detached HEAD.
func (g *Git) CurrentBranch(ctx context.Context) (string, error) {
	out, _, err := g.probe(ctx, []int{1}, "symbolic-ref", "--short", "-q", "HEAD")
	return out, err
}

// HeadSHA returns the full SHA of HEAD, or "" when HEAD is unborn.
func (g *Git) HeadSHA(ctx context.Context) (string, error) {
	out, _, err := g.probe(ctx, []int{1}, "rev-parse", "-q", "--verify", "HEAD^{commit}")
	return out, err
}

// ConfigGet returns a config value; ok is false when the key is not set.
func (g *Git) ConfigGet(ctx context.Context, key string) (value string, ok bool, err error) {
	return g.probe(ctx, []int{1}, "config", "--get", key)
}

// ConfigGetAll returns every value of a multi-valued key (nil when unset).
func (g *Git) ConfigGetAll(ctx context.Context, key string) ([]string, error) {
	out, ok, err := g.probe(ctx, []int{1}, "config", "--get-all", key)
	if err != nil || !ok {
		return nil, err
	}
	return splitLines(out), nil
}

// BranchExists reports whether refs/heads/<name> exists.
func (g *Git) BranchExists(ctx context.Context, name string) (bool, error) {
	_, ok, err := g.probe(ctx, []int{1}, "show-ref", "--verify", "--quiet", "refs/heads/"+name)
	return ok, err
}

// StatusEntry is one line of `git status --porcelain`.
type StatusEntry struct {
	X, Y     byte   // index and worktree status ('?' for untracked)
	Path     string // path (new path for renames/copies)
	OrigPath string // original path for renames/copies
}

// Untracked reports whether the entry is an untracked file.
func (e StatusEntry) Untracked() bool { return e.X == '?' }

// Staged reports whether the entry has changes in the index.
func (e StatusEntry) Staged() bool { return e.X != ' ' && e.X != '?' && e.X != '!' }

// StatusPorcelain returns `git status --porcelain=v1 -z` entries.
func (g *Git) StatusPorcelain(ctx context.Context) ([]StatusEntry, error) {
	res, err := g.Run(ctx, "status", "--porcelain=v1", "-z")
	if err != nil {
		return nil, err
	}
	return ParseStatusZ(res.Stdout), nil
}

// ParseStatusZ parses NUL-separated porcelain v1 output.
func ParseStatusZ(out string) []StatusEntry {
	parts := strings.Split(out, "\x00")
	var entries []StatusEntry
	for i := 0; i < len(parts); i++ {
		p := parts[i]
		if len(p) < 4 {
			continue
		}
		e := StatusEntry{X: p[0], Y: p[1], Path: p[3:]}
		if (isRenameCopy(e.X) || isRenameCopy(e.Y)) && i+1 < len(parts) {
			e.OrigPath = parts[i+1]
			i++
		}
		entries = append(entries, e)
	}
	return entries
}

func isRenameCopy(c byte) bool { return c == 'R' || c == 'C' }

func splitLines(s string) []string {
	var out []string
	for _, l := range strings.Split(strings.ReplaceAll(s, "\r\n", "\n"), "\n") {
		if l = strings.TrimSpace(l); l != "" {
			out = append(out, l)
		}
	}
	return out
}
