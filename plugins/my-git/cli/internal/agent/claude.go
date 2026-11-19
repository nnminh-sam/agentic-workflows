package agent

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// Claude speaks Claude Code's hook protocol (PreToolUse for the Bash and
// PowerShell tools, SessionStart). The output fields are the ones
// scripts/pre_tool_use.py and scripts/session_start.py emit today:
// hookSpecificOutput.permissionDecision (ask|deny) with
// permissionDecisionReason, systemMessage + additionalContext for
// warnings, nothing at all for allow. The exit code is always 0: the
// decision travels in the JSON.
type Claude struct{}

// Name implements Adapter.
func (Claude) Name() string { return "claude" }

type claudeIn struct {
	ToolName  string `json:"tool_name"`
	ToolInput struct {
		Command string `json:"command"`
	} `json:"tool_input"`
	Cwd string `json:"cwd"`
}

// Decode implements Adapter. Tools other than Bash and PowerShell are
// skipped (allowed untouched). An empty payload is treated as {}.
func (Claude) Decode(stdin []byte, kind Kind) (Request, error) {
	var in claudeIn
	if len(bytes.TrimSpace(stdin)) > 0 {
		if err := json.Unmarshal(stdin, &in); err != nil {
			return Request{Kind: kind}, fmt.Errorf("bad hook input (%v)", err)
		}
	}
	req := Request{Kind: kind, Tool: in.ToolName, Command: in.ToolInput.Command, Cwd: in.Cwd,
		Dialect: engine.DialectFor(in.ToolName)}
	if kind == PreCommand && in.ToolName != "Bash" && in.ToolName != "PowerShell" {
		req.Skip = true
	}
	return req, nil
}

type claudeSpecific struct {
	HookEventName            string `json:"hookEventName"`
	PermissionDecision       string `json:"permissionDecision,omitempty"`
	PermissionDecisionReason string `json:"permissionDecisionReason,omitempty"`
	AdditionalContext        string `json:"additionalContext,omitempty"`
}

type claudeOut struct {
	SystemMessage      string         `json:"systemMessage,omitempty"`
	HookSpecificOutput claudeSpecific `json:"hookSpecificOutput"`
}

// Encode implements Adapter.
func (Claude) Encode(res Result) ([]byte, int) {
	if res.Kind == SessionStart {
		if len(res.Context) == 0 {
			return nil, 0
		}
		return marshalLine(claudeOut{HookSpecificOutput: claudeSpecific{
			HookEventName: "SessionStart", AdditionalContext: strings.Join(res.Context, "\n"),
		}}), 0
	}
	switch d := res.Decision(); d {
	case verdict.Allow:
		return nil, 0
	case verdict.DecWarn:
		parts := make([]string, 0, len(res.Verdict.Findings))
		for _, f := range res.Verdict.Findings {
			parts = append(parts, f.String())
		}
		text := "mygit warnings: " + strings.Join(parts, "; ")
		return marshalLine(claudeOut{SystemMessage: text, HookSpecificOutput: claudeSpecific{
			HookEventName: "PreToolUse", AdditionalContext: text,
		}}), 0
	default:
		reason := res.Reason()
		if res.FailSafe == "" {
			reason += "\n" + explainHint(res.Verdict)
		}
		return marshalLine(claudeOut{HookSpecificOutput: claudeSpecific{
			HookEventName: "PreToolUse", PermissionDecision: string(d), PermissionDecisionReason: reason,
		}}), 0
	}
}

// explainHint replaces the Python "Rules: rules.html" line: it points at
// `mygit rules explain` for the first blocking rule.
func explainHint(v verdict.Verdict) string {
	for _, f := range v.Findings {
		if f.Severity >= verdict.Ask && f.RuleID != ApplyRuleID {
			return "Rule details: mygit rules explain " + f.RuleID
		}
	}
	return "Rule details: mygit rules list"
}

// marshalLine encodes v as one JSON line without HTML escaping.
func marshalLine(v any) []byte {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v) // plain structs of strings: cannot fail
	return b.Bytes()
}
