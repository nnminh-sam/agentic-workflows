package profile

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Step kinds. KindGitConfig steps carry arguments of `git` (usually
// `config …`, also `remote set-url …`).
const (
	KindGitConfig = "git-config"
	KindGH        = "gh"
	KindWriteFile = "write-file"
)

// Step is one declarative action of applying a profile. Args is the argument
// list after the program name: for "git-config" the arguments of `git`
// (e.g. config --local user.name "Octo Cat"), for "gh" the arguments of
// `gh`, and for "write-file" [path, content]. Another package turns steps
// into an executable plan.
type Step struct {
	Kind        string   `json:"kind"`
	Args        []string `json:"args"`
	Description string   `json:"description"`
}

// ApplyOptions tune ApplySteps.
type ApplyOptions struct {
	// Name is the profile name recorded in the local config file.
	Name string
	// Global writes git config with --global instead of --local, and skips
	// the local config file.
	Global bool
	// LocalConfigPath is <repo>/.mygit.local.json; "" skips writing it.
	LocalConfigPath string
	// ExistingLocal is the current content of LocalConfigPath (nil if absent);
	// its other keys are preserved.
	ExistingLocal []byte
	// RemoteName and RemoteURL are the repository's remote (name defaults
	// to "origin") and its current URL. With transport gh and an SSH URL, a
	// step rewrites it to HTTPS; an empty URL skips that step.
	RemoteName string
	RemoteURL  string
}

// ApplySteps returns the ordered steps that apply p: author identity, SSH
// key pinning, signing, gh account and credential helper, then recording
// the choice in .mygit.local.json. It is pure and never executes anything.
func ApplySteps(p Profile, opts ApplyOptions) ([]Step, error) {
	if err := p.Validate(); err != nil {
		return nil, err
	}
	scope := "--local"
	if opts.Global {
		scope = "--global"
	}
	cfg := func(key, value string) Step {
		return Step{Kind: KindGitConfig, Args: []string{"config", scope, key, value},
			Description: fmt.Sprintf("git config %s %s", key, value)}
	}
	var steps []Step
	if p.Git.Name != "" {
		steps = append(steps, cfg("user.name", p.Git.Name))
	}
	if len(p.Git.Email) > 0 {
		steps = append(steps, cfg("user.email", p.Git.Email[0]))
	}
	if p.GitHub.Transport != TransportGH && p.GitHub.SSHKey != "" {
		steps = append(steps, cfg("core.sshCommand", SSHCommand(p.GitHub.SSHKey)))
	}
	for _, kv := range SigningConfig(p.Git.Signing) {
		steps = append(steps, cfg(kv[0], kv[1]))
	}
	if p.GitHub.User != "" {
		steps = append(steps, Step{Kind: KindGH, Args: []string{"auth", "switch", "--user", p.GitHub.User},
			Description: "gh auth switch --user " + p.GitHub.User})
	}
	if p.GitHub.Transport == TransportGH {
		steps = append(steps, Step{Kind: KindGH, Args: []string{"auth", "setup-git"},
			Description: "gh auth setup-git (git authenticates through gh)"})
	}
	if step, ok := remoteHTTPSStep(p, opts); ok {
		steps = append(steps, step)
	}
	if !opts.Global && opts.LocalConfigPath != "" && opts.Name != "" {
		content, err := localConfig(opts.ExistingLocal, opts.Name)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", opts.LocalConfigPath, err)
		}
		steps = append(steps, Step{Kind: KindWriteFile, Args: []string{opts.LocalConfigPath, content},
			Description: fmt.Sprintf("write %s {\"profile\": %q}", opts.LocalConfigPath, opts.Name)})
	}
	return steps, nil
}

// remoteHTTPSStep rewrites an SSH remote to HTTPS for transport gh, so
// pushes authenticate through the gh credential helper. It is repo-local,
// so a Global apply skips it.
func remoteHTTPSStep(p Profile, opts ApplyOptions) (Step, bool) {
	if p.GitHub.Transport != TransportGH || opts.Global || opts.RemoteURL == "" {
		return Step{}, false
	}
	https, ok := SSHToHTTPS(opts.RemoteURL)
	if !ok {
		return Step{}, false
	}
	name := opts.RemoteName
	if name == "" {
		name = "origin"
	}
	return Step{Kind: KindGitConfig, Args: []string{"remote", "set-url", name, https},
		Description: fmt.Sprintf("git remote set-url %s %s (was %s)", name, https, opts.RemoteURL)}, true
}

// SSHToHTTPS converts an SSH remote URL (git@host:owner/repo.git,
// ssh://[user@]host[:port]/owner/repo.git, git+ssh://…) into
// https://host/owner/repo.git. ok is false for non-SSH or malformed URLs.
func SSHToHTTPS(raw string) (string, bool) {
	u := strings.TrimSpace(raw)
	var host, path string
	switch {
	case strings.HasPrefix(u, "ssh://"), strings.HasPrefix(u, "git+ssh://"), strings.HasPrefix(u, "ssh+git://"):
		rest := u[strings.Index(u, "://")+3:]
		slash := strings.IndexByte(rest, '/')
		if slash < 0 {
			return "", false
		}
		host, path = rest[:slash], rest[slash+1:]
		if i := strings.LastIndexByte(host, ':'); i >= 0 {
			host = host[:i] // drop the SSH port; HTTPS uses its own
		}
	case !strings.Contains(u, "://"):
		colon := strings.IndexByte(u, ':')
		if colon <= 0 || strings.Contains(u[:colon], "/") {
			return "", false // a local path, not scp-like syntax
		}
		host, path = u[:colon], u[colon+1:]
	default:
		return "", false
	}
	if i := strings.LastIndexByte(host, '@'); i >= 0 {
		host = host[i+1:]
	}
	path = strings.TrimSuffix(strings.Trim(path, "/"), ".git")
	if host == "" || !strings.Contains(path, "/") {
		return "", false
	}
	return "https://" + host + "/" + path + ".git", true
}

// SSHCommand is the core.sshCommand that pins key.
func SSHCommand(key string) string {
	return "ssh -i " + key + " -o IdentitiesOnly=yes"
}

// SigningConfig returns the git config key/value pairs for s, in order.
// Format "" leaves signing alone; "none" turns commit signing off.
func SigningConfig(s Signing) [][2]string {
	var kv [][2]string
	add := func(k, v string) { kv = append(kv, [2]string{k, v}) }
	switch s.Format {
	case "":
		return nil
	case SignNone:
		add("commit.gpgsign", "false")
		return kv
	case SignDefault:
		add("commit.gpgsign", "true")
		return kv
	}
	add("commit.gpgsign", "true")
	add("gpg.format", s.Format)
	if s.Key != "" {
		add("user.signingkey", s.Key)
	}
	switch {
	case s.Program != "" && s.Format == SignOpenPGP:
		add("gpg.program", s.Program)
	case s.Program != "" && s.Format == SignX509:
		add("gpg.x509.program", s.Program)
	}
	return kv
}

// localConfig sets "profile" in the existing local config JSON.
func localConfig(existing []byte, name string) (string, error) {
	doc := map[string]any{}
	if len(existing) > 0 {
		if err := json.Unmarshal(existing, &doc); err != nil {
			return "", err
		}
		if doc == nil {
			doc = map[string]any{}
		}
	}
	doc["profile"] = name
	out, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return "", err
	}
	return string(out) + "\n", nil
}
