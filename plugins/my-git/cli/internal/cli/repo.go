package cli

import (
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/repoops"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(65, newRepoCmd) }

func newRepoCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "repo", Short: "create, protect and bootstrap a repository (plans)"}
	cmd.AddCommand(repoCreateCmd(a, s), repoProtectCmd(a, s), repoBootstrapCmd(a, s))
	return cmd
}

// repoLine renders c as a bash command line for the rules engine.
func repoLine(c execx.Cmd) string {
	parts := []string{c.Name}
	for _, arg := range c.Args {
		parts = append(parts, shellfmt.Quote(shellfmt.Bash, arg))
	}
	return strings.Join(parts, " ")
}

// repoEvaluate runs the rules engine (identity, branching, commit message,
// gh policy incl. the gh account check ID005) on the commands.
func repoEvaluate(a *App, e *app.Env, cmds ...execx.Cmd) verdict.Verdict {
	var v verdict.Verdict
	if e.Config == nil || e.Config.Policy == nil {
		return v
	}
	dir := e.RepoRoot
	if dir == "" {
		dir = e.Cwd
	}
	deps := engine.Deps{
		Repo:     func(d string) rules.Repo { return rules.NewGitRepo(a.Runner, d) },
		ReadFile: os.ReadFile,
		Env:      a.Getenv,
	}
	for _, c := range cmds {
		v.Extend(engine.Evaluate(engine.Request{Command: repoLine(c), Dialect: shell.Bash, Cwd: dir}, e.Config.Policy, deps))
	}
	return v
}

// repoDeny prints the findings and returns the deny exit.
func repoDeny(a *App, v verdict.Verdict) error {
	if a.Opts.JSON {
		if err := emitJSON(a.Out, map[string]any{"decision": v.Decision(), "findings": v.Findings, "reason": v.Reason()}); err != nil {
			return err
		}
	} else {
		printFindings(a.Out, v)
	}
	return Exit(2)
}

// repoChecks records the findings on the plan (an "ok" line when empty).
func repoChecks(p *app.Planner, v verdict.Verdict) {
	if len(v.Findings) == 0 {
		p.Check("rules", true, verdict.Off, "gh/git policy")
		return
	}
	for _, f := range v.Findings {
		p.Check(f.RuleID, false, f.Severity, f.Message)
	}
}

// repoGate runs the engine on cmds, refuses on deny and otherwise records
// the findings as plan checks (ask findings are confirmed when applying).
func repoGate(a *App, p *app.Planner, cmds ...execx.Cmd) error {
	v := repoEvaluate(a, p.Env, cmds...)
	if v.Decision() == verdict.DecDeny {
		return repoDeny(a, v)
	}
	repoChecks(p, v)
	return nil
}

// ---------------------------------------------------------------- create

