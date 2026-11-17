package engine

// Port of tests/test_identity.py. Each subtest name is the Python test name.
// test_ssh_option_spellings, test_gh_helper_spellings and test_cache are
// unit tests of rules functions and live in internal/rules/identity_test.go.

import (
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

const commitX = "git commit -m 'feat: add x'"

func noSSH(mut func(*fakeRepo)) *fakeRepo {
	return fake(func(r *fakeRepo) {
		delete(r.config, "core.sshCommand")
		if mut != nil {
			mut(r)
		}
	})
}

func TestAuthor(t *testing.T) {
	t.Run("test_matching_identity_passes", func(t *testing.T) { clean(t, commitX, opts{}) })
	t.Run("test_wrong_name_and_email_denied", func(t *testing.T) {
		v := expect(t, commitX, opts{repo: fake(func(r *fakeRepo) {
			r.config["user.name"], r.config["user.email"] = "someone-else", "x@other.example"
		})}, verdict.DecDeny, []string{"ID001", "ID002"})
		if len(v.Findings) == 0 || !strings.Contains(v.Findings[0].Message, "git -C") {
			t.Errorf("first message lacks 'git -C': %s", v.Reason())
		}
	})
	t.Run("test_unset_identity_denied", func(t *testing.T) {
		expect(t, commitX, opts{repo: fake(func(r *fakeRepo) {
			delete(r.config, "user.name")
			delete(r.config, "user.email")
		})}, verdict.DecDeny, []string{"ID001", "ID002"})
	})
	t.Run("test_author_flag_overrides_config", func(t *testing.T) {
		bad := fake(func(r *fakeRepo) { r.config["user.name"], r.config["user.email"] = "other", "o@x.y" })
		clean(t, `git commit --author="octocat <123+OctoCat@users.noreply.github.com>" -m 'feat: add x'`, opts{repo: bad})
		expect(t, "git commit --author='Mallory <m@evil.io>' -m 'feat: add x'", opts{}, verdict.DecDeny, []string{"ID001", "ID002"})
	})
	t.Run("test_dash_c_and_env_overrides", func(t *testing.T) {
		expect(t, "git -c user.email=me@corp.com commit -m 'feat: add x'", opts{}, verdict.DecDeny, []string{"ID002"})
		expect(t, "GIT_AUTHOR_NAME=bob git commit -m 'feat: add x'", opts{}, verdict.DecDeny, []string{"ID001"})
	})
	t.Run("test_other_git_commands_do_not_check_author", func(t *testing.T) {
		clean(t, "git status && git log", opts{repo: fake(func(r *fakeRepo) { r.config["user.name"], r.config["user.email"] = "x", "y" })})
	})
}

func TestNetwork(t *testing.T) {
	t.Run("test_push_with_pinned_key_and_allowed_remote", func(t *testing.T) { clean(t, "git push origin develop/feat/test", opts{}) })
	t.Run("test_push_without_ssh_command", func(t *testing.T) {
		expect(t, "git push", opts{repo: noSSH(nil)}, verdict.DecDeny, []string{"ID003"})
	})
	t.Run("test_ssh_command_from_env_or_flag", func(t *testing.T) {
		clean(t, "GIT_SSH_COMMAND='"+goodSSH+"' git fetch", opts{repo: noSSH(nil)})
		clean(t, "git -c core.sshCommand='"+goodSSH+"' pull", opts{repo: noSSH(nil)})
		clean(t, "git fetch", opts{repo: noSSH(nil), env: map[string]string{"GIT_SSH_COMMAND": goodSSH}})
	})
	t.Run("test_wrong_key_or_missing_identities_only", func(t *testing.T) {
		v := run(t, "git push", opts{repo: fake(func(r *fakeRepo) { r.config["core.sshCommand"] = "ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes" })})
		if len(v.Findings) == 0 || !strings.Contains(v.Findings[0].Message, "is not the profile's ssh_key") {
			t.Errorf("wrong key: %s", v.Reason())
		}
		v = run(t, "git push", opts{repo: fake(func(r *fakeRepo) { r.config["core.sshCommand"] = `ssh -i "D:/keys/gh/id_ed25519"` })})
		if len(v.Findings) == 0 || !strings.Contains(v.Findings[0].Message, "IdentitiesOnly=yes") {
			t.Errorf("identities only: %s", v.Reason())
		}
	})
	t.Run("test_https_remote_denied", func(t *testing.T) {
		expect(t, "git push", opts{repo: fake(func(r *fakeRepo) { r.remotes = map[string]string{"origin": "https://github.com/octocat/repo.git"} })},
			verdict.DecDeny, []string{"ID004"})
	})
	t.Run("test_foreign_owner_denied", func(t *testing.T) {
		expect(t, "git push upstream", opts{repo: fake(func(r *fakeRepo) { r.remotes = map[string]string{"upstream": "git@github.com:someone/repo.git"} })},
			verdict.DecDeny, []string{"ID004"})
	})
	t.Run("test_push_uses_branch_remote", func(t *testing.T) {
		r := fake(func(r *fakeRepo) {
			r.remotes = map[string]string{"origin": "git@github.com:octocat/r.git", "fork": "git@github.com:bad/r.git"}
			r.config["branch.develop/feat/test.remote"] = "fork"
		})
		expect(t, "git push", opts{repo: r}, verdict.DecDeny, []string{"ID004"})
	})
	t.Run("test_fetch_all_checks_every_remote", func(t *testing.T) {
		r := fake(func(r *fakeRepo) {
			r.remotes = map[string]string{"origin": "git@github.com:octocat/r.git", "other": "https://gitlab.com/x/y.git"}
		})
		expect(t, "git fetch --all", opts{repo: r}, verdict.DecDeny, []string{"ID004"})
	})
	t.Run("test_clone", func(t *testing.T) {
		clean(t, "git clone -c core.sshCommand='"+goodSSH+"' git@github.com:octocat/x.git", opts{repo: noSSH(nil)})
		expect(t, "git clone https://github.com/octocat/x.git", opts{repo: noSSH(nil)}, verdict.DecDeny, []string{"ID004"})
		expect(t, "git clone git@github.com:octocat/x.git", opts{repo: noSSH(nil)}, verdict.DecDeny, []string{"ID003"})
	})
	t.Run("test_local_clone_ignored", func(t *testing.T) {
		clean(t, "git clone ../other copy", opts{repo: noSSH(nil)})
		clean(t, "git clone D:/repos/x copy", opts{repo: noSSH(nil)})
	})
	t.Run("test_remote_add_and_set_url", func(t *testing.T) {
		expect(t, "git remote add up https://github.com/a/b", opts{}, verdict.DecDeny, []string{"ID004"})
		expect(t, "git remote set-url origin git@github.com:other/b.git", opts{}, verdict.DecDeny, []string{"ID004"})
		clean(t, "git remote set-url origin git@github.com:octocat/b.git", opts{})
	})
	t.Run("test_unknown_remote_is_not_guessed", func(t *testing.T) {
		clean(t, "git push", opts{repo: fake(func(r *fakeRepo) { r.remotes = map[string]string{} })})
	})
}

// ghEval is test_identity.gh_eval.
func ghEval(t *testing.T, cmd string, mut func(*fakeRepo)) verdict.Verdict {
	t.Helper()
	r := noSSH(func(r *fakeRepo) {
		r.remotes = map[string]string{"origin": "https://github.com/octocat/repo.git"}
		r.config["credential.helper"] = ghHelper
		if mut != nil {
			mut(r)
		}
	})
	return run(t, cmd, opts{repo: r, pol: testPolicy(t, ghProfile(), nil)})
}

func ghWant(t *testing.T, cmd string, mut func(*fakeRepo), want ...string) verdict.Verdict {
	t.Helper()
	v := ghEval(t, cmd, mut)
	got := ids(v)
	if want == nil {
		want = []string{}
	}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("%q: got %v want %v\n%s", cmd, got, want, v.Reason())
	}
	return v
}

