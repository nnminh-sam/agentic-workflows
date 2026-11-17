package agent

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/engine"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// JSON is the generic protocol for any tool without its own adapter
// (`mygit guard --agent json`):
//
//	stdin   {"command": "git push", "cwd": "/repo", "dialect": "bash|powershell"}
//	stdout  {"decision": "allow|warn|ask|deny", "findings": [...], "reason": "..."}
//	exit    0 allow/warn · 1 ask · 2 deny
//
// "tool" may replace "dialect" (PowerShell → powershell). At session start
// the output is {"decision": "allow", "context": [...]}.
//
// The codex and agy adapters are aliases of JSON until those agents' hook
// formats are confirmed (see the TODOs in agent.go).
type JSON struct{ name string }

// Name implements Adapter.
func (j JSON) Name() string {
	if j.name == "" {
		return "json"
	}
	return j.name
}

type jsonIn struct {
	Command string `json:"command"`
	Cwd     string `json:"cwd"`
	Dialect string `json:"dialect"`
	Tool    string `json:"tool"`
}

// Decode implements Adapter. An empty payload is treated as {}.
func (JSON) Decode(stdin []byte, kind Kind) (Request, error) {
	var in jsonIn
	if len(bytes.TrimSpace(stdin)) > 0 {
		if err := json.Unmarshal(stdin, &in); err != nil {
			return Request{Kind: kind}, fmt.Errorf("bad hook input (%v)", err)
		}
	}
	req := Request{Kind: kind, Tool: in.Tool, Command: in.Command, Cwd: in.Cwd, Dialect: engine.DialectFor(in.Tool)}
	switch strings.ToLower(strings.TrimSpace(in.Dialect)) {
	case "":
	case "bash", "sh", "zsh", "gitbash":
		req.Dialect = shell.Bash
	case "powershell", "pwsh":
		req.Dialect = shell.PowerShell
	default:
		return req, fmt.Errorf("bad hook input (unknown dialect %q, want bash|powershell)", in.Dialect)
	}
	return req, nil
}

type jsonOut struct {
	Decision verdict.Decision  `json:"decision"`
	Findings []verdict.Finding `json:"findings"`
	Reason   string            `json:"reason,omitempty"`
	Context  []string          `json:"context,omitempty"`
}

// Encode implements Adapter.
func (JSON) Encode(res Result) ([]byte, int) {
	out := jsonOut{Decision: res.Decision(), Findings: res.Verdict.Findings}
	if out.Findings == nil {
		out.Findings = []verdict.Finding{}
	}
	if res.Kind == SessionStart {
		out.Decision, out.Context = verdict.Allow, res.Context
		return marshalLine(out), 0
	}
	if out.Decision != verdict.Allow {
		out.Reason = res.Reason()
	}
	return marshalLine(out), out.Decision.ExitCode()
}
