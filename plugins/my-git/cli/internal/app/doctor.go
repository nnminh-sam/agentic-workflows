package app

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
)

// Check levels.
const (
	LevelOK   = "ok"
	LevelWarn = "warn" // shown with "!", does not fail doctor
	LevelFail = "fail" // shown with "✗", doctor exits 1
)

// Check is one doctor line.
type Check struct {
	Name   string `json:"name"`
	Level  string `json:"level"`
	Detail string `json:"detail"`
	// File, Field and Line locate a config error.
	File  string `json:"file,omitempty"`
	Field string `json:"field,omitempty"`
	Line  int    `json:"line,omitempty"`
}

// Mark renders the level: ✓, ! or ✗.
func (c Check) Mark() string {
	switch c.Level {
	case LevelOK:
		return "✓"
	case LevelWarn:
		return "!"
	}
	return "✗"
}

// Diagnose runs every doctor check against e. exists reports whether a
// file exists (nil means os.Stat).
func Diagnose(ctx context.Context, e *Env, exists func(string) bool) []Check {
	if exists == nil {
		exists = func(p string) bool { _, err := os.Stat(p); return err == nil }
	}
	var out []Check
	add := func(name, level, format string, args ...any) *Check {
		out = append(out, Check{Name: name, Level: level, Detail: fmt.Sprintf(format, args...)})
		return &out[len(out)-1]
	}
	if v, err := e.Git.Version(ctx); err != nil {
		add("git", LevelFail, "git not found: %v", err)
	} else {
		add("git", LevelOK, "git %s", v)
	}
	if v, err := e.Gh.Version(ctx); err != nil {
		add("gh", LevelFail, "gh not found (mygit runs GitHub calls through gh)")
	} else {
		add("gh", LevelOK, "gh %s", v)
	}

	files := 0
	for _, p := range []string{e.Paths.Global, e.Paths.Project, e.Paths.Local} {
		if p == "" {
			continue
		}
		if b, err := e.ReadOptional(p); err == nil && b != nil {
			files++
		}
	}
	if e.ConfigErr != nil {
		c := add("config", LevelFail, "config invalid: %v", e.ConfigErr)
		var ce *config.Error
		if errors.As(e.ConfigErr, &ce) {
			c.File, c.Field, c.Line = e.Display(ce.File), ce.Field, ce.Line
		}
		return append(out, shellCheck(e))
	}
	add("config", LevelOK, "config valid (%d %s)", files, map[bool]string{true: "file", false: "files"}[files == 1])
	for _, w := range e.Config.Warnings {
		add("config", LevelWarn, "%s", w)
	}

	c := e.Config
	if c.ProfileName == "" {
		add("profile", LevelWarn, "no active profile (identity rules use presence checks)")
	} else {
		add("profile", LevelOK, "profile %s (%s)", c.ProfileName, c.ProfileReason)
		out = append(out, identityChecks(ctx, e, c.Profile)...)
	}
	out = append(out, signingChecks(ctx, e, c.Profile, exists)...)
	if e.InRepo() {
		hp, ok, _ := e.Git.ConfigGet(ctx, "core.hooksPath")
		switch {
		case ok && strings.TrimSpace(hp) != "":
			add("hooks", LevelOK, "hooks core.hooksPath=%s", strings.TrimSpace(hp))
		default:
			add("hooks", LevelWarn, "hooks not installed (mygit hooks install)")
		}
	}
	return append(out, shellCheck(e))
}

func shellCheck(e *Env) Check {
	sh, mode := "bash", "auto"
	if e.Config != nil {
		sh, mode = e.Config.Execution.Shell, e.Config.Execution.Mode
	}
	return Check{Name: "shell", Level: LevelOK, Detail: fmt.Sprintf("shell %s (mode %s)", sh, mode)}
}