func repoCreateCmd(a *App, s *session) *cobra.Command {
	var private, public bool
	cmd := &cobra.Command{
		Use:   "create <owner/name> --private|--public",
		Short: "create the GitHub repository from this one and push it (plan)",
		Long: "create runs gh repo create <owner/name> --private|--public --source . --push. The profile is picked by the " +
			"owner (config \"match\" with remoteOwner), unless --profile is given; the active gh account must be that profile's github.user (ID005).",
		Example: "  mygit repo create acme/api --private",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if private == public {
				return fmt.Errorf("choose exactly one of --private or --public")
			}
			owner, _, err := repoops.ParseSlug(args[0])
			if err != nil {
				return err
			}
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			if err := e.RequireRepo(); err != nil {
				return fmt.Errorf("%w: run it in the local repository to publish", err)
			}
			reason := c.ProfileReason
			if a.Opts.Profile == "" {
				dir, _ := a.Cwd()
				pick := func(m profile.Match) string { return m.Profile }
				if name, why := profile.FindMatch(c.File.Match, profile.Where{RemoteOwner: owner, Dir: dir, Home: e.Home, GOOS: e.GOOS}, pick); name != "" {
					reason = why
					if name != c.ProfileName {
						deps := depsOf(a)
						deps.Flags.Profile = name
						if e, err = app.Load(cmd.Context(), deps); err != nil {
							return err
						}
						if c, err = e.Require(); err != nil {
							return err
						}
					}
				}
			}
			p := e.NewPlanner(planArgv(cmd, args))
			remote := "origin"
			if c.Workflow != nil && c.Workflow.Remote != "" {
				remote = c.Workflow.Remote
			}
			if urls, _ := e.Git.RemoteURL(cmd.Context(), remote); urls != "" {
				return fmt.Errorf("remote %s already exists (%s); nothing to create", remote, urls)
			}
			if sha, _ := e.Git.HeadSHA(cmd.Context()); sha == "" {
				return fmt.Errorf("the repository has no commits to push yet: run mygit repo bootstrap first")
			}
			vis := "--private"
			if public {
				vis = "--public"
			}
			ghArgs := []string{"repo", "create", args[0], vis, "--source", ".", "--push"}
			if remote != "origin" {
				ghArgs = append(ghArgs, "--remote", remote)
			}
			if err := repoGate(a, p, e.Gh.Cmd(ghArgs...)); err != nil {
				return err
			}
			if allowed, known := app.OwnerCheck(c.Profile, owner); known && !allowed {
				p.Check("owner", false, verdict.Warn, fmt.Sprintf("%s is not the profile's github.user or orgs", owner))
			}
			if c.ProfileName != "" {
				p.Note("profile %s (%s)", c.ProfileName, reason)
			}
			p.Gh("create "+args[0]+" ("+strings.TrimPrefix(vis, "--")+") and push", ghArgs...)
			return s.runExtra(cmd, p, nil, map[string]any{
				"repo": args[0], "visibility": strings.TrimPrefix(vis, "--"),
				"profile": c.ProfileName, "profileReason": reason,
			})
		},
	}
	cmd.Flags().BoolVar(&private, "private", false, "create a private repository")
	cmd.Flags().BoolVar(&public, "public", false, "create a public repository")
	s.addOutFlag(cmd)
	return cmd
}

// ---------------------------------------------------------------- protect

