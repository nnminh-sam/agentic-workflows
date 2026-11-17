package cli

import (
	"fmt"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/sign"
)

func init() { register(40, newSignCmd) }

func newSignCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "sign", Short: "commit signing: setup, status, verify"}
	cmd.AddCommand(signSetupCmd(a, s), signStatusCmd(a, s), signVerifyCmd(a, s))
	return cmd
}

func fileExists(p string) bool { _, err := os.Stat(p); return err == nil }

func signSetupCmd(a *App, s *session) *cobra.Command {
	var format, key, program string
	var upload, global bool
	cmd := &cobra.Command{
		Use:   "setup",
		Short: "configure commit signing for the active profile (plan)",
		Long: "set gpg.format, user.signingkey and commit.gpgsign (repo-local, or --global / outside a repo), " +
			"write the allowed-signers file for ssh, record the signing setup in the active profile, " +
			"and with --upload add the key to GitHub through gh.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			c, err := e.Require()
			if err != nil {
				return err
			}
			prof := c.Profile
			if format == "" {
				format = prof.Git.Signing.Format
			}
			if format == "" {
				format = sign.SSH
			}
			if key == "" {
				key = prof.Git.Signing.Key
			}
			if program == "" {
				program = prof.Git.Signing.Program
			}
			opts := sign.SetupOptions{Format: format, Program: program, Upload: upload, Global: global || !e.InRepo()}
			if format == sign.SSH && key != "" {
				pub := sign.PublicKeyPath(key, func(p string) bool { return fileExists(sign.ExpandHome(p, e.Home)) })
				data, err := e.ReadFile(sign.ExpandHome(pub, e.Home))
				if err != nil {
					return fmt.Errorf("ssh public key: %w", err)
				}
				key = pub
				opts.PublicKey = string(data)
				opts.Emails = prof.Git.Email
				if len(opts.Emails) == 0 {
					if em, ok, _ := e.Git.ConfigGet(cmd.Context(), "user.email"); ok && strings.TrimSpace(em) != "" {
						opts.Emails = []string{strings.TrimSpace(em)}
					}
				}
				opts.AllowedSignersPath = sign.AllowedSignersPath(e.Paths.GlobalDir)
				if opts.ExistingAllowed, err = e.ReadOptional(opts.AllowedSignersPath); err != nil {
					return err
				}
			}
			opts.Key = key
			if upload && format == sign.OpenPGP && key != "" {
				res, err := p.Read(cmd.Context(), sign.GPGExportCmd(key), "export public key", func(r execx.Result) string {
					if !r.OK() || strings.TrimSpace(r.Stdout) == "" {
						return "no such key"
					}
					return "armored key"
				})
				if err != nil {
					return err
				}
				if !res.OK() || strings.TrimSpace(res.Stdout) == "" {
					return fmt.Errorf("gpg has no public key %s", key)
				}
				opts.ArmoredKeyPath = p.File("asc", res.Stdout)
			}
			steps, err := sign.SetupSteps(opts)
			if err != nil {
				return err
			}
			if err := p.Add(app.FromSignSteps(steps)); err != nil {
				return err
			}
			if c.ProfileName != "" && inGlobal(c, c.ProfileName) {
				sv := map[string]any{"format": format}
				if key != "" && format != sign.Default && format != sign.None {
					sv["key"] = key
				}
				if program != "" {
					sv["program"] = program
				}
				if _, err := configEdit(p, config.ScopeGlobal,
					fmt.Sprintf("profile %s signing %s in %s", c.ProfileName, format, e.Display(e.Paths.Global)),
					setKey(config.ScopeGlobal, "profiles."+c.ProfileName+".git.signing", sv)); err != nil {
					return err
				}
			} else if c.ProfileName != "" {
				p.Note("profile %s is not in the global config: update its git.signing yourself", c.ProfileName)
			} else {
				p.Note("no active profile: signing is set in git config only")
			}
			if upload && format == sign.SSH {
				p.Note("gh needs the admin:ssh_signing_key scope: gh auth refresh -s admin:ssh_signing_key")
			}
			return s.run(cmd, p, nil)
		},
	}
	f := cmd.Flags()
	f.StringVar(&format, "format", "", "ssh|openpgp|x509|default|none (default: the profile's, else ssh)")
	f.StringVar(&key, "key", "", "signing key: SSH key path (.pub is used when present) or gpg key id")
	f.StringVar(&program, "program", "", "gpg.program (openpgp) or gpg.x509.program (x509)")
	f.BoolVar(&upload, "upload", false, "add the key to the GitHub account through gh")
	f.BoolVar(&global, "global", false, "write git config --global")
	s.addOutFlag(cmd)
	return cmd
}

func signStatusCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "show the signing config git uses",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, err := s.env(cmd)
			if err != nil {
				return err
			}
			st, err := sign.ReadStatus(cmd.Context(), e.Git)
			if err != nil {
				return err
			}
			want := ""
			if e.Config != nil {
				want = e.Config.Profile.Git.Signing.Format
			}
			keyOK := st.Key == "" || st.Format != sign.SSH || strings.HasPrefix(st.Key, "key::") ||
				fileExists(sign.ExpandHome(st.Key, e.Home))
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"git": st, "profileFormat": want, "keyFound": keyOK})
			}
			format := st.Format
			if format == "" {
				format = "openpgp (git default)"
			}
			a.Printf("%s commit.gpgsign              %v\n", mark(st.GPGSign || want == "" || want == sign.None), st.GPGSign)
			a.Printf("  gpg.format                  %s\n", format)
			if st.Key != "" {
				a.Printf("%s user.signingkey             %s\n", mark(keyOK), st.Key)
			}
			if st.Program != "" {
				a.Printf("  program                     %s\n", st.Program)
			}
			if st.Format == sign.SSH {
				a.Printf("%s gpg.ssh.allowedSignersFile  %s\n", mark(st.AllowedSignersFile != ""), orDash(st.AllowedSignersFile))
			}
			if want != "" {
				a.Printf("  profile signing             %s\n", want)
			}
			return nil
		},
	}
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func signVerifyCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "verify [range]",
		Short: "list unsigned or badly signed commits (exit 2 if any)",
		Long:  "check signatures of a commit range (default: <staging branch>..HEAD, else the last 20 commits).",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			e, err := s.env(cmd)
			if err != nil {
				return err
			}
			if err := e.RequireRepo(); err != nil {
				return err
			}
			rng := ""
			if len(args) == 1 {
				rng = args[0]
			} else {
				rng = defaultVerifyRange(cmd, e)
			}
			var commits []sign.Commit
			if rng == "" {
				res, err := e.Git.Run(cmd.Context(), append(sign.LogArgs(""), "-n", "20")...)
				if err != nil {
					return err
				}
				commits, rng = sign.ParseLog(res.Stdout), "last 20 commits"
			} else if commits, err = sign.Verify(cmd.Context(), e.Git, rng); err != nil {
				return err
			}
			bad := sign.Problems(commits)
			if a.Opts.JSON {
				if err := emitJSON(a.Out, map[string]any{"range": rng, "commits": commits, "problems": bad,
					"ok": len(bad) == 0}); err != nil {
					return err
				}
			} else {
				for _, c := range bad {
					a.Printf("✗ %s  %-40s  %s\n", short(c.SHA), sign.Describe(c.Status), c.Subject)
				}
				a.Printf("%s %d/%d signed   %s\n", mark(len(bad) == 0), len(commits)-len(bad), len(commits), rng)
			}
			if len(bad) > 0 {
				return Exit(2)
			}
			return nil
		},
	}
}

func defaultVerifyRange(cmd *cobra.Command, e *app.Env) string {
	if e.Config == nil || e.Config.Workflow == nil || e.Config.Workflow.Staging == "" {
		return ""
	}
	st := e.Config.Workflow.Staging
	if ok, err := e.Git.BranchExists(cmd.Context(), st); err != nil || !ok {
		return ""
	}
	if cur, _ := e.Git.CurrentBranch(cmd.Context()); cur == st {
		return ""
	}
	return st + "..HEAD"
}

func short(sha string) string {
	if len(sha) > 7 {
		return sha[:7]
	}
	return sha
}

// inGlobal reports whether the global config file defines profile name.
func inGlobal(c *config.Resolved, name string) bool {
	for _, l := range c.Layers {
		if l.Scope == config.ScopeGlobal {
			_, ok := l.File.Profiles[name]
			return ok
		}
	}
	return false
}
