// Package engine evaluates one shell command against every contract: a
// cheap pre-filter, then the shell parser, then the per-program rule checks,
// with all findings collected into one verdict (the strictest wins). It is
// the port of scripts/gitguard/engine.py and the single entry point used by
// the agent hooks, `mygit guard` and `mygit lint cmd`.
package engine

import (
	"regexp"
	"strings"
	"time"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// interesting is the cheap pre-filter: commands that cannot involve git, gh
// or the GitHub API skip parsing.
var interesting = regexp.MustCompile(`(?i)\b(git|gh)(\.exe)?\b|github\.com`)

// Request is one command to evaluate.
type Request struct {
	Command string        // the command line as the agent / user typed it
	Dialect shell.Dialect // grammar of Command
	Cwd     string        // directory the command starts in
}

// Deps are the engine's side effects, all injectable.
type Deps struct {
	// Repo returns the repository facts for a working directory (one call per
	// distinct directory per evaluation), e.g.
	// func(cwd string) rules.Repo { return rules.NewGitRepo(execx.OS{}, cwd) }.
	Repo func(cwd string) rules.Repo
	// ReadFile reads commit message / PR body files; nil makes them unreadable.
	ReadFile rules.ReadFile
	// Env is the process environment (os.Getenv); nil means empty.
	Env func(string) string
}

// DialectFor maps an agent tool name to a dialect: "powershell" (any case)
// is PowerShell, anything else (Bash, shell, ...) is Bash.
func DialectFor(toolName string) shell.Dialect {
	if strings.EqualFold(toolName, "powershell") {
		return shell.PowerShell
	}
	return shell.Bash
}

// Evaluate checks one command line against the policy.
func Evaluate(req Request, pol *policy.Policy, deps Deps) verdict.Verdict {
	var v verdict.Verdict
	if req.Command == "" || !interesting.MatchString(req.Command) {
		return v
	}
	e := newEval(pol, deps)
	for _, inv := range shell.ParseWith(req.Command, req.Dialect, req.Cwd, shell.Options{Home: rules.HomeDir(e.getenv)}) {
		switch {
		case inv.Program == "git":
			v.Extend(e.git(inv))
		case inv.Program == "gh":
			v.Extend(e.gh(inv))
		case shell.IsHTTPClient(inv.Program):
			v.Extend(rules.CheckHTTPClient(inv, pol.Contract(policy.GitHubCLI)))
		}
	}
	return v
}

// eval holds the per-evaluation state: contracts and one Repo per cwd.
type eval struct {
	pol    *policy.Policy
	deps   Deps
	repos  map[string]rules.Repo
	getenv func(string) string
}

func newEval(pol *policy.Policy, deps Deps) *eval {
	getenv := deps.Env
	if getenv == nil {
		getenv = func(string) string { return "" }
	}
	return &eval{pol: pol, deps: deps, repos: map[string]rules.Repo{}, getenv: getenv}
}

func (e *eval) contract(name string) *policy.Contract { return e.pol.Contract(name) }

func (e *eval) vars() map[string]any {
	if e.pol == nil {
		return nil
	}
	return e.pol.Vars
}

func (e *eval) repo(cwd string) rules.Repo {
	if r, ok := e.repos[cwd]; ok {
		return r
	}
	var r rules.Repo = noRepo{}
	if e.deps.Repo != nil {
		if got := e.deps.Repo(cwd); got != nil {
			r = got
		}
	}
	e.repos[cwd] = r
	return r
}

func (e *eval) git(inv shell.Invocation) verdict.Verdict {
	var v verdict.Verdict
	call, ok := shell.AsGit(inv)
	if !ok {
		return v
	}
	id := e.contract(policy.Identity)
	repo := e.repo(call.Cwd)
	if call.Sub == "commit" {
		v.Extend(rules.CheckAuthor(call, id, repo, e.getenv))
		v.Extend(rules.CheckSigning(call, id, e.vars()))
		v.Extend(rules.CheckCommitMessage(call, e.contract(policy.Commit), e.deps.ReadFile))
	}
	v.Extend(rules.CheckNetwork(call, rules.NetworkInput{Contract: id, Vars: e.vars(), Repo: repo, Getenv: e.getenv}))
	v.Extend(rules.CheckGitBranching(call, e.contract(policy.Branching), repo))
	return v
}

func (e *eval) gh(inv shell.Invocation) verdict.Verdict {
	var v verdict.Verdict
	if argv := inv.Argv(); len(argv) >= 2 && argv[0] == "auth" && (argv[1] == "switch" || argv[1] == "login" || argv[1] == "logout") {
		for _, r := range e.repos {
			r.ClearGhCache()
		}
	}
	repo := e.repo(inv.Cwd)
	v.Extend(rules.CheckGhAccount(inv, e.contract(policy.Identity), repo, e.getenv))
	v.Extend(rules.CheckGh(inv, rules.GhInput{
		GH: e.contract(policy.GitHubCLI), Commit: e.contract(policy.Commit), ReadFile: e.deps.ReadFile,
	}))
	v.Extend(rules.CheckGhBranching(inv, e.contract(policy.Branching), repo))
	return v
}

// noRepo answers "unknown" to everything; used when Deps.Repo is nil.
type noRepo struct{}

func (noRepo) Config(string) (string, bool)                 { return "", false }
func (noRepo) ConfigURLMatch(string, string) (string, bool) { return "", false }
func (noRepo) Remotes() []string                            { return nil }
func (noRepo) RemoteURL(string, bool) (string, bool)        { return "", false }
func (noRepo) CurrentBranch() (string, bool)                { return "", false }
func (noRepo) HasCommits() bool                             { return true }
func (noRepo) RemoteBranchExists(string, string) bool       { return true }
func (noRepo) GhActiveLogin(string, string, time.Duration) (string, string) {
	return "", "no repository information available"
}
func (noRepo) ClearGhCache() {}
