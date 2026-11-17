package pr

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/workflow"
)

// bodyCheckPath is the stand-in path the engine reads the planned body from
// while the real body file does not exist yet.
const bodyCheckPath = "/mygit-plan/pr-body.md"

// Input is everything Plan needs.
type Input struct {
	Env    *app.Env
	Config *config.Resolved
	Target Target
	// Current is the checked-out branch ("" when unknown).
	Current string
	Draft   bool
	// Title overrides the derived title.
	Title string
	// Body replaces the rendered body (still validated); HasBody tells
	// "empty body given" from "none given".
	Body    string
	HasBody bool
	// Sections fills template sections by name (case-insensitive).
	Sections map[string]string
	Version  string
	Now      time.Time
}

// Result is what Plan found.
type Result struct {
	Target Target
	Title  string
	Body   string
	// Existing is the URL of the already open PR; when set nothing else was
	// planned.
	Existing string
	// Verdict holds every finding. A deny or ask decision means the plan must
	// not be applied.
	Verdict verdict.Verdict
	// Push and Create are the exact commands planned (shell-quoted, bash).
	Push   string
	Create string
	// Signed and Total count the commits of the range by signature state.
	Signed, Total int
	Checks        []CheckRun
}

// Plan runs steps ② to ⑦ of the PR sequence on p: read the branch range,
// run the configured test and lint commands, check gh and look for an open
// PR, render and validate the title and body, verify signatures, then record
// the push and `gh pr create` writes. Reads run now; writes are only
// recorded. Findings go to Result.Verdict and to the plan's checks.
func Plan(ctx context.Context, p *app.Planner, in Input) (*Result, error) {
	e, c, t := in.Env, in.Config, in.Target
	if c.Workflow == nil {
		return nil, errors.New("no workflow template is active")
	}
	res := &Result{Target: t}
	g := e.Git
	remote := c.Workflow.Remote

	// ② branch, log, diff
	headRef, err := resolveHead(ctx, e, remote, t)
	if err != nil {
		return nil, err
	}
	baseRef := t.Base
	if r, err := g.Exec(ctx, "rev-parse", "--verify", "-q", "refs/remotes/"+remote+"/"+t.Base); err == nil && r.OK() {
		baseRef = remote + "/" + t.Base
	} else if ok, _ := g.BranchExists(ctx, t.Base); !ok {
		return nil, fmt.Errorf("base branch %s was not found locally or on %s", t.Base, remote)
	}
	logRes, err := p.Read(ctx, g.Cmd("log", LogFormat, baseRef+".."+headRef),
		"git log "+baseRef+".."+headRef, nil)
	if err != nil {
		return nil, err
	}
	if !logRes.OK() {
		return nil, fmt.Errorf("git log %s..%s: %s", baseRef, headRef, strings.TrimSpace(logRes.Stderr))
	}
	commits := ParseCommits(logRes.Stdout)
	if len(commits) == 0 {
		return nil, fmt.Errorf("nothing to merge: %s has no commits that are not already in %s", headRef, baseRef)
	}
	statRes, err := p.Read(ctx, g.Cmd("diff", "--stat", baseRef+"..."+headRef),
		"git diff --stat "+baseRef+"..."+headRef, nil)
	if err != nil {
		return nil, err
	}
	p.Note("%s: %d commit(s) ahead of %s", t.Head, len(commits), baseRef)
	if t.Protected {
		if n := aheadOfRemote(ctx, e, remote, t.Head); n > 0 {
			p.Note("%d local commit(s) on %s are not on %s and will not be in the PR", n, t.Head, remote)
		}
	}

	// ③ test + lint (pr.checks.*)
	if in.Current != "" && in.Current != t.Head {
		p.Note("test and lint run on the checked-out branch %s, not on %s", in.Current, t.Head)
	}
	for _, ck := range []struct{ label, cmd string }{
		{"Unit tests", c.File.PR.Checks.Test}, {"Lint", c.File.PR.Checks.Lint},
	} {
		run := CheckRun{Label: ck.label, Command: strings.TrimSpace(ck.cmd)}
		if run.Command != "" {
			r, err := p.Read(ctx, CheckCmd(run.Command, e.RepoRoot, e.GOOS), run.Command, CheckSummary)
			run.ExitCode, run.Output = r.ExitCode, strings.TrimSpace(r.Stdout+"\n"+r.Stderr)
			if err != nil {
				run.StartError = strings.TrimPrefix(err.Error(), "plan read ")
			}
			if !run.Passed() {
				p.Note("%s did not pass; the PR body reports it", strings.ToLower(ck.label))
			}
		}
		res.Checks = append(res.Checks, run)
	}

	// ④ gh account (ID005), then: is a PR already open?
	if _, err := p.Read(ctx, e.Gh.Cmd("auth", "status"), "gh auth status", authSummary); err != nil {
		return nil, err
	}
	pol := c.Policy
	ev := newEvaluator(e, pol, in.bodyForCheck(""))
	listArgs := []string{"pr", "list", "--head", t.Head, "--json", "url"}
	pre := ev.eval("gh", listArgs, "ID005")
	if blocked(pre) {
		res.Verdict.Extend(pre)
		recordChecks(p, pol, res.Verdict, "ID005")
		return res, nil
	}
	listRes, err := p.Read(ctx, e.Gh.Cmd(listArgs...), "gh pr list --head "+t.Head+" --json url", func(r execx.Result) string {
		if u := firstURL(r.Stdout); u != "" {
			return "already open: " + u
		}
		return "none open"
	})
	if err != nil {
		return nil, err
	}
	if !listRes.OK() {
		return nil, fmt.Errorf("gh pr list: %s", strings.TrimSpace(listRes.Stderr))
	}
	if u := firstURL(listRes.Stdout); u != "" {
		res.Existing = u
		return res, nil
	}

	// ⑤ title, body, rules, signatures
	ghc := pol.Contract(policy.GitHubCLI)
	var gh008 map[string]any
	if ghc != nil {
		gh008 = ghc.Rule("GH008").Params
	}
	var templated []string
	if tp, ok := c.Workflow.Template.PRTemplates[t.Template]; ok {
		templated = tp.Sections
	}
	names := SectionNames(configuredSections(c), templated, gh008)
	filled := AutoSections(t, commits, statRes.Stdout, in.Version, in.Now)
	filled["test results"] = TestResults(res.Checks)
	for k, v := range in.Sections {
		filled[strings.ToLower(strings.TrimSpace(k))] = v
	}
	res.Title = strings.TrimSpace(in.Title)
	if res.Title == "" {
		res.Title = DeriveTitle(t, commits, c.Workflow.WorkTypes, in.Version, in.Now)
	}
	if in.HasBody {
		res.Body = in.Body
	} else {
		res.Body, err = RenderBody(e, c, t, names, filled, in.Version)
		if err != nil {
			return nil, err
		}
	}

	createArgs := []string{"pr", "create", "--base", t.Base, "--head", t.Head, "--title", res.Title, "--body-file", bodyCheckPath}
	if in.Draft {
		createArgs = append(createArgs, "--draft")
	}
	ev = newEvaluator(e, pol, in.bodyForCheck(res.Body))
	res.Verdict.Extend(branchNameFindings(t, c))
	res.Verdict.Extend(ev.eval("gh", createArgs, "ID005", "BR005", "GH005", "GH008", "GH009"))
	pushArgs := PushArgs(c.Profile, remote, t.Head)
	if !t.Protected {
		res.Verdict.Extend(ev.eval("git", pushArgs, "ID003", "ID004", "BR001", "BR002", "BR004"))
	}
	signed, total, sv := verifySigning(ctx, p, e, c, baseRef, headRef)
	res.Signed, res.Total = signed, total
	res.Verdict.Extend(sv)
	recordChecks(p, pol, res.Verdict, "ID005", "BR003", "BR005", "GH005", "GH008", "GH009", "ID003", "ID004", "ID006")

	// ⑥ push -u, ⑦ gh pr create
	if t.Protected {
		p.Note("%s is protected: it is not pushed; it must already be on %s", t.Head, remote)
	} else {
		p.Git("git push -u "+remote+" "+t.Head, pushArgs...)
		res.Push = "git " + quoteArgs(pushArgs)
	}
	bodyPath := p.File("pr-body.md", res.Body)
	createArgs[bodyFileArg] = bodyPath
	p.Gh("gh pr create "+t.String(), createArgs...)
	res.Create = "gh " + quoteArgs(createArgs)
	return res, nil
}

