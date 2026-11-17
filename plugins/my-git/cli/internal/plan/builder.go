package plan

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// StateFunc returns a fingerprint of the repo state a plan depends on
// (HEAD, index tree or status hash, branch tips, active profile). The plan
// id hashes it, so Apply can detect that anything changed since planning.
type StateFunc func(ctx context.Context) (string, error)

// Meta describes the invocation a plan is built for.
type Meta struct {
	Command  []string // argv of the mygit invocation
	Profile  string
	Ruleset  string
	Workflow string
	Mode     string // auto | manual
	Shell    string
}

// Builder assembles a Plan. Read steps run immediately; write steps are
// only recorded.
type Builder struct {
	runner execx.Runner
	plan   Plan
	// Trace, when set, is called before every read command (--verbose).
	Trace func(execx.Cmd)
}

// NewBuilder returns a Builder that runs reads through r.
func NewBuilder(r execx.Runner, m Meta) *Builder {
	return &Builder{runner: r, plan: Plan{
		Command: m.Command, Profile: m.Profile, Ruleset: m.Ruleset,
		Workflow: m.Workflow, Mode: m.Mode, Shell: m.Shell,
	}}
}

// Read runs c now and records it as a read step. summarize (optional)
// turns the result into the short text shown next to the step. A non-zero
// exit is recorded, not returned as an error.
func (b *Builder) Read(ctx context.Context, c execx.Cmd, description string, summarize func(execx.Result) string) (execx.Result, error) {
	if b.Trace != nil {
		b.Trace(c)
	}
	res, err := b.runner.Run(ctx, c)
	step := Step{Kind: Read, Cmd: c, Description: description, Result: &res}
	if err == nil && summarize != nil {
		step.Summary = summarize(res)
	}
	if err != nil {
		step.Summary = err.Error()
	}
	b.plan.Steps = append(b.plan.Steps, step)
	if err != nil {
		return res, fmt.Errorf("plan read %s: %w", c, err)
	}
	return res, nil
}

// Write records c as a write step without running it.
func (b *Builder) Write(c execx.Cmd, description string) {
	b.plan.Steps = append(b.plan.Steps, Step{Kind: Write, Cmd: c, Description: description})
}

// File records a file to write before the writes run and returns its
// placeholder path "{{TEMP}}/mygit-{{id}}.<name>" for use in step args.
func (b *Builder) File(name, content string) string {
	path := TempToken + "/mygit-" + IDToken + "." + name
	b.plan.Files = append(b.plan.Files, File{Name: name, Path: path, Content: content})
	return path
}

// Check records a rule check.
func (b *Builder) Check(ruleID string, ok bool, sev verdict.Severity, message string) {
	b.plan.Checks = append(b.plan.Checks, Check{RuleID: ruleID, OK: ok, Severity: sev, Message: message})
}

// Note records a free-text note shown with the plan.
func (b *Builder) Note(format string, args ...any) {
	b.plan.Notes = append(b.plan.Notes, fmt.Sprintf(format, args...))
}

// Build fingerprints the repo state and returns the plan with its id.
// state may be nil (the id then depends on the commands only).
func (b *Builder) Build(ctx context.Context, state StateFunc) (*Plan, error) {
	s, err := runState(ctx, state)
	if err != nil {
		return nil, err
	}
	p := b.plan
	p.ID = ComputeID(&p, s)
	return &p, nil
}

func runState(ctx context.Context, state StateFunc) (string, error) {
	if state == nil {
		return "", nil
	}
	s, err := state(ctx)
	if err != nil {
		return "", fmt.Errorf("repo state: %w", err)
	}
	return s, nil
}

// idInput is the canonical hashed form of a plan.
type idInput struct {
	Command []string `json:"command"`
	Steps   []idStep `json:"steps"`
	Files   []File   `json:"files"`
	State   string   `json:"state"`
}

type idStep struct {
	Kind Kind    `json:"kind"`
	Cmd  cmdJSON `json:"cmd"`
}

// ComputeID returns the 6-hex-digit id over the mygit argv, every step's
// command, the planned files and the repo state fingerprint. Read results,
// descriptions, checks and display metadata are not hashed.
func ComputeID(p *Plan, state string) string {
	in := idInput{Command: p.Command, Files: p.Files, State: state}
	for _, s := range p.Steps {
		in.Steps = append(in.Steps, idStep{Kind: s.Kind, Cmd: cmdJSON(s.Cmd)})
	}
	b, _ := json.Marshal(in) // plain strings and slices: cannot fail
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])[:6]
}
