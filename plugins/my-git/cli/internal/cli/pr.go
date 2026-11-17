package cli

import (
	"fmt"
	"io"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/pr"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(64, newPRCmd) }

// prFlags are the flags of the commands that open a pull request.
type prFlags struct {
	draft    bool
	to       string
	title    string
	bodyFile string
	version  string
	sections []string
	noChecks bool
	// allowFailing turns failing test/lint from a blocking finding into a note.
	allowFailing bool
}

func (f *prFlags) add(cmd *cobra.Command, withTo bool) {
	fl := cmd.Flags()
	fl.BoolVar(&f.draft, "draft", false, "open the pull request as a draft")
	if withTo {
		fl.StringVar(&f.to, "to", "", "target branch (default: what the workflow says; BR005 checks it)")
	}
	fl.StringVar(&f.title, "title", "", "PR title (default: derived from the commits or branch)")
	fl.StringVar(&f.bodyFile, "body-file", "", "use this markdown file as the body instead of the template ('-' for stdin)")
	fl.StringArrayVar(&f.sections, "section", nil, `fill a body section: "Explanation=why we did this" (repeatable)`)
	fl.BoolVar(&f.noChecks, "no-checks", false, "skip the configured test and lint commands (the body says they were not run)")
	fl.BoolVar(&f.allowFailing, "allow-failing-checks", false, "open the PR although test or lint failed (the body reports it)")
	fl.StringVar(&f.version, "version", "", "release version for the title and summary (default: today's date)")
}

func newPRCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{
		Use:   "pr",
		Short: "open and check pull requests (plan)",
		Long: "pr create and pr release run the sequence: read the branch range, run the configured test and lint (failing ones block unless\n" +
			"--allow-failing-checks; --no-checks skips them), check the gh account,\n" +
			"look for an open PR, render the title and body, validate them, then push and run gh pr create. Dry run stops after the checks\n" +
			"and prints the title, the body and the exact commands.",
	}
	cmd.AddCommand(
		prOpenCmd(a, s, "create", "open a pull request for the current branch",
			func(c *config.Resolved, cur string, f *prFlags, _ []string) (pr.Target, error) {
				return pr.ResolveCreate(c.Workflow, cur, f.to)
			}, cobra.NoArgs, true),
		prOpenCmd(a, s, "release", "open the release pull request (staging into production, from the workflow)",
			func(c *config.Resolved, _ string, _ *prFlags, _ []string) (pr.Target, error) {
				return pr.ResolveRelease(c.Workflow)
			}, cobra.NoArgs, false),
		prOpenCmd(a, s, "run <transition>", "open the pull request of a workflow transition",
			func(c *config.Resolved, cur string, f *prFlags, args []string) (pr.Target, error) {
				return pr.ResolveTransition(c.Workflow, args[0], cur, f.to)
			}, cobra.ExactArgs(1), true),
		prCheckCmd(a, s),
		prTemplateCmd(a, s),
	)
	return cmd
}

type prResolver func(c *config.Resolved, current string, f *prFlags, args []string) (pr.Target, error)

func prOpenCmd(a *App, s *session, use, short string, resolve prResolver, args cobra.PositionalArgs, withTo bool) *cobra.Command {
	f := &prFlags{}
	cmd := &cobra.Command{
		Use:   use,
		Short: short,
		Args:  args,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runPROpen(a, s, cmd, args, f, resolve)
		},
	}
	f.add(cmd, withTo)
	s.addOutFlag(cmd)
	return cmd
}

func runPROpen(a *App, s *session, cmd *cobra.Command, args []string, f *prFlags, resolve prResolver) error {
	p, e, err := s.planner(cmd, args)
	if err != nil {
		return err
	}
	if err := e.RequireRepo(); err != nil {
		return err
	}
	c, err := e.Require()
	if err != nil {
		return err
	}
	if c.Workflow == nil {
		return fmt.Errorf("no workflow template is active")
	}
	cur, _ := e.Git.CurrentBranch(cmd.Context())
	t, err := resolve(c, cur, f, args)
	if err != nil {
		return err
	}
	sections, err := prParseSections(f.sections)
	if err != nil {
		return err
	}
	in := pr.Input{Env: e, Config: c, Target: t, Current: cur, Draft: f.draft, Title: f.title,
		Sections: sections, Version: f.version, Now: a.Now(), NoChecks: f.noChecks, AllowFailingChecks: f.allowFailing}
	if f.bodyFile != "" {
		var b []byte
		if f.bodyFile == "-" {
			b, err = io.ReadAll(a.In)
		} else {
			// Relative to the repository (-C), read through the Env.
			path := f.bodyFile
			if !filepath.IsAbs(path) {
				path = filepath.Join(e.Cwd, path)
			}
			b, err = e.ReadFile(path)
		}
		if err != nil {
			return err
		}
		in.Body, in.HasBody = string(b), true
	}
	res, err := pr.Plan(cmd.Context(), p, in)
	if err != nil {
		return err
	}
	extra := map[string]any{
		"head": t.Head, "base": t.Base, "transition": t.Transition, "draft": f.draft,
		"findings": prFindings(res.Verdict),
	}
	if res.Title != "" {
		extra["title"], extra["body"] = res.Title, res.Body
		extra["commands"] = prNonEmpty(res.Push, res.Create)
		extra["signed"], extra["commits"] = res.Signed, res.Total
	}
	if res.Existing != "" {
		extra["existingPr"] = res.Existing
		if !a.Opts.JSON {
			a.Printf("PR already open: %s   %s\n", res.Existing, t)
		}
		return s.runExtra(cmd, p, nil, extra)
	}
	if res.Blocked() {
		return prBlocked(a, p, res, extra)
	}
	if !a.Opts.JSON {
		if len(res.Verdict.Findings) > 0 {
			printFindings(a.Out, res.Verdict)
		}
		prPrintDraft(a, res)
	}
	built, err := p.Build(cmd.Context())
	if err != nil {
		return err
	}
	var url string
	e2 := *e
	e2.Runner = pr.URLCapture{Next: e.Runner, URL: &url}
	return s.execute(cmd, &e2, built, func(w io.Writer) {
		if url != "" {
			fmt.Fprintf(w, "✓ %s   %s\n", url, t)
		}
	}, extra)
}

