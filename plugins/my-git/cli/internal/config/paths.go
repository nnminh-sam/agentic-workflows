package config

import (
	"path"
	"path/filepath"
	"strings"
)

// File names inside a repository.
const (
	ProjectFileName     = ".mygit.json"
	LocalFileName       = ".mygit.local.json"
	ProjectWorkflowsDir = ".mygit/workflows"
)

// Paths are the config locations for one machine and repository.
type Paths struct {
	// GlobalDir is $XDG_CONFIG_HOME/mygit, ~/.config/mygit or %APPDATA%\mygit.
	GlobalDir string
	// Global is GlobalDir/config.json.
	Global string
	// GlobalWorkflows is GlobalDir/workflows.
	GlobalWorkflows string
	// Project, Local and ProjectWorkflows are "" outside a repository.
	Project          string
	Local            string
	ProjectWorkflows string
}

// GlobalDir returns the global config directory for goos: $XDG_CONFIG_HOME/mygit
// when set, else %APPDATA%\mygit on Windows (falling back to
// home\AppData\Roaming), else home/.config/mygit.
func GlobalDir(goos string, getenv func(string) string, home string) string {
	env := func(k string) string {
		if getenv == nil {
			return ""
		}
		return strings.TrimSpace(getenv(k))
	}
	j := joiner(goos)
	if x := env("XDG_CONFIG_HOME"); x != "" {
		return j(x, "mygit")
	}
	if goos == "windows" {
		if a := env("APPDATA"); a != "" {
			return j(a, "mygit")
		}
		return j(home, "AppData", "Roaming", "mygit")
	}
	return j(home, ".config", "mygit")
}

// ResolvePaths computes every config path. repoRoot may be "".
func ResolvePaths(goos string, getenv func(string) string, home, repoRoot string) Paths {
	j := joiner(goos)
	dir := GlobalDir(goos, getenv, home)
	p := Paths{GlobalDir: dir, Global: j(dir, "config.json"), GlobalWorkflows: j(dir, "workflows")}
	if repoRoot != "" {
		p.Project = j(repoRoot, ProjectFileName)
		p.Local = j(repoRoot, LocalFileName)
		p.ProjectWorkflows = j(repoRoot, ".mygit", "workflows")
	}
	return p
}

// joiner joins with the target OS's separator, independent of the host OS,
// so Windows paths can be computed (and tested) anywhere.
func joiner(goos string) func(...string) string {
	if goos == "windows" {
		return func(parts ...string) string {
			out := strings.TrimRight(parts[0], `\/`)
			for _, p := range parts[1:] {
				out += `\` + strings.Trim(p, `\/`)
			}
			return out
		}
	}
	return func(parts ...string) string { return path.Join(parts...) }
}

// Display shortens p for messages: relative to repoRoot when inside it,
// "~/…" when inside home, else unchanged.
func Display(p, repoRoot, home string) string {
	if p == "" {
		return p
	}
	if repoRoot != "" {
		if rel, ok := under(p, repoRoot); ok {
			return rel
		}
	}
	if home != "" {
		if rel, ok := under(p, home); ok {
			return "~/" + rel
		}
	}
	return p
}

func under(p, dir string) (string, bool) {
	rel, err := filepath.Rel(dir, p)
	if err != nil || rel == "." || strings.HasPrefix(rel, "..") {
		return "", false
	}
	return filepath.ToSlash(rel), true
}
