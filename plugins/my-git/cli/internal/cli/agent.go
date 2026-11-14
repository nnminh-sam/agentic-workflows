package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	mgapp "github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/tmpl"
)

func init() { register(155, newAgentCmd) }

// installable agents (json is a protocol, not something to install).
var installAgents = []string{"claude", "codex", "agy"}

const (
	agentsBlockBegin = "<!-- mygit:begin"
	agentsBlockEnd   = "<!-- mygit:end -->"
	// claudeGuardMark identifies mygit's own entries in Claude settings.
	claudeGuardMark = "guard --agent claude"
)

func newAgentCmd(a *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agent",
		Short: "connect AI agents to mygit (pre-command hook, instructions, git hooks)",
	}
	var scope string
	mk := func(verb string, install bool) *cobra.Command {
		c := &cobra.Command{
			Use:       verb + " <" + strings.Join(installAgents, "|") + "> [--scope project|user]",
			Short:     verb + " mygit for an agent (dry run first)",
			Args:      cobra.ExactArgs(1),
			ValidArgs: installAgents,
			RunE: func(cmd *cobra.Command, args []string) error {
				return agentChange(a, strings.ToLower(args[0]), scope, install)
			},
		}
		c.Flags().StringVar(&scope, "scope", "project", "project (this repository) or user (every repository)")
		return c
	}
	status := &cobra.Command{
		Use:   "status",
		Short: "show which agents are connected to mygit",
		Args:  cobra.NoArgs,
		RunE:  func(cmd *cobra.Command, _ []string) error { return agentStatus(a) },
	}
	cmd.AddCommand(mk("install", true), mk("uninstall", false), status)
	return cmd
}

// agentTarget is the file an agent install edits.
type agentTarget struct {
	Agent string `json:"agent"`
	Scope string `json:"scope"`
	Path  string `json:"path"`
}

func agentTargetFor(a *App, env *mgapp.Env, name, scope string) (agentTarget, error) {
	t := agentTarget{Agent: name, Scope: scope}
	known := false
	for _, n := range installAgents {
		known = known || n == name
	}
	if !known {
		return t, fmt.Errorf("unknown agent %q (want %s)", name, strings.Join(installAgents, "|"))
	}
	if scope != "project" && scope != "user" {
		return t, fmt.Errorf("unknown scope %q (want project|user)", scope)
	}
	if scope == "project" && !env.InRepo() {
		return t, errors.New("--scope project needs a git repository (or use --scope user)")
	}
	home := env.Home
	switch {
	case name == "claude" && scope == "project":
		t.Path = filepath.Join(env.RepoRoot, ".claude", "settings.json")
	case name == "claude":
		t.Path = filepath.Join(home, ".claude", "settings.json")
	case scope == "project":
		t.Path = filepath.Join(env.RepoRoot, "AGENTS.md")
	case name == "codex":
		dir := a.Getenv("CODEX_HOME")
		if dir == "" {
			dir = filepath.Join(home, ".codex")
		}
		t.Path = filepath.Join(dir, "AGENTS.md")
	default:
		return t, fmt.Errorf("%s: --scope user is not supported yet (its instruction file location is unconfirmed); use --scope project", name)
	}
	return t, nil
}

