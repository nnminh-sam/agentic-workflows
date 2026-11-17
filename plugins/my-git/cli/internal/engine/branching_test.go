package engine

// Port of tests/test_branching.py. Subtest names are the Python test names.

import (
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

var prFiles = map[string]string{"pr.md": goodBody}

func on(branch string) *fakeRepo { return fake(func(r *fakeRepo) { r.branch = branch }) }

func br(repo *fakeRepo) opts { return opts{repo: repo, files: prFiles} }

func TestProtectedCommit(t *testing.T) {
	t.Run("test_commit_on_protected_denied", func(t *testing.T) {
		for _, b := range []string{"main", "develop/base"} {
			expect(t, commitX, br(on(b)), verdict.DecDeny, []string{"BR001"})
		}
	})
	t.Run("test_commit_on_work_branch_allowed", func(t *testing.T) { clean(t, commitX, br(nil)) })
	t.Run("test_merge_and_cherry_pick_on_protected", func(t *testing.T) {
		has(t, "git merge develop/feat/x", br(on("develop/base")), "BR001")
		has(t, "git cherry-pick abc", br(on("main")), "BR001")
		clean(t, "git merge --ff-only origin/develop/base", br(on("develop/base")))
		clean(t, "git rebase --abort", br(on("main")))
	})
	t.Run("test_initial_commit_asks", func(t *testing.T) {
		r := on("main")
		r.hasCommits = false
		expect(t, "git commit -m 'chore: initialize repository'", br(r), verdict.DecAsk, []string{"BR001"})
	})
}

func TestProtectedPush(t *testing.T) {
	t.Run("test_push_protected_denied", func(t *testing.T) {
		for _, c := range []struct{ cmd, branch string }{
			{"git push origin develop/base", "develop/feat/x"}, {"git push", "main"},
			{"git push origin HEAD", "develop/base"}, {"git push origin x:main", "x"},
			{"git push origin HEAD:refs/heads/main", "develop/feat/x"},
			{"git push --force origin +develop/base", "develop/feat/x"},
		} {
			v := has(t, c.cmd, br(on(c.branch)), "BR002")
			if v.Decision() != verdict.DecDeny {
				t.Errorf("%q: %s", c.cmd, v.Decision())
			}
		}
	})
	t.Run("test_delete_and_all_denied", func(t *testing.T) {
		has(t, "git push origin --delete main", br(nil), "BR002")
		has(t, "git push origin :develop/base", br(nil), "BR002")
		has(t, "git push --all origin", br(nil), "BR002")
	})
	t.Run("test_work_branch_push_allowed", func(t *testing.T) {
		clean(t, "git push -u origin develop/feat/test", br(nil))
		clean(t, "git push", br(nil))
	})
	t.Run("test_first_push_of_protected_asks", func(t *testing.T) {
		r := on("develop/base")
		r.remoteBranches = map[string]bool{}
		expect(t, "git push -u origin develop/base", br(r), verdict.DecAsk, []string{"BR002"})
	})
}

func TestNewBranch(t *testing.T) {
	t.Run("test_good_branch", func(t *testing.T) {
		for _, cmd := range []string{"git switch -c develop/feat/login develop/base", "git checkout -b develop/fix/x origin/develop/base",
			"git branch develop/chore/deps origin/develop/base",
			"git switch -c develop/feat/login --track origin/develop/base"} {
			clean(t, cmd, br(nil))
		}
		clean(t, "git switch -c develop/feat/login", br(on("develop/base")))
	})
	t.Run("test_bad_name", func(t *testing.T) {
		for _, name := range []string{"login", "feat/login", "develop/feat/Login", "develop/feature/x"} {
			expect(t, "git switch -c "+name+" develop/base", br(nil), verdict.DecDeny, []string{"BR003"})
		}
	})
	t.Run("test_bad_base", func(t *testing.T) {
		expect(t, "git switch -c develop/feat/x main", br(nil), verdict.DecDeny, []string{"BR004"})
		expect(t, "git checkout -b develop/feat/x", br(nil), verdict.DecDeny, []string{"BR004"})
	})
	t.Run("test_protected_names_and_listing_ok", func(t *testing.T) {
		clean(t, "git switch -c develop/base origin/develop/base", br(on("main")))
		for _, cmd := range []string{"git branch", "git branch -a", "git branch -d develop/feat/old", "git switch develop/base"} {
			clean(t, cmd, br(nil))
		}
	})
}

func TestPullRequest(t *testing.T) {
	t.Run("test_default_target", func(t *testing.T) {
		clean(t, "gh pr create --base develop/base --title 'feat: add x' -F pr.md", br(nil))
	})
	t.Run("test_base_required", func(t *testing.T) {
		has(t, "gh pr create --title 'feat: add x' -F pr.md", br(nil), "BR005")
	})
	t.Run("test_release_only_from_staging", func(t *testing.T) {
		has(t, "gh pr create --base main --title 'feat: add x' -F pr.md", br(nil), "BR005")
		clean(t, "gh pr create --base main --head develop/base --title 'chore(release): v1.0.0' -F pr.md", br(nil))
		clean(t, "gh pr create -B main --title 'chore(release): v1.0.0' -F pr.md", br(on("develop/base")))
	})
	t.Run("test_other_bases_denied", func(t *testing.T) {
		has(t, "gh pr create --base develop/feat/y --title 'feat: add x' -F pr.md", br(nil), "BR005")
		has(t, "gh pr edit 3 --base main", br(nil), "BR005")
	})
	t.Run("test_body_sections", func(t *testing.T) {
		bad := map[string]string{"pr.md": "## Summary\nAdd x.\n\n## Explanation\n\n## Test results\nUnit: ok\n"}
		v := expect(t, "gh pr create --base develop/base --title 'feat: add x' -F pr.md", opts{files: bad}, verdict.DecDeny, []string{"GH008"})
		for _, s := range []string{"Detailed changes", "'Explanation' is empty", "'lint'"} {
			if !strings.Contains(messages(v), s) {
				t.Errorf("missing %q in %s", s, v.Reason())
			}
		}
	})
	t.Run("test_placeholders_and_comments_are_empty", func(t *testing.T) {
		body := strings.Replace(goodBody, "Needed.", "<!-- why? -->\nTODO", 1)
		v := run(t, "gh pr create --base develop/base --title 'feat: add x' -F pr.md", opts{files: map[string]string{"pr.md": body}})
		if got := ids(v); len(got) != 1 || got[0] != "GH008" {
			t.Errorf("got %v", got)
		}
	})
	t.Run("test_body_ai_attribution", func(t *testing.T) {
		body := goodBody + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
		v := run(t, "gh pr create --base develop/base --title 'feat: add x' -F pr.md", opts{files: map[string]string{"pr.md": body}})
		if got := ids(v); len(got) != 1 || got[0] != "GH009" {
			t.Errorf("got %v", got)
		}
	})
	t.Run("test_edit_without_body_skips_sections", func(t *testing.T) {
		clean(t, "gh pr edit 3 --title 'feat: add y'", br(nil))
	})
}
