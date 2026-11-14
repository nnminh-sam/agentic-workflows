// Package profile models a named identity (git author, signing, GitHub
// account and transport), selects the active profile, turns it into the
// ${var} variables the rule params reference, and describes the git/gh
// settings that apply it. The package is pure: it never runs processes or
// touches files; callers pass in env, paths and existing file contents.
package profile

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Signing formats.
const (
	SignSSH     = "ssh"
	SignOpenPGP = "openpgp"
	SignX509    = "x509"
	SignDefault = "default"
	SignNone    = "none"
)

// Transports.
const (
	TransportSSH = "ssh"
	TransportGH  = "gh"
)

// Profile is one named identity from the "profiles" map of a config file.
type Profile struct {
	Git    Git    `json:"git"`
	GitHub GitHub `json:"github"`
}

// Git holds the author identity and signing setup.
type Git struct {
	Name    string     `json:"name,omitempty"`
	Email   StringList `json:"email,omitempty"`
	Signing Signing    `json:"signing"`
}

// Signing describes how commits are signed.
type Signing struct {
	// Format is ssh, openpgp, x509, default (git's own setup) or none.
	Format string `json:"format,omitempty"`
	// Key is the signing key: a .pub path for ssh, a key id for openpgp.
	Key string `json:"key,omitempty"`
	// Program is gpg.program (openpgp) or gpg.x509.program (x509).
	Program string `json:"program,omitempty"`
}

// GitHub holds the GitHub account and how git authenticates to it.
type GitHub struct {
	User string     `json:"user,omitempty"`
	Orgs StringList `json:"orgs,omitempty"`
	// Transport is "ssh" (pinned key) or "gh" (gh credential helper).
	Transport string `json:"transport,omitempty"`
	SSHKey    string `json:"sshKey,omitempty"`
}

// StringList is a list of strings that also accepts a single string in JSON.
type StringList []string

// UnmarshalJSON accepts "a" or ["a", "b"].
func (l *StringList) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		if s == "" {
			*l = nil
		} else {
			*l = StringList{s}
		}
		return nil
	}
	var list []string
	if err := json.Unmarshal(b, &list); err != nil {
		return fmt.Errorf("want a string or a list of strings")
	}
	*l = list
	return nil
}

// IsZero reports whether no field is set.
func (p Profile) IsZero() bool {
	return p.Git.Name == "" && len(p.Git.Email) == 0 && p.Git.Signing == (Signing{}) &&
		p.GitHub.User == "" && len(p.GitHub.Orgs) == 0 && p.GitHub.Transport == "" && p.GitHub.SSHKey == ""
}

// Validate checks the enumerated fields.
func (p Profile) Validate() error {
	switch p.Git.Signing.Format {
	case "", SignSSH, SignOpenPGP, SignX509, SignDefault, SignNone:
	default:
		return fmt.Errorf("git.signing.format: %q is not one of ssh, openpgp, x509, default, none", p.Git.Signing.Format)
	}
	switch p.GitHub.Transport {
	case "", TransportSSH, TransportGH:
	default:
		return fmt.Errorf("github.transport: %q is not one of ssh, gh", p.GitHub.Transport)
	}
	return nil
}

// Vars returns the ${var} variables of p, keyed by the Python names
// (github_user, github_orgs, git_name, git_email, git_transport, ssh_key,
// signing_key, signing_format) and by dotted aliases (github.user,
// github.orgs, github.transport, github.sshKey, git.name, git.email,
// git.signing.key, git.signing.format). Unset values are omitted.
// Strings are string; lists are []string.
func Vars(p Profile) map[string]any {
	v := map[string]any{}
	setS := func(val string, keys ...string) {
		if val = strings.TrimSpace(val); val != "" {
			for _, k := range keys {
				v[k] = val
			}
		}
	}
	setL := func(val []string, keys ...string) {
		if l := clean(val); len(l) > 0 {
			for _, k := range keys {
				v[k] = l
			}
		}
	}
	setS(p.GitHub.User, "github_user", "github.user")
	setL(p.GitHub.Orgs, "github_orgs", "github.orgs")
	setS(p.Git.Name, "git_name", "git.name")
	setL(p.Git.Email, "git_email", "git.email")
	setS(p.GitHub.Transport, "git_transport", "github.transport")
	setS(p.GitHub.SSHKey, "ssh_key", "github.sshKey")
	setS(p.Git.Signing.Format, "signing_format", "git.signing.format")
	setS(signingKeyVar(p.Git.Signing), "signing_key")
	setS(p.Git.Signing.Key, "git.signing.key")
	return v
}

// signingKeyVar maps signing to the Python signing_key variable: "default"
// for git's own setup, a key path otherwise, unset for none.
func signingKeyVar(s Signing) string {
	switch {
	case s.Format == SignNone:
		return ""
	case s.Key != "":
		return s.Key
	case s.Format == SignDefault:
		return SignDefault
	}
	return ""
}

func clean(l []string) []string {
	var out []string
	for _, s := range l {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}
