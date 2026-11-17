package repoops_test

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/repoops"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

func compiled(t *testing.T, mutate func(*workflow.Template)) *workflow.Compiled {
	t.Helper()
	cat, err := workflow.Builtins(defaults.FS)
	if err != nil {
		t.Fatal(err)
	}
	r, err := workflow.Resolve("default", cat)
	if err != nil {
		t.Fatal(err)
	}
	if mutate != nil {
		mutate(&r.Template)
	}
	c, err := workflow.Compile(r, workflow.CompileOptions{})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func ptr(i int) *int { return &i }

func TestParseSlug(t *testing.T) {
	for in, ok := range map[string]bool{"acme/api": true, "a.b/c-d_e": true, "acme": false, "a/b/c": false, "": false, "acme/": false, "ac me/x": false} {
		_, _, err := repoops.ParseSlug(in)
		if (err == nil) != ok {
			t.Errorf("ParseSlug(%q) err=%v, want ok=%v", in, err, ok)
		}
	}
	o, n, _ := repoops.ParseSlug("acme/api")
	if o != "acme" || n != "api" {
		t.Errorf("got %s/%s", o, n)
	}
}

func TestRefPatternAndName(t *testing.T) {
	if got := repoops.RefPattern("release/{version}"); got != "refs/heads/release/*" {
		t.Errorf("got %s", got)
	}
	if got := repoops.RefPattern("develop/base"); got != "refs/heads/develop/base" {
		t.Errorf("got %s", got)
	}
	for want, in := range map[string][]string{
		"my-git: protect main":                         {"main"},
		"my-git: protect main and develop/base":        {"main", "develop/base"},
		"my-git: protect main, develop/base and rel/*": {"main", "develop/base", "rel/*"},
		"my-git: protect branches":                     nil,
	} {
		if got := repoops.RulesetName(in); got != want {
			t.Errorf("RulesetName(%v) = %q, want %q", in, got, want)
		}
	}
}

func ruleTypes(r repoops.Ruleset) []string {
	var out []string
	for _, x := range r.Rules {
		out = append(out, x.Type)
	}
	return out
}

func TestBuildRuleset(t *testing.T) {
	tests := []struct {
		name      string
		mutate    func(*workflow.Template)
		opt       repoops.RulesetOptions
		wantTypes []string
		approvals int
		wantErr   string
	}{
		{name: "default template", wantTypes: []string{"deletion", "non_fast_forward", "pull_request"}},
		{name: "approvals override", opt: repoops.RulesetOptions{Approvals: ptr(2)},
			wantTypes: []string{"deletion", "non_fast_forward", "pull_request"}, approvals: 2},
		{name: "require signed", opt: repoops.RulesetOptions{RequireSigned: true},
			wantTypes: []string{"deletion", "non_fast_forward", "pull_request", "required_signatures"}},
		{name: "template signed + linear", mutate: func(t *workflow.Template) {
			t.Protection.RequireSigned, t.Protection.LinearHistory = true, true
		}, wantTypes: []string{"deletion", "non_fast_forward", "pull_request", "required_linear_history", "required_signatures"}},
		{name: "template allows force push and deletion, no PR", mutate: func(t *workflow.Template) {
			t.Protection.ForcePush, t.Protection.Deletion, t.Protection.RequirePR = true, true, false
		}, wantTypes: nil},
		{name: "negative approvals", opt: repoops.RulesetOptions{Approvals: ptr(-1)}, wantErr: "approvals must be 0 or more"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rs, err := repoops.BuildRuleset(compiled(t, tt.mutate), tt.opt)
			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("err = %v, want %q", err, tt.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got := ruleTypes(rs); !reflect.DeepEqual(got, tt.wantTypes) {
				t.Errorf("rules = %v, want %v", got, tt.wantTypes)
			}
			var doc struct {
				Name       string `json:"name"`
				Conditions struct {
					RefName struct {
						Include []string `json:"include"`
					} `json:"ref_name"`
				} `json:"conditions"`
				Rules []struct {
					Type       string         `json:"type"`
					Parameters map[string]any `json:"parameters"`
				} `json:"rules"`
			}
			if err := json.Unmarshal([]byte(rs.JSON()), &doc); err != nil {
				t.Fatalf("invalid json: %v", err)
			}
			if want := []string{"refs/heads/main", "refs/heads/develop/base"}; !reflect.DeepEqual(doc.Conditions.RefName.Include, want) {
				t.Errorf("include = %v, want %v", doc.Conditions.RefName.Include, want)
			}
			if doc.Name != "my-git: protect main and develop/base" {
				t.Errorf("name = %q", doc.Name)
			}
			for _, r := range doc.Rules {
				if r.Type == "pull_request" {
					if got := int(r.Parameters["required_approving_review_count"].(float64)); got != tt.approvals {
						t.Errorf("approvals = %d, want %d", got, tt.approvals)
					}
				}
			}
		})
	}
}

func TestBuildRulesetPatternBranch(t *testing.T) {
	c := compiled(t, func(t *workflow.Template) {
		t.Branches["release"] = workflow.Branch{Name: "release/{version}", Protected: true, Pattern: true}
		t.Transitions = append(t.Transitions, workflow.Transition{ID: "hotfix", From: "release", To: workflow.Targets{"production"}, Via: "pr"})
	})
	rs, err := repoops.BuildRuleset(c, repoops.RulesetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(rs.JSON(), `"refs/heads/release/*"`) {
		t.Errorf("pattern branch not in include list:\n%s", rs.JSON())
	}
}

func TestExistingAndDefaultBranch(t *testing.T) {
	l, err := repoops.ParseExisting(`[{"id":7,"name":"other"},{"id":9,"name":"my-git: protect main and develop/base","target":"branch"}]`)
	if err != nil {
		t.Fatal(err)
	}
	if id, ok := repoops.FindByName(l, "my-git: protect main and develop/base"); !ok || id != 9 {
		t.Errorf("id = %d ok=%v", id, ok)
	}
	if _, ok := repoops.FindByName(l, "nope"); ok {
		t.Error("found a missing ruleset")
	}
	if l, err := repoops.ParseExisting("  "); err != nil || l != nil {
		t.Errorf("empty: %v %v", l, err)
	}
	if _, err := repoops.ParseExisting("{"); err == nil {
		t.Error("want a decode error")
	}
	if got := repoops.DefaultBranch(compiled(t, nil)); got != "develop/base" {
		t.Errorf("default branch = %s", got)
	}
}

func TestIdentityConfig(t *testing.T) {
	p := profile.Profile{}
	p.Git.Name, p.Git.Email = "Octo", profile.StringList{"o@x.io", "b@x.io"}
	p.Git.Signing = profile.Signing{Format: "ssh", Key: "/k.pub"}
	kv, sign := repoops.IdentityConfig(p)
	want := [][2]string{{"user.name", "Octo"}, {"user.email", "o@x.io"}, {"gpg.format", "ssh"}, {"user.signingkey", "/k.pub"}}
	if !sign || !reflect.DeepEqual(kv, want) {
		t.Errorf("kv=%v sign=%v", kv, sign)
	}
	p.Git.Signing = profile.Signing{Format: "none"}
	if kv, sign := repoops.IdentityConfig(p); sign || len(kv) != 2 {
		t.Errorf("none: kv=%v sign=%v", kv, sign)
	}
	if kv, sign := repoops.IdentityConfig(profile.Profile{}); sign || kv != nil {
		t.Errorf("empty: kv=%v sign=%v", kv, sign)
	}
	got := repoops.GitArgs([][2]string{{"user.name", "O"}}, "commit", "-m", "x")
	if !reflect.DeepEqual(got, []string{"-c", "user.name=O", "commit", "-m", "x"}) {
		t.Errorf("GitArgs = %v", got)
	}
}

func TestPlan(t *testing.T) {
	base := repoops.Facts{InRepo: true, Production: "main", Staging: "develop/base", CurrentBranch: "main"}
	with := func(f func(*repoops.Facts)) repoops.Facts { c := base; f(&c); return c }
	msg := "chore: bootstrap repository"
	tests := []struct {
		name    string
		f       repoops.Facts
		want    [][]string // git args of each step
		done    bool
		wantErr string
	}{
		{name: "not a repository", f: with(func(f *repoops.Facts) { f.InRepo, f.CurrentBranch = false, "" }),
			want: [][]string{{"init", "-b", "main"}, {"commit", "--allow-empty", "-m", msg}, {"branch", "develop/base", "main"}}},
		{name: "unborn on master", f: with(func(f *repoops.Facts) { f.CurrentBranch = "master" }),
			want: [][]string{{"symbolic-ref", "HEAD", "refs/heads/main"}, {"commit", "--allow-empty", "-m", msg}, {"branch", "develop/base", "main"}}},
		{name: "unborn on main, staged files", f: with(func(f *repoops.Facts) { f.Staged = true }),
			want: [][]string{{"commit", "-m", msg}, {"branch", "develop/base", "main"}}},
		{name: "bootstrap commit exists, staging missing", f: with(func(f *repoops.Facts) {
			f.HasCommits, f.ProductionRef, f.ProductionLen, f.ProductionRoot = true, true, 1, repoops.BootstrapMessage
		}), want: [][]string{{"branch", "develop/base", "main"}}},
		{name: "everything exists", f: with(func(f *repoops.Facts) {
			f.HasCommits, f.ProductionRef, f.StagingRef, f.ProductionLen, f.ProductionRoot = true, true, true, 1, repoops.BootstrapMessage
		}), done: true},
		{name: "history and both branches: already set up", f: with(func(f *repoops.Facts) {
			f.HasCommits, f.ProductionRef, f.StagingRef, f.ProductionLen, f.ProductionRoot = true, true, true, 12, "feat: x"
		}), done: true},
		{name: "history on main, no staging: refuse", f: with(func(f *repoops.Facts) {
			f.HasCommits, f.ProductionRef, f.ProductionLen, f.ProductionRoot = true, true, 12, "feat: x"
		}), wantErr: "refusing to bootstrap"},
		{name: "commits but no main", f: with(func(f *repoops.Facts) { f.HasCommits, f.CurrentBranch = true, "master" }),
			wantErr: `no "main" branch`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			steps, done, err := repoops.Plan(tt.f)
			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("err = %v, want %q", err, tt.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			var got [][]string
			for _, s := range steps {
				got = append(got, s.Args)
			}
			if !reflect.DeepEqual(got, tt.want) || done != tt.done {
				t.Errorf("steps = %v done=%v, want %v done=%v", got, done, tt.want, tt.done)
			}
		})
	}
}
