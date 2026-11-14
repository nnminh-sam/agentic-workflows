package cli

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/agent"
	mgapp "github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
)

func init() { register(160, newGuardCmd) }

func newGuardCmd(a *App) *cobra.Command {
	var agentName string
	var sessionStart bool
	cmd := &cobra.Command{
		Use:   "guard --agent <" + strings.Join(agent.Names(), "|") + ">",
		Short: "check the command an AI agent wants to run (reads the agent's hook payload on stdin)",
		Long: "guard is called by an agent's pre-command hook, not by you. It reads the hook payload on stdin,\n" +
			"checks the command against the active rules and answers in the agent's own format.\n" +
			"An invalid config makes it ask for every git/gh command; it never silently allows.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ad, err := agent.New(agentName)
			if err != nil {
				return err
			}
			raw, err := io.ReadAll(a.In)
			if err != nil {
				return fmt.Errorf("read stdin: %w", err)
			}
			kind := agent.PreCommand
			if sessionStart {
				kind = agent.SessionStart
			}
			res := guardRun(a, ad, raw, kind)
			out, code := ad.Encode(res)
			if _, err := a.Out.Write(out); err != nil {
				return err
			}
			if code != 0 {
				return Exit(code)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&agentName, "agent", "", "agent protocol: "+strings.Join(agent.Names(), "|"))
	cmd.Flags().BoolVar(&sessionStart, "session-start", false, "answer the agent's session-start hook (identity briefing)")
	_ = cmd.MarkFlagRequired("agent")
	return cmd
}

// guardRun decodes, checks and returns the result. It never fails: any
// problem becomes a fail-safe ask (pre-command) or a note (session start).
func guardRun(a *App, ad agent.Adapter, raw []byte, kind agent.Kind) (res agent.Result) {
	defer func() {
		if r := recover(); r != nil {
			res = guardFailure(kind, fmt.Errorf("internal error: %v", r))
		}
	}()
	req, derr := ad.Decode(raw, kind)
	if kind == agent.SessionStart {
		return guardSession(a, req) // like session_start.py, bad input means {}
	}
	if derr != nil {
		return agent.FailSafe(derr.Error())
	}
	if req.Skip || strings.TrimSpace(req.Command) == "" {
		return agent.Allow(kind)
	}
	cwd, err := guardCwd(a, req.Cwd)
	if err != nil {
		return agent.FailSafe(err.Error())
	}
	cfg, err := guardResolve(a, cwd)
	if err != nil {
		if agent.MayInvolveGit(req.Command) {
			return agent.FailSafe(err.Error())
		}
		return agent.Allow(kind)
	}
	v := engine.Evaluate(engine.Request{Command: req.Command, Dialect: req.Dialect, Cwd: cwd}, cfg.Policy, guardEngineDeps(a))
	v.Extend(agent.CheckApply(req, cfg.Execution.AgentApply))
	return agent.Result{Kind: kind, Verdict: v}
}

func guardFailure(kind agent.Kind, err error) agent.Result {
	if kind == agent.SessionStart {
		return agent.Result{Kind: kind, Context: []string{"mygit: session check skipped (" + err.Error() + ")"}}
	}
	return agent.FailSafe(err.Error())
}

func guardSession(a *App, req agent.Request) agent.Result {
	res := agent.Result{Kind: agent.SessionStart}
	cwd, err := guardCwd(a, req.Cwd)
	if err != nil {
		return guardFailure(agent.SessionStart, err)
	}
	cfg, err := guardResolve(a, cwd)
	if err != nil {
		res.Context = agent.InvalidConfigContext(err)
		return res
	}
	notes := agent.SessionNotes(agent.SessionInput{
		Cwd: cwd, Policy: cfg.Policy, Repo: rules.NewGitRepo(a.Runner, cwd), Getenv: a.Getenv,
	})
	res.Context = agent.SessionContext(notes)
	if cfg.Profile.Git.Name == "" && len(cfg.Profile.Git.Email) == 0 && cfg.Profile.GitHub.User == "" {
		res.Context = append(res.Context, "mygit: no profile is active, so the identity rules only check that values are set. "+
			"Set one up with: mygit profile add <name> --from-current (ask the user first; it records their identity)")
	}
	return res
}

// guardCwd returns the payload's cwd, else -C / the process directory.
func guardCwd(a *App, cwd string) (string, error) {
	if cwd != "" {
		return cwd, nil
	}
	return a.Cwd()
}

// guardEngineDeps are the engine's side effects for real runs.
func guardEngineDeps(a *App) engine.Deps {
	return engine.Deps{
		Repo:     func(dir string) rules.Repo { return rules.NewGitRepo(a.Runner, dir) },
		ReadFile: os.ReadFile,
		Env:      a.Getenv,
	}
}

// --------------------------------------------------------------- shared plumbing
// The helpers below adapt *App to internal/app for the guard, hook, hooks,
// agent and lint commands. TODO(orchestrator): dedupe with the shared
// helpers in internal/cli once they land.

// guardDeps adapts the App to app.Deps for cwd.
func guardDeps(a *App, cwd string) mgapp.Deps {
	home, _ := a.Home()
	return mgapp.Deps{
		Runner: a.Runner, Getenv: a.Getenv, Cwd: cwd, Home: home, GOOS: a.GOOS,
		Flags: config.Flags{
			Profile: a.Opts.Profile, Ruleset: a.Opts.Ruleset, Workflow: a.Opts.Workflow,
			Rules: a.Opts.Rules, Mode: a.Opts.Mode, Shell: a.Opts.Shell,
		},
	}
}

// guardEnv loads the environment for cwd ("" = -C / process cwd).
func guardEnv(a *App, cwd string) (*mgapp.Env, error) {
	if cwd == "" {
		var err error
		if cwd, err = a.Cwd(); err != nil {
			return nil, err
		}
	}
	return mgapp.Load(a.Context(), guardDeps(a, cwd))
}

// guardResolve resolves the config for cwd; an invalid config is an error.
func guardResolve(a *App, cwd string) (*config.Resolved, error) {
	env, err := guardEnv(a, cwd)
	if err != nil {
		return nil, err
	}
	return env.Require()
}

// guardRunPlan runs a built plan the way every mutating command does
// (dry run by default; --yes, a y at the prompt, or --apply <id> applies).
func guardRunPlan(ctx context.Context, a *App, env *mgapp.Env, p *plan.Plan, extra map[string]any) error {
	o := mgapp.RunOptions{
		Mode: a.Opts.Mode, Shell: a.Opts.Shell, ApplyID: a.Opts.Apply, Yes: a.Opts.Yes, JSON: a.Opts.JSON,
		Interactive: a.IsTerminal != nil && a.IsTerminal(), TempDir: os.TempDir(), Extra: extra,
	}
	if env.Config != nil {
		if o.Mode == "" {
			o.Mode = env.Config.Execution.Mode
		}
		if o.Shell == "" {
			o.Shell = env.Config.Execution.Shell
		}
	}
	x := &mgapp.Executor{Env: env, In: a.In, Out: a.Out, Err: a.Err}
	_, err := x.Run(ctx, p, o)
	return err
}
