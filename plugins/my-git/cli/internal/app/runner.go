package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"slices"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
)

// RunOptions are the execution settings of one run, from the global flags
// (--mode, --shell, --apply, --yes, --json), the per-command --out flag
// and the resolved execution config.
type RunOptions struct {
	Mode    string // auto | manual ("" = auto)
	Shell   string // manual-mode shell ("" = bash)
	ApplyID string // --apply <id>
	Yes     bool
	JSON    bool
	// Interactive is true when stdin is a terminal (a y/N prompt is possible).
	Interactive bool
	// OutFile saves the manual-mode script instead of printing it.
	OutFile string
	// TempDir receives the plan's files (os.TempDir()).
	TempDir string
	// Extra is copied into Outcome.Extra (command-specific JSON data).
	Extra map[string]any
}

// Status of a finished run.
const (
	StatusPlanned  = "planned"  // dry run only; apply with --apply <id> or --yes
	StatusApplied  = "applied"  // every write step ran
	StatusManual   = "manual"   // the script was printed or saved
	StatusDeclined = "declined" // the prompt was answered no
	StatusFailed   = "failed"   // a write step failed
	StatusNoop     = "noop"     // the plan has no write steps
)

// Outcome is what a run did.
type Outcome struct {
	Status     string       `json:"status"`
	Plan       *plan.Plan   `json:"plan"`
	Results    []StepResult `json:"results,omitempty"`
	Script     string       `json:"script,omitempty"`
	ScriptPath string       `json:"scriptPath,omitempty"`
	ApplyHint  string       `json:"applyCommand,omitempty"`
	Error      string       `json:"error,omitempty"`
	// Extra holds command-specific data for the JSON output.
	Extra map[string]any `json:"extra,omitempty"`
}

// StepResult is the JSON form of one executed write step.
type StepResult struct {
	Index       int    `json:"index"`
	Description string `json:"description,omitempty"`
	Command     string `json:"command"`
	ExitCode    int    `json:"exitCode"`
	Stdout      string `json:"stdout,omitempty"`
	Stderr      string `json:"stderr,omitempty"`
}

// ErrStepFailed is returned (wrapped) when a write step failed.
var ErrStepFailed = errors.New("plan step failed")

// Executor runs built plans.
type Executor struct {
	Env *Env
	In  io.Reader
	Out io.Writer
	Err io.Writer
	// After, when set, is called after a successful apply in text mode
	// (e.g. to print the remote owner check of `profile use`).
	After func(w io.Writer)
}

// Run executes p according to o:
//
//   - always: print the plan (text, or nothing yet in JSON mode) and save it;
//   - manual mode: print (or save with --out) the script for the shell;
//   - auto + --apply <id>: load the stored plan, check it matches this
//     command and is still fresh, then apply it;
//   - auto + --yes, or "y" at the prompt on a terminal: apply;
//   - otherwise stop after the dry run and print the --apply hint.
//
// In JSON mode one Outcome object is printed at the end. A failed step
// returns an error wrapping ErrStepFailed (and *plan.StepError).
func (x *Executor) Run(ctx context.Context, p *plan.Plan, o RunOptions) (*Outcome, error) {
	if o.Mode == "" {
		o.Mode = "auto"
	}
	p.Mode = o.Mode
	if o.Mode == "manual" && o.Shell != "" {
		p.Shell = o.Shell
	}
	out := &Outcome{Status: StatusPlanned, Plan: p, Extra: o.Extra}
	if !o.JSON {
		fmt.Fprint(x.Out, plan.Text(p))
	}
	if len(p.Writes()) == 0 {
		out.Status = StatusNoop
		if !o.JSON {
			fmt.Fprintln(x.Out, "nothing to do")
		}
		return out, x.finish(out, o)
	}
	if err := x.Env.Store().Save(p); err != nil {
		return out, err
	}
	out.ApplyHint = ApplyHint(p, o.Shell)
	if o.Mode == "manual" {
		if err := x.manual(p, o, out); err != nil {
			return out, err
		}
		return out, x.finish(out, o)
	}
	target := p
	switch {
	case o.ApplyID != "":
		t, err := x.stored(ctx, p, o.ApplyID)
		if err != nil {
			return out, err
		}
		target = t
	case o.Yes:
	case o.Interactive && !o.JSON:
		ok, err := plan.Confirm(x.In, x.Out)
		if err != nil {
			return out, err
		}
		if !ok {
			out.Status = StatusDeclined
			fmt.Fprintln(x.Out, "not applied")
			return out, nil
		}
	default:
		if !o.JSON {
			fmt.Fprintf(x.Out, "not applied (dry run). to apply: %s\n", out.ApplyHint)
		}
		return out, x.finish(out, o)
	}
	err := x.apply(ctx, target, o, out)
	if ferr := x.finish(out, o); err == nil {
		err = ferr
	}
	return out, err
}

