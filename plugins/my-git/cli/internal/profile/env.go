package profile

import (
	"fmt"
	"strings"
)

// EnvVar describes one environment override of a profile field. MYGIT_*
// wins over the legacy GIT_GUARD_* name of the Python implementation.
type EnvVar struct {
	Names []string // checked in order; the first non-blank one wins
	Field string   // dotted profile field, e.g. "github.user"
	List  bool     // comma-separated list
	apply func(p *Profile, s string, l []string)
}

// EnvVars returns the supported environment overrides.
func EnvVars() []EnvVar {
	return []EnvVar{
		{Names: []string{"MYGIT_GITHUB_USER", "GIT_GUARD_GITHUB_USER"}, Field: "github.user",
			apply: func(p *Profile, s string, _ []string) { p.GitHub.User = s }},
		{Names: []string{"MYGIT_GITHUB_ORGS", "GIT_GUARD_GITHUB_ORGS"}, Field: "github.orgs", List: true,
			apply: func(p *Profile, _ string, l []string) { p.GitHub.Orgs = l }},
		{Names: []string{"MYGIT_GIT_NAME", "GIT_GUARD_GIT_NAME"}, Field: "git.name",
			apply: func(p *Profile, s string, _ []string) { p.Git.Name = s }},
		{Names: []string{"MYGIT_GIT_EMAIL", "GIT_GUARD_GIT_EMAIL"}, Field: "git.email", List: true,
			apply: func(p *Profile, _ string, l []string) { p.Git.Email = l }},
		{Names: []string{"MYGIT_GIT_TRANSPORT", "GIT_GUARD_GIT_TRANSPORT"}, Field: "github.transport",
			apply: func(p *Profile, s string, _ []string) { p.GitHub.Transport = s }},
		{Names: []string{"MYGIT_SSH_KEY", "GIT_GUARD_SSH_KEY"}, Field: "github.sshKey",
			apply: func(p *Profile, s string, _ []string) { p.GitHub.SSHKey = s }},
		{Names: []string{"MYGIT_SIGNING_FORMAT"}, Field: "git.signing.format",
			apply: func(p *Profile, s string, _ []string) { p.Git.Signing.Format = s }},
		{Names: []string{"MYGIT_SIGNING_KEY", "GIT_GUARD_SIGNING_KEY"}, Field: "git.signing.key",
			apply: applySigningKey},
	}
}

// applySigningKey follows the Python signing_key semantics: "default" means
// git's own signing setup, any other value is an SSH key path.
func applySigningKey(p *Profile, s string, _ []string) {
	if s == SignDefault {
		p.Git.Signing.Format, p.Git.Signing.Key = SignDefault, ""
		return
	}
	p.Git.Signing.Key = s
	if p.Git.Signing.Format == "" || p.Git.Signing.Format == SignDefault || p.Git.Signing.Format == SignNone {
		p.Git.Signing.Format = SignSSH
	}
}

// ApplyEnv returns p with environment overrides applied, plus the origin of
// each overridden field ("github.user" → "env MYGIT_GITHUB_USER").
func ApplyEnv(p Profile, getenv func(string) string) (Profile, map[string]string, error) {
	origins := map[string]string{}
	if getenv == nil {
		return p, origins, nil
	}
	for _, ev := range EnvVars() {
		name, raw := firstSet(getenv, ev.Names)
		if name == "" {
			continue
		}
		var list []string
		if ev.List {
			list = clean(strings.Split(raw, ","))
		}
		ev.apply(&p, raw, list)
		origins[ev.Field] = "env " + name
	}
	if err := p.Validate(); err != nil {
		return p, origins, fmt.Errorf("profile from environment: %w", err)
	}
	return p, origins, nil
}

func firstSet(getenv func(string) string, names []string) (string, string) {
	for _, n := range names {
		if v := strings.TrimSpace(getenv(n)); v != "" {
			return n, v
		}
	}
	return "", ""
}
