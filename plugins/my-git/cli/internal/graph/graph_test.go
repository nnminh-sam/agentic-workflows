package graph

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

func compileDefault(t *testing.T) *workflow.Compiled {
	t.Helper()
	cat, err := workflow.Builtins(defaults.FS)
	if err != nil {
		t.Fatal(err)
	}
	res, err := workflow.Resolve("default", cat)
	if err != nil {
		t.Fatal(err)
	}
	wf, err := workflow.Compile(res, workflow.CompileOptions{})
	if err != nil {
		t.Fatal(err)
	}
	return wf
}

func TestModelAndParseHelpers(t *testing.T) {
	c := Commit{SHA: "1234567890abcdef"}
	if c.Short() != "1234567" || (Commit{SHA: "abc"}).Short() != "abc" {
		t.Error("Short")
	}

	refs := ParseDecorations("HEAD -> main, origin/main, origin/HEAD, tag: v1.0, HEAD, refs/stash")
	if len(refs) != 5 || !refs[0].Head || refs[0].Name != "main" || refs[2].Kind != RefTag || refs[3].Kind != RefHead || refs[4].Kind != RefOther {
		t.Errorf("ParseDecorations = %+v", refs)
	}

	branches := ParseBranches(
		"refs/heads/main\x1faaa\x1forigin/main\x1f[ahead 1, behind 2]\n" +
			"refs/remotes/origin/main\x1faaa\x1f\x1f\n" +
			"refs/remotes/origin/HEAD\x1faaa\x1f\x1f\n" +
			"refs/tags/v1\x1faaa\x1f\x1f\n")
	if len(branches) != 2 || branches[0].Ahead != 1 || branches[0].Behind != 2 || !branches[1].Remote {
		t.Errorf("ParseBranches = %+v", branches)
	}

	commits := []Commit{{SHA: "aaa", Refs: refs}}
	ClassifyRefs(commits, branches)
	if commits[0].Refs[1].Kind != RefRemote {
		t.Errorf("ClassifyRefs did not mark origin/main remote: %+v", commits[0].Refs)
	}

	for _, tc := range []struct {
		code, key, email string
		wantStatus, kind string
	}{
		{"G", "SHA256:abc", "a@b.c", SigOK, "ssh"},
		{"U", "ABCDEF", "a@b.c", SigOK, "gpg"},
		{"X", "CN=test", "a@b.c", SigOK, "x509"},
		{"E", "4AEE18F83AFDEB23", "x@y.z", SigOK, "github"},
		{"E", "SHA256:abc", "a@b.c", SigUnknown, "ssh"},
		{"B", "ABCDEF", "a@b.c", SigBad, "gpg"},
		{"N", "", "a@b.c", SigUnsigned, ""},
	} {
		sig := ParseSignature(tc.code, tc.key, tc.email)
		if sig.Status != tc.wantStatus || sig.Kind != tc.kind {
			t.Errorf("ParseSignature(%q,%q,%q) = %+v, want %s/%s", tc.code, tc.key, tc.email, sig, tc.wantStatus, tc.kind)
		}
	}
}