// prBlocked prints the plan and the findings that forbid applying it.
func prBlocked(a *App, p *app.Planner, res *pr.Result, extra map[string]any) error {
	built, err := p.Build(a.Context())
	if err != nil {
		return err
	}
	code := res.Verdict.Decision().ExitCode()
	if a.Opts.JSON {
		out := map[string]any{"status": "blocked", "decision": res.Verdict.Decision(), "plan": built, "extra": extra}
		if err := emitJSON(a.Out, out); err != nil {
			return err
		}
		return Exit(code)
	}
	fmt.Fprint(a.Out, plan.Text(built))
	printFindings(a.Out, res.Verdict)
	fmt.Fprintln(a.Out, "not opened: fix the findings above (or adjust a rule with --rule ID=severity)")
	return Exit(code)
}

// prPrintDraft shows the title and body the PR will get.
func prPrintDraft(a *App, res *pr.Result) {
	a.Printf("title  %s\n", res.Title)
	a.Printf("base   %s ← %s\n", res.Target.Base, res.Target.Head)
	a.Printf("body\n")
	for _, l := range strings.Split(strings.TrimRight(res.Body, "\n"), "\n") {
		a.Printf("  | %s\n", l)
	}
	fmt.Fprintln(a.Out)
}

func prParseSections(items []string) (map[string]string, error) {
	out := map[string]string{}
	for _, it := range items {
		name, text, ok := strings.Cut(it, "=")
		if !ok || strings.TrimSpace(name) == "" {
			return nil, fmt.Errorf("--section %q: want NAME=TEXT", it)
		}
		out[strings.TrimSpace(name)] = text
	}
	return out, nil
}

func prFindings(v verdict.Verdict) []verdict.Finding {
	if v.Findings == nil {
		return []verdict.Finding{}
	}
	return v.Findings
}

func prNonEmpty(items ...string) []string {
	out := []string{}
	for _, s := range items {
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

// ---------------------------------------------------------------- check

func prCheckCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "check [num]",
		Short: "check an open pull request against the rules (GH005, GH008, GH009, BR005); read-only",
		Long:  "reads gh pr view N --json title,body,baseRefName,headRefName (no num: the PR of the current branch). Exit: 0 ok or warnings, 1 ask, 2 deny.",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			if err := e.RequireRepo(); err != nil {
				return err
			}
			num := 0
			if len(args) == 1 {
				if num, err = strconv.Atoi(strings.TrimPrefix(args[0], "#")); err != nil || num <= 0 {
					return fmt.Errorf("pr number %q is not a positive integer", args[0])
				}
			}
			info, v, err := pr.Check(cmd.Context(), e, c, num)
			if err != nil {
				return err
			}
			if a.Opts.JSON {
				out := lintJSON{Decision: v.Decision(), Findings: prFindings(v)}
				if out.Decision != verdict.Allow {
					out.Reason = v.Reason()
				}
				if err := emitJSON(a.Out, map[string]any{"pr": info, "decision": out.Decision,
					"findings": out.Findings, "reason": out.Reason}); err != nil {
					return err
				}
				if code := out.Decision.ExitCode(); code != 0 {
					return Exit(code)
				}
				return nil
			}
			if info.Title != "" {
				a.Printf("%s   %s ← %s\n", info.Title, info.Base, info.Head)
			}
			return lintReport(a, v)
		},
	}
}

// ---------------------------------------------------------------- template

func prTemplateCmd(a *App, s *session) *cobra.Command {
	var kind string
	cmd := &cobra.Command{
		Use:   "template [--kind default|release]",
		Short: "print the empty PR body template (pr.template or the built-in one) with the configured sections",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			e, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			if c.Workflow == nil {
				return fmt.Errorf("no workflow template is active")
			}
			var t pr.Target
			switch kind {
			case tmpl.PRRelease:
				if t, err = pr.ResolveRelease(c.Workflow); err != nil {
					return err
				}
			case tmpl.PRDefault, "":
				cur := "<branch>"
				if e.InRepo() {
					if b, _ := e.Git.CurrentBranch(cmd.Context()); b != "" {
						cur = b
					}
				}
				t = pr.Target{Transition: "merge", Kind: tmpl.PRDefault, Template: tmpl.PRDefault, Head: cur, Base: c.Workflow.Staging}
			default:
				return fmt.Errorf("--kind %q: want default or release", kind)
			}
			names := pr.Sections(c, t)
			body, err := pr.RenderBody(e, c, t, names, nil, "")
			if err != nil {
				return err
			}
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"kind": t.Kind, "sections": names, "body": body})
			}
			_, err = io.WriteString(a.Out, body)
			return err
		},
	}
	cmd.Flags().StringVar(&kind, "kind", "default", "template kind: default|release")
	return cmd
}
