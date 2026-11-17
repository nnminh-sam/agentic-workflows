package config

import (
	"errors"
	"io/fs"
	"path"
	"reflect"
	"sort"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const (
	home   = "/home/u"
	repo   = "/home/u/work/api"
	gpath  = "/home/u/.config/mygit/config.json"
	ppath  = repo + "/.mygit.json"
	lpath  = repo + "/.mygit.local.json"
	gwfdir = "/home/u/.config/mygit/workflows"
	pwfdir = repo + "/.mygit/workflows"
)

// world is an in-memory machine: files and environment.
type world struct {
	files map[string]string
	env   map[string]string
	owner string
	goos  string
	flags Flags
}

func newWorld() *world {
	return &world{files: map[string]string{}, env: map[string]string{}, goos: "linux"}
}

func (w *world) inputs() Inputs {
	return Inputs{
		Cwd: repo, RepoRoot: repo, RemoteOwner: w.owner, GOOS: w.goos, Home: home,
		Getenv: func(k string) string { return w.env[k] },
		ReadFile: func(p string) ([]byte, error) {
			if s, ok := w.files[p]; ok {
				return []byte(s), nil
			}
			return nil, fs.ErrNotExist
		},
		Glob: func(pat string) ([]string, error) {
			var out []string
			for k := range w.files {
				if ok, _ := path.Match(pat, k); ok {
					out = append(out, k)
				}
			}
			sort.Strings(out)
			return out, nil
		},
		Flags: w.flags,
	}
}

func (w *world) resolve(t *testing.T) *Resolved {
	t.Helper()
	r, err := Resolve(w.inputs())
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	return r
}

func rule(t *testing.T, r *Resolved, id string) policy.Rule {
	t.Helper()
	ru, ok := r.Policy.Rule(id)
	if !ok {
		t.Fatalf("rule %s missing", id)
	}
	return ru
}

func wantRule(t *testing.T, r *Resolved, id string, sev verdict.Severity, origin string) policy.Rule {
	t.Helper()
	ru := rule(t, r, id)
	if ru.Severity != sev || (origin != "" && ru.Origin != origin) {
		t.Errorf("%s = %s ← %q; want %s ← %q", id, ru.Severity, ru.Origin, sev, origin)
	}
	return ru
}

func reflectFileType() reflect.Type { return reflect.TypeOf(File{}) }

var errFake = errors.New("permission denied")