func TestCollectAndRenderGraph(t *testing.T) {
	f := execx.NewFake()
	f.OnOut("git symbolic-ref --short -q HEAD", "develop/feat/login\n")
	logOut := "a1f9c020000\x1f3e21aa00000\x1fHEAD -> develop/feat/login\x1fG\x1fSHA256:xyz\x1fo@x.io\x1ffeat(auth): add login endpoint and a very long subject line that exceeds max width\x1e" +
		"3e21aa00000\x1f\x1fdevelop/base, origin/develop/base, tag: v1.0\x1fE\x1f4AEE18F83AFDEB23\x1fnoreply@github.com\x1fMerge pull request #16\x1e"
	f.OnOut("git log --all --topo-order "+logFormat+" -n 2", logOut)
	refOut := "refs/heads/develop/feat/login\x1fa1f9c020000\x1forigin/develop/feat/login\x1f[ahead 2, behind 1]\n" +
		"refs/heads/develop/base\x1f3e21aa00000\x1forigin/develop/base\x1f\n" +
		"refs/remotes/origin/develop/base\x1f3e21aa00000\x1f\x1f\n"
	f.OnOut("git for-each-ref "+refFormat+" refs/heads refs/remotes", refOut)
	f.OnOut("gh pr list --state open --limit 100 --json "+prFields,
		`[{"number":18,"headRefName":"develop/feat/login","baseRefName":"develop/base","isDraft":false,"state":"OPEN","statusCheckRollup":[{"status":"COMPLETED","conclusion":"SUCCESS"}]}]`)

	repo, err := Collect(context.Background(), gitx.New(f, "/repo"), ghx.New(f, "/repo"), Options{Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	if !repo.Limited || len(repo.Commits) != 1 || len(repo.PRs) != 1 || repo.PRs[0].Checks != ChecksPass {
		t.Fatalf("Collect = %+v", repo)
	}

	// Full collect without limit.
	f.OnOut("git log --all --topo-order "+logFormat, logOut)
	repo, err = Collect(context.Background(), gitx.New(f, "/repo"), ghx.New(f, "/repo"), Options{})
	if err != nil {
		t.Fatal(err)
	}
	repo.Warnings = []string{"sample warning"}
	rows := Layout(repo.Commits)
	wf := compileDefault(t)

	var textBuf bytes.Buffer
	if err := RenderText(&textBuf, repo, rows, TextOptions{Color: true, Protected: wf.IsProtected}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(textBuf.String(), "a1f9c02") || !strings.Contains(textBuf.String(), "PR #18 open") || !strings.Contains(textBuf.String(), "legend") {
		t.Errorf("RenderText:\n%s", textBuf.String())
	}

	var asciiBuf bytes.Buffer
	if err := RenderText(&asciiBuf, repo, rows, TextOptions{ASCII: true, Protected: wf.IsProtected}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(asciiBuf.String(), "[P]") {
		t.Errorf("RenderText ASCII:\n%s", asciiBuf.String())
	}

	var htmlBuf bytes.Buffer
	if err := RenderHTML(&htmlBuf, repo, rows, HTMLOptions{Protected: wf.IsProtected}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(htmlBuf.String(), "<svg") || !strings.Contains(htmlBuf.String(), "a1f9c02") {
		t.Errorf("RenderHTML:\n%s", htmlBuf.String())
	}
}

func TestCollectFlowAndRender(t *testing.T) {
	wf := compileDefault(t)
	repo := Repo{
		Remote: "origin",
		Head:   "develop/feat/login",
		Branches: []Branch{
			{Name: "main", SHA: "sha-main", Upstream: "origin/main"},
			{Name: "origin/main", SHA: "sha-main", Remote: true},
			{Name: "develop/base", SHA: "sha-base", Upstream: "origin/develop/base"},
			{Name: "origin/develop/base", SHA: "sha-base", Remote: true},
			{Name: "develop/feat/login", SHA: "sha-login", Upstream: "origin/develop/feat/login", Ahead: 1},
			{Name: "origin/develop/feat/login", SHA: "sha-login-old", Remote: true},
			{Name: "develop/fix/new", SHA: "sha-base"},
			{Name: "develop/docs/done", SHA: "sha-done", Upstream: "origin/develop/docs/done"},
			{Name: "scratch", SHA: "sha-scratch"},
		},
		PRs: []PR{
			{Number: 18, Head: "develop/feat/login", Base: "develop/base", State: PROpen, Checks: ChecksFail},
			{Number: 16, Head: "develop/docs/done", Base: "develop/base", State: PRMerged},
			{Number: 15, Head: "develop/base", Base: "main", State: PRMerged},
		},
		Warnings: []string{"test warning"},
	}

	f := execx.NewFake()
	f.OnOut("git rev-list --left-right --count origin/develop/base...develop/feat/login", "1\t2\n")
	f.OnOut("git rev-list --left-right --count origin/develop/base...develop/fix/new", "0\t0\n")
	f.OnOut("git rev-list --left-right --count origin/develop/base...develop/docs/done", "0\t0\n")
	f.OnOut("git rev-list --left-right --count origin/main...origin/develop/base", "0\t3\n")

	flow, err := CollectFlow(context.Background(), gitx.New(f, "/repo"), wf, repo)
	if err != nil {
		t.Fatal(err)
	}
	if len(flow.Work) != 3 || len(flow.Long) != 2 || len(flow.Ignored) != 1 || flow.Ignored[0] != "scratch" {
		t.Fatalf("flow = %+v", flow)
	}
	if len(flow.Next) != 1 || !strings.Contains(flow.Next[0], "mygit pr release (3 commits)") {
		t.Errorf("Next = %v", flow.Next)
	}

	var textBuf bytes.Buffer
	if err := RenderFlowText(&textBuf, flow, FlowTextOptions{Color: true}); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"WORK BRANCHES", "develop/feat/login", "develop/fix/new", "develop/docs/done", "mygit branch prune", "outside the workflow: scratch"} {
		if !strings.Contains(textBuf.String(), want) {
			t.Errorf("RenderFlowText lacks %q:\n%s", want, textBuf.String())
		}
	}

	var htmlBuf bytes.Buffer
	if err := RenderFlowHTML(&htmlBuf, flow, ""); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(htmlBuf.String(), "<svg") || !strings.Contains(htmlBuf.String(), "develop/feat/login") {
		t.Errorf("RenderFlowHTML:\n%s", htmlBuf.String())
	}

	// Also render empty work list in HTML.
	flow.Work = nil
	htmlBuf.Reset()
	if err := RenderFlowHTML(&htmlBuf, flow, "custom"); err != nil || !strings.Contains(htmlBuf.String(), "no work branches") {
		t.Errorf("RenderFlowHTML empty work: %v\n%s", err, htmlBuf.String())
	}
}

func TestSummarizeChecksAndClassifyWork(t *testing.T) {
	if got := summarizeChecks(nil); got != ChecksNone {
		t.Error(got)
	}
	if got := summarizeChecks([]ghCheck{{Status: "COMPLETED", Conclusion: "FAILURE"}}); got != ChecksFail {
		t.Error(got)
	}
	if got := summarizeChecks([]ghCheck{{Status: "IN_PROGRESS"}}); got != ChecksPending {
		t.Error(got)
	}
	if got := summarizeChecks([]ghCheck{{Status: "COMPLETED", Conclusion: "SUCCESS"}}); got != ChecksPass {
		t.Error(got)
	}

	// classifyWork branches
	st, nxt := classifyWork(WorkItem{Ahead: 2}, false)
	if st != StateNoPR || nxt != "mygit pr create" {
		t.Errorf("no-pr: %s / %s", st, nxt)
	}
	st, nxt = classifyWork(WorkItem{PR: &PR{State: PROpen, Draft: true}}, false)
	if st != StatePROpen || !strings.Contains(nxt, "ready for review") {
		t.Errorf("draft: %s / %s", st, nxt)
	}
	st, nxt = classifyWork(WorkItem{PR: &PR{State: PROpen, Checks: ChecksFail}}, false)
	if st != StatePROpen || !strings.Contains(nxt, "fix failing checks") {
		t.Errorf("failing checks: %s / %s", st, nxt)
	}
	st, nxt = classifyWork(WorkItem{PR: &PR{State: PROpen, Checks: ChecksPass}}, false)
	if st != StatePROpen || !strings.Contains(nxt, "wait for review") {
		t.Errorf("pass checks: %s / %s", st, nxt)
	}
}