// bodyFileArg is the index of the --body-file value in the create args.
const bodyFileArg = 9

// bodyForCheck serves the planned body to the engine at bodyCheckPath and
// reads every other file from disk through the Env.
func (in Input) bodyForCheck(body string) rules.ReadFile {
	return func(path string) ([]byte, error) {
		if path == bodyCheckPath {
			return []byte(body), nil
		}
		return in.Env.ReadFile(path)
	}
}

// resolveHead returns the ref the PR range ends at: the local head branch,
// else its remote-tracking branch.
func resolveHead(ctx context.Context, e *app.Env, remote string, t Target) (string, error) {
	if ok, err := e.Git.BranchExists(ctx, t.Head); err != nil {
		return "", err
	} else if ok {
		return t.Head, nil
	}
	if r, err := e.Git.Exec(ctx, "rev-parse", "--verify", "-q", "refs/remotes/"+remote+"/"+t.Head); err == nil && r.OK() {
		return remote + "/" + t.Head, nil
	}
	return "", fmt.Errorf("branch %s was not found locally or on %s", t.Head, remote)
}

func aheadOfRemote(ctx context.Context, e *app.Env, remote, head string) int {
	r, err := e.Git.Exec(ctx, "rev-list", "--count", remote+"/"+head+".."+head)
	if err != nil || !r.OK() {
		return 0
	}
	var n int
	fmt.Sscan(strings.TrimSpace(r.Stdout), &n)
	return n
}