// identityChecks compares git config and the gh account with the profile.
func identityChecks(ctx context.Context, e *Env, p profile.Profile) []Check {
	var out []Check
	get := func(k string) string {
		v, _, _ := e.Git.ConfigGet(ctx, k)
		return strings.TrimSpace(v)
	}
	if p.Git.Name != "" {
		if got := get("user.name"); got == p.Git.Name {
			out = append(out, Check{Name: "identity", Level: LevelOK, Detail: "git user.name " + got})
		} else {
			out = append(out, Check{Name: "identity", Level: LevelFail,
				Detail: fmt.Sprintf("git user.name %q, profile wants %q (mygit profile use)", got, p.Git.Name)})
		}
	}
	if len(p.Git.Email) > 0 {
		got := get("user.email")
		ok := false
		for _, em := range p.Git.Email {
			ok = ok || strings.EqualFold(em, got)
		}
		if ok {
			out = append(out, Check{Name: "identity", Level: LevelOK, Detail: "git user.email " + got})
		} else {
			out = append(out, Check{Name: "identity", Level: LevelFail,
				Detail: fmt.Sprintf("git user.email %q not in profile (%s)", got, strings.Join(p.Git.Email, ", "))})
		}
	}
	if p.GitHub.User != "" {
		login, err := e.Gh.ActiveLogin(ctx, "")
		switch {
		case err != nil:
			out = append(out, Check{Name: "gh account", Level: LevelWarn, Detail: "gh login unknown: " + firstLine(err.Error())})
		case strings.EqualFold(login, p.GitHub.User):
			out = append(out, Check{Name: "gh account", Level: LevelOK, Detail: "gh account " + login})
		default:
			out = append(out, Check{Name: "gh account", Level: LevelFail,
				Detail: fmt.Sprintf("gh account %s, profile wants %s (gh auth switch --user %s)", login, p.GitHub.User, p.GitHub.User)})
		}
	}
	if e.RemoteOwner != "" {
		if allowed, known := OwnerCheck(p, e.RemoteOwner); known && !allowed {
			out = append(out, Check{Name: "remote", Level: LevelFail,
				Detail: fmt.Sprintf("origin owner %s is not the profile's github.user or orgs", e.RemoteOwner)})
		} else if known {
			out = append(out, Check{Name: "remote", Level: LevelOK, Detail: "origin owner " + e.RemoteOwner})
		}
	}
	return out
}

// signingChecks compares git's signing config with the profile.
func signingChecks(ctx context.Context, e *Env, p profile.Profile, exists func(string) bool) []Check {
	st, err := sign.ReadStatus(ctx, e.Git)
	if err != nil {
		return []Check{{Name: "signing", Level: LevelWarn, Detail: "signing config unreadable: " + err.Error()}}
	}
	want := p.Git.Signing.Format
	format := st.Format
	if format == "" {
		format = sign.OpenPGP
	}
	switch {
	case want == "" && !st.GPGSign:
		return []Check{{Name: "signing", Level: LevelOK, Detail: "signing off"}}
	case want == sign.None:
		if st.GPGSign {
			return []Check{{Name: "signing", Level: LevelFail, Detail: "commit.gpgsign is on but the profile says none"}}
		}
		return []Check{{Name: "signing", Level: LevelOK, Detail: "signing none"}}
	case want != "" && !st.GPGSign:
		return []Check{{Name: "signing", Level: LevelFail,
			Detail: fmt.Sprintf("profile signs with %s but commit.gpgsign is off (mygit sign setup)", want)}}
	case want != "" && want != sign.Default && want != format:
		return []Check{{Name: "signing", Level: LevelFail,
			Detail: fmt.Sprintf("gpg.format %s, profile wants %s (mygit sign setup)", format, want)}}
	}
	var out []Check
	detail := "signing " + format
	if st.Key != "" {
		detail += " (" + st.Key + ")"
	}
	out = append(out, Check{Name: "signing", Level: LevelOK, Detail: detail})
	if format == sign.SSH {
		if st.Key != "" && !strings.HasPrefix(st.Key, "key::") && !exists(sign.ExpandHome(st.Key, e.Home)) {
			out = append(out, Check{Name: "signing", Level: LevelFail, Detail: "signing key file not found: " + st.Key})
		}
		if st.AllowedSignersFile == "" {
			out = append(out, Check{Name: "signing", Level: LevelWarn,
				Detail: "gpg.ssh.allowedSignersFile not set: local verify shows E (mygit sign setup)"})
		}
	}
	return out
}

func firstLine(s string) string {
	l, _, _ := strings.Cut(strings.TrimSpace(s), "\n")
	return l
}