func TestGhTransport(t *testing.T) {
	t.Run("test_push_over_https_with_gh_helper_passes", func(t *testing.T) {
		for _, cmd := range []string{"git push", "git push -u origin develop/feat/test", "git fetch", "git pull", "git ls-remote origin"} {
			ghWant(t, cmd, nil)
		}
	})
	t.Run("test_commit_still_guarded", func(t *testing.T) {
		ghWant(t, commitX, nil)
		ghWant(t, commitX, func(r *fakeRepo) { r.config["user.name"], r.config["user.email"] = "other", "o@x.y" }, "ID001", "ID002")
	})
	t.Run("test_ssh_remote_denied", func(t *testing.T) {
		v := ghWant(t, "git push", func(r *fakeRepo) { r.remotes["origin"] = "git@github.com:octocat/repo.git" }, "ID004")
		if !strings.Contains(messages(v), "HTTPS remote") {
			t.Errorf("message: %s", v.Reason())
		}
	})
	t.Run("test_foreign_owner_still_denied", func(t *testing.T) {
		ghWant(t, "git push", func(r *fakeRepo) { r.remotes["origin"] = "https://github.com/someone/r.git" }, "ID004")
	})
	t.Run("test_non_gh_helper_denied", func(t *testing.T) {
		v := ghWant(t, "git push", func(r *fakeRepo) { r.config["credential.helper"] = "osxkeychain" }, "ID003")
		if !strings.Contains(messages(v), "gh auth setup-git") {
			t.Errorf("message: %s", v.Reason())
		}
		ghWant(t, "git push", func(r *fakeRepo) { delete(r.config, "credential.helper") }, "ID003")
	})
	t.Run("test_dash_c_helper_override", func(t *testing.T) {
		ghWant(t, "git -c credential.helper=store push", nil, "ID003")
		ghWant(t, "git -c credential.https://github.com.helper=store push", nil, "ID003")
		ghWant(t, "git -c 'credential.helper=!gh auth git-credential' push", func(r *fakeRepo) { delete(r.config, "credential.helper") })
	})
	t.Run("test_wrong_gh_account_blocks_push", func(t *testing.T) {
		v := ghWant(t, "git push", func(r *fakeRepo) { r.ghLogin = "someone-else" }, "ID003")
		if !strings.Contains(messages(v), "gh auth switch") {
			t.Errorf("message: %s", v.Reason())
		}
		ghWant(t, "git push", func(r *fakeRepo) { r.ghLogin = "" }, "ID003")
	})
	t.Run("test_token_in_url_denied_and_redacted", func(t *testing.T) {
		v := ghWant(t, "git push https://x:ghp_secret@github.com/octocat/repo.git develop/feat/test", nil, "ID003")
		if strings.Contains(messages(v), "ghp_secret") {
			t.Errorf("token leaked: %s", v.Reason())
		}
	})
	t.Run("test_clone_and_remote_set_url", func(t *testing.T) {
		ghWant(t, "git clone https://github.com/octocat/x.git", nil)
		ghWant(t, "git clone git@github.com:octocat/x.git", nil, "ID004")
		ghWant(t, "git remote set-url origin https://github.com/octocat/b.git", nil)
	})
	t.Run("test_unset_transport_keeps_ssh_rules", func(t *testing.T) {
		r := noSSH(func(r *fakeRepo) {
			r.remotes = map[string]string{"origin": "https://github.com/octocat/repo.git"}
			r.config["credential.helper"] = ghHelper
		})
		expect(t, "git push", opts{repo: r}, verdict.DecDeny, []string{"ID004"})
	})
}

