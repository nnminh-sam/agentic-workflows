import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import NO_PROFILE, FakeRunner, contracts, evaluate, ids
from gitguard import contracts as contracts_mod
from gitguard import identity
from gitguard import profile as P
import render_rules
import setup_profile

DECLS = contracts_mod.load_raw()["identity"]["profile_variables"]


class Loading(unittest.TestCase):
    def test_precedence_env_then_project_then_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, user = Path(tmp, "p.json"), Path(tmp, "u.json")
            proj.write_text(json.dumps({"github_user": "proj-user"}), encoding="utf-8")
            user.write_text(json.dumps({"github_user": "home-user", "git_name": "Home", "github_orgs": ["a", "b"]}),
                            encoding="utf-8")
            prof = P.load(DECLS, env={}, files=(proj, user))
            self.assertEqual((prof["github_user"].value, prof["github_user"].source), ("proj-user", str(proj)))
            self.assertEqual(prof["git_name"].value, "Home")
            self.assertEqual(prof["github_orgs"].value, ["a", "b"])
            self.assertEqual((prof["ssh_key"].value, prof["ssh_key"].source), (None, "unset"))
            prof = P.load(DECLS, env={"GIT_GUARD_GITHUB_USER": "env-user", "GIT_GUARD_GIT_EMAIL": "a@x.io, b@x.io"},
                          files=(proj, user))
            self.assertEqual(prof["github_user"].value, "env-user")
            self.assertEqual(prof["git_email"].value, ["a@x.io", "b@x.io"])

    def test_string_list_is_coerced(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "p.json")
            f.write_text(json.dumps({"git_email": "only@x.io"}), encoding="utf-8")
            self.assertEqual(P.load(DECLS, env={}, files=(f,))["git_email"].value, ["only@x.io"])

    def test_bad_profile_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "p.json")
            f.write_text("[1,2]", encoding="utf-8")
            with self.assertRaises(P.ProfileError):
                P.load(DECLS, env={}, files=(f,))
            f.write_text(json.dumps({"github_user": ["a", "b"]}), encoding="utf-8")
            with self.assertRaises(P.ProfileError):
                P.load(DECLS, env={}, files=(f,))

    def test_load_wraps_profile_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "p.json")
            f.write_text("{oops", encoding="utf-8")
            with self.assertRaises(contracts_mod.ContractError):
                contracts_mod.load(env={}, profile_files=(f,))


class Substitution(unittest.TestCase):
    prof = {"u": P.Value("a.b", "t"), "orgs": P.Value(["o1", "o2"], "t"), "none": P.Value(None, "unset")}

    def test_rules(self):
        s = lambda o, rx=False: P.substitute(o, self.prof, rx)
        self.assertEqual(s("${u}"), "a.b")
        self.assertIsNone(s("${none}"))
        self.assertEqual(s(["${u}", "${orgs}", "${none}", "fixed"]), ["a.b", "o1", "o2", "fixed"])
        self.assertEqual(s("^${u}@x$", rx=True), r"^a\.b@x$")
        self.assertEqual(s(["^${none}$", "keep"], rx=True), ["keep"])
        self.assertEqual(s({"k": ["${orgs}"]}), {"k": ["o1", "o2"]})

    def test_references(self):
        self.assertEqual(P.references({"a": ["${x}", "y"], "b": "pre-${z}"}), {"x", "z"})

    def test_undeclared_variable_is_a_contract_error(self):
        raw = copy.deepcopy(contracts_mod.load_raw())
        raw["github-cli"]["rules"][5]["params"]["allowed_owners"] = ["${nope}"]
        self.assertTrue(any("${nope}" in e for e in contracts_mod.validate_all(raw)))


class WithoutProfile(unittest.TestCase):
    """No account values configured: rules check presence and consistency only."""

    def ev(self, cmd, runner=None):
        return evaluate(cmd, runner=runner, contracts_obj=contracts(profile=NO_PROFILE))

    def test_any_set_identity_commits(self):
        r = FakeRunner(name="Anyone", email="anyone@example.org")
        self.assertEqual(self.ev("git commit -m 'feat: add x'", runner=r).findings, [])

    def test_missing_or_bad_identity_denied(self):
        self.assertEqual(ids(self.ev("git commit -m 'feat: add x'", runner=FakeRunner(name=None, email=None))),
                         ["ID001", "ID002"])
        self.assertEqual(ids(self.ev("git commit -m 'feat: add x'", runner=FakeRunner(email="not-an-email"))), ["ID002"])

    def test_remote_any_owner_but_ssh_and_host(self):
        ok = FakeRunner(remotes={"origin": "git@github.com:anybody/repo.git"}, ssh="ssh -i ~/.ssh/k -o IdentitiesOnly=yes")
        self.assertEqual(self.ev("git push", runner=ok).findings, [])
        self.assertEqual(ids(self.ev("git push", runner=FakeRunner(remotes={"origin": "https://github.com/a/b"}))), ["ID004"])
        self.assertEqual(ids(self.ev("git push", runner=FakeRunner(remotes={"origin": "git@gitlab.com:a/b.git"}))), ["ID004"])

    def test_ssh_still_needs_a_pinned_key(self):
        self.assertEqual(ids(self.ev("git push", runner=FakeRunner(ssh=None))), ["ID003"])
        self.assertEqual(ids(self.ev("git push", runner=FakeRunner(ssh="ssh -o IdentitiesOnly=yes"))), ["ID003"])

    def test_gh_any_logged_in_account(self):
        self.assertEqual(self.ev("gh pr list", runner=FakeRunner(gh_login="whoever")).findings, [])
        self.assertEqual(ids(self.ev("gh pr list", runner=FakeRunner(gh_login=None))), ["ID005"])

    def test_owner_scope_is_skipped(self):
        self.assertEqual(self.ev("gh issue list -R someone/x", runner=FakeRunner()).findings, [])


