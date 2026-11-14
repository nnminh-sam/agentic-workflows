// Package verdict holds rule findings and how they combine into one decision.
// It is the shared result type of the rules engine, the git hooks, the agent
// adapters and `mygit lint`.
package verdict

import (
	"fmt"
	"strings"
)

// Severity of a rule. Order matters: a higher value is stricter.
type Severity int

const (
	Off Severity = iota
	Warn
	Ask
	Deny
)

var severityNames = [...]string{"off", "warn", "ask", "deny"}

func (s Severity) String() string {
	if s < Off || s > Deny {
		return fmt.Sprintf("Severity(%d)", int(s))
	}
	return severityNames[s]
}

// ParseSeverity parses "off", "warn", "ask" or "deny" (case-insensitive).
func ParseSeverity(s string) (Severity, error) {
	for i, n := range severityNames {
		if strings.EqualFold(strings.TrimSpace(s), n) {
			return Severity(i), nil
		}
	}
	return Off, fmt.Errorf("unknown severity %q (want off|warn|ask|deny)", s)
}

func (s Severity) MarshalText() ([]byte, error) { return []byte(s.String()), nil }

func (s *Severity) UnmarshalText(b []byte) error {
	v, err := ParseSeverity(string(b))
	if err != nil {
		return err
	}
	*s = v
	return nil
}

// Decision is the combined outcome of a Verdict.
type Decision string

const (
	Allow   Decision = "allow"
	DecWarn Decision = "warn"
	DecAsk  Decision = "ask"
	DecDeny Decision = "deny"
)

// ExitCode maps a decision to the CLI exit code contract:
// 0 allow/warn, 1 ask, 2 deny.
func (d Decision) ExitCode() int {
	switch d {
	case DecAsk:
		return 1
	case DecDeny:
		return 2
	default:
		return 0
	}
}

// Finding is one rule hit.
type Finding struct {
	RuleID   string   `json:"rule"`
	Severity Severity `json:"severity"`
	Message  string   `json:"message"`
}

func (f Finding) String() string { return fmt.Sprintf("[%s] %s", f.RuleID, f.Message) }

// Verdict collects findings. The zero value is ready to use.
type Verdict struct {
	Findings []Finding `json:"findings"`
}

// Add records a finding unless sev is Off.
func (v *Verdict) Add(ruleID string, sev Severity, msg string) {
	if sev == Off {
		return
	}
	v.Findings = append(v.Findings, Finding{RuleID: ruleID, Severity: sev, Message: msg})
}

// Addf is Add with fmt.Sprintf formatting.
func (v *Verdict) Addf(ruleID string, sev Severity, format string, args ...any) {
	v.Add(ruleID, sev, fmt.Sprintf(format, args...))
}

// Extend appends all findings of other.
func (v *Verdict) Extend(other Verdict) { v.Findings = append(v.Findings, other.Findings...) }

// Decision returns the strictest severity: deny > ask > warn > allow.
func (v Verdict) Decision() Decision {
	top := Off
	for _, f := range v.Findings {
		if f.Severity > top {
			top = f.Severity
		}
	}
	switch top {
	case Deny:
		return DecDeny
	case Ask:
		return DecAsk
	case Warn:
		return DecWarn
	default:
		return Allow
	}
}

// Of returns the findings with exactly the given severity.
func (v Verdict) Of(sev Severity) []Finding {
	var out []Finding
	for _, f := range v.Findings {
		if f.Severity == sev {
			out = append(out, f)
		}
	}
	return out
}

// Reason renders blocking findings first, then warnings on one line.
func (v Verdict) Reason() string {
	var lines []string
	for _, f := range v.Findings {
		if f.Severity == Deny || f.Severity == Ask {
			lines = append(lines, f.String())
		}
	}
	if warns := v.Of(Warn); len(warns) > 0 {
		parts := make([]string, len(warns))
		for i, f := range warns {
			parts[i] = f.String()
		}
		lines = append(lines, "Warnings: "+strings.Join(parts, "; "))
	}
	return "mygit: " + strings.Join(lines, "\n")
}
