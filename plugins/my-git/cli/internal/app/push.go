package app

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// PushOptions tune PlanPush.
type PushOptions struct {
	// Upstream adds -u so the branch tracks the remote branch afterwards.
	// A branch with no remote counterpart gets -u even without it.
	Upstream bool
	// Remote is the remote to push to ("" = the workflow's remote, else origin).
	Remote string
	// Branch is the local branch to push ("" = the checked-out branch).
	// Every read then uses refs/heads/<branch>, never HEAD.
	Branch string
	// SkipSigning leaves out the ID006 signature check of the commits to
	// push (for callers that verify signatures over their own range).
	SkipSigning bool
}

// PushPlan is what PlanPush found and recorded.
type PushPlan struct {
	Branch string `json:"branch"`
	Remote string `json:"remote"`
	// Upstream is the configured upstream ("origin/x"), else the same-named
	// remote-tracking branch when it exists, else "" (a new remote branch).
	Upstream string `json:"upstream,omitempty"`
	// Ahead and Behind count commits against Upstream (0 when it is unknown).
	Ahead  int `json:"ahead"`
	Behind int `json:"behind"`
	// NewBranch is true when the remote branch does not exist yet.
	NewBranch bool `json:"newBranch"`
	// SetUpstream is true when the push carries -u (asked for, or added
	// because the branch has no remote counterpart).
	SetUpstream bool `json:"setUpstream"`
	// Tracked is true when the branch's configured upstream is <remote>/<branch>.
	Tracked bool `json:"tracked"`
	// Args are the git arguments of the push step ("-c", "core.sshCommand=…",
	// "push", "-u", "origin", branch). Always set, also when Denied.
	Args []string `json:"args"`
	// Command is Args rendered for bash, as the rules engine saw it.
	Command string `json:"command"`
	// Verdict holds every rule finding (identity, remote, protected branch, signing).
	Verdict verdict.Verdict `json:"-"`
	// Findings is Verdict.Findings, never nil (for JSON).
	Findings []verdict.Finding `json:"findings"`
	// Decision is the verdict's decision: allow, warn, ask or deny.
	Decision verdict.Decision `json:"decision"`
	// Denied is true when a rule denies the push; no write step was recorded.
	Denied bool `json:"denied"`
	// UpToDate is true when there is nothing to push; no write step was recorded.
	UpToDate bool `json:"upToDate"`
	// Pushed is true when the push write step was recorded in the plan.
	Pushed bool `json:"pushed"`
	// SigningChecked is true when the commits to push were checked for signatures.
	SigningChecked bool `json:"signingChecked"`
	// Commits are the commits the push would send (only when SigningChecked).
	Commits []sign.Commit `json:"commits,omitempty"`
	// Unsigned are the commits among Commits without a good signature.
	Unsigned []sign.Commit `json:"unsigned,omitempty"`
}

// ErrDetachedHead is returned by PlanPush when HEAD is not on a branch.
var ErrDetachedHead = errors.New("HEAD is detached: check out a branch before pushing")