func authSummary(r execx.Result) string {
	for _, a := range ghx.ParseAuthStatus(r.Stdout + "\n" + r.Stderr) {
		if a.Active {
			return a.Login + " on " + a.Host
		}
	}
	return ""
}

func firstURL(listJSON string) string {
	var prs []struct {
		URL string `json:"url"`
	}
	if json.Unmarshal([]byte(listJSON), &prs) == nil && len(prs) > 0 {
		return prs[0].URL
	}
	return ""
}

// PushArgs are the git arguments of the PR push. TODO(orchestrator): swap
// this private helper for the shared push planner of `mygit push`. With the
// ssh transport the profile's key is pinned for this one command (rule
// ID003); with the gh transport git authenticates through gh.
func PushArgs(p profile.Profile, remote, branch string) []string {
	var args []string
	if p.GitHub.Transport != profile.TransportGH && p.GitHub.SSHKey != "" {
		args = append(args, "-c", "core.sshCommand="+profile.SSHCommand(p.GitHub.SSHKey))
	}
	return append(args, "push", "-u", remote, branch)
}

// ---------------------------------------------------------------- rules

// evaluator runs planned commands through the rules engine.
type evaluator struct {
	e    *app.Env
	pol  *policy.Policy
	deps engine.Deps
}

func newEvaluator(e *app.Env, pol *policy.Policy, read rules.ReadFile) *evaluator {
	return &evaluator{e: e, pol: pol, deps: engine.Deps{
		Repo:     func(dir string) rules.Repo { return rules.NewGitRepo(e.Runner, dir) },
		ReadFile: read,
		Env:      e.Getenv,
	}}
}

// eval checks `prog args…` and keeps the findings of the listed rules.
func (v *evaluator) eval(prog string, args []string, only ...string) verdict.Verdict {
	line := prog + " " + quoteArgs(args)
	got := engine.Evaluate(engine.Request{Command: line, Dialect: shell.Bash, Cwd: v.e.RepoRoot}, v.pol, v.deps)
	var out verdict.Verdict
	for _, f := range got.Findings {
		for _, id := range only {
			if f.RuleID == id {
				out.Findings = append(out.Findings, f)
				break
			}
		}
	}
	return out
}

func quoteArgs(args []string) string {
	q := make([]string, len(args))
	for i, a := range args {
		q[i] = shellfmt.Quote(shellfmt.Bash, a)
	}
	return strings.Join(q, " ")
}

func blocked(v verdict.Verdict) bool {
	d := v.Decision()
	return d == verdict.DecDeny || d == verdict.DecAsk
}

// Blocked reports whether the findings forbid applying the plan.
func (r *Result) Blocked() bool { return blocked(r.Verdict) }

// branchNameFindings applies BR003 to the head of a work-branch PR.
func branchNameFindings(t Target, c *config.Resolved) verdict.Verdict {
	var v verdict.Verdict
	br := c.Policy.Contract(policy.Branching)
	if br == nil || t.Protected {
		return v
	}
	r := br.Rule("BR003")
	if r.Severity == verdict.Off || t.Head == c.Workflow.Production || t.Head == c.Workflow.Staging {
		return v
	}
	pats := policy.Strings(r.Params, "patterns")
	for _, pat := range pats {
		if re, err := regexp.Compile(pat); err == nil && re.MatchString(t.Head) {
			return v
		}
	}
	if len(pats) > 0 {
		v.Addf("BR003", r.Severity, "branch '%s' does not match the naming rule %s; rename it with git branch -m", t.Head, strings.Join(pats, " or "))
	}
	return v
}

