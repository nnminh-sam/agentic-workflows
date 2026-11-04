import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import GOOD_SSH, CWD, FakeRunner, evaluate, ids
from gitguard import identity


class Author(unittest.TestCase):
    def test_matching_identity_passes(self):
        self.assertEqual(evaluate("git commit -m 'feat: add x'").findings, [])

    def test_wrong_name_and_email_denied(self):
        v = evaluate("git commit -m 'feat: add x'", runner=FakeRunner(name="someone-else", email="x@other.example"))
        self.assertEqual((v.decision, ids(v)), ("deny", ["ID001", "ID002"]))
        self.assertIn("git -C", v.findings[0].message)

    def test_unset_identity_denied(self):
        self.assertEqual(ids(evaluate("git commit -m 'feat: add x'", runner=FakeRunner(name=None, email=None))),
                         ["ID001", "ID002"])

    def test_author_flag_overrides_config(self):
        bad = FakeRunner(name="other", email="o@x.y")
        ok = '--author="octocat <123+OctoCat@users.noreply.github.com>"'  # email match is case-insensitive
        self.assertEqual(evaluate(f"git commit {ok} -m 'feat: add x'", runner=bad).findings, [])
        v = evaluate("git commit --author='Mallory <m@evil.io>' -m 'feat: add x'")
        self.assertEqual(ids(v), ["ID001", "ID002"])

    def test_dash_c_and_env_overrides(self):
        v = evaluate("git -c user.email=me@corp.com commit -m 'feat: add x'")
        self.assertEqual(ids(v), ["ID002"])
        v = evaluate("GIT_AUTHOR_NAME=bob git commit -m 'feat: add x'")
        self.assertEqual(ids(v), ["ID001"])

    def test_other_git_commands_do_not_check_author(self):
        self.assertEqual(evaluate("git status && git log", runner=FakeRunner(name="x", email="y")).findings, [])


class Network(unittest.TestCase):
    def test_push_with_pinned_key_and_allowed_remote(self):
        self.assertEqual(evaluate("git push origin main").findings, [])

    def test_push_without_ssh_command(self):
        v = evaluate("git push", runner=FakeRunner(ssh=None))
        self.assertEqual(ids(v), ["ID003"])

    def test_ssh_command_from_env_or_flag(self):
        none = FakeRunner(ssh=None)
        self.assertEqual(evaluate(f"GIT_SSH_COMMAND='{GOOD_SSH}' git fetch", runner=none).findings, [])
        self.assertEqual(evaluate(f"git -c core.sshCommand='{GOOD_SSH}' pull", runner=none).findings, [])
        self.assertEqual(evaluate("git fetch", runner=none, env={"GIT_SSH_COMMAND": GOOD_SSH}).findings, [])

    def test_wrong_key_or_missing_identities_only(self):
        v = evaluate("git push", runner=FakeRunner(ssh="ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes"))
        self.assertIn("is not the profile's ssh_key", v.findings[0].message)
        v = evaluate("git push", runner=FakeRunner(ssh='ssh -i "D:/keys/gh/id_ed25519"'))
        self.assertIn("IdentitiesOnly=yes", v.findings[0].message)

    def test_ssh_option_spellings(self):
        p = identity.ssh_command_problems(r'ssh -i "D:\keys\gh\id_ed25519" -oIdentitiesOnly=YES',
                                          "keys/gh/id_ed25519", ["IdentitiesOnly=yes"])
        self.assertEqual(p, [])
        # git runs core.sshCommand through sh, so unquoted backslashes are really lost: flag it
        p = identity.ssh_command_problems(r'ssh -i D:\keys\gh\id_ed25519 -o IdentitiesOnly=yes',
                                          "keys/gh/id_ed25519", ["IdentitiesOnly=yes"])
        self.assertEqual(len(p), 1)

    def test_https_remote_denied(self):
        v = evaluate("git push", runner=FakeRunner(remotes={"origin": "https://github.com/octocat/repo.git"}))
        self.assertEqual(ids(v), ["ID004"])

    def test_foreign_owner_denied(self):
        v = evaluate("git push upstream", runner=FakeRunner(remotes={"upstream": "git@github.com:someone/repo.git"}))
        self.assertEqual(ids(v), ["ID004"])

    def test_push_uses_branch_remote(self):
        r = FakeRunner(remotes={"origin": "git@github.com:octocat/r.git", "fork": "git@github.com:bad/r.git"})
        r.config["branch.main.remote"] = "fork"
        self.assertEqual(ids(evaluate("git push", runner=r)), ["ID004"])

    def test_fetch_all_checks_every_remote(self):
        r = FakeRunner(remotes={"origin": "git@github.com:octocat/r.git", "other": "https://gitlab.com/x/y.git"})
        self.assertEqual(ids(evaluate("git fetch --all", runner=r)), ["ID004"])

    def test_clone(self):
        none = FakeRunner(ssh=None)
        good = f"git clone -c core.sshCommand='{GOOD_SSH}' git@github.com:octocat/x.git"
        self.assertEqual(evaluate(good, runner=none).findings, [])
        self.assertEqual(ids(evaluate("git clone https://github.com/octocat/x.git", runner=none)), ["ID004"])
        self.assertEqual(ids(evaluate("git clone git@github.com:octocat/x.git", runner=none)), ["ID003"])

    def test_local_clone_ignored(self):
        self.assertEqual(evaluate("git clone ../other copy", runner=FakeRunner(ssh=None)).findings, [])
        self.assertEqual(evaluate("git clone D:/repos/x copy", runner=FakeRunner(ssh=None)).findings, [])

    def test_remote_add_and_set_url(self):
        self.assertEqual(ids(evaluate("git remote add up https://github.com/a/b")), ["ID004"])
        self.assertEqual(ids(evaluate("git remote set-url origin git@github.com:other/b.git")), ["ID004"])
        self.assertEqual(evaluate("git remote set-url origin git@github.com:octocat/b.git").findings, [])

    def test_unknown_remote_is_not_guessed(self):
        self.assertEqual(evaluate("git push", runner=FakeRunner(remotes={})).findings, [])