func TestGhAccount(t *testing.T) {
	t.Run("test_expected_account", func(t *testing.T) { clean(t, "gh pr list", opts{}) })
	t.Run("test_wrong_account_denied", func(t *testing.T) {
		v := expect(t, "gh pr list", opts{repo: fake(func(r *fakeRepo) { r.ghLogin = "someone-else" })}, verdict.DecDeny, []string{"ID005"})
		if !strings.Contains(v.Findings[0].Message, "gh auth switch") {
			t.Errorf("message: %s", v.Reason())
		}
	})
	t.Run("test_not_logged_in", func(t *testing.T) {
		v := run(t, "gh issue list", opts{repo: fake(func(r *fakeRepo) { r.ghLogin = "" })})
		if len(v.Findings) == 0 || !strings.Contains(v.Findings[0].Message, "gh auth login") {
			t.Errorf("message: %s", v.Reason())
		}
	})
	t.Run("test_auth_commands_skip_account_check", func(t *testing.T) {
		r := fake(func(r *fakeRepo) { r.ghLogin = "someone-else" })
		clean(t, "gh auth status", opts{repo: r})
		if contains(ids(run(t, "gh auth switch --user octocat", opts{repo: r})), "ID005") {
			t.Error("gh auth switch should skip ID005")
		}
	})
	t.Run("gh_state_not_success", func(t *testing.T) { // FakeRunner(gh_state=...) path
		expect(t, "gh pr list", opts{repo: fake(func(r *fakeRepo) { r.ghState = "error" })}, verdict.DecDeny, []string{"ID005"})
	})
	t.Run("auth_switch_clears_cache", func(t *testing.T) {
		r := newFake()
		run(t, "gh pr list && gh auth switch --user octocat && gh pr list", opts{repo: r})
		if r.cleared == 0 {
			t.Error("gh auth switch should clear the gh cache")
		}
	})
}
