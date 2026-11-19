package agent

import (
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// SessionInput is what the session-start briefing reads.
type SessionInput struct {
	Cwd    string
	Policy *policy.Policy
	Repo   rules.Repo
	Getenv func(string) string
}

// SessionNotes returns the identity problems an agent should know about
// before it runs any git or gh command (the port of session_start.py's
// summary): ID001/ID002 author, ID005 gh account, ID003 gh credential
// helper. The checks reuse the rule functions on a synthetic `git commit`
// and `gh` call, so the wording matches what the guard would block with.
func SessionNotes(in SessionInput) []string {
	var notes []string
	add := func(v verdict.Verdict) {
		for _, f := range v.Findings {
			notes = append(notes, fmt.Sprintf("%s (%s).", strings.TrimSuffix(f.Message, "."), f.RuleID))
		}
	}
	id := in.Policy.Contract(policy.Identity)
	if id == nil {
		return nil
	}
	commit := &shell.GitCall{Sub: "commit", Cwd: in.Cwd, Config: map[string]string{}}
	add(rules.CheckAuthor(commit, id, in.Repo, in.Getenv))
	for _, inv := range shell.ParseWith("gh repo view", shell.Bash, in.Cwd, shell.Options{}) {
		add(rules.CheckGhAccount(inv, id, in.Repo, in.Getenv))
	}
	if id.Rule("ID003").Severity != verdict.Off && rules.Transport(in.Policy.Vars) == "gh" {
		host := policy.Str(id.Settings, "gh_hostname", "github.com")
		helper, ok := in.Repo.ConfigURLMatch("credential.helper", "https://"+host)
		if !ok || !rules.IsGhHelper(helper) {
			shown := "not set"
			if ok {
				shown = "'" + helper + "'"
			}
			notes = append(notes, fmt.Sprintf("git_transport is 'gh' but the git credential helper for %s is %s; "+
				"pushes will be blocked. Fix: gh auth setup-git --hostname %s (ID003).", host, shown, host))
		}
	}
	return notes
}

// SessionContext wraps notes in the briefing text shown to the agent; nil
// when there is nothing to say.
func SessionContext(notes []string) []string {
	if len(notes) == 0 {
		return nil
	}
	lines := []string{"mygit identity check (matching commits / pushes / gh calls will be blocked until fixed):"}
	for _, n := range notes {
		lines = append(lines, "- "+n)
	}
	return append(lines, "Do not change git/gh identity yourself; tell the user and let them decide.")
}

// InvalidConfigContext is the briefing when the config cannot be loaded.
func InvalidConfigContext(err error) []string {
	return []string{fmt.Sprintf("mygit: the configuration is invalid, git/gh commands will need confirmation "+
		"until it is fixed (run mygit doctor): %v", err)}
}
