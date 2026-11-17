package gitx

import (
	"context"
	"fmt"
	"net/url"
	"strings"
)

// Remotes lists the configured remote names.
func (g *Git) Remotes(ctx context.Context) ([]string, error) {
	out, err := g.Query(ctx, "remote")
	if err != nil {
		return nil, err
	}
	return splitLines(out), nil
}

// RemoteURL returns the fetch URL of a remote.
func (g *Git) RemoteURL(ctx context.Context, name string) (string, error) {
	return g.Query(ctx, "remote", "get-url", name)
}

// RemoteOwner returns the GitHub owner (user or org) of a remote's URL.
func (g *Git) RemoteOwner(ctx context.Context, name string) (string, error) {
	u, err := g.RemoteURL(ctx, name)
	if err != nil {
		return "", err
	}
	ref, err := ParseRemoteURL(u)
	if err != nil {
		return "", err
	}
	return ref.Owner, nil
}

// RemoteRef is a parsed remote URL.
type RemoteRef struct {
	Host  string // e.g. "github.com" (an ssh host alias is kept as written)
	Owner string
	Repo  string // without ".git"
}

// String renders "owner/repo".
func (r RemoteRef) String() string { return r.Owner + "/" + r.Repo }

// ParseRemoteURL parses the common remote URL forms:
// git@github.com:o/r.git, ssh://git@github.com[:22]/o/r.git,
// https://[user@]github.com/o/r(.git), git://host/o/r.git.
func ParseRemoteURL(raw string) (RemoteRef, error) {
	raw = strings.TrimSpace(raw)
	host, path, err := splitRemote(raw)
	if err != nil {
		return RemoteRef{}, err
	}
	path = strings.TrimSuffix(strings.Trim(path, "/"), ".git")
	parts := strings.Split(path, "/")
	if len(parts) < 2 || parts[len(parts)-2] == "" || parts[len(parts)-1] == "" {
		return RemoteRef{}, fmt.Errorf("remote url %q: want <owner>/<repo>", raw)
	}
	return RemoteRef{Host: host, Owner: parts[len(parts)-2], Repo: parts[len(parts)-1]}, nil
}

// splitRemote separates host and path for URL and scp-like forms.
func splitRemote(raw string) (host, path string, err error) {
	if strings.Contains(raw, "://") {
		u, perr := url.Parse(raw)
		if perr != nil {
			return "", "", fmt.Errorf("remote url %q: %w", raw, perr)
		}
		return u.Hostname(), u.Path, nil
	}
	// scp-like: [user@]host:path (not a Windows drive like C:\…)
	i := strings.IndexByte(raw, ':')
	if i <= 1 || strings.ContainsAny(raw[:i], `/\`) {
		return "", "", fmt.Errorf("remote url %q: unsupported form", raw)
	}
	host = raw[:i]
	if at := strings.LastIndexByte(host, '@'); at >= 0 {
		host = host[at+1:]
	}
	return host, raw[i+1:], nil
}