class GhAccount(unittest.TestCase):
    def test_expected_account(self):
        self.assertEqual(evaluate("gh pr list").findings, [])

    def test_wrong_account_denied(self):
        v = evaluate("gh pr list", runner=FakeRunner(gh_login="someone-else"))
        self.assertEqual((v.decision, ids(v)), ("deny", ["ID005"]))
        self.assertIn("gh auth switch", v.findings[0].message)

    def test_not_logged_in(self):
        v = evaluate("gh issue list", runner=FakeRunner(gh_login=None))
        self.assertIn("gh auth login", v.findings[0].message)

    def test_auth_commands_skip_account_check(self):
        r = FakeRunner(gh_login="someone-else")
        self.assertEqual(evaluate("gh auth status", runner=r).findings, [])
        self.assertNotIn("ID005", ids(evaluate("gh auth switch --user octocat", runner=r)))

    def test_cache(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(identity, "cache_dir", return_value=Path(tmp)):
            env = {"GH_CONFIG_DIR": os.path.join(tmp, "cfg")}
            r = FakeRunner()
            t = [1000.0]
            first = identity.gh_active_login("github.com", env, r, CWD, ttl=60, now=lambda: t[0])
            r.gh_login = "changed"
            t[0] += 30
            cached = identity.gh_active_login("github.com", env, r, CWD, ttl=60, now=lambda: t[0])
            t[0] += 60
            fresh = identity.gh_active_login("github.com", env, r, CWD, ttl=60, now=lambda: t[0])
            self.assertEqual((first[0], cached[0], fresh[0]), ("octocat", "octocat", "changed"))
            self.assertEqual(sum(1 for c in r.calls if c[0] == "gh"), 2)
            # a different token is a different cache entry
            other = identity.gh_active_login("github.com", {**env, "GH_TOKEN": "t"}, r, CWD, ttl=60, now=lambda: t[0])
            self.assertEqual(other[0], "changed")
            identity.clear_gh_cache()
            self.assertEqual(list(Path(tmp).glob("gh-*.json")), [])


if __name__ == "__main__":
    unittest.main()
