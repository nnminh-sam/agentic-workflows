package app

import (
	"errors"
	"path/filepath"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// Helpers of `mygit commit`, kept out of internal/cli so they can be tested
// without cobra. Every identifier starts with Commit (or commit).

// commitMsgFile is the name the not-yet-written message is checked under.
const commitMsgFile = "mygit-planned.msg"

var errCommitNoFile = errors.New("file not found")

// CommitStatus counts the entries of `git status --porcelain=v2`.
type CommitStatus struct {
	Total    int // every changed, untracked or unmerged entry (what -a would commit)
	Staged   int // entries with a change in the index
	Unmerged int // unresolved conflicts
}

// CommitParseStatus parses `git status --porcelain=v2` output (branch
// headers and ignored entries are skipped).
func CommitParseStatus(out string) CommitStatus {
	var s CommitStatus
	for _, l := range strings.Split(out, "\n") {
		l = strings.TrimRight(l, "\r")
		if l == "" || l[0] == '#' || l[0] == '!' {
			continue
		}
		s.Total++
		f := strings.Fields(l)
		switch l[0] {
		case 'u':
			s.Unmerged++
		case '1', '2':
			if len(f) > 1 && len(f[1]) == 2 && f[1][0] != '.' {
				s.Staged++
			}
		}
	}
	return s
}

// CommitTemplatePath resolves commit.template: "" for the built-in one, a
// path under the repository root (or ~ / absolute) otherwise.
func CommitTemplatePath(e *Env, c *config.Resolved) string {
	p := c.File.Commit.Template
	if p == "" {
		return ""
	}
	p = sign.ExpandHome(p, e.Home)
	if !filepath.IsAbs(p) && e.RepoRoot != "" {
		p = filepath.Join(e.RepoRoot, p)
	}
	return p
}

// CommitRender renders the commit message from the configured template.
func CommitRender(e *Env, c *config.Resolved, d tmpl.CommitData) (string, error) {
	return tmpl.New(defaults.FS, e.ReadFile).RenderCommit(d, CommitTemplatePath(e, c))
}

// CommitGitArgs returns the `-c …` arguments that pin the profile's
// identity and signing on one commit, and whether the commit is signed
// (`-S` is then added). Nothing is pinned for values the profile leaves
// empty, so the repository's own git config applies and the identity rules
// judge it. Format "none" pins commit.gpgsign=false.
func CommitGitArgs(e *Env, c *config.Resolved) (args []string, signed bool) {
	g := c.Profile.Git
	pin := func(k, v string) {
		if v != "" {
			args = append(args, "-c", k+"="+v)
		}
	}
	pin("user.name", g.Name)
	if len(g.Email) > 0 {
		pin("user.email", g.Email[0])
	}
	sg := g.Signing
	switch sg.Format {
	case "":
	case sign.Default:
		signed = true
	case sign.None:
		pin("commit.gpgsign", "false")
	default:
		pin("gpg.format", sg.Format)
		pin("user.signingkey", sign.ExpandHome(sg.Key, e.Home))
		switch sg.Format {
		case sign.OpenPGP:
			pin("gpg.program", sg.Program)
		case sign.X509:
			pin("gpg.x509.program", sg.Program)
		}
		signed = true
	}
	return args, signed
}

// CommitStepArgs returns the arguments of the planned `git commit` step.
func CommitStepArgs(pinned []string, signed bool, msgPath string) []string {
	out := append([]string{}, pinned...)
	out = append(out, "commit")
	if signed {
		out = append(out, "-S")
	}
	return append(out, "-F", msgPath)
}

// CommitEvaluate runs the whole rule engine (CM*, ID001, ID002, ID006,
// BR001 and whatever else applies to a git commit) over the exact command
// the plan will run. The message is served to the engine in place of the
// plan file, which does not exist yet.
func CommitEvaluate(e *Env, c *config.Resolved, pinned []string, signed bool, msg string) verdict.Verdict {
	words := []string{"git"}
	for _, a := range CommitStepArgs(pinned, signed, commitMsgFile) {
		words = append(words, shellfmt.Quote(shellfmt.Bash, a))
	}
	deps := engine.Deps{
		Repo: func(dir string) rules.Repo { return rules.NewGitRepo(e.Runner, dir) },
		ReadFile: func(path string) ([]byte, error) {
			if filepath.Base(path) == commitMsgFile {
				return []byte(msg), nil
			}
			return nil, errCommitNoFile
		},
		Env: e.Getenv,
	}
	return engine.Evaluate(engine.Request{Command: strings.Join(words, " "), Dialect: shell.Bash, Cwd: e.RepoRoot}, c.Policy, deps)
}

// CommitRecordChecks adds one plan check per active rule of the commit,
// identity and branching contracts that applies to a commit (passes as a
// tick, findings with their severity). Findings of other rules are
// recorded too.
func CommitRecordChecks(p *Planner, pol *policy.Policy, v verdict.Verdict, branch string, signed bool) {
	hit := map[string]bool{}
	for _, f := range v.Findings {
		hit[f.RuleID] = true
	}
	note := func(id string) string {
		switch {
		case id == "ID006" && signed:
			return "signed"
		case id == "BR001" && branch != "":
			return "on " + branch
		}
		return ""
	}
	consider := func(contract string, match func(string) bool) {
		c := pol.Contract(contract)
		if c == nil {
			return
		}
		for _, r := range c.Rules {
			if match(r.ID) && r.Severity != verdict.Off && !hit[r.ID] {
				p.Check(r.ID, true, r.Severity, note(r.ID))
			}
		}
	}
	consider(policy.Commit, func(id string) bool { return strings.HasPrefix(id, "CM") })
	consider(policy.Identity, func(id string) bool { return id == "ID001" || id == "ID002" || id == "ID006" })
	consider(policy.Branching, func(id string) bool { return id == "BR001" })
	for _, f := range v.Findings {
		p.Check(f.RuleID, false, f.Severity, strings.Join(strings.Fields(f.Message), " "))
	}
}

// CommitSigStatus names a `git log %G?` code; "" means unsigned or unknown.
func CommitSigStatus(code string) string {
	switch code {
	case "G":
		return "Good"
	case "U":
		return "Good, untrusted"
	case "B":
		return "Bad"
	case "X":
		return "Expired"
	case "Y":
		return "Expired key"
	case "R":
		return "Revoked"
	case "E":
		return "Cannot check"
	}
	return ""
}
