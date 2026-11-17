package app

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/ghx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
)

// DetectProfile captures a profile from the current git config and gh
// login, like setup_profile.py --from-current: user.name/email, the key
// pinned in core.sshCommand, signing (format and key when set) and the
// active gh account. Transport is ssh when a key is pinned, else gh when
// gh is logged in. notes describe what was captured.
func DetectProfile(ctx context.Context, g *gitx.Git, gh *ghx.Gh) (p profile.Profile, notes []string) {
	get := func(k string) string {
		v, ok, err := g.ConfigGet(ctx, k)
		if err != nil || !ok {
			return ""
		}
		return strings.TrimSpace(v)
	}
	if v := get("user.name"); v != "" {
		p.Git.Name = v
		notes = append(notes, "git user.name")
	}
	if v := get("user.email"); v != "" {
		p.Git.Email = profile.StringList{v}
		notes = append(notes, "git user.email")
	}
	if key := SSHKeyFromCommand(get("core.sshCommand")); key != "" {
		p.GitHub.SSHKey = key
		notes = append(notes, "ssh key "+key)
	}
	gpgsign := strings.ToLower(get("commit.gpgsign"))
	signKey := get("user.signingkey")
	switch {
	case signKey != "":
		f := get("gpg.format")
		if f == "" {
			f = profile.SignOpenPGP
		}
		p.Git.Signing = profile.Signing{Format: f, Key: signKey}
	case gpgsign == "true" || gpgsign == "yes" || gpgsign == "on" || gpgsign == "1":
		p.Git.Signing = profile.Signing{Format: profile.SignDefault}
	}
	if p.Git.Signing.Format != "" {
		notes = append(notes, "signing: "+p.Git.Signing.Format)
	}
	if login, err := gh.ActiveLogin(ctx, ""); err == nil && login != "" {
		p.GitHub.User = login
		notes = append(notes, "gh login "+login)
	}
	switch {
	case p.GitHub.SSHKey != "":
		p.GitHub.Transport = profile.TransportSSH
	case p.GitHub.User != "":
		p.GitHub.Transport = profile.TransportGH
	}
	if p.GitHub.Transport != "" {
		notes = append(notes, "transport "+p.GitHub.Transport)
	}
	return p, notes
}

// SSHKeyFromCommand extracts the -i key from a core.sshCommand value.
func SSHKeyFromCommand(cmd string) string {
	f := strings.Fields(cmd)
	for i, w := range f {
		if w == "-i" && i+1 < len(f) {
			return strings.Trim(f[i+1], `"'`)
		}
		if strings.HasPrefix(w, "-i") && len(w) > 2 {
			return strings.Trim(w[2:], `"'`)
		}
	}
	return ""
}

// LegacyPaths are the git-guard (Python) profile files:
// ~/.claude/git-guard/profile.json and
// <repo>/.claude/git-guard/profile.local.json.
func LegacyPaths(home, repoRoot string) (user, project string) {
	user = filepath.Join(home, ".claude", "git-guard", "profile.json")
	if repoRoot != "" {
		project = filepath.Join(repoRoot, ".claude", "git-guard", "profile.local.json")
	}
	return user, project
}

// FromLegacy maps a git-guard profile document (github_user, github_orgs,
// git_name, git_email, git_transport, ssh_key, signing_key) to a profile.
// signing_key "default" means git's own signing setup; any other value is
// an SSH key path (gpg.format=ssh). Unknown keys are returned.
func FromLegacy(data []byte) (profile.Profile, []string, error) {
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		return profile.Profile{}, nil, err
	}
	var p profile.Profile
	var unknown []string
	str := func(v any) string {
		s, _ := v.(string)
		return strings.TrimSpace(s)
	}
	list := func(v any) profile.StringList {
		var out profile.StringList
		switch t := v.(type) {
		case string:
			for _, s := range strings.Split(t, ",") {
				if s = strings.TrimSpace(s); s != "" {
					out = append(out, s)
				}
			}
		case []any:
			for _, x := range t {
				if s := str(x); s != "" {
					out = append(out, s)
				}
			}
		}
		return out
	}
	for k, v := range doc {
		switch k {
		case "github_user":
			p.GitHub.User = str(v)
		case "github_orgs":
			p.GitHub.Orgs = list(v)
		case "git_name":
			p.Git.Name = str(v)
		case "git_email":
			p.Git.Email = list(v)
		case "git_transport":
			p.GitHub.Transport = str(v)
		case "ssh_key":
			p.GitHub.SSHKey = str(v)
		case "signing_key":
			switch s := str(v); s {
			case "":
			case profile.SignDefault:
				p.Git.Signing = profile.Signing{Format: profile.SignDefault}
			default:
				p.Git.Signing = profile.Signing{Format: profile.SignSSH, Key: s}
			}
		default:
			unknown = append(unknown, k)
		}
	}
	if err := p.Validate(); err != nil {
		return p, unknown, fmt.Errorf("legacy profile: %w", err)
	}
	return p, unknown, nil
}

// MergeProfiles returns base with every non-empty field of over applied.
func MergeProfiles(base, over profile.Profile) profile.Profile {
	if over.Git.Name != "" {
		base.Git.Name = over.Git.Name
	}
	if len(over.Git.Email) > 0 {
		base.Git.Email = over.Git.Email
	}
	if over.Git.Signing.Format != "" {
		base.Git.Signing = over.Git.Signing
	}
	if over.GitHub.User != "" {
		base.GitHub.User = over.GitHub.User
	}
	if len(over.GitHub.Orgs) > 0 {
		base.GitHub.Orgs = over.GitHub.Orgs
	}
	if over.GitHub.Transport != "" {
		base.GitHub.Transport = over.GitHub.Transport
	}
	if over.GitHub.SSHKey != "" {
		base.GitHub.SSHKey = over.GitHub.SSHKey
	}
	if base.GitHub.Transport == profile.TransportGH {
		base.GitHub.SSHKey = ""
	}
	return base
}

// ProfileValue turns a profile into the JSON value stored under
// "profiles.<name>" (empty fields omitted).
func ProfileValue(p profile.Profile) (any, error) {
	b, err := json.Marshal(p)
	if err != nil {
		return nil, err
	}
	var v map[string]any
	if err := json.Unmarshal(b, &v); err != nil {
		return nil, err
	}
	pruneEmpty(v)
	return v, nil
}

func pruneEmpty(m map[string]any) {
	for k, v := range m {
		if sub, ok := v.(map[string]any); ok {
			pruneEmpty(sub)
			if len(sub) == 0 {
				delete(m, k)
			}
		}
	}
}

// OwnerCheck reports whether owner is one of the profile's accounts
// (github.user or github.orgs), case-insensitively. known is false when the
// profile names no accounts at all.
func OwnerCheck(p profile.Profile, owner string) (allowed, known bool) {
	accounts := append([]string{p.GitHub.User}, p.GitHub.Orgs...)
	for _, a := range accounts {
		if a == "" {
			continue
		}
		known = true
		if strings.EqualFold(a, owner) {
			return true, true
		}
	}
	return false, known
}
