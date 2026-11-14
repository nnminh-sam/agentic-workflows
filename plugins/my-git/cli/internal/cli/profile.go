package cli

import (
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/spf13/cobra"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/profile"
)

func init() { register(30, newProfileCmd) }

func newProfileCmd(a *App) *cobra.Command {
	s := newSession(a)
	cmd := &cobra.Command{Use: "profile", Short: "named identities: git author, signing, GitHub account"}
	cmd.AddCommand(profileListCmd(a, s), profileShowCmd(a, s), profileAddCmd(a, s), profileRmCmd(s),
		profileUseCmd(a, s), profileCurrentCmd(a, s), profileImportCmd(a, s))
	return cmd
}

// profileOrigins maps each profile name to the layer defining it.
func profileOrigins(c *config.Resolved) map[string]string {
	out := map[string]string{}
	for _, l := range c.Layers {
		for n := range l.File.Profiles {
			out[n] = l.Origin
		}
	}
	return out
}

func sortedProfileNames(m map[string]profile.Profile) []string {
	out := make([]string, 0, len(m))
	for n := range m {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

func profileListCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "list profiles (* = active)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			origins := profileOrigins(c)
			type row struct {
				Name   string `json:"name"`
				Active bool   `json:"active"`
				User   string `json:"githubUser,omitempty"`
				Email  string `json:"email,omitempty"`
				Origin string `json:"origin"`
			}
			var rows []row
			for _, n := range sortedProfileNames(c.File.Profiles) {
				p := c.File.Profiles[n]
				r := row{Name: n, Active: n == c.ProfileName, User: p.GitHub.User, Origin: origins[n]}
				if len(p.Git.Email) > 0 {
					r.Email = p.Git.Email[0]
				}
				rows = append(rows, r)
			}
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"profiles": rows, "active": c.ProfileName, "reason": c.ProfileReason})
			}
			if len(rows) == 0 {
				a.Printf("no profiles; add one: mygit profile add <name> --from-current\n")
				return nil
			}
			for _, r := range rows {
				star := " "
				if r.Active {
					star = "*"
				}
				a.Printf("%s %-12s  %-16s  %-32s  %s\n", star, r.Name, r.User, r.Email, r.Origin)
			}
			return nil
		},
	}
}

// printProfile writes a profile as aligned field lines.
func printProfile(w io.Writer, p profile.Profile, origins map[string]string) {
	line := func(k, v string) {
		if v == "" {
			return
		}
		if o := origins[k]; o != "" {
			v += "   (" + o + ")"
		}
		fmt.Fprintf(w, "  %-18s  %s\n", k, v)
	}
	line("git.name", p.Git.Name)
	line("git.email", strings.Join(p.Git.Email, ", "))
	line("git.signing.format", p.Git.Signing.Format)
	line("git.signing.key", p.Git.Signing.Key)
	line("git.signing.program", p.Git.Signing.Program)
	line("github.user", p.GitHub.User)
	line("github.orgs", strings.Join(p.GitHub.Orgs, ", "))
	line("github.transport", p.GitHub.Transport)
	line("github.sshKey", p.GitHub.SSHKey)
}

func profileShowCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "show [name]",
		Short: "print a profile (default: the active one)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			name, reason, p, origins := c.ProfileName, c.ProfileReason, c.Profile, c.ProfileOrigins
			if len(args) == 1 && args[0] != c.ProfileName {
				name, reason, origins = args[0], "", nil
				if p, err = profile.Lookup(c.File.Profiles, name, "argument"); err != nil {
					return err
				}
			}
			if name == "" {
				return &ExitError{Code: 1, Err: fmt.Errorf("no active profile (identity rules use presence checks)")}
			}
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"name": name, "reason": reason, "profile": p,
					"origin": profileOrigins(c)[name], "overrides": origins})
			}
			head := "profile " + name
			if reason != "" {
				head += "   (" + reason + ")"
			}
			a.Printf("%s\n", head)
			printProfile(a.Out, p, origins)
			return nil
		},
	}
}

func profileCurrentCmd(a *App, s *session) *cobra.Command {
	return &cobra.Command{
		Use:   "current",
		Short: "print the active profile and why it was chosen",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, c, err := s.config(cmd)
			if err != nil {
				return err
			}
			if a.Opts.JSON {
				return emitJSON(a.Out, map[string]any{"name": c.ProfileName, "reason": c.ProfileReason})
			}
			if c.ProfileName == "" {
				a.Printf("none\n")
				return nil
			}
			a.Printf("%s   (%s)\n", c.ProfileName, c.ProfileReason)
			return nil
		},
	}
}

// profileFlags are the field flags of `profile add`.
type profileFlags struct {
	fromCurrent bool
	gitName     string
	gitEmails   []string
	githubUser  string
	orgs        []string
	transport   string
	sshKey      string
	signFormat  string
	signKey     string
}

