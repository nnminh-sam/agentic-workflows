package plan

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// ErrStale means the repo state (or the plan itself) changed since planning.
var ErrStale = errors.New("plan is stale: the repository changed since it was planned; plan again")

// ApplyOptions configures Apply.
type ApplyOptions struct {
	// TempDir replaces the temp placeholder in file paths and args
	// (callers pass os.TempDir()). Required when the plan has Files.
	TempDir string
	// Trace, when set, is called before every write command (--verbose).
	Trace func(execx.Cmd)
}

// StepResult is the outcome of one executed write step.
type StepResult struct {
	Index  int // 1-based position in Plan.Steps
	Cmd    execx.Cmd
	Result execx.Result
}

// StepError reports the write step that failed; later steps did not run.
type StepError struct {
	StepResult
	Err error // start failure, or nil when the command exited non-zero
}

func (e *StepError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("step %d (%s): %v", e.Index, e.Cmd, e.Err)
	}
	return fmt.Sprintf("step %d (%s): exit %d: %s", e.Index, e.Cmd, e.Result.ExitCode, firstLine(e.Result.Stderr))
}

func (e *StepError) Unwrap() error { return e.Err }

// Fresh reports whether p still matches the current repo state.
func Fresh(ctx context.Context, p *Plan, state StateFunc) (bool, error) {
	s, err := runState(ctx, state)
	if err != nil {
		return false, err
	}
	return ComputeID(p, s) == p.ID, nil
}

// Apply re-checks the repo state, refuses with ErrStale if the id no longer
// matches, writes the plan's files, then runs the write steps in order and
// stops at the first failure (returned as *StepError). The results of every
// step that ran are returned either way.
func Apply(ctx context.Context, p *Plan, r execx.Runner, state StateFunc, opts ApplyOptions) ([]StepResult, error) {
	fresh, err := Fresh(ctx, p, state)
	if err != nil {
		return nil, err
	}
	if !fresh {
		return nil, fmt.Errorf("plan %s: %w", p.ID, ErrStale)
	}
	if _, err := p.WriteFiles(opts.TempDir); err != nil {
		return nil, err
	}
	var results []StepResult
	for i, s := range p.Steps {
		if s.Kind != Write {
			continue
		}
		c := p.Resolve(s.Cmd, opts.TempDir)
		if opts.Trace != nil {
			opts.Trace(c)
		}
		res, err := r.Run(ctx, c)
		sr := StepResult{Index: i + 1, Cmd: c, Result: res}
		results = append(results, sr)
		if err != nil || !res.OK() {
			return results, &StepError{StepResult: sr, Err: err}
		}
	}
	return results, nil
}

// WriteFiles writes the plan's files under tempDir (mode 0600) and returns
// their resolved paths. Manual mode calls it at planning time so the
// printed commands find the files; Apply calls it again before the writes.
func (p *Plan) WriteFiles(tempDir string) ([]string, error) {
	if len(p.Files) == 0 {
		return nil, nil
	}
	if tempDir == "" {
		return nil, errors.New("plan files: no temp dir given")
	}
	paths := make([]string, 0, len(p.Files))
	for _, f := range p.Files {
		path := filepath.FromSlash(p.ResolvePath(f.Path, tempDir))
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return paths, fmt.Errorf("plan file %s: %w", f.Name, err)
		}
		if err := os.WriteFile(path, []byte(f.Content), 0o600); err != nil {
			return paths, fmt.Errorf("plan file %s: %w", f.Name, err)
		}
		paths = append(paths, path)
	}
	return paths, nil
}
