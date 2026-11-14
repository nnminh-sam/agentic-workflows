// Package agent translates between AI agents' hook protocols and the rules
// engine. Each agent has one Adapter: Decode turns the agent's stdin payload
// into a Request, Encode turns a Result into the agent's stdout bytes and
// process exit code. Adapters are pure (bytes in, bytes out); resolving the
// config, running the engine and reading the repository happen in the
// caller (`mygit guard`).
package agent

import (
	"fmt"
	"sort"
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// Kind is the hook event a payload belongs to.
type Kind int

const (
	// PreCommand is a check before the agent runs a shell command.
	PreCommand Kind = iota
	// SessionStart briefs the agent when a session starts.
	SessionStart
)

func (k Kind) String() string {
	if k == SessionStart {
		return "session-start"
	}
	return "pre-command"
}

// Request is the agent-neutral form of one hook call.
type Request struct {
	Kind    Kind
	Tool    string        // agent tool name, e.g. "Bash" (may be "")
	Command string        // the command line the agent wants to run
	Dialect shell.Dialect // grammar of Command
	Cwd     string        // working directory ("" = the guard's own cwd)
	// Skip is true when the payload is not about a shell command (another
	// tool): the adapter answers allow without any check.
	Skip bool
}

// Result is what the guard decided for one Request.
type Result struct {
	Kind    Kind
	Verdict verdict.Verdict
	// FailSafe, when non-empty, overrides the verdict: the guard could not
	// check the command (bad input, invalid config, internal error) and asks
	// the user. It holds the full reason text.
	FailSafe string
	// Context holds the session-start briefing lines (SessionStart only).
	Context []string
}

// Decision is the effective decision of r.
func (r Result) Decision() verdict.Decision {
	if r.FailSafe != "" {
		return verdict.DecAsk
	}
	return r.Verdict.Decision()
}

// Reason is the text shown to the user / agent for an ask or deny.
func (r Result) Reason() string {
	if r.FailSafe != "" {
		return r.FailSafe
	}
	return r.Verdict.Reason()
}

// FailSafe returns the ask result used when a command could not be checked
// (the port of pre_tool_use.py's _fail_safe).
func FailSafe(reason string) Result {
	return Result{Kind: PreCommand, FailSafe: "mygit could not check this command: " + reason}
}

// Allow is the result for a command that needs no check.
func Allow(kind Kind) Result { return Result{Kind: kind} }

// MayInvolveGit is the deliberately broad pre-filter used when the config is
// invalid: any command mentioning git, gh or github must be confirmed, the
// rest pass (the same substring test as pre_tool_use.py).
func MayInvolveGit(command string) bool {
	return strings.Contains(command, "git") || strings.Contains(command, "gh")
}

// Adapter speaks one agent's hook protocol.
type Adapter interface {
	// Name is the --agent value, e.g. "claude".
	Name() string
	// Decode parses the agent's stdin payload for the given hook kind.
	Decode(stdin []byte, kind Kind) (Request, error)
	// Encode renders a result as stdout bytes (possibly empty) and the
	// process exit code the agent expects.
	Encode(res Result) (stdout []byte, exitCode int)
}

var adapters = map[string]func() Adapter{
	"claude": func() Adapter { return Claude{} },
	"json":   func() Adapter { return JSON{name: "json"} },
	// TODO(open question): Codex CLI's pre-exec hook event and payload are
	// unconfirmed. Until they are, codex speaks the generic json protocol.
	"codex": func() Adapter { return JSON{name: "codex"} },
	// TODO(open question): Antigravity (agy) has no confirmed pre-command
	// hook. Until it does, agy speaks the generic json protocol.
	"agy": func() Adapter { return JSON{name: "agy"} },
}

// New returns the adapter for an agent name.
func New(name string) (Adapter, error) {
	f, ok := adapters[strings.ToLower(strings.TrimSpace(name))]
	if !ok {
		return nil, fmt.Errorf("unknown agent %q (want %s)", name, strings.Join(Names(), "|"))
	}
	return f(), nil
}

// Names lists the supported --agent values, sorted.
func Names() []string {
	out := make([]string, 0, len(adapters))
	for n := range adapters {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}