// PlanPush plans `git push [-u] <remote> <branch>` on p (the checked-out
// branch unless PushOptions.Branch names one). It is the push step of
// `mygit push` and of `mygit pr create` (same Planner, before the gh step).
//
// Reads recorded on p (they run immediately): the upstream and ahead/behind
// state of refs/heads/<branch>, and, when the profile signs commits and ID006 is enabled, one
// `git log` over the commits to push (the equivalent of `mygit sign
// verify`). The write step is the push, pinned with
// `-c core.sshCommand=ssh -i <key> -o IdentitiesOnly=yes` when the profile
// uses the ssh transport with an ssh key (nothing is pinned for transport
// gh); the key is always double-quoted, and the pin decision looks at the
// remote's push URL. The configured upstream is only trusted when it is
// <remote>/<branch>; a branch with no remote counterpart is pushed with -u. The planned command is run through the rules engine (ID003, ID004,
// BR002) and every passed or failed rule becomes a plan check; unsigned
// commits are ID006 findings. The push is never forced.
//
// No write step is recorded when a rule denies the push (PushPlan.Denied)
// or when there is nothing to push (PushPlan.UpToDate). Callers should stop
// on Denied. An error is returned for a missing repository, config, remote
// or commit, and for a detached HEAD (ErrDetachedHead).
func PlanPush(ctx context.Context, p *Planner, o PushOptions) (*PushPlan, error) {
	e := p.Env
	if err := e.RequireRepo(); err != nil {
		return nil, err
	}
	cfg, err := e.Require()
	if err != nil {
		return nil, err
	}
	remote := o.Remote
	if remote == "" && cfg.Workflow != nil {
		remote = cfg.Workflow.Remote
	}
	if remote == "" {
		remote = "origin"
	}
	branch := o.Branch
	if branch == "" {
		if branch, err = e.Git.CurrentBranch(ctx); err != nil {
			return nil, err
		}
		if branch == "" {
			return nil, ErrDetachedHead
		}
	}
	tip, err := e.Git.Exec(ctx, "rev-parse", "-q", "--verify", "refs/heads/"+branch+"^{commit}")
	if err != nil {
		return nil, err
	}
	if !tip.OK() || strings.TrimSpace(tip.Stdout) == "" {
		return nil, fmt.Errorf("branch %s has no commits yet: nothing to push", branch)
	}
	url, err := e.Git.RemoteURL(ctx, remote)
	if err != nil || strings.TrimSpace(url) == "" {
		return nil, fmt.Errorf("remote %q is not configured (add it with: git remote add %s <url>)", remote, remote)
	}
	// The push URL decides whether an ssh key is pinned (pushurl may differ).
	pushURL := url
	if u, err := e.Git.Query(ctx, "remote", "get-url", "--push", remote); err == nil && u != "" {
		pushURL = u
	}
	res := &PushPlan{Branch: branch, Remote: remote}

	if err := pushTrack(ctx, p, res); err != nil {
		return nil, err
	}
	res.SetUpstream = o.Upstream || res.NewBranch
	if res.NewBranch && !o.Upstream {
		p.Note("%s has no remote counterpart: adding -u so it tracks %s/%s", branch, remote, branch)
	}

	// The planned command, pinned like the profile requires.
	res.Args = pushArgs(cfg.Profile, pushURL, e.Home, res.SetUpstream, remote, branch)
	res.Command = "git " + joinQuoted(res.Args)
	pol := cfg.Policy
	res.Verdict = engine.Evaluate(
		engine.Request{Command: res.Command, Dialect: shell.Bash, Cwd: e.RepoRoot}, pol, e.EngineDeps())

	if !o.SkipSigning {
		if err := pushSigning(ctx, p, cfg.Profile, pol, remote, res); err != nil {
			return nil, err
		}
	}
	pushChecks(p, pol, res)

	res.Findings = res.Verdict.Findings
	if res.Findings == nil {
		res.Findings = []verdict.Finding{}
	}
	res.Decision = res.Verdict.Decision()
	res.Denied = res.Decision == verdict.DecDeny

	remoteKnown := res.Upstream != ""
	res.UpToDate = remoteKnown && res.Ahead == 0 && (!res.SetUpstream || res.Tracked)
	if res.Behind > 0 {
		p.Note("%s is %d commit(s) behind %s: git rejects a push of a diverged branch as non-fast-forward. "+
			"mygit never forces; update the branch first (rebase or merge)", branch, res.Behind, res.Upstream)
	}
	switch {
	case res.Denied:
		p.Note("push denied by the rules above; nothing was planned")
	case res.UpToDate:
		p.Note("%s has nothing new for %s; nothing to push", branch, res.Upstream)
	default:
		p.Git("git push "+pushTarget(res.SetUpstream, remote, branch), res.Args...)
		res.Pushed = true
	}
	return res, nil
}

// pushTarget is "[-u] remote branch" for descriptions.
func pushTarget(upstream bool, remote, branch string) string {
	if upstream {
		return "-u " + remote + " " + branch
	}
	return remote + " " + branch
}

// pushArgs returns the git arguments of the push step. home expands a
// leading ~ in the ssh key, which sits in double quotes (sh would not).
func pushArgs(p profile.Profile, remoteURL, home string, upstream bool, remote, branch string) []string {
	var args []string
	if p.GitHub.Transport != profile.TransportGH && p.GitHub.SSHKey != "" && !rules.IsLocalURL(remoteURL) {
		if kind, _, _ := rules.ParseRemote(remoteURL); kind == "ssh" {
			args = append(args, "-c", "core.sshCommand="+pushSSHCommand(p.GitHub.SSHKey, home))
		}
	}
	args = append(args, "push")
	if upstream {
		args = append(args, "-u")
	}
	return append(args, remote, branch)
}

