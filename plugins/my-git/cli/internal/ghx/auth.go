package ghx

import (
	"context"
	"fmt"
	"regexp"
	"strings"
)

// Account is one login reported by `gh auth status`.
type Account struct {
	Host     string
	Login    string
	Active   bool     // the account gh uses for this host
	OK       bool     // ✓ logged in (false: ✗ token invalid / failed)
	Protocol string   // git operations protocol: https or ssh
	Scopes   []string // token scopes, when reported
}

// AuthStatus runs `gh auth status` and parses every account on every host.
// gh exits non-zero when any account has a problem, so the exit code is
// ignored as long as something could be parsed.
func (g *Gh) AuthStatus(ctx context.Context) ([]Account, error) {
	res, err := g.Exec(ctx, "auth", "status")
	if err != nil {
		return nil, err
	}
	accts := ParseAuthStatus(res.Stdout + "\n" + res.Stderr)
	if len(accts) == 0 && !res.OK() {
		return nil, &ExitError{Cmd: g.Cmd("auth", "status"), Result: res}
	}
	return accts, nil
}

var (
	// "✓ Logged in to github.com account octo (keyring)"   (gh ≥ 2.40)
	// "✓ Logged in to github.com as octo (oauth_token)"     (older gh)
	// "X Failed to log in to github.com account octo (GH_TOKEN)"
	loginRe  = regexp.MustCompile(`(?i)(logged in|failed to log in) to (\S+) (?:account|as) (\S+)`)
	activeRe = regexp.MustCompile(`(?i)active account:\s*(true|false)`)
	// "- Git operations protocol: https"                                (gh ≥ 2.40)
	// "✓ Git operations for github.com configured to use https protocol." (older gh)
	protoRe  = regexp.MustCompile(`(?i)git operations (?:protocol:\s*|for \S+ configured to use )([a-z]+)`)
	scopesRe = regexp.MustCompile(`(?i)token scopes:\s*(.*)$`)
)

// ParseAuthStatus parses the text of `gh auth status` (old and new formats).
// With the old single-account format every account is marked active.
func ParseAuthStatus(out string) []Account {
	var accts []Account
	sawActiveLine := false
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if m := loginRe.FindStringSubmatch(line); m != nil {
			accts = append(accts, Account{
				Host:  m[2],
				Login: m[3],
				OK:    strings.EqualFold(m[1], "logged in"),
			})
			continue
		}
		if len(accts) == 0 {
			continue
		}
		cur := &accts[len(accts)-1]
		if m := activeRe.FindStringSubmatch(line); m != nil {
			sawActiveLine = true
			cur.Active = strings.EqualFold(m[1], "true")
		} else if m := protoRe.FindStringSubmatch(line); m != nil {
			cur.Protocol = m[1]
		} else if m := scopesRe.FindStringSubmatch(line); m != nil {
			cur.Scopes = parseScopes(m[1])
		}
	}
	if !sawActiveLine {
		for i := range accts {
			accts[i].Active = true
		}
	}
	return accts
}

func parseScopes(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.Trim(strings.TrimSpace(p), `'"`); p != "" && p != "none" {
			out = append(out, p)
		}
	}
	return out
}

// ActiveLogin returns the login gh acts as for host ("" = github.com).
// It asks `gh api user --jq .login` first and falls back to the active
// account in `gh auth status`. An active account whose token is invalid
// returns "" and an error, so it never counts as logged in.
func (g *Gh) ActiveLogin(ctx context.Context, host string) (string, error) {
	args := []string{"api", "user", "--jq", ".login"}
	if host != "" && host != "github.com" {
		args = append(args, "--hostname", host)
	}
	if login, err := g.Query(ctx, args...); err == nil && login != "" {
		return login, nil
	}
	accts, err := g.AuthStatus(ctx)
	if err != nil {
		return "", err
	}
	if host == "" {
		host = "github.com"
	}
	for _, a := range accts {
		if a.Active && strings.EqualFold(a.Host, host) {
			if !a.OK {
				return "", fmt.Errorf("gh account %s on %s is not logged in (invalid token); run gh auth login -h %s", a.Login, a.Host, a.Host)
			}
			return a.Login, nil
		}
	}
	return "", nil
}
