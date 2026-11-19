// Package sign sets up, inspects and verifies commit signing: the git
// config a signing format needs (gpg.format, user.signingkey,
// commit.gpgsign), the SSH allowed-signers file git uses to verify SSH
// signatures, uploading the key to GitHub through gh, and checking that a
// range of commits is signed.
//
// Setup is declarative (SetupSteps returns actions another package turns
// into a plan); Status and Verify only read.
package sign

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
)

// Signing formats.
const (
	SSH     = "ssh"
	OpenPGP = "openpgp"
	X509    = "x509"
	Default = "default"
	None    = "none"
)

// Formats lists the accepted --format values.
func Formats() []string { return []string{SSH, OpenPGP, X509, Default, None} }

// Step kinds (the same strings profile.Step uses).
const (
	KindGitConfig = "git-config"
	KindGH        = "gh"
	KindWriteFile = "write-file"
)

// Step is one declarative setup action. Args follow the program name: git
// args, gh args, or [path, content] for write-file.
type Step struct {
	Kind        string
	Args        []string
	Description string
}

// SetupOptions describe one `mygit sign setup`.
type SetupOptions struct {
	Format string
	// Key is the signing key: an SSH public key path for ssh, a key id for
	// openpgp, a certificate id for x509.
	Key string
	// Program is gpg.program (openpgp) or gpg.x509.program (x509).
	Program string
	// Global writes git config with --global instead of --local.
	Global bool
	// SSH only: the public key content and the principals (emails) to
	// trust it for in the allowed-signers file.
	PublicKey          string
	Emails             []string
	AllowedSignersPath string
	ExistingAllowed    []byte
	// Upload adds the key to the GitHub account through gh. For openpgp,
	// ArmoredKeyPath is a file holding `gpg --armor --export KEY`.
	Upload         bool
	ArmoredKeyPath string
}

// AllowedSignersPath is the allowed-signers file in the mygit config dir
// (~/.config/mygit/allowed_signers).
func AllowedSignersPath(globalDir string) string {
	return filepath.Join(globalDir, "allowed_signers")
}

// SetupSteps returns the actions that set up signing for o.Format.
func SetupSteps(o SetupOptions) ([]Step, error) {
	scope := "--local"
	if o.Global {
		scope = "--global"
	}
	cfg := func(k, v string) Step {
		return Step{Kind: KindGitConfig, Args: []string{"config", scope, k, v},
			Description: "git config " + k + " " + v}
	}
	var steps []Step
	switch o.Format {
	case None:
		return []Step{cfg("commit.gpgsign", "false")}, nil
	case Default:
		return []Step{cfg("commit.gpgsign", "true")}, nil
	case SSH, OpenPGP, X509:
	default:
		return nil, fmt.Errorf("signing format %q is not one of %s", o.Format, strings.Join(Formats(), ", "))
	}
	if o.Key == "" && o.Format != X509 {
		return nil, fmt.Errorf("signing format %s needs --key", o.Format)
	}
	steps = append(steps, cfg("gpg.format", o.Format))
	if o.Key != "" {
		steps = append(steps, cfg("user.signingkey", o.Key))
	}
	switch {
	case o.Program != "" && o.Format == OpenPGP:
		steps = append(steps, cfg("gpg.program", o.Program))
	case o.Program != "" && o.Format == X509:
		steps = append(steps, cfg("gpg.x509.program", o.Program))
	}
	steps = append(steps, cfg("commit.gpgsign", "true"))
	if o.Format == SSH {
		if o.AllowedSignersPath == "" {
			return nil, errors.New("ssh signing needs an allowed-signers path")
		}
		if len(o.Emails) == 0 {
			return nil, errors.New("ssh signing needs an email for the allowed-signers file (set git.email in the profile or user.email)")
		}
		content, err := MergeAllowedSigners(o.ExistingAllowed, o.Emails, o.PublicKey)
		if err != nil {
			return nil, err
		}
		steps = append(steps,
			Step{Kind: KindWriteFile, Args: []string{o.AllowedSignersPath, content},
				Description: "wrote " + o.AllowedSignersPath},
			cfg("gpg.ssh.allowedSignersFile", o.AllowedSignersPath))
	}
	if o.Upload {
		switch o.Format {
		case SSH:
			steps = append(steps, Step{Kind: KindGH,
				Args:        []string{"ssh-key", "add", o.Key, "--type", "signing", "--title", "mygit signing key"},
				Description: "gh ssh-key add " + o.Key + " --type signing"})
		case OpenPGP:
			if o.ArmoredKeyPath == "" {
				return nil, errors.New("openpgp upload needs the exported public key")
			}
			steps = append(steps, Step{Kind: KindGH, Args: []string{"gpg-key", "add", o.ArmoredKeyPath},
				Description: "gh gpg-key add (key " + o.Key + ")"})
		default:
			return nil, fmt.Errorf("--upload is not supported for %s keys", o.Format)
		}
	}
	return steps, nil
}

// GPGExportCmd exports an OpenPGP public key in ASCII armor (a read step).
func GPGExportCmd(key string) execx.Cmd {
	return execx.Cmd{Name: "gpg", Args: []string{"--armor", "--export", key}}
}