// verifySigning runs `git log %G?` over base..head as a read step and
// reports unsigned commits under ID006 when the profile signs commits.
func verifySigning(ctx context.Context, p *app.Planner, e *app.Env, c *config.Resolved, baseRef, headRef string) (signed, total int, v verdict.Verdict) {
	rng := baseRef + ".." + headRef
	res, err := p.Read(ctx, e.Git.Cmd(sign.LogArgs(rng)...), "mygit sign verify "+rng, func(r execx.Result) string {
		cs := sign.ParseLog(r.Stdout)
		return fmt.Sprintf("%d/%d signed", len(cs)-len(sign.Problems(cs)), len(cs))
	})
	if err != nil || !res.OK() {
		return 0, 0, v
	}
	cs := sign.ParseLog(res.Stdout)
	bad := sign.Problems(cs)
	f := c.Profile.Git.Signing.Format
	if len(bad) == 0 || f == "" || f == profile.SignNone {
		return len(cs) - len(bad), len(cs), v
	}
	sev := verdict.Warn
	if id := c.Policy.Contract(policy.Identity); id != nil {
		if r := id.Rule("ID006"); r.Severity != verdict.Off {
			sev = r.Severity
		}
	}
	var lines []string
	for _, b := range bad {
		short := b.SHA
		if len(short) > 7 {
			short = short[:7]
		}
		lines = append(lines, fmt.Sprintf("%s %s (%s)", short, b.Subject, sign.Describe(b.Status)))
	}
	v.Addf("ID006", sev, "%d of %d commit(s) in %s are not signed (profile signs with %s): %s",
		len(bad), len(cs), rng, f, strings.Join(lines, "; "))
	return len(cs) - len(bad), len(cs), v
}

// recordChecks adds one plan check per evaluated rule: failed when a
// finding names it, passed otherwise. Rules that are off are left out.
func recordChecks(p *app.Planner, pol *policy.Policy, v verdict.Verdict, ids ...string) {
	for _, id := range ids {
		rule, ok := pol.Rule(id)
		if !ok || rule.Severity == verdict.Off {
			continue
		}
		failed := false
		for _, f := range v.Findings {
			if f.RuleID == id {
				p.Check(id, false, f.Severity, f.Message)
				failed = true
				break
			}
		}
		if !failed {
			p.Check(id, true, rule.Severity, "")
		}
	}
}

// ---------------------------------------------------------------- body

// RenderBody renders the PR body of target t from the configured template
// (pr.template) or the built-in one of its kind.
func RenderBody(e *app.Env, c *config.Resolved, t Target, names []string, filled map[string]string, version string) (string, error) {
	custom := strings.TrimSpace(c.File.PR.Template)
	if custom != "" && !filepath.IsAbs(custom) && e.RepoRoot != "" {
		custom = filepath.Join(e.RepoRoot, custom)
	}
	set := tmpl.Default()
	set.ReadFile = e.ReadFile
	return set.RenderPRBody(t.Kind, tmpl.PRData{
		Head: t.Head, Base: t.Base, Version: version, Sections: tmpl.Sections(names, fillFor(names, filled)),
	}, custom)
}

// configuredSections is pr.sections when the user set it; the built-in
// default (which mirrors the GH008 names) yields to the workflow template's
// own section list.
func configuredSections(c *config.Resolved) []string {
	if c.Origins.Of("pr.sections") == workflow.Builtin {
		return nil
	}
	return c.File.PR.Sections
}

// fillFor keys filled by the exact section names, matching names loosely
// (case, and a trailing " (local)").
func fillFor(names []string, filled map[string]string) map[string]string {
	norm := func(s string) string {
		return strings.TrimSpace(strings.TrimSuffix(strings.ToLower(strings.TrimSpace(s)), "(local)"))
	}
	byNorm := map[string]string{}
	for k, v := range filled {
		byNorm[norm(k)] = v
	}
	out := map[string]string{}
	for _, n := range names {
		if v, ok := byNorm[norm(n)]; ok {
			out[n] = v
		}
	}
	return out
}

// Sections returns the section names the body of kind carries in config c.
func Sections(c *config.Resolved, t Target) []string {
	var gh008 map[string]any
	if g := c.Policy.Contract(policy.GitHubCLI); g != nil {
		gh008 = g.Rule("GH008").Params
	}
	var templated []string
	if c.Workflow != nil {
		if tp, ok := c.Workflow.Template.PRTemplates[t.Template]; ok {
			templated = tp.Sections
		}
	}
	return SectionNames(configuredSections(c), templated, gh008)
}

// ---------------------------------------------------------------- url capture

// URLCapture wraps a Runner and remembers the URL `gh pr create` printed.
type URLCapture struct {
	Next execx.Runner
	URL  *string
}

// Run implements execx.Runner.
func (u URLCapture) Run(ctx context.Context, c execx.Cmd) (execx.Result, error) {
	res, err := u.Next.Run(ctx, c)
	if c.Name == "gh" && len(c.Args) >= 2 && c.Args[0] == "pr" && c.Args[1] == "create" && res.OK() {
		lines := strings.Split(strings.TrimSpace(res.Stdout), "\n")
		for i := len(lines) - 1; i >= 0; i-- {
			if l := strings.TrimSpace(lines[i]); strings.HasPrefix(l, "http") {
				*u.URL = l
				break
			}
		}
	}
	return res, err
}
