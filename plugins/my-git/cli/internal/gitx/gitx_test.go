package gitx

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

var ctx = context.Background()

func TestParseVersion(t *testing.T) {
	tests := map[string]string{
		"git version 2.43.0\n":               "2.43.0",
		"git version 2.45.1.windows.1":       "2.45.1.windows.1",
		"git version 2.39.3 (Apple Git-146)": "2.39.3",
		"weird":                              "weird",
	}
	for in, want := range tests {
		if got := ParseVersion(in); got != want {
			t.Errorf("ParseVersion(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRunAndQuery(t *testing.T) {
	f := execx.NewFake().
		OnOut("git version", "git version 2.43.0\n").
		On("git status", execx.Result{ExitCode: 128, Stderr: "fatal: not a git repository\n"}).
		On("git quiet", execx.Result{ExitCode: 1, Stdout: "only stdout\n"})
	f.Errors["git boom"] = execx.ErrNotFound
	var traced []string
	g := New(f, "/repo")
	g.Trace = func(c execx.Cmd) { traced = append(traced, c.String()) }

	v, err := g.Version(ctx)
	if err != nil || v != "2.43.0" {
		t.Fatalf("Version = %q, %v", v, err)
	}
	_, err = g.Run(ctx, "status")
	var ee *ExitError
	if !errors.As(err, &ee) || ee.Result.ExitCode != 128 {
		t.Fatalf("Run(status) err = %v", err)
	}
	if want := "git status: exit 128: fatal: not a git repository"; err.Error() != want {
		t.Errorf("ExitError = %q, want %q", err, want)
	}
	if _, err := g.Query(ctx, "quiet"); err == nil || !strings.HasSuffix(err.Error(), "exit 1: only stdout") {
		t.Errorf("stdout fallback = %v", err)
	}
	if _, err := g.Exec(ctx, "boom"); !errors.Is(err, execx.ErrNotFound) {
		t.Errorf("Exec start error = %v", err)
	}
	if _, err := g.Version(ctx); err != nil {
		t.Error(err)
	}
	if f.Calls[0].Dir != "/repo" {
		t.Errorf("Dir not passed: %+v", f.Calls[0])
	}
	if len(traced) != len(f.Calls) {
		t.Errorf("trace saw %d of %d calls", len(traced), len(f.Calls))
	}
	f2 := execx.NewFake().On("git version", execx.Result{ExitCode: 1})
	if _, err := New(f2, "").Version(ctx); err == nil {
		t.Error("Version on failure should error")
	}
}

func TestParseRemoteURL(t *testing.T) {
	tests := []struct {
		in                string
		host, owner, repo string
		wantErr           bool
	}{
		{"git@github.com:acme/api.git", "github.com", "acme", "api", false},
		{"git@github.com:acme/api", "github.com", "acme", "api", false},
		{"github.com:acme/api.git", "github.com", "acme", "api", false},
		{"git@github-work:acme/api.git", "github-work", "acme", "api", false},
		{"ssh://git@github.com/acme/api.git", "github.com", "acme", "api", false},
		{"ssh://git@github.com:22/acme/api.git", "github.com", "acme", "api", false},
		{"https://github.com/acme/api.git", "github.com", "acme", "api", false},
		{"https://github.com/acme/api", "github.com", "acme", "api", false},
		{"https://github.com/acme/api/", "github.com", "acme", "api", false},
		{"https://octo@github.com/acme/api.git", "github.com", "acme", "api", false},
		{"https://x-access-token:tok@github.com/acme/api.git", "github.com", "acme", "api", false},
		{"https://ghe.acme.example/platform/api.git", "ghe.acme.example", "platform", "api", false},
		{"git@ghe.acme.example:platform/api.git", "ghe.acme.example", "platform", "api", false},
		{"https://ghe.acme.example:8443/platform/api.git\n", "ghe.acme.example", "platform", "api", false},
		{"git://github.com/acme/api.git", "github.com", "acme", "api", false},
		{"https://gitlab.com/group/sub/api.git", "gitlab.com", "sub", "api", false},
		{"https://github.com/acme/my.repo.git", "github.com", "acme", "my.repo", false},
		{"https://github.com/acme", "", "", "", true},
		{"git@github.com:api.git", "", "", "", true},
		{`C:\src\api`, "", "", "", true},
		{"/srv/git/acme/api.git", "", "", "", true},
		{"", "", "", "", true},
		{"https://%zz/a/b", "", "", "", true},
	}
	for _, tt := range tests {
		got, err := ParseRemoteURL(tt.in)
		if (err != nil) != tt.wantErr {
			t.Errorf("ParseRemoteURL(%q) err = %v, wantErr %v", tt.in, err, tt.wantErr)
			continue
		}
		if !tt.wantErr && (got.Host != tt.host || got.Owner != tt.owner || got.Repo != tt.repo) {
			t.Errorf("ParseRemoteURL(%q) = %+v, want %s %s/%s", tt.in, got, tt.host, tt.owner, tt.repo)
		}
	}
	if s := (RemoteRef{Owner: "a", Repo: "b"}).String(); s != "a/b" {
		t.Errorf("String = %q", s)
	}
}

func TestRemotes(t *testing.T) {
	f := execx.NewFake().
		OnOut("git remote", "origin\nupstream\r\n\n").
		OnOut("git remote get-url origin", "git@github.com:acme/api.git\n").
		OnOut("git remote get-url bad", "not a url\n").
		On("git remote get-url none", execx.Result{ExitCode: 2, Stderr: "error: No such remote 'none'"})
	g := New(f, "")
	rs, err := g.Remotes(ctx)
	if err != nil || !reflect.DeepEqual(rs, []string{"origin", "upstream"}) {
		t.Errorf("Remotes = %q, %v", rs, err)
	}
	owner, err := g.RemoteOwner(ctx, "origin")
	if err != nil || owner != "acme" {
		t.Errorf("RemoteOwner = %q, %v", owner, err)
	}
	if _, err := g.RemoteOwner(ctx, "bad"); err == nil {
		t.Error("RemoteOwner(bad) should fail")
	}
	if _, err := g.RemoteOwner(ctx, "none"); err == nil {
		t.Error("RemoteOwner(none) should fail")
	}
	if _, err := New(execx.NewFake(), "").Remotes(ctx); err == nil {
		t.Error("Remotes failure should error")
	}
}

func TestParseTrack(t *testing.T) {
	tests := []struct {
		in            string
		ahead, behind int
		gone          bool
	}{
		{"", 0, 0, false},
		{"[ahead 3]", 3, 0, false},
		{"[behind 12]", 0, 12, false},
		{"[ahead 1, behind 2]", 1, 2, false},
		{"[gone]", 0, 0, true},
	}
	for _, tt := range tests {
		a, b, g := ParseTrack(tt.in)
		if a != tt.ahead || b != tt.behind || g != tt.gone {
			t.Errorf("ParseTrack(%q) = %d %d %v", tt.in, a, b, g)
		}
	}
}

func TestRefTrack(t *testing.T) {
	out := "main\x1forigin/main\x1f\n" +
		"develop/feat/x\x1forigin/develop/feat/x\x1f[ahead 2, behind 1]\r\n" +
		"old\x1forigin/old\x1f[gone]\n" +
		"local\x1f\x1f\n" +
		"\n" +
		"broken-line\n"
	f := execx.NewFake().OnOut("git for-each-ref --format=%(refname:short)%1f%(upstream:short)%1f%(upstream:track) refs/heads", out)
	got, err := New(f, "").RefTrack(ctx)
	if err != nil {
		t.Fatal(err)
	}
	want := []Track{
		{Branch: "main", Upstream: "origin/main"},
		{Branch: "develop/feat/x", Upstream: "origin/develop/feat/x", Ahead: 2, Behind: 1},
		{Branch: "old", Upstream: "origin/old", Gone: true},
		{Branch: "local"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("RefTrack =\n%+v\nwant\n%+v", got, want)
	}
	if _, err := New(execx.NewFake(), "").RefTrack(ctx); err == nil {
		t.Error("RefTrack failure should error")
	}
}

func TestLog(t *testing.T) {
	out := "aaa\x1fppp qqq\x1fOcto Cat\x1focto@acme.example\x1f1700000000\x1ffeat(auth): add login\x1fBody line 1\n\nRefs: #12\n\x1e\n" +
		"bbb\x1f\x1fA\x1fa@b\x1f1600000000\x1fchore: init\x1f\x1e\n"
	f := execx.NewFake().OnOut("git log "+logFormat+" -n 2 main..HEAD", out)
	got, err := New(f, "").Log(ctx, "-n", "2", "main..HEAD")
	if err != nil {
		t.Fatal(err)
	}
	want := []Commit{
		{SHA: "aaa", Parents: []string{"ppp", "qqq"}, AuthorName: "Octo Cat", AuthorEmail: "octo@acme.example",
			Time: time.Unix(1700000000, 0).UTC(), Subject: "feat(auth): add login", Body: "Body line 1\n\nRefs: #12"},
		{SHA: "bbb", Parents: []string{}, AuthorName: "A", AuthorEmail: "a@b", Time: time.Unix(1600000000, 0).UTC(), Subject: "chore: init"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Log =\n%#v\nwant\n%#v", got, want)
	}
	if got := ParseLog(""); got != nil {
		t.Errorf("ParseLog(\"\") = %v", got)
	}
	if got := ParseLog("short\x1frecord\x1e"); got != nil {
		t.Errorf("ParseLog(short) = %v", got)
	}
	if _, err := New(execx.NewFake(), "").Log(ctx); err == nil {
		t.Error("Log failure should error")
	}
}

func TestCurrentBranchAndHead(t *testing.T) {
	tests := []struct {
		name       string
		branch     execx.Result
		head       execx.Result
		wantBranch string
		wantHead   string
		branchErr  bool
		headErr    bool
	}{
		{"normal", execx.Result{Stdout: "main\n"}, execx.Result{Stdout: "abc123\n"}, "main", "abc123", false, false},
		{"detached", execx.Result{ExitCode: 1}, execx.Result{Stdout: "abc123\n"}, "", "abc123", false, false},
		{"unborn", execx.Result{Stdout: "main\n"}, execx.Result{ExitCode: 1}, "main", "", false, false},
		{"not a repo", execx.Result{ExitCode: 128, Stderr: "fatal: not a git repository"}, execx.Result{ExitCode: 129}, "", "", true, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := execx.NewFake().
				On("git symbolic-ref --short -q HEAD", tt.branch).
				On("git rev-parse -q --verify HEAD^{commit}", tt.head)
			g := New(f, "")
			b, err := g.CurrentBranch(ctx)
			if b != tt.wantBranch || (err != nil) != tt.branchErr {
				t.Errorf("CurrentBranch = %q, %v", b, err)
			}
			h, err := g.HeadSHA(ctx)
			if h != tt.wantHead || (err != nil) != tt.headErr {
				t.Errorf("HeadSHA = %q, %v", h, err)
			}
		})
	}
	f := execx.NewFake()
	f.Errors["git symbolic-ref --short -q HEAD"] = execx.ErrNotFound
	if _, err := New(f, "").CurrentBranch(ctx); !errors.Is(err, execx.ErrNotFound) {
		t.Errorf("start error = %v", err)
	}
}

func TestConfigGet(t *testing.T) {
	f := execx.NewFake().
		OnOut("git config --get user.name", "Octo Cat\n").
		On("git config --get user.email", execx.Result{ExitCode: 1}).
		On("git config --get bad", execx.Result{ExitCode: 2, Stderr: "error: key does not contain a section: bad"}).
		On("git config --get broken.file", execx.Result{ExitCode: 3, Stderr: "fatal: bad config line 1"}).
		OnOut("git config --get-all remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*\n+refs/tags/*:refs/tags/*\n").
		On("git config --get-all none.key", execx.Result{ExitCode: 1}).
		On("git config --get-all bad", execx.Result{ExitCode: 2})
	g := New(f, "")
	tests := []struct {
		key     string
		val     string
		ok      bool
		wantErr bool
	}{
		{"user.name", "Octo Cat", true, false},
		{"user.email", "", false, false},
		{"bad", "", false, true},
		{"broken.file", "", false, true},
	}
	for _, tt := range tests {
		v, ok, err := g.ConfigGet(ctx, tt.key)
		if v != tt.val || ok != tt.ok || (err != nil) != tt.wantErr {
			t.Errorf("ConfigGet(%s) = %q %v %v", tt.key, v, ok, err)
		}
	}
	all, err := g.ConfigGetAll(ctx, "remote.origin.fetch")
	if err != nil || len(all) != 2 {
		t.Errorf("ConfigGetAll = %q %v", all, err)
	}
	if all, err := g.ConfigGetAll(ctx, "none.key"); all != nil || err != nil {
		t.Errorf("ConfigGetAll missing = %q %v", all, err)
	}
	if _, err := g.ConfigGetAll(ctx, "bad"); err == nil {
		t.Error("ConfigGetAll(bad) should error")
	}
}

func TestBranchExistsAndPaths(t *testing.T) {
	f := execx.NewFake().
		On("git show-ref --verify --quiet refs/heads/main", execx.Result{}).
		On("git show-ref --verify --quiet refs/heads/nope", execx.Result{ExitCode: 1}).
		On("git show-ref --verify --quiet refs/heads/err", execx.Result{ExitCode: 128}).
		OnOut("git rev-parse --show-toplevel", "/repo\n").
		OnOut("git rev-parse --absolute-git-dir", "/repo/.git\n")
	g := New(f, "")
	for name, want := range map[string]bool{"main": true, "nope": false} {
		if ok, err := g.BranchExists(ctx, name); ok != want || err != nil {
			t.Errorf("BranchExists(%s) = %v %v", name, ok, err)
		}
	}
	if _, err := g.BranchExists(ctx, "err"); err == nil {
		t.Error("BranchExists exit 128 should error")
	}
	if r, err := g.RepoRoot(ctx); r != "/repo" || err != nil {
		t.Errorf("RepoRoot = %q %v", r, err)
	}
	if d, err := g.GitDir(ctx); d != "/repo/.git" || err != nil {
		t.Errorf("GitDir = %q %v", d, err)
	}
}

func TestParseStatusZ(t *testing.T) {
	out := " M a.go\x00M  b.go\x00?? new file.txt\x00R  new.go\x00old.go\x00A  c\x00!! ignored\x00"
	got := ParseStatusZ(out)
	want := []StatusEntry{
		{X: ' ', Y: 'M', Path: "a.go"},
		{X: 'M', Y: ' ', Path: "b.go"},
		{X: '?', Y: '?', Path: "new file.txt"},
		{X: 'R', Y: ' ', Path: "new.go", OrigPath: "old.go"},
		{X: 'A', Y: ' ', Path: "c"},
		{X: '!', Y: '!', Path: "ignored"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseStatusZ =\n%+v\nwant\n%+v", got, want)
	}
	checks := []struct {
		i                 int
		untracked, staged bool
	}{{0, false, false}, {1, false, true}, {2, true, false}, {3, false, true}, {5, false, false}}
	for _, c := range checks {
		e := got[c.i]
		if e.Untracked() != c.untracked || e.Staged() != c.staged {
			t.Errorf("%+v: untracked=%v staged=%v", e, e.Untracked(), e.Staged())
		}
	}
	f := execx.NewFake().OnOut("git status --porcelain=v1 -z", "?? x\x00")
	if es, err := New(f, "").StatusPorcelain(ctx); err != nil || len(es) != 1 {
		t.Errorf("StatusPorcelain = %+v %v", es, err)
	}
	if _, err := New(execx.NewFake(), "").StatusPorcelain(ctx); err == nil {
		t.Error("StatusPorcelain failure should error")
	}
}

// A rename in the worktree (intent-to-add, " R") also carries the original
// path as the next NUL field.
func TestParseStatusZWorktreeRename(t *testing.T) {
	got := ParseStatusZ(" R new.go\x00old.go\x00")
	want := []StatusEntry{{X: ' ', Y: 'R', Path: "new.go", OrigPath: "old.go"}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v, want %+v", got, want)
	}
}