func repoProtectCmd(a *App, s *session) *cobra.Command {
	var approvals int
	var requireSigned bool
	var repoFlag string
	cmd := &cobra.Command{
		Use:   "protect [--approvals N] [--require-signed]",
		Short: "apply the workflow's branch protection as a GitHub ruleset (plan)",
		Long: "protect builds a ruleset from the active workflow template's protected branches (PR required, no force-push, " +
			"no deletion, signed commits with --require-signed), creates it or updates the mygit ruleset of the same name, " +
			"sets the GitHub default branch and turns on deleting merged head branches.",
		Example: "  mygit repo protect --approvals 1 --require-signed",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			c, err := e.Require()
			if err != nil {
				return err
			}
			if c.Workflow == nil {
				return fmt.Errorf("no workflow template is active")
			}
			ctx := cmd.Context()
			slug := repoFlag
			if slug == "" {
				if err := e.RequireRepo(); err != nil {
					return fmt.Errorf("%w (or pass --repo owner/name)", err)
				}
				remote := c.Workflow.Remote
				u, err := e.Git.RemoteURL(ctx, remote)
				if err != nil || u == "" {
					return fmt.Errorf("no %q remote: run mygit repo create first, or pass --repo owner/name", remote)
				}
				ref, err := parseRemoteSlug(u)
				if err != nil {
					return err
				}
				slug = ref
			}
			if _, _, err := repoops.ParseSlug(slug); err != nil {
				return err
			}
			opt := repoops.RulesetOptions{RequireSigned: requireSigned}
			switch {
			case cmd.Flags().Changed("approvals"):
				opt.Approvals = &approvals
			case c.File.GitHub.Ruleset.Approvals != nil:
				opt.Approvals = c.File.GitHub.Ruleset.Approvals
			}
			rs, err := repoops.BuildRuleset(c.Workflow, opt)
			if err != nil {
				return err
			}
			// ID005 before the first gh call.
			listCmd := e.Gh.Cmd("api", "repos/"+slug+"/rulesets")
			if v := repoEvaluate(a, e, listCmd); v.Decision() == verdict.DecDeny {
				return repoDeny(a, v)
			}
			var existing []repoops.Existing
			res, err := p.Read(ctx, listCmd, "list rulesets", func(r execx.Result) string {
				if !r.OK() {
					return "unavailable"
				}
				l, _ := repoops.ParseExisting(r.Stdout)
				return fmt.Sprintf("%d existing", len(l))
			})
			if err != nil {
				return err
			}
			if res.OK() {
				if existing, err = repoops.ParseExisting(res.Stdout); err != nil {
					return err
				}
			} else {
				p.Note("could not list rulesets (%s); planning a new one. Rulesets on private repositories need GitHub Pro/Team/Enterprise",
					strings.TrimSpace(firstLine(res.Stderr)))
			}
			id, found := repoops.FindByName(existing, rs.Name)

			// default branch: only when it already exists on GitHub
			def := repoops.DefaultBranch(c.Workflow)
			defOnRemote := false
			bres, err := p.Read(ctx, e.Gh.Cmd("api", "repos/"+slug+"/branches/"+def, "--silent"), "default branch "+def+" on GitHub",
				func(r execx.Result) string {
					if r.OK() {
						return "exists"
					}
					return "missing"
				})
			if err != nil {
				return err
			}
			defOnRemote = bres.OK()

			method, endpoint := "POST", "repos/"+slug+"/rulesets"
			desc := "create ruleset " + rs.Name
			if found {
				method, endpoint = "PUT", endpoint+"/"+strconv.FormatInt(id, 10)
				desc = "update ruleset " + rs.Name + " (id " + strconv.FormatInt(id, 10) + ")"
			}
			bodyPath := p.File("ruleset.json", rs.JSON())
			rulesetCmd := e.Gh.Cmd("api", "-X", method, endpoint, "--input", bodyPath)
			patch := []string{"api", "-X", "PATCH", "repos/" + slug}
			if defOnRemote {
				patch = append(patch, "-f", "default_branch="+def)
			} else {
				p.Note("branch %s is not on GitHub yet: push it, then re-run to set it as the default branch", def)
			}
			patch = append(patch, "-F", "delete_branch_on_merge=true")
			patchCmd := e.Gh.Cmd(patch...)
			if err := repoGate(a, p, rulesetCmd, patchCmd); err != nil {
				return err
			}
			p.Gh(desc, rulesetCmd.Args...)
			patchDesc := "delete merged head branches"
			if defOnRemote {
				patchDesc = "default branch " + def + ", delete merged head branches"
			}
			p.Gh(patchDesc, patchCmd.Args...)
			var approvalsOut any
			for _, r := range rs.Rules {
				if pp, ok := r.Parameters.(repoops.PullRequestParams); ok {
					approvalsOut = pp.Approvals
				}
			}
			p.Note("ruleset protects %s", strings.Join(c.Workflow.Protected, ", "))
			return s.runExtra(cmd, p, nil, map[string]any{
				"repo": slug, "ruleset": rs.Name, "method": method, "rulesetId": id,
				"approvals": approvalsOut, "requireSigned": rs.Has("required_signatures"),
				"defaultBranch": def, "protected": c.Workflow.Protected,
			})
		},
	}
	cmd.Flags().IntVar(&approvals, "approvals", 0, "required approving reviews (default: config github.ruleset.approvals, else the workflow template)")
	cmd.Flags().BoolVar(&requireSigned, "require-signed", false, "also require signed commits (required_signatures)")
	cmd.Flags().StringVar(&repoFlag, "repo", "", "owner/name (default: the workflow remote of this repository)")
	s.addOutFlag(cmd)
	return cmd
}

