package pr

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/app"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/rules"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

// Info is the PR fields `mygit pr check` reads.
type Info struct {
	Title string `json:"title"`
	Body  string `json:"body,omitempty"`
	Base  string `json:"baseRefName"`
	Head  string `json:"headRefName"`
}

// Findings checks a PR's title (GH005), body (GH008, GH009) and target
// (BR005) like check_pr.py does.
func Findings(in Info, pol *policy.Policy) verdict.Verdict {
	v := rules.CheckPR(in.Title, in.Body, pol.Contract(policy.GitHubCLI), pol.Contract(policy.Commit))
	if br := pol.Contract(policy.Branching); br != nil {
		if r := br.Rule("BR005"); r.Severity != verdict.Off {
			if prob := rules.PRTargetProblem(in.Base, in.Head, br); prob != "" {
				v.Add("BR005", r.Severity, prob)
			}
		}
	}
	return v
}

// Check reads pull request num (0 = the PR of the current branch) with
// `gh pr view --json title,body,baseRefName,headRefName`, after the active
// gh account passed ID005, and returns its findings. It only reads.
func Check(ctx context.Context, e *app.Env, c *config.Resolved, num int) (Info, verdict.Verdict, error) {
	var info Info
	args := []string{"pr", "view"}
	if num > 0 {
		args = append(args, strconv.Itoa(num))
	}
	args = append(args, "--json", "title,body,baseRefName,headRefName")
	ev := newEvaluator(e, c.Policy, nil)
	if pre := ev.eval("gh", args, "ID005"); blocked(pre) {
		return info, pre, nil
	}
	res, err := e.Gh.Run(ctx, args...)
	if err != nil {
		return info, verdict.Verdict{}, err
	}
	if err := json.Unmarshal([]byte(res.Stdout), &info); err != nil {
		return info, verdict.Verdict{}, fmt.Errorf("gh pr view: unexpected output: %w", err)
	}
	return info, Findings(info, c.Policy), nil
}
