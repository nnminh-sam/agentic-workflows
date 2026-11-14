package agent

import (
	"strings"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/shell"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// ApplyRuleID labels the finding of the agent --apply policy.
const ApplyRuleID = "agentApply"

// CheckApply implements the execution.agentApply policy: when an agent runs
// `mygit … --apply <id>` (or `--yes` / `-y`), the guard asks the user first
// unless agentApply is "allow". Any other mygit call (dry runs, --json
// plans, reads) passes.
func CheckApply(req Request, agentApply string) verdict.Verdict {
	var v verdict.Verdict
	if agentApply == "allow" || !strings.Contains(req.Command, "mygit") {
		return v
	}
	for _, inv := range shell.ParseWith(req.Command, req.Dialect, req.Cwd, shell.Options{}) {
		if inv.Program != "mygit" {
			continue
		}
		if how, ok := applies(inv.Argv()); ok {
			v.Addf(ApplyRuleID, verdict.Ask, "the agent wants to run a mygit plan (%s) without you confirming it: "+
				"check that it showed you this plan first. Set execution.agentApply to \"allow\" to skip this question", how)
		}
	}
	return v
}

// applies reports whether a mygit argv executes a plan, and how.
func applies(argv []string) (string, bool) {
	for i, a := range argv {
		switch {
		case a == "--":
			return "", false
		case a == "--apply":
			if i+1 < len(argv) && !strings.HasPrefix(argv[i+1], "-") {
				return "--apply " + argv[i+1], true
			}
			return "--apply", true
		case strings.HasPrefix(a, "--apply="):
			return a, true
		case a == "--yes" || a == "-y" || a == "--yes=true":
			return a, true
		}
	}
	return "", false
}