func (f *profileFlags) apply(p profile.Profile) profile.Profile {
	over := profile.Profile{}
	over.Git.Name = f.gitName
	over.Git.Email = f.gitEmails
	over.GitHub.User = f.githubUser
	over.GitHub.Orgs = f.orgs
	over.GitHub.Transport = f.transport
	over.GitHub.SSHKey = f.sshKey
	if f.signFormat != "" || f.signKey != "" {
		over.Git.Signing = profile.Signing{Format: f.signFormat, Key: f.signKey}
		if over.Git.Signing.Format == "" {
			over.Git.Signing.Format = profile.SignSSH
		}
	}
	if over.GitHub.SSHKey != "" && over.GitHub.Transport == "" && p.GitHub.Transport == "" {
		over.GitHub.Transport = profile.TransportSSH
	}
	return app.MergeProfiles(p, over)
}

func profileAddCmd(a *App, s *session) *cobra.Command {
	f := &profileFlags{}
	cmd := &cobra.Command{
		Use:   "add <name>",
		Short: "add or update a profile in the global config (plan)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			name := args[0]
			var prof profile.Profile
			if e.Config != nil {
				prof = e.Config.File.Profiles[name]
			}
			if f.fromCurrent {
				cur, notes := app.DetectProfile(cmd.Context(), e.Git, e.Gh)
				prof = app.MergeProfiles(prof, cur)
				if !a.Opts.JSON {
					a.Printf("captured %s\n", strings.Join(notes, ", "))
				}
			}
			prof = f.apply(prof)
			if prof.IsZero() {
				return fmt.Errorf("profile %s is empty: pass --from-current or field flags (--git-email, --github-user, …)", name)
			}
			if err := prof.Validate(); err != nil {
				return err
			}
			v, err := app.ProfileValue(prof)
			if err != nil {
				return err
			}
			edits := []func([]byte) ([]byte, error){setKey(config.ScopeGlobal, "profiles."+name, v)}
			if e.Config != nil && e.Config.File.DefaultProfile == "" && len(e.Config.File.Profiles) == 0 {
				edits = append(edits, setKey(config.ScopeGlobal, "defaultProfile", name))
				p.Note("first profile: it becomes defaultProfile")
			}
			if _, err := configEdit(p, config.ScopeGlobal, fmt.Sprintf("profile %s saved in %s", name, e.Display(e.Paths.Global)), edits...); err != nil {
				return err
			}
			return s.run(cmd, p, nil)
		},
	}
	fl := cmd.Flags()
	fl.BoolVar(&f.fromCurrent, "from-current", false, "capture git user.name/email, ssh key, signing and gh login from the current setup")
	fl.StringVar(&f.gitName, "git-name", "", "commit author name")
	fl.StringArrayVar(&f.gitEmails, "git-email", nil, "commit author email (repeatable; the first is applied)")
	fl.StringVar(&f.githubUser, "github-user", "", "GitHub login gh must be switched to")
	fl.StringArrayVar(&f.orgs, "org", nil, "extra GitHub owner you work in (repeatable)")
	fl.StringVar(&f.transport, "transport", "", "how git authenticates: ssh|gh")
	fl.StringVar(&f.sshKey, "ssh-key", "", "SSH key pinned with core.sshCommand (transport ssh)")
	fl.StringVar(&f.signFormat, "signing-format", "", "ssh|openpgp|x509|default|none")
	fl.StringVar(&f.signKey, "signing-key", "", "signing key (.pub path for ssh, key id for openpgp)")
	s.addOutFlag(cmd)
	return cmd
}

func profileRmCmd(s *session) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "rm <name>",
		Short: "remove a profile from the global config (plan)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			name := args[0]
			edits := []func([]byte) ([]byte, error){unsetKey(config.ScopeGlobal, "profiles."+name)}
			if e.Config != nil && e.Config.File.DefaultProfile == name {
				edits = append(edits, unsetKey(config.ScopeGlobal, "defaultProfile"))
			}
			changed, err := configEdit(p, config.ScopeGlobal, fmt.Sprintf("removed profile %s from %s", name, e.Display(e.Paths.Global)), edits...)
			if err != nil {
				return err
			}
			if !changed {
				return fmt.Errorf("profile %q is not in %s", name, e.Display(e.Paths.Global))
			}
			return s.run(cmd, p, nil)
		},
	}
	s.addOutFlag(cmd)
	return cmd
}

