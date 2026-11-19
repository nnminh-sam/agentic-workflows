// Package plan holds the plan object every mutating mygit command builds
// first. Read steps run while planning; write steps are only recorded. A plan
// is shown (Text/JSON), printed for a shell (Manual), saved (Store) and later
// executed by Apply, which refuses to run if the repo state changed.
package plan

import (
	"encoding/json"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shellfmt"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// IDToken is replaced by the plan id when a step or file path is resolved.
// It lets file names contain the id although the id hashes the steps.
const IDToken = "{{id}}"

// TempToken is the temp-directory placeholder shared with shellfmt.
const TempToken = shellfmt.TempDir

// Kind tells read steps (run during planning) from write steps.
type Kind string

// Step kinds.
const (
	Read  Kind = "read"
	Write Kind = "write"
)

// Plan is the single shared object of plan-first execution.
type Plan struct {
	ID       string   `json:"id"`
	Command  []string `json:"command"` // argv of the mygit invocation
	Profile  string   `json:"profile,omitempty"`
	Ruleset  string   `json:"ruleset,omitempty"`
	Workflow string   `json:"workflow,omitempty"`
	Mode     string   `json:"mode,omitempty"`  // auto | manual
	Shell    string   `json:"shell,omitempty"` // powershell | cmd | gitbash | bash | zsh
	Steps    []Step   `json:"steps"`
	Checks   []Check  `json:"checks"`
	Files    []File   `json:"files,omitempty"`
	Notes    []string `json:"notes,omitempty"`
}

// Step is one command of the plan.
type Step struct {
	Kind        Kind          `json:"kind"`
	Cmd         execx.Cmd     `json:"-"`
	Description string        `json:"description,omitempty"`
	Result      *execx.Result `json:"-"` // reads only: what planning saw
	Summary     string        `json:"summary,omitempty"`
}

// Check is one rule check evaluated while planning.
type Check struct {
	RuleID   string           `json:"rule"`
	OK       bool             `json:"ok"`
	Severity verdict.Severity `json:"severity"`
	Message  string           `json:"message,omitempty"`
}

// File is a file mygit writes during planning (e.g. the commit message used
// with -F). Path may contain TempToken and IDToken.
type File struct {
	Name    string `json:"name"` // short label shown as <name> in Text
	Path    string `json:"path"`
	Content string `json:"content"`
}

// Writes returns the write steps in order.
func (p *Plan) Writes() []Step {
	var out []Step
	for _, s := range p.Steps {
		if s.Kind == Write {
			out = append(out, s)
		}
	}
	return out
}

// OK reports whether every check passed.
func (p *Plan) OK() bool {
	for _, c := range p.Checks {
		if !c.OK {
			return false
		}
	}
	return true
}

// ResolvePath replaces IDToken with the plan id and TempToken with tempDir.
// When tempDir is "" the temp placeholder is kept (for shell rendering).
func (p *Plan) ResolvePath(s, tempDir string) string {
	s = strings.ReplaceAll(s, IDToken, p.ID)
	if tempDir != "" {
		s = strings.ReplaceAll(s, TempToken+"/", strings.TrimRight(tempDir, `/\`)+pathSep(tempDir))
		s = strings.ReplaceAll(s, TempToken, tempDir)
	}
	return s
}

// Resolve returns c with tokens in Args, Env, Dir and Stdin resolved.
func (p *Plan) Resolve(c execx.Cmd, tempDir string) execx.Cmd {
	out := c
	out.Args = p.resolveAll(c.Args, tempDir)
	out.Env = p.resolveAll(c.Env, tempDir)
	out.Dir = p.ResolvePath(c.Dir, tempDir)
	out.Stdin = p.ResolvePath(c.Stdin, tempDir)
	return out
}

func (p *Plan) resolveAll(in []string, tempDir string) []string {
	if in == nil {
		return nil
	}
	out := make([]string, len(in))
	for i, s := range in {
		out[i] = p.ResolvePath(s, tempDir)
	}
	return out
}

// pathSep guesses the separator style of dir (Windows paths use \).
func pathSep(dir string) string {
	if strings.Contains(dir, `\`) {
		return `\`
	}
	return "/"
}

// cmdJSON is the lowerCamelCase wire form of execx.Cmd.
type cmdJSON struct {
	Name    string   `json:"name"`
	Args    []string `json:"args"`
	Dir     string   `json:"dir,omitempty"`
	Env     []string `json:"env,omitempty"`
	Stdin   string   `json:"stdin,omitempty"`
	CmdLine string   `json:"cmdLine,omitempty"`
}

type resultJSON struct {
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
	ExitCode int    `json:"exitCode"`
}

type stepAlias Step

type stepJSON struct {
	stepAlias
	Cmd    cmdJSON     `json:"cmd"`
	Result *resultJSON `json:"result,omitempty"`
}

// MarshalJSON writes the step with lowerCamelCase cmd/result fields.
func (s Step) MarshalJSON() ([]byte, error) {
	j := stepJSON{stepAlias: stepAlias(s), Cmd: cmdJSON(s.Cmd)}
	if s.Result != nil {
		r := resultJSON(*s.Result)
		j.Result = &r
	}
	return json.Marshal(j)
}

// UnmarshalJSON reads what MarshalJSON writes.
func (s *Step) UnmarshalJSON(b []byte) error {
	var j stepJSON
	if err := json.Unmarshal(b, &j); err != nil {
		return err
	}
	*s = Step(j.stepAlias)
	s.Cmd = execx.Cmd(j.Cmd)
	if j.Result != nil {
		r := execx.Result(*j.Result)
		s.Result = &r
	}
	return nil
}