func firstLine(s string) string {
	l, _, _ := strings.Cut(strings.TrimSpace(s), "\n")
	return l
}

// parseRemoteSlug turns a remote URL into owner/name.
func parseRemoteSlug(u string) (string, error) {
	ref, err := gitx.ParseRemoteURL(u)
	if err != nil {
		return "", err
	}
	return ref.String(), nil
}

// ---------------------------------------------------------------- bootstrap

func repoBootstrapCmd(a *App, s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "bootstrap",
		Short: "first commit on the production branch and the staging branch (plan)",
		Long: "bootstrap prepares a fresh repository: initial commit on the workflow's production branch (signed, identity pinned; " +
			"empty when nothing is staged) and the staging branch created from it. Steps that already hold are skipped; a repository " +
			"whose production branch already has other history is refused.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			c, err := e.Require()
			if err != nil {
				return err
			}
			if c.Workflow == nil {
				return fmt.Errorf("no workflow template is active")
			}
			ctx := cmd.Context()
			f := repoops.Facts{InRepo: e.InRepo(), Production: c.Workflow.Production, Staging: c.Workflow.Staging}
			if f.InRepo {
				g := e.Git
				if f.CurrentBranch, err = g.CurrentBranch(ctx); err != nil {
					return err
				}
				sha, err := g.HeadSHA(ctx)
				if err != nil {
					return err
				}
				f.HasCommits = sha != ""
				if f.ProductionRef, err = g.BranchExists(ctx, f.Production); err != nil {
					return err
				}
				if f.StagingRef, err = g.BranchExists(ctx, f.Staging); err != nil {
					return err
				}
				if f.ProductionRef {
					ref := "refs/heads/" + f.Production
					if n, err := g.Query(ctx, "rev-list", "--count", ref); err == nil {
						f.ProductionLen, _ = strconv.Atoi(n)
					}
					if root, err := g.Query(ctx, "log", "--max-parents=0", "--format=%s", ref); err == nil {
						f.ProductionRoot = firstLine(root)
					}
				}
				entries, err := g.StatusPorcelain(ctx)
				if err != nil {
					return err
				}
				for _, en := range entries {
					if en.Staged() {
						f.Staged = true
						break
					}
				}
			}
			steps, done, err := repoops.Plan(f)
			if err != nil {
				return err
			}
			if done {
				p.Note("%s and %s already exist: nothing to bootstrap", f.Production, f.Staging)
			}
			kv, sign := repoops.IdentityConfig(c.Profile)
			var checked []execx.Cmd
			type planned struct {
				desc string
				args []string
			}
			var plannedSteps []planned
			for _, st := range steps {
				args := st.Args
				if st.Commit {
					sub := []string{"commit"}
					if sign {
						sub = append(sub, "-S")
					}
					args = repoops.GitArgs(kv, append(sub, st.Args[1:]...)...)
					checked = append(checked, e.Git.Cmd(args...))
				}
				plannedSteps = append(plannedSteps, planned{st.Description, args})
			}
			if len(checked) > 0 {
				if err := repoGate(a, p, checked...); err != nil {
					return err
				}
			}
			names := make([]string, 0, len(plannedSteps))
			for _, st := range plannedSteps {
				p.Git(st.desc, st.args...)
				names = append(names, st.desc)
			}
			if len(plannedSteps) > 0 {
				p.Note("work branches: git switch -c %s %s", strings.ReplaceAll(c.Workflow.Template.Work.Pattern, "{type}/{name}", "feat/<name>"), f.Staging)
			}
			return s.runExtra(cmd, p, func(w io.Writer) {
				fmt.Fprintf(w, "bootstrapped %s and %s\n", f.Production, f.Staging)
			}, map[string]any{"production": f.Production, "staging": f.Staging, "steps": names, "alreadyDone": done})
		},
	}
	s.addOutFlag(cmd)
	return cmd
}