func profileUseCmd(a *App, s *session) *cobra.Command {
	var global bool
	cmd := &cobra.Command{
		Use:   "use <name>",
		Short: "apply a profile: git config, gh account, .mygit.local.json (plan)",
		Long: "apply a profile to this repository: repo-local git config (user.name, user.email, " +
			"core.sshCommand, signing), gh auth switch, and record the choice in .mygit.local.json. " +
			"--global writes git config --global and sets defaultProfile instead.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			c, err := e.Require()
			if err != nil {
				return err
			}
			name := args[0]
			prof, err := profile.Lookup(c.File.Profiles, name, "argument")
			if err != nil {
				return err
			}
			if !global {
				if err := e.RequireRepo(); err != nil {
					return fmt.Errorf("%w (or pass --global)", err)
				}
			}
			opts := profile.ApplyOptions{Name: name, Global: global}
			if !global {
				opts.LocalConfigPath = e.Paths.Local
				if opts.ExistingLocal, err = e.ReadOptional(e.Paths.Local); err != nil {
					return err
				}
				opts.RemoteName = "origin"
				if c.Workflow != nil && c.Workflow.Remote != "" {
					opts.RemoteName = c.Workflow.Remote
				}
				if u, err := e.Git.RemoteURL(cmd.Context(), opts.RemoteName); err == nil {
					opts.RemoteURL = u
				}
			}
			steps, err := profile.ApplySteps(prof, opts)
			if err != nil {
				return err
			}
			acts := app.FromProfileSteps(steps)
			for i := range acts {
				if acts[i].Kind == app.ActWriteFile {
					acts[i].Description = fmt.Sprintf("%-28s %s", "wrote "+e.Display(acts[i].Args[0]), fmt.Sprintf("{\"profile\": %q}", name))
				}
			}
			if err := p.Add(acts); err != nil {
				return err
			}
			if global {
				if _, err := configEdit(p, config.ScopeGlobal, "defaultProfile = "+name+" in "+e.Display(e.Paths.Global),
					setKey(config.ScopeGlobal, "defaultProfile", name)); err != nil {
					return err
				}
			}
			ownerLine := remoteOwnerLine(e, prof)
			if ownerLine != "" {
				p.Note("%s", ownerLine)
			}
			return s.runExtra(cmd, p, func(w io.Writer) {
				if ownerLine != "" {
					fmt.Fprintln(w, ownerLine)
				}
			}, map[string]any{"remoteOwnerCheck": ownerLine})
		},
	}
	cmd.Flags().BoolVar(&global, "global", false, "write git config --global and make it the defaultProfile")
	s.addOutFlag(cmd)
	return cmd
}

// remoteOwnerLine renders "remote origin URL → owner X ✓ allowed".
func remoteOwnerLine(e *app.Env, p profile.Profile) string {
	if e.RemoteURL == "" || e.RemoteOwner == "" {
		return ""
	}
	allowed, known := app.OwnerCheck(p, e.RemoteOwner)
	verdict := "✓ allowed"
	switch {
	case !known:
		verdict = "(profile names no GitHub accounts)"
	case !allowed:
		verdict = "✗ not the profile's github.user or orgs"
	}
	return fmt.Sprintf("remote origin %s → owner %s %s", e.RemoteURL, e.RemoteOwner, verdict)
}

func profileImportCmd(a *App, s *session) *cobra.Command {
	var name string
	cmd := &cobra.Command{
		Use:   "import",
		Short: "import the git-guard profile files (~/.claude/git-guard/profile.json, .claude/git-guard/profile.local.json)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, e, err := s.planner(cmd, args)
			if err != nil {
				return err
			}
			userPath, projPath := app.LegacyPaths(e.Home, e.RepoRoot)
			var merged profile.Profile
			var found []string
			for _, path := range []string{userPath, projPath} {
				if path == "" {
					continue
				}
				data, err := e.ReadOptional(path)
				if err != nil {
					return err
				}
				if data == nil {
					continue
				}
				lp, unknown, err := app.FromLegacy(data)
				if err != nil {
					return fmt.Errorf("%s: %w", e.Display(path), err)
				}
				if len(unknown) > 0 {
					sort.Strings(unknown)
					p.Note("%s: ignored unknown keys %s", e.Display(path), strings.Join(unknown, ", "))
				}
				merged = app.MergeProfiles(merged, lp)
				found = append(found, e.Display(path))
			}
			if len(found) == 0 {
				return fmt.Errorf("no git-guard profile found (%s, %s)", e.Display(userPath), orNone(e.Display(projPath)))
			}
			p.Note("imported from %s", strings.Join(found, ", "))
			v, err := app.ProfileValue(merged)
			if err != nil {
				return err
			}
			edits := []func([]byte) ([]byte, error){setKey(config.ScopeGlobal, "profiles."+name, v)}
			if e.Config == nil || e.Config.File.DefaultProfile == "" {
				edits = append(edits, setKey(config.ScopeGlobal, "defaultProfile", name))
			}
			if _, err := configEdit(p, config.ScopeGlobal, fmt.Sprintf("profile %s saved in %s", name, e.Display(e.Paths.Global)), edits...); err != nil {
				return err
			}
			return s.run(cmd, p, nil)
		},
	}
	cmd.Flags().StringVar(&name, "name", "personal", "name of the imported profile")
	s.addOutFlag(cmd)
	return cmd
}

func orNone(s string) string {
	if s == "" {
		return "no repository"
	}
	return s
}