// MergeAllowedSigners returns the allowed-signers file with one line
// `<email> namespaces="git" <key>` per email. Lines for the same email and
// key are replaced; every other line is kept.
func MergeAllowedSigners(existing []byte, emails []string, publicKey string) (string, error) {
	key := strings.TrimSpace(publicKey)
	f := strings.Fields(key)
	if len(f) < 2 || !strings.HasPrefix(f[0], "ssh-") && !strings.HasPrefix(f[0], "ecdsa-") && !strings.HasPrefix(f[0], "sk-") {
		return "", errors.New("not an SSH public key (want e.g. \"ssh-ed25519 AAAA… comment\")")
	}
	keyID := f[0] + " " + f[1]
	want := map[string]bool{}
	for _, e := range emails {
		want[strings.ToLower(e)] = true
	}
	var lines []string
	for _, l := range strings.Split(strings.ReplaceAll(string(existing), "\r\n", "\n"), "\n") {
		t := strings.TrimSpace(l)
		if t == "" {
			continue
		}
		if !strings.HasPrefix(t, "#") {
			fs := strings.Fields(t)
			if len(fs) > 0 && want[strings.ToLower(fs[0])] && strings.Contains(t, keyID) {
				continue
			}
		}
		lines = append(lines, l)
	}
	for _, e := range emails {
		lines = append(lines, fmt.Sprintf("%s namespaces=\"git\" %s", e, keyID))
	}
	return strings.Join(lines, "\n") + "\n", nil
}

// PublicKeyPath returns the .pub path for an SSH key path: path itself when
// it ends in .pub, else path+".pub" when exists reports it, else path.
func PublicKeyPath(path string, exists func(string) bool) string {
	if strings.HasSuffix(path, ".pub") {
		return path
	}
	if exists != nil && exists(path+".pub") {
		return path + ".pub"
	}
	return path
}

// ExpandHome replaces a leading ~ with home.
func ExpandHome(p, home string) string {
	if p == "~" {
		return home
	}
	if strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		return filepath.Join(home, p[2:])
	}
	return p
}

// Status is the signing configuration git sees.
type Status struct {
	Format             string `json:"format"` // gpg.format ("" = openpgp default)
	Key                string `json:"key,omitempty"`
	GPGSign            bool   `json:"gpgsign"`
	AllowedSignersFile string `json:"allowedSignersFile,omitempty"`
	Program            string `json:"program,omitempty"`
}

// ReadStatus reads the effective signing config through g.
func ReadStatus(ctx context.Context, g *gitx.Git) (Status, error) {
	var s Status
	get := func(k string) (string, error) {
		v, _, err := g.ConfigGet(ctx, k)
		return strings.TrimSpace(v), err
	}
	var err error
	if s.Format, err = get("gpg.format"); err != nil {
		return s, err
	}
	if s.Key, err = get("user.signingkey"); err != nil {
		return s, err
	}
	v, err := get("commit.gpgsign")
	if err != nil {
		return s, err
	}
	switch strings.ToLower(v) {
	case "true", "yes", "on", "1":
		s.GPGSign = true
	}
	if s.AllowedSignersFile, err = get("gpg.ssh.allowedSignersFile"); err != nil {
		return s, err
	}
	prog := "gpg.program"
	if s.Format == X509 {
		prog = "gpg.x509.program"
	}
	s.Program, err = get(prog)
	return s, err
}

// Commit is one commit's signature state from `git log %G?`.
type Commit struct {
	SHA     string `json:"sha"`
	Status  string `json:"status"` // the %G? letter
	Signer  string `json:"signer,omitempty"`
	Subject string `json:"subject"`
}

// OK reports a good signature (G, or U: good but unknown validity; X/Y:
// good but expired).
func (c Commit) OK() bool {
	switch c.Status {
	case "G", "U", "X", "Y":
		return true
	}
	return false
}

// Describe explains a %G? letter.
func Describe(status string) string {
	switch status {
	case "G":
		return "good"
	case "U":
		return "good, unknown validity"
	case "X":
		return "good, signature expired"
	case "Y":
		return "good, key expired"
	case "R":
		return "revoked key"
	case "E":
		return "cannot be checked (missing key or allowed signers)"
	case "B":
		return "bad signature"
	case "N":
		return "unsigned"
	}
	return "unknown status " + status
}

// LogArgs are the git log arguments Verify runs for rng.
func LogArgs(rng string) []string {
	args := []string{"log", "--format=%H%x1f%G?%x1f%GS%x1f%s"}
	if rng != "" {
		args = append(args, rng)
	}
	return args
}

// Verify lists the commits of rng with their signature state.
func Verify(ctx context.Context, g *gitx.Git, rng string) ([]Commit, error) {
	res, err := g.Run(ctx, LogArgs(rng)...)
	if err != nil {
		return nil, err
	}
	return ParseLog(res.Stdout), nil
}

// ParseLog parses `git log --format=%H%x1f%G?%x1f%GS%x1f%s` output.
func ParseLog(out string) []Commit {
	var cs []Commit
	for _, l := range strings.Split(strings.ReplaceAll(out, "\r\n", "\n"), "\n") {
		if strings.TrimSpace(l) == "" {
			continue
		}
		f := strings.SplitN(l, "\x1f", 4)
		for len(f) < 4 {
			f = append(f, "")
		}
		cs = append(cs, Commit{SHA: f[0], Status: f[1], Signer: f[2], Subject: f[3]})
	}
	return cs
}

// Problems returns the commits without a good signature.
func Problems(cs []Commit) []Commit {
	var out []Commit
	for _, c := range cs {
		if !c.OK() {
			out = append(out, c)
		}
	}
	return out
}

// Counts summarises commits by %G? letter, e.g. "G=3 N=1".
func Counts(cs []Commit) string {
	m := map[string]int{}
	for _, c := range cs {
		m[c.Status]++
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, len(keys))
	for i, k := range keys {
		parts[i] = fmt.Sprintf("%s=%d", k, m[k])
	}
	return strings.Join(parts, " ")
}
