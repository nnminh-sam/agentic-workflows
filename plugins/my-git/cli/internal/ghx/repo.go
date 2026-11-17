package ghx

import (
	"context"
	"strconv"
)

// PR is a pull request as returned by gh pr list / gh pr view --json.
type PR struct {
	Number      int    `json:"number"`
	Title       string `json:"title"`
	Body        string `json:"body,omitempty"`
	URL         string `json:"url"`
	State       string `json:"state"`
	IsDraft     bool   `json:"isDraft"`
	HeadRefName string `json:"headRefName"`
	BaseRefName string `json:"baseRefName"`
}

const (
	prListFields = "number,title,url,state,isDraft,headRefName,baseRefName"
	prViewFields = "number,title,body,url,state,isDraft,headRefName,baseRefName"
)

// PRList lists PRs filtered by head branch, base branch and state
// ("open", "closed", "merged", "all"); empty filters are omitted.
func (g *Gh) PRList(ctx context.Context, head, base, state string) ([]PR, error) {
	args := []string{"pr", "list", "--json", prListFields}
	for _, f := range [][2]string{{"--head", head}, {"--base", base}, {"--state", state}} {
		if f[1] != "" {
			args = append(args, f[0], f[1])
		}
	}
	var prs []PR
	if err := g.JSON(ctx, args, &prs); err != nil {
		return nil, err
	}
	return prs, nil
}

// PRView returns one PR including its body.
func (g *Gh) PRView(ctx context.Context, number int) (PR, error) {
	var pr PR
	err := g.JSON(ctx, []string{"pr", "view", strconv.Itoa(number), "--json", prViewFields}, &pr)
	return pr, err
}

// Repo is the subset of `gh repo view --json` mygit uses.
type Repo struct {
	NameWithOwner string `json:"nameWithOwner"`
	Name          string `json:"name"`
	Owner         struct {
		Login string `json:"login"`
	} `json:"owner"`
	URL              string `json:"url"`
	Visibility       string `json:"visibility"`
	IsPrivate        bool   `json:"isPrivate"`
	DefaultBranchRef struct {
		Name string `json:"name"`
	} `json:"defaultBranchRef"`
}

// RepoView describes the repo of g.Dir (or name, "owner/repo", when set).
func (g *Gh) RepoView(ctx context.Context, name string) (Repo, error) {
	args := []string{"repo", "view"}
	if name != "" {
		args = append(args, name)
	}
	args = append(args, "--json", "nameWithOwner,name,owner,url,visibility,isPrivate,defaultBranchRef")
	var r Repo
	err := g.JSON(ctx, args, &r)
	return r, err
}