func agentChange(a *App, name, scope string, install bool) error {
	env, err := guardEnv(a, "")
	if err != nil {
		return err
	}
	t, err := agentTargetFor(a, env, name, scope)
	if err != nil {
		return err
	}
	verb := "uninstall"
	if install {
		verb = "install"
	}
	p := env.NewPlanner([]string{"agent", verb, name, "--scope", scope})
	cur, err := env.ReadOptional(t.Path)
	if err != nil {
		return err
	}
	var next []byte
	if name == "claude" {
		next, err = claudeSettings(cur, install)
	} else {
		next, err = agentsMDBlock(cur, install)
	}
	if err != nil {
		return fmt.Errorf("%s: %w", env.Display(t.Path), err)
	}
	switch {
	case next == nil && cur != nil:
		p.Cmd(removeFileCmd(a.GOOS, t.Path, filepath.Dir(t.Path)), "remove "+env.Display(t.Path)+" (only mygit's content was left)")
	case next != nil && !bytes.Equal(next, cur):
		desc := "add mygit to " + env.Display(t.Path)
		if !install {
			desc = "remove mygit from " + env.Display(t.Path)
		}
		p.WriteFile(t.Path, string(next), desc)
	}
	if name == "claude" {
		if install {
			p.Note("Claude Code runs `mygit guard --agent claude` before every Bash/PowerShell command; mygit must be on PATH")
			p.Note("if the my-git plugin's Python hooks are still enabled, commands are checked twice; disable them once this works")
		}
	} else {
		p.Note("%s: its pre-command hook format is unconfirmed, so no guard hook is installed; "+
			"only the AGENTS.md instructions and the git hooks (the backstop) protect this repository", name)
	}
	if install && env.InRepo() {
		if err := planHooksInstall(a, env, p, false); err != nil {
			p.Note("git hooks not installed: %v", err)
		}
	} else if !install {
		p.Note("the git hooks stay installed (other agents and people rely on them); remove them with mygit hooks uninstall")
	}
	built, err := p.Build(a.Context())
	if err != nil {
		return err
	}
	return guardRunPlan(a.Context(), a, env, built, map[string]any{"target": t})
}

// agentsMDBlock inserts (install) or removes the mygit block of an
// AGENTS.md file. It returns nil when the file would be left empty.
func agentsMDBlock(cur []byte, install bool) ([]byte, error) {
	text := strings.ReplaceAll(string(cur), "\r\n", "\n")
	before, after, found := text, "", false
	if i := strings.Index(text, agentsBlockBegin); i >= 0 {
		j := strings.Index(text[i:], agentsBlockEnd)
		if j < 0 {
			return nil, fmt.Errorf("found %q without %q; fix the file by hand", agentsBlockBegin, agentsBlockEnd)
		}
		before, after, found = text[:i], text[i+j+len(agentsBlockEnd):], true
		after = strings.TrimPrefix(after, "\n")
	}
	block := ""
	if install {
		b, err := tmpl.Default().RenderAgentsMD()
		if err != nil {
			return nil, err
		}
		block = strings.TrimRight(b, "\n") + "\n"
		if !found && strings.TrimSpace(before) != "" {
			before = strings.TrimRight(before, "\n") + "\n\n"
		}
	}
	out := before + block + after
	if !install && !found {
		return cur, nil
	}
	if strings.TrimSpace(out) == "" {
		return nil, nil
	}
	return []byte(out), nil
}