// pushSSHCommand is profile.SSHCommand with the key always double-quoted
// (Windows paths carry backslashes and spaces), a leading ~ expanded.
func pushSSHCommand(key, home string) string {
	if home != "" && (key == "~" || strings.HasPrefix(key, "~/") || strings.HasPrefix(key, `~\`)) {
		key = home + key[1:]
	}
	key = strings.NewReplacer(`"`, `\"`, "$", `\$`, "`", "\\`").Replace(key)
	return `ssh -i "` + key + `" -o IdentitiesOnly=yes`
}

func joinQuoted(args []string) string {
	out := make([]string, len(args))
	for i, a := range args {
		out[i] = shellfmt.Quote(shellfmt.Bash, a)
	}
	return strings.Join(out, " ")
}

// pushTrack records the upstream and ahead/behind reads and fills res. Only
// an upstream equal to <remote>/<branch> is trusted; another one (a branch
// cut from origin/<base> tracks the base) is ignored with a note.
func pushTrack(ctx context.Context, p *Planner, res *PushPlan) error {
	e := p.Env
	ref := "refs/heads/" + res.Branch
	want := res.Remote + "/" + res.Branch
	up, err := p.Read(ctx, e.Git.Cmd("rev-parse", "--abbrev-ref", "--symbolic-full-name", res.Branch+"@{upstream}"),
		"upstream of "+res.Branch, func(r execx.Result) string {
			if r.OK() && strings.TrimSpace(r.Stdout) != "" {
				return strings.TrimSpace(r.Stdout)
			}
			return "none configured"
		})
	if err != nil {
		return err
	}
	if configured := strings.TrimSpace(up.Stdout); up.OK() && configured != "" {
		if configured == want {
			res.Upstream, res.Tracked = want, true
		} else {
			p.Note("%s tracks %s, not %s: comparing against %s instead", res.Branch, configured, want, want)
		}
	}
	if res.Upstream == "" {
		// Not tracking <remote>/<branch>: fall back to the same-named remote branch.
		r, err := e.Git.Exec(ctx, "rev-parse", "--verify", "-q", "refs/remotes/"+want)
		if err != nil {
			return err
		}
		if r.OK() {
			res.Upstream = want
		}
	}
	if res.Upstream == "" {
		res.NewBranch = true
		p.Note("%s/%s does not exist yet: this push creates it", res.Remote, res.Branch)
		return nil
	}
	r, err := p.Read(ctx, e.Git.Cmd("rev-list", "--left-right", "--count", res.Upstream+"..."+ref),
		"ahead/behind vs "+res.Upstream, func(r execx.Result) string {
			res.Behind, res.Ahead = parseLeftRight(r.Stdout)
			return fmt.Sprintf("↑%d ahead · ↓%d behind", res.Ahead, res.Behind)
		})
	if err != nil {
		return err
	}
	if !r.OK() {
		return fmt.Errorf("ahead/behind vs %s: %s", res.Upstream, strings.TrimSpace(r.Stderr))
	}
	return nil
}

// parseLeftRight parses "<behind>\t<ahead>" from rev-list --left-right --count upstream...HEAD.
func parseLeftRight(out string) (behind, ahead int) {
	f := strings.Fields(out)
	if len(f) == 2 {
		behind, _ = strconv.Atoi(f[0])
		ahead, _ = strconv.Atoi(f[1])
	}
	return behind, ahead
}

// pushSigning verifies the signatures of the commits the push would send
// when the profile signs commits and ID006 is enabled; unsigned commits
// become ID006 findings in res.Verdict.
func pushSigning(ctx context.Context, p *Planner, prof profile.Profile, pol *policy.Policy, remote string, res *PushPlan) error {
	var r policy.Rule
	if id := pol.Contract(policy.Identity); id != nil {
		r = id.Rule("ID006")
	}
	format := prof.Git.Signing.Format
	if r.Severity == verdict.Off || r.ID == "" || format == "" || format == profile.SignNone {
		return nil
	}
	args := append(sign.LogArgs(""), "refs/heads/"+res.Branch, "--not", "--remotes="+remote)
	var commits []sign.Commit
	if _, err := p.Read(ctx, p.Env.Git.Cmd(args...), "mygit sign verify (commits to push)", func(x execx.Result) string {
		commits = sign.ParseLog(x.Stdout)
		return fmt.Sprintf("%d/%d signed", len(commits)-len(sign.Problems(commits)), len(commits))
	}); err != nil {
		return err
	}
	res.SigningChecked, res.Commits, res.Unsigned = true, commits, sign.Problems(commits)
	for _, c := range res.Unsigned {
		sha := c.SHA
		if len(sha) > 7 {
			sha = sha[:7]
		}
		res.Verdict.Addf("ID006", r.Severity, "commit %s is not signed (%s): %s (profile signing format '%s')",
			sha, sign.Describe(c.Status), c.Subject, format)
	}
	return nil
}

// pushChecks records one plan check per relevant enabled rule: failed
// when the verdict has a finding for it, passed otherwise.
func pushChecks(p *Planner, pol *policy.Policy, res *PushPlan) {
	byRule := map[string][]verdict.Finding{}
	for _, f := range res.Verdict.Findings {
		byRule[f.RuleID] = append(byRule[f.RuleID], f)
	}
	seen := map[string]bool{}
	for _, f := range res.Verdict.Findings {
		if seen[f.RuleID] {
			continue
		}
		seen[f.RuleID] = true
		for _, g := range byRule[f.RuleID] {
			p.Check(g.RuleID, false, g.Severity, g.Message)
		}
	}
	pass := []struct{ contract, id, msg string }{
		{policy.Identity, "ID003", "credential pinned"},
		{policy.Identity, "ID004", "remote allowed"},
		{policy.Branching, "BR002", "not a protected branch"},
	}
	if res.SigningChecked {
		pass = append(pass, struct{ contract, id, msg string }{policy.Identity, "ID006", "signed"})
	}
	for _, c := range pass {
		if seen[c.id] {
			continue
		}
		con := pol.Contract(c.contract)
		if con == nil || con.Rule(c.id).Severity == verdict.Off {
			continue
		}
		p.Check(c.id, true, verdict.Off, c.msg)
	}
}
