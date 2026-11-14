package gitx

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// realGit returns a Git over a fresh repo in t.TempDir(), isolated from the
// user's git config. It skips under -short or when git is missing.
func realGit(t *testing.T) (*Git, string) {
	t.Helper()
	if testing.Short() {
		t.Skip("needs a real git binary")
	}
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not on PATH")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("GIT_CONFIG_GLOBAL", filepath.Join(home, "gitconfig"))
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	t.Setenv("GIT_AUTHOR_NAME", "Test")
	t.Setenv("GIT_AUTHOR_EMAIL", "test@example.com")
	t.Setenv("GIT_COMMITTER_NAME", "Test")
	t.Setenv("GIT_COMMITTER_EMAIL", "test@example.com")
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	g := New(execx.OS{}, dir)
	mustGit(t, g, "init", "-q", "-b", "main")
	return g, dir
}

func mustGit(t *testing.T, g *Git, args ...string) string {
	t.Helper()
	out, err := g.Query(ctx, args...)
	if err != nil {
		t.Fatalf("git %v: %v", args, err)
	}
	return out
}

func TestRealGitUnbornAndDetached(t *testing.T) {
	g, dir := realGit(t)

	// unborn HEAD
	b, err := g.CurrentBranch(ctx)
	if err != nil || b != "main" {
		t.Fatalf("unborn CurrentBranch = %q %v", b, err)
	}
	h, err := g.HeadSHA(ctx)
	if err != nil || h != "" {
		t.Fatalf("unborn HeadSHA = %q %v", h, err)
	}
	if es, err := g.StatusPorcelain(ctx); err != nil || len(es) != 0 {
		t.Fatalf("empty status = %+v %v", es, err)
	}

	if err := os.WriteFile(filepath.Join(dir, "a b.txt"), []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	mustGit(t, g, "add", "-A")
	mustGit(t, g, "commit", "-q", "-m", "feat: first\n\nbody line")
	sha := mustGit(t, g, "rev-parse", "HEAD")
	if h, _ := g.HeadSHA(ctx); h != sha {
		t.Errorf("HeadSHA = %q, want %q", h, sha)
	}

	commits, err := g.Log(ctx, "-n", "1")
	if err != nil || len(commits) != 1 {
		t.Fatalf("Log = %+v %v", commits, err)
	}
	c := commits[0]
	if c.SHA != sha || c.Subject != "feat: first" || c.Body != "body line" || c.AuthorEmail != "test@example.com" || len(c.Parents) != 0 {
		t.Errorf("Log commit = %+v", c)
	}

	// rename shows R with the original path
	mustGit(t, g, "mv", "a b.txt", "c.txt")
	es, err := g.StatusPorcelain(ctx)
	if err != nil || len(es) != 1 || es[0].X != 'R' || es[0].Path != "c.txt" || es[0].OrigPath != "a b.txt" {
		t.Errorf("rename status = %+v %v", es, err)
	}

	// detached HEAD
	mustGit(t, g, "checkout", "-q", "--detach")
	b, err = g.CurrentBranch(ctx)
	if err != nil || b != "" {
		t.Errorf("detached CurrentBranch = %q %v", b, err)
	}

	root, err := g.RepoRoot(ctx)
	if err != nil || root != dir {
		t.Errorf("RepoRoot = %q %v, want %q", root, err, dir)
	}
	if gd, err := g.GitDir(ctx); err != nil || gd != filepath.Join(dir, ".git") {
		t.Errorf("GitDir = %q %v", gd, err)
	}
	if ok, err := g.BranchExists(ctx, "main"); !ok || err != nil {
		t.Errorf("BranchExists(main) = %v %v", ok, err)
	}
	if ok, err := g.BranchExists(ctx, "nope"); ok || err != nil {
		t.Errorf("BranchExists(nope) = %v %v", ok, err)
	}
	if v, err := g.Version(ctx); err != nil || v == "" {
		t.Errorf("Version = %q %v", v, err)
	}
}

func TestRealGitConfigAndTrack(t *testing.T) {
	g, _ := realGit(t)
	if _, ok, err := g.ConfigGet(ctx, "user.name"); ok || err != nil {
		t.Errorf("missing key: ok=%v err=%v", ok, err)
	}
	// note: git >= 2.4x exits 1 (not 2) for "key does not contain a section",
	// so ConfigGet reports it as missing; that is git's choice, not a bug.
	mustGit(t, g, "config", "user.name", "Octo Cat")
	if v, ok, err := g.ConfigGet(ctx, "user.name"); v != "Octo Cat" || !ok || err != nil {
		t.Errorf("ConfigGet = %q %v %v", v, ok, err)
	}
	mustGit(t, g, "config", "--add", "x.multi", "a")
	mustGit(t, g, "config", "--add", "x.multi", "b")
	if all, err := g.ConfigGetAll(ctx, "x.multi"); err != nil || len(all) != 2 {
		t.Errorf("ConfigGetAll = %q %v", all, err)
	}

	mustGit(t, g, "commit", "-q", "--allow-empty", "-m", "one")
	mustGit(t, g, "branch", "feature")
	mustGit(t, g, "branch", "--set-upstream-to=main", "feature")
	mustGit(t, g, "commit", "-q", "--allow-empty", "-m", "two")
	tracks, err := g.RefTrack(ctx)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, tr := range tracks {
		if tr.Branch == "feature" {
			found = true
			if tr.Upstream != "main" || tr.Behind != 1 || tr.Ahead != 0 || tr.Gone {
				t.Errorf("feature track = %+v", tr)
			}
		}
	}
	if !found {
		t.Errorf("RefTrack = %+v", tracks)
	}

	mustGit(t, g, "remote", "add", "origin", "git@github.com:acme/api.git")
	if o, err := g.RemoteOwner(ctx, "origin"); o != "acme" || err != nil {
		t.Errorf("RemoteOwner = %q %v", o, err)
	}
}

// Outside a repository HeadSHA should fail rather than report "unborn".
func TestRealGitHeadSHAOutsideRepo(t *testing.T) {
	g, _ := realGit(t)
	g.Dir = t.TempDir()
	t.Setenv("GIT_CEILING_DIRECTORIES", filepath.Dir(g.Dir))
	if _, err := g.CurrentBranch(ctx); err == nil {
		t.Error("CurrentBranch outside a repo should error")
	}
	h, err := g.HeadSHA(ctx)
	if err == nil {
		t.Errorf("HeadSHA outside a repo = (%q, nil), want an error", h)
	}
}