class WithProfile(unittest.TestCase):
    def test_orgs_extend_allowed_owners(self):
        self.assertEqual(evaluate("git push", runner=FakeRunner(remotes={"origin": "git@github.com:octo-org/r.git"})).findings, [])
        self.assertEqual(evaluate("gh issue list -R octo-org/r").findings, [])

    def test_extra_patterns_allow_aliases(self):
        def m(raw):
            for r in raw["identity"]["rules"]:
                if r["id"] == "ID004":
                    r["params"]["extra_patterns"] = [r"^git@github-${github_user}:.+$"]
        r = FakeRunner(remotes={"origin": "git@github-octocat:octocat/r.git"})
        self.assertEqual(evaluate("git push", runner=r, contracts_obj=contracts(mutate=m)).findings, [])

    def test_key_matching(self):
        self.assertTrue(identity._key_matches("C:/Users/me/.ssh/id_gh", "~/.ssh/id_gh"))
        self.assertTrue(identity._key_matches(r"D:\keys\gh\id_ed25519", "keys/gh/id_ed25519"))
        self.assertFalse(identity._key_matches("/x/notkeys/gh/id_ed25519", "keys/gh/id_ed25519"))
        self.assertFalse(identity._key_matches("~/.ssh/id_rsa", "~/.ssh/id_gh"))

    def test_parse_remote(self):
        self.assertEqual(identity.parse_remote("git@github.com:o/r.git"), ("ssh", "github.com", "o"))
        self.assertEqual(identity.parse_remote("ssh://git@github.com:22/o/r"), ("ssh", "github.com", "o"))
        self.assertEqual(identity.parse_remote("https://github.com/o/r"), ("https", "github.com", "o"))


class SetupProfileCli(unittest.TestCase):
    def test_init_show_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, user = Path(tmp, "proj.json"), Path(tmp, "user.json")
            files = {"project": proj, "user": user}
            r = FakeRunner(name="Jane", email="jane@x.io", ssh="ssh -i ~/.ssh/jane -o IdentitiesOnly=yes", gh_login="jane")
            got = setup_profile.detect_current(tmp, run=r, env={"GH_CONFIG_DIR": tmp})
            self.assertEqual(got, {"git_name": "Jane", "git_email": ["jane@x.io"], "ssh_key": "~/.ssh/jane",
                                   "github_user": "jane"})
            with mock.patch.dict(setup_profile.FILES, files), mock.patch("builtins.print"), \
                    mock.patch.object(setup_profile, "detect_current", lambda cwd: got):
                self.assertEqual(setup_profile.main(["init", "--github-user", "joe", "--git-email", "a@x.io,b@x.io"]), 0)
                self.assertEqual(json.loads(proj.read_text(encoding="utf-8")),
                                 {"github_user": "joe", "git_email": ["a@x.io", "b@x.io"]})
                # --from-current fills only what is missing (explicit flags still win)
                self.assertEqual(setup_profile.main(["init", "--from-current"]), 0)
                self.assertEqual(json.loads(proj.read_text(encoding="utf-8")),
                                 {"github_user": "joe", "git_email": ["a@x.io", "b@x.io"], "git_name": "Jane",
                                  "ssh_key": "~/.ssh/jane"})
                self.assertEqual(setup_profile.main(["unset", "git_email", "ssh_key"]), 0)
                self.assertEqual(json.loads(proj.read_text(encoding="utf-8")), {"github_user": "joe", "git_name": "Jane"})
                self.assertEqual(setup_profile.main(["init", "--scope", "user", "--ssh-key", "~/.ssh/k"]), 0)
                self.assertEqual(json.loads(user.read_text(encoding="utf-8")), {"ssh_key": "~/.ssh/k"})
                self.assertFalse(Path(tmp, "real").exists())


class RenderProfile(unittest.TestCase):
    def test_profile_rows_in_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "p.json")
            f.write_text(json.dumps({"github_user": "jane"}), encoding="utf-8")
            html = render_rules.build_html(env={}, profile_files=(f,))
        data = json.loads(re.search(r'application/json">(.*?)</script>', html, re.DOTALL).group(1).replace("<\\/", "</"))
        rows = {r["name"]: r for r in data["profile"]}
        self.assertEqual(rows["github_user"]["value"], "jane")
        self.assertEqual(rows["ssh_key"]["source"], "unset")

    def test_shipped_contracts_name_no_account(self):
        """The shared contracts must stay account-agnostic: every account value is a ${variable}."""
        raw = contracts_mod.load_raw()
        for name, data in raw.items():
            for r in data["rules"]:
                for key in ("expected", "expected_logins", "allowed_owners", "key_path"):
                    vals = r.get("params", {}).get(key)
                    if vals is None:
                        continue
                    for v in vals if isinstance(vals, list) else [vals]:
                        self.assertRegex(v, r"^\$\{\w+\}$", f"{name} {r['id']} {key} hardcodes {v!r}")


if __name__ == "__main__":
    unittest.main()