// claudeSettings merges (install) or removes mygit's hook entries in a
// Claude Code settings.json. Other keys and other hooks are kept; entries
// are recognised by their command running `… guard --agent claude`. It
// returns nil when the file would be left as an empty object.
func claudeSettings(cur []byte, install bool) ([]byte, error) {
	doc := map[string]any{}
	if len(bytes.TrimSpace(cur)) > 0 {
		if err := json.Unmarshal(cur, &doc); err != nil {
			return nil, fmt.Errorf("not valid JSON (%v); fix it by hand first", err)
		}
	}
	hooks, _ := doc["hooks"].(map[string]any)
	if hooks == nil {
		if _, exists := doc["hooks"]; exists {
			return nil, errors.New(`"hooks" is not an object`)
		}
		hooks = map[string]any{}
	}
	for event, groups := range hooks {
		if kept := withoutMygit(groups); len(kept) > 0 {
			hooks[event] = kept
		} else {
			delete(hooks, event)
		}
	}
	if install {
		rendered, err := tmpl.Default().RenderClaudeHooks(tmpl.ClaudeHooksData{})
		if err != nil {
			return nil, err
		}
		var ours struct {
			Hooks map[string][]any `json:"hooks"`
		}
		if err := json.Unmarshal([]byte(rendered), &ours); err != nil {
			return nil, err
		}
		for event, groups := range ours.Hooks {
			existing, _ := hooks[event].([]any)
			hooks[event] = append(existing, groups...)
		}
	}
	if len(hooks) > 0 {
		doc["hooks"] = hooks
	} else {
		delete(doc, "hooks")
	}
	if len(doc) == 0 {
		if cur == nil {
			return nil, nil
		}
		return []byte("{}\n"), nil
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(doc); err != nil {
		return nil, err
	}
	// keep the original bytes when nothing changed semantically (idempotent install)
	if cur != nil && jsonEqual(cur, b.Bytes()) {
		return cur, nil
	}
	return b.Bytes(), nil
}

// withoutMygit drops mygit guard commands from a list of matcher groups,
// and groups left without hooks.
func withoutMygit(groups any) []any {
	list, ok := groups.([]any)
	if !ok {
		return nil
	}
	var out []any
	for _, g := range list {
		gm, ok := g.(map[string]any)
		if !ok {
			out = append(out, g)
			continue
		}
		inner, ok := gm["hooks"].([]any)
		if !ok {
			out = append(out, g)
			continue
		}
		var keep []any
		for _, h := range inner {
			if !isMygitHook(h) {
				keep = append(keep, h)
			}
		}
		if len(keep) == 0 {
			continue
		}
		if len(keep) != len(inner) {
			copied := map[string]any{}
			for k, v := range gm {
				copied[k] = v
			}
			copied["hooks"] = keep
			gm = copied
		}
		out = append(out, gm)
	}
	return out
}

func isMygitHook(h any) bool {
	m, ok := h.(map[string]any)
	if !ok {
		return false
	}
	cmd, _ := m["command"].(string)
	return strings.Contains(cmd, "mygit") && strings.Contains(cmd, claudeGuardMark)
}

func jsonEqual(x, y []byte) bool {
	var a, b any
	if json.Unmarshal(x, &a) != nil || json.Unmarshal(y, &b) != nil {
		return false
	}
	ja, _ := json.Marshal(a)
	jb, _ := json.Marshal(b)
	return bytes.Equal(ja, jb)
}

// agentStatusRow is one line of `mygit agent status`.
type agentStatusRow struct {
	agentTarget
	Installed bool   `json:"installed"`
	Detail    string `json:"detail,omitempty"`
}

func agentStatus(a *App) error {
	env, err := guardEnv(a, "")
	if err != nil {
		return err
	}
	var rows []agentStatusRow
	for _, name := range installAgents {
		for _, scope := range []string{"project", "user"} {
			t, err := agentTargetFor(a, env, name, scope)
			if err != nil {
				continue
			}
			row := agentStatusRow{agentTarget: t}
			b, err := os.ReadFile(t.Path)
			switch {
			case errors.Is(err, fs.ErrNotExist):
				row.Detail = "file missing"
			case err != nil:
				row.Detail = err.Error()
			case name == "claude":
				row.Installed = strings.Contains(string(b), claudeGuardMark)
			default:
				row.Installed = strings.Contains(string(b), agentsBlockBegin)
			}
			row.Path = env.Display(t.Path)
			rows = append(rows, row)
		}
	}
	var hooks *HooksStatus
	if env.InRepo() {
		st := hooksStatus(a, env)
		hooks = &st
	}
	if a.Opts.JSON {
		b, err := json.MarshalIndent(map[string]any{"agents": rows, "gitHooks": hooks}, "", "  ")
		if err != nil {
			return err
		}
		fmt.Fprintln(a.Out, string(b))
		return nil
	}
	for _, r := range rows {
		mark := "✗"
		if r.Installed {
			mark = "✓"
		}
		line := fmt.Sprintf("%s %-7s %-8s %s", mark, r.Agent, r.Scope, r.Path)
		if r.Detail != "" {
			line += "  (" + r.Detail + ")"
		}
		a.Printf("%s\n", line)
	}
	if hooks != nil {
		mark := "✗"
		if hooks.Installed {
			mark = "✓"
		}
		a.Printf("%s git hooks         .githooks (core.hooksPath %q)\n", mark, hooks.HooksPath)
	}
	a.Printf("codex and agy: pre-command hook formats are unconfirmed; they rely on AGENTS.md and the git hooks\n")
	return nil
}
