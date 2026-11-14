package app

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
)

// File writes are plan steps too. internal/plan only executes processes,
// so a file write is recorded as
//
//	mygit file write --from {{TEMP}}/mygit-{{id}}.<name> --to <path>
//
// with the content as a plan File (written to the temp dir before the
// writes run). In auto mode FileRunner performs the copy in-process (no
// process is started); in manual mode the printed command is a real,
// hidden mygit subcommand (internal/cli/file.go) that does the same copy.

// File-write step command: `mygit file write --from F --to T`.
const (
	fileProgram = "mygit"
	fileVerb1   = "file"
	fileVerb2   = "write"
)

// fileWriteCmd returns the step command that copies from to to.
func fileWriteCmd(from, to string) execx.Cmd {
	return execx.Cmd{Name: fileProgram, Args: []string{fileVerb1, fileVerb2, "--from", from, "--to", to}}
}

// fileNames keeps plan file labels unique within one plan.
type fileNames struct{ seen map[string]int }

func (f *fileNames) unique(name string) string {
	if f.seen == nil {
		f.seen = map[string]int{}
	}
	f.seen[name]++
	if n := f.seen[name]; n > 1 {
		return fmt.Sprintf("%d.%s", n, name)
	}
	return name
}

// IsFileWrite reports whether c is a file-write step and returns its paths.
func IsFileWrite(c execx.Cmd) (from, to string, ok bool) {
	if c.Name != fileProgram || len(c.Args) != 6 || c.Args[0] != fileVerb1 || c.Args[1] != fileVerb2 ||
		c.Args[2] != "--from" || c.Args[4] != "--to" {
		return "", "", false
	}
	return c.Args[3], c.Args[5], true
}

// FileRunner executes file-write steps in-process and passes every other
// command to Next.
type FileRunner struct {
	Next execx.Runner
}

// Run implements execx.Runner.
func (r FileRunner) Run(ctx context.Context, c execx.Cmd) (execx.Result, error) {
	if from, to, ok := IsFileWrite(c); ok {
		if err := CopyFile(from, to); err != nil {
			return execx.Result{ExitCode: 1, Stderr: err.Error() + "\n"}, nil
		}
		return execx.Result{}, nil
	}
	return r.Next.Run(ctx, c)
}

// CopyFile writes the content of from to to, creating parent directories.
// An existing file keeps its permission bits; a new one gets 0644. The
// write goes through a temp file and a rename so readers never see a
// partial file.
func CopyFile(from, to string) error {
	data, err := os.ReadFile(from)
	if err != nil {
		return fmt.Errorf("write %s: %w", to, err)
	}
	return WriteFileAtomic(to, data)
}

// WriteFileAtomic writes data to path (see CopyFile).
func WriteFileAtomic(path string, data []byte) error {
	mode := os.FileMode(0o644)
	if fi, err := os.Stat(path); err == nil {
		mode = fi.Mode().Perm()
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp*")
	if err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := os.Chmod(tmp.Name(), mode); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}
