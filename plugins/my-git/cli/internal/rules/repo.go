package rules

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// Repo answers the repository and account facts the rules need. One Repo
// describes one working directory.
type Repo interface {
	// Config is `git config --get key` (trimmed); ok is false when unset.
	Config(key string) (string, bool)
	// ConfigURLMatch is `git config --get-urlmatch key url` (last line).
	ConfigURLMatch(key, url string) (string, bool)
	// Remotes lists the remote names (`git remote`).
	Remotes() []string
	// RemoteURL is `git remote get-url [--push] name` (first line).
	RemoteURL(name string, push bool) (string, bool)
	// CurrentBranch is `git symbolic-ref --short -q HEAD`; ok is false when detached.
	CurrentBranch() (string, bool)
	// HasCommits reports whether HEAD resolves (the repository has a commit).
	HasCommits() bool
	// RemoteBranchExists reports whether refs/remotes/<remote>/<branch> exists.
	RemoteBranchExists(remote, branch string) bool
	// GhActiveLogin returns the active gh login on host, or "" and a note
	// explaining why gh is not usable. token (GH_TOKEN / GITHUB_TOKEN) is part
	// of the cache key; ttl <= 0 disables the cache.
	GhActiveLogin(host, token string, ttl time.Duration) (login, note string)
	// ClearGhCache forgets cached gh logins (after gh auth switch/login/logout).
	ClearGhCache()
}

// GitRepo is the Repo backed by git and gh through an execx.Runner. Git
// answers are cached for the lifetime of the GitRepo (create one per
// evaluation); gh logins are cached in memory for the requested ttl.
type GitRepo struct {
	Runner execx.Runner
	Cwd    string
	// Now is the clock for the gh cache (time.Now when nil).
	Now func() time.Time
	// Ctx bounds every process (context.Background when nil).
	Ctx context.Context

	mu    sync.Mutex
	git   map[string]execx.Result
	ghTTL map[string]ghEntry
}

type ghEntry struct {
	at          time.Time
	login, note string
}

// NewGitRepo returns a Repo for cwd that runs git and gh through r.
func NewGitRepo(r execx.Runner, cwd string) *GitRepo {
	return &GitRepo{Runner: r, Cwd: cwd}
}

const processTimeout = 15 * time.Second

func (g *GitRepo) run(name string, args ...string) execx.Result {
	ctx := g.Ctx
	if ctx == nil {
		ctx = context.Background()
	}
	ctx, cancel := context.WithTimeout(ctx, processTimeout)
	defer cancel()
	res, err := g.Runner.Run(ctx, execx.Cmd{Name: name, Args: args})
	if err != nil {
		return execx.Result{ExitCode: 127}
	}
	return res
}

// gitQuery runs `git -C <cwd> args...`, cached by argument list.
func (g *GitRepo) gitQuery(args ...string) execx.Result {
	key := strings.Join(args, "\x00")
	g.mu.Lock()
	if res, ok := g.git[key]; ok {
		g.mu.Unlock()
		return res
	}
	g.mu.Unlock()
	res := g.run("git", append([]string{"-C", g.Cwd}, args...)...)
	g.mu.Lock()
	if g.git == nil {
		g.git = map[string]execx.Result{}
	}
	g.git[key] = res
	g.mu.Unlock()
	return res
}

func (g *GitRepo) trimmed(args ...string) (string, bool) {
	res := g.gitQuery(args...)
	out := strings.TrimSpace(res.Stdout)
	return out, res.OK() && out != ""
}

// Config implements Repo.
func (g *GitRepo) Config(key string) (string, bool) { return g.trimmed("config", "--get", key) }

// ConfigURLMatch implements Repo.
func (g *GitRepo) ConfigURLMatch(key, url string) (string, bool) {
	out, ok := g.trimmed("config", "--get-urlmatch", key, url)
	if !ok {
		return "", false
	}
	lines := strings.Split(out, "\n")
	return strings.TrimSpace(lines[len(lines)-1]), true
}

// Remotes implements Repo.
func (g *GitRepo) Remotes() []string {
	res := g.gitQuery("remote")
	if !res.OK() {
		return nil
	}
	return strings.Fields(res.Stdout)
}

// RemoteURL implements Repo.
func (g *GitRepo) RemoteURL(name string, push bool) (string, bool) {
	args := []string{"remote", "get-url"}
	if push {
		args = append(args, "--push")
	}
	out, ok := g.trimmed(append(args, name)...)
	if !ok {
		return "", false
	}
	return strings.TrimSpace(strings.SplitN(out, "\n", 2)[0]), true
}

// CurrentBranch implements Repo.
func (g *GitRepo) CurrentBranch() (string, bool) {
	return g.trimmed("symbolic-ref", "--short", "-q", "HEAD")
}

// HasCommits implements Repo.
func (g *GitRepo) HasCommits() bool { return g.gitQuery("rev-parse", "--verify", "-q", "HEAD").OK() }

// RemoteBranchExists implements Repo.
func (g *GitRepo) RemoteBranchExists(remote, branch string) bool {
	return g.gitQuery("rev-parse", "--verify", "-q", "refs/remotes/"+remote+"/"+branch).OK()
}

func (g *GitRepo) now() time.Time {
	if g.Now != nil {
		return g.Now()
	}
	return time.Now()
}

// GhActiveLogin implements Repo.
func (g *GitRepo) GhActiveLogin(host, token string, ttl time.Duration) (string, string) {
	sum := sha256.Sum256([]byte(token))
	key := host + "|" + hex.EncodeToString(sum[:])
	if ttl > 0 {
		g.mu.Lock()
		e, ok := g.ghTTL[key]
		g.mu.Unlock()
		if ok && g.now().Sub(e.at) < ttl {
			return e.login, e.note
		}
	}
	res := g.run("gh", "auth", "status", "--active", "--hostname", host, "--json", "hosts")
	login, note := parseGhStatus(res, host)
	if ttl > 0 && login != "" {
		g.mu.Lock()
		if g.ghTTL == nil {
			g.ghTTL = map[string]ghEntry{}
		}
		g.ghTTL[key] = ghEntry{at: g.now(), login: login, note: note}
		g.mu.Unlock()
	}
	return login, note
}

// ClearGhCache implements Repo.
func (g *GitRepo) ClearGhCache() {
	g.mu.Lock()
	g.ghTTL = nil
	g.mu.Unlock()
}

type ghHostEntry struct {
	Active bool   `json:"active"`
	State  string `json:"state"`
	Login  string `json:"login"`
}

// parseGhStatus reads `gh auth status --json hosts` like identity.gh_active_login.
func parseGhStatus(res execx.Result, host string) (string, string) {
	var data struct {
		Hosts map[string][]ghHostEntry `json:"hosts"`
	}
	if err := json.Unmarshal([]byte(res.Stdout), &data); err != nil || data.Hosts == nil {
		return "", fmt.Sprintf("could not read 'gh auth status' (exit %d)", res.ExitCode)
	}
	entries := data.Hosts[host]
	var active []ghHostEntry
	for _, e := range entries {
		if e.Active {
			active = append(active, e)
		}
	}
	if len(active) == 0 {
		active = entries
	}
	switch {
	case len(active) > 0 && active[0].State == "success":
		return active[0].Login, ""
	case len(active) > 0:
		return "", fmt.Sprintf("gh auth for %s is in state '%s'", host, active[0].State)
	default:
		return "", fmt.Sprintf("gh is not logged in to %s", host)
	}
}