func (x *Executor) finish(out *Outcome, o RunOptions) error {
	if !o.JSON {
		return nil
	}
	b, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(x.Out, string(b))
	return err
}

// stored loads plan id and checks it belongs to this command.
func (x *Executor) stored(ctx context.Context, current *plan.Plan, id string) (*plan.Plan, error) {
	if id == current.ID {
		return current, nil
	}
	sp, err := x.Env.Store().Load(id)
	if errors.Is(err, plan.ErrNotFound) {
		return nil, fmt.Errorf("plan %s not found; the current plan for this command is %s", id, current.ID)
	}
	if err != nil {
		return nil, err
	}
	if !slices.Equal(sp.Command, current.Command) {
		return nil, fmt.Errorf("plan %s was made for `mygit %s`, not this command", id, strings.Join(sp.Command, " "))
	}
	return nil, fmt.Errorf("plan %s: %w (the current plan is %s)", id, plan.ErrStale, current.ID)
}

func (x *Executor) apply(ctx context.Context, p *plan.Plan, o RunOptions, out *Outcome) error {
	results, err := plan.Apply(ctx, p, FileRunner{Next: x.Env.Runner}, x.Env.State, plan.ApplyOptions{TempDir: o.TempDir})
	for _, r := range results {
		desc := p.Steps[r.Index-1].Description
		out.Results = append(out.Results, StepResult{
			Index: r.Index, Description: desc, Command: r.Cmd.String(),
			ExitCode: r.Result.ExitCode, Stdout: r.Result.Stdout, Stderr: r.Result.Stderr,
		})
		if o.JSON {
			continue
		}
		if desc == "" {
			desc = r.Cmd.String()
		}
		mark := "✓"
		if !r.Result.OK() {
			mark = "✗"
		}
		fmt.Fprintf(x.Out, "%s %s\n", mark, desc)
	}
	var se *plan.StepError
	if errors.As(err, &se) {
		out.Status, out.Error = StatusFailed, se.Error()
		if !o.JSON && strings.TrimSpace(se.Result.Stderr) != "" {
			fmt.Fprint(x.Err, indent(se.Result.Stderr, "  "))
		}
		return fmt.Errorf("%w: %v", ErrStepFailed, err)
	}
	if err != nil {
		out.Status, out.Error = StatusFailed, err.Error()
		return err
	}
	out.Status = StatusApplied
	if !o.JSON && x.After != nil {
		x.After(x.Out)
	}
	return nil
}

func (x *Executor) manual(p *plan.Plan, o RunOptions, out *Outcome) error {
	sh, err := shellfmt.ParseShell(orDefault(o.Shell, "bash"))
	if err != nil {
		return err
	}
	if _, err := p.WriteFiles(o.TempDir); err != nil {
		return err
	}
	out.Status = StatusManual
	if o.OutFile == "" {
		out.Script = plan.Manual(p, sh, false)
		if !o.JSON {
			fmt.Fprint(x.Out, out.Script)
		}
		return nil
	}
	path, sh := ScriptPath(o.OutFile, sh)
	out.Script, out.ScriptPath = plan.Manual(p, sh, true), path
	if err := WriteFileAtomic(path, []byte(out.Script)); err != nil {
		return err
	}
	if !o.JSON {
		fmt.Fprintf(x.Out, "wrote %s (%s); run it, then: mygit plan verify %s\n", path, sh, p.ID)
	}
	return nil
}

// ScriptPath picks the shell from the --out extension (.ps1 PowerShell,
// .cmd/.bat cmd, .sh keeps a POSIX shell or uses bash) and appends the
// shell's extension when path has none.
func ScriptPath(path string, sh shellfmt.Shell) (string, shellfmt.Shell) {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".ps1":
		return path, shellfmt.PowerShell
	case ".cmd", ".bat":
		return path, shellfmt.Cmd
	case ".sh":
		if !sh.Posix() {
			sh = shellfmt.Bash
		}
		return path, sh
	case "":
		return path + shellfmt.ScriptExt(sh), sh
	}
	return path, sh
}

// ApplyHint is the command that applies p: "mygit <argv> --apply <id>".
func ApplyHint(p *plan.Plan, shell string) string {
	sh, err := shellfmt.ParseShell(orDefault(shell, "bash"))
	if err != nil {
		sh = shellfmt.Bash
	}
	words := []string{"mygit"}
	for _, a := range p.Command {
		words = append(words, shellfmt.Quote(sh, a))
	}
	return strings.Join(append(words, "--apply", p.ID), " ")
}

func orDefault(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

func indent(s, prefix string) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	for i, l := range lines {
		lines[i] = prefix + l
	}
	return strings.Join(lines, "\n") + "\n"
}
