package app

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/plan"
)

// State fingerprints what a plan was made against: the active profile,
// the content of every config file, and inside a repository HEAD, the
// `git status --porcelain=v2` output and every ref tip. It is the
// plan.StateFunc of every command run in e.
func (e *Env) State(ctx context.Context) (string, error) {
	h := sha256.New()
	fmt.Fprintf(h, "profile=%s\n", e.ProfileName())
	for _, p := range []string{e.Paths.Global, e.Paths.Project, e.Paths.Local} {
		if p == "" {
			continue
		}
		b, err := e.ReadOptional(p)
		if err != nil {
			return "", err
		}
		fmt.Fprintf(h, "file %s %s\n", p, hashOrAbsent(b))
	}
	if e.InRepo() {
		head, err := e.Git.HeadSHA(ctx)
		if err != nil {
			return "", err
		}
		fmt.Fprintf(h, "head=%s\n", head)
		for _, args := range [][]string{
			{"status", "--porcelain=v2"},
			{"for-each-ref", "--format=%(refname) %(objectname)"},
		} {
			res, err := e.Git.Run(ctx, args...)
			if err != nil {
				return "", err
			}
			fmt.Fprintf(h, "%s\n", strings.Join(args, " "))
			io.WriteString(h, res.Stdout)
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func hashOrAbsent(b []byte) string {
	if b == nil {
		return "absent"
	}
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

// StoreDir is where plans are saved: $XDG_STATE_HOME/mygit/plans, else
// %LOCALAPPDATA%\mygit\plans on Windows, else ~/.local/state/mygit/plans.
func StoreDir(goos string, getenv func(string) string, home string) string {
	if getenv == nil {
		getenv = func(string) string { return "" }
	}
	if x := strings.TrimSpace(getenv("XDG_STATE_HOME")); x != "" {
		return filepath.Join(x, "mygit", "plans")
	}
	if goos == "windows" {
		if l := strings.TrimSpace(getenv("LOCALAPPDATA")); l != "" {
			return filepath.Join(l, "mygit", "plans")
		}
		return filepath.Join(home, "AppData", "Local", "mygit", "plans")
	}
	return filepath.Join(home, ".local", "state", "mygit", "plans")
}

// Store returns the plan store for e.
func (e *Env) Store() plan.Store { return plan.Store{Dir: StoreDir(e.GOOS, e.Getenv, e.Home)} }
