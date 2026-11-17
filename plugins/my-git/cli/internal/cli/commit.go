package cli

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func init() { register(61, newCommitCmd) }

// commitFlags are the flags of `mygit commit`.
type commitFlags struct {
	typ, scope, subject, body string
	breaking, all             bool
	trailers                  []string
}

func newCommitCmd(a *App) *cobra.Command {
	s := newSession(a)
	f := &commitFlags{}
	cmd := &cobra.Command{
		Use:   "commit [-t type] [-s scope] [-m subject] [--body text] [--breaking] [-a]",
		Short: "commit with a message built from the commit template, checked by the rules, signed per profile",
		Long: "commit fills the commit template (commit.template, else the built-in one) from the flags, checks the\n" +
			"message and the git invocation against the commit-message, identity, signing and branching rules,\n" +
			"and plans the commit. Without --apply or --yes it only shows the plan.\n" +
			"A missing --type or --message is asked for when stdin is a terminal.",
		Example: `  mygit commit -a -t feat -s auth -m "add login endpoint"
  mygit commit -t fix -m "handle empty input" --body "The parser crashed on an empty file." --apply 7f3a2c`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error { return commitRun(a, s, f, cmd, args) },
	}
	fl := cmd.Flags()
	fl.StringVarP(&f.typ, "type", "t", "", "commit type (feat, fix, …)")
	fl.StringVarP(&f.scope, "scope", "s", "", "optional scope")
	fl.StringVarP(&f.subject, "message", "m", "", "subject: imperative summary")
	fl.StringVar(&f.body, "body", "", "message body")
	fl.BoolVar(&f.breaking, "breaking", false, "mark a breaking change (adds '!')")
	fl.BoolVarP(&f.all, "all", "a", false, "stage everything first (git add -A)")
	fl.StringArrayVar(&f.trailers, "trailer", nil, "trailer line 'Key: value' (repeatable), e.g. 'Refs: #12'")
	s.addOutFlag(cmd)
	return cmd
}

func commitRun(a *App, s *session, f *commitFlags, cmd *cobra.Command, args []string) error {
	ctx := cmd.Context()
	env, cfg, err := s.config(cmd)
	if err != nil {
		return err
	}
	if err := env.RequireRepo(); err != nil {
		return err
	}
	if err := commitPrompt(a, f); err != nil {
		return err
	}

	msg, err := app.CommitRender(env, cfg, tmpl.CommitData{
		Type: strings.TrimSpace(f.typ), Scope: strings.TrimSpace(f.scope), Breaking: f.breaking,
		Subject: strings.TrimSpace(f.subject), Body: strings.TrimSpace(f.body), Trailers: f.trailers,
	})
	if err != nil {
		return err
	}
	header := strings.SplitN(msg, "\n", 2)[0]
	pinned, signed := app.CommitGitArgs(env, cfg)

	p, _, err := s.planner(cmd, args)
	if err != nil {
		return err
	}

	// read: what would be committed
	var st app.CommitStatus
	res, err := p.Read(ctx, env.Git.Cmd("status", "--porcelain=v2"), "", func(r execx.Result) string {
		st = app.CommitParseStatus(r.Stdout)
		n := st.Staged
		if f.all {
			n = st.Total
		}
		return fmt.Sprintf("%d %s changed", n, commitPlural(n, "file", "files"))
	})
	if err != nil {
		return err
	}
	if res.ExitCode != 0 {
		return fmt.Errorf("git status failed: %s", strings.TrimSpace(res.Stderr))
	}
	files := st.Staged
	if f.all {
		files = st.Total
	}
	switch {
	case st.Unmerged > 0:
		return errors.New("unresolved merge conflicts: resolve them before committing")
	case st.Total == 0:
		return errors.New("nothing to commit (working tree clean)")
	case files == 0:
		return fmt.Errorf("nothing staged to commit (%d unstaged %s): stage files with git add, or pass -a",
			st.Total, commitPlural(st.Total, "change", "changes"))
	}

	// the rules judge the exact command the plan will run
	v := app.CommitEvaluate(env, cfg, pinned, signed, msg)
	branch, _ := env.Git.CurrentBranch(ctx)
	app.CommitRecordChecks(p, cfg.Policy, v, branch, signed)
	if v.Decision() == verdict.DecDeny {
		fmt.Fprintf(a.Err, "mygit: commit refused by the rules; the message built from the template was:\n%s", commitIndent(msg, "  "))
		return lintReport(a, v)
	}

	if f.all {
		p.Git("stage all changes", "add", "-A")
	}
	msgPath := p.File("msg", msg)
	p.Git("commit", app.CommitStepArgs(pinned, signed, msgPath)...)

	format := cfg.Profile.Git.Signing.Format
	extra := map[string]any{
		"header": header, "message": msg, "files": files, "stageAll": f.all,
		"signing": format, "signed": signed, "branch": branch,
		"findings": append([]verdict.Finding{}, v.Findings...),
	}
	after := func(w io.Writer) { commitReport(ctx, w, env, format, header) }
	return s.runExtra(cmd, p, after, extra)
}

// commitReport prints "✓ a1f9c02 feat(auth): add login   signed ssh (Good)".
func commitReport(ctx context.Context, w io.Writer, env *app.Env, format, header string) {
	out, err := env.Git.Query(ctx, "log", "-1", "--format=%h%x1f%G?")
	if err != nil {
		fmt.Fprintf(w, "✓ committed %s\n", header)
		return
	}
	hash, code, _ := strings.Cut(out, "\x1f")
	sig := "unsigned"
	if name := app.CommitSigStatus(code); name != "" {
		kind := ""
		if format != "" && format != "default" {
			kind = format + " "
		}
		sig = fmt.Sprintf("signed %s(%s)", kind, name)
	}
	fmt.Fprintf(w, "✓ %s %s   %s\n", hash, header, sig)
}

// commitPrompt asks for a missing type and subject on a terminal, else fails
// with a usage message.
func commitPrompt(a *App, f *commitFlags) error {
	missing := func() []string {
		var m []string
		if strings.TrimSpace(f.typ) == "" {
			m = append(m, "--type")
		}
		if strings.TrimSpace(f.subject) == "" {
			m = append(m, "--message")
		}
		return m
	}
	m := missing()
	if len(m) == 0 {
		return nil
	}
	if a.IsTerminal == nil || !a.IsTerminal() || a.Opts.JSON {
		return fmt.Errorf("missing %s\nusage: mygit commit -t <type> [-s <scope>] -m <subject> [--body <text>] [--breaking] [-a]",
			strings.Join(m, " and "))
	}
	in := bufio.NewReader(a.In)
	ask := func(label string, dst *string) error {
		if strings.TrimSpace(*dst) != "" {
			return nil
		}
		fmt.Fprintf(a.Out, "%s: ", label)
		line, err := in.ReadString('\n')
		if err != nil && (err != io.EOF || line == "") {
			return fmt.Errorf("read %s: %w", label, err)
		}
		*dst = strings.TrimSpace(line)
		return nil
	}
	if err := ask("type", &f.typ); err != nil {
		return err
	}
	if err := ask("subject", &f.subject); err != nil {
		return err
	}
	if m := missing(); len(m) > 0 {
		return fmt.Errorf("missing %s", strings.Join(m, " and "))
	}
	return nil
}

func commitPlural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

func commitIndent(s, prefix string) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	for i, l := range lines {
		lines[i] = prefix + l
	}
	return strings.Join(lines, "\n") + "\n"
}
