"""Shared test helpers: import path, contracts without the gh cache, and a fake command runner."""
from __future__ import annotations

import copy
import json
import os
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from gitguard import contracts as contracts_mod  # noqa: E402
from gitguard import profile as profile_mod  # noqa: E402

GOOD_SSH = 'ssh -i "D:/keys/gh/id_ed25519" -o IdentitiesOnly=yes'
CWD = os.path.abspath(os.sep + "repo")
_RAW = contracts_mod.load_raw()

# A fictional user. Tests never depend on the real machine's profile files or env.
PROFILE = {
    "github_user": "octocat",
    "github_orgs": ["octo-org"],
    "git_name": "octocat",
    "git_email": ["123+octocat@users.noreply.github.com"],
    "ssh_key": "keys/gh/id_ed25519",
}
NO_PROFILE: dict = {}


def contracts(mutate=None, profile=None):
    """Fresh Contracts from the shipped JSON bound to a test profile, gh status cache disabled."""
    raw = copy.deepcopy(_RAW)
    raw["identity"]["settings"]["gh_status_cache_seconds"] = 0
    if mutate:
        mutate(raw)
    prof = PROFILE if profile is None else profile
    values = {k: profile_mod.Value(prof.get(k), "test" if k in prof else "unset")
              for k in raw["identity"]["profile_variables"]}
    return contracts_mod.build(raw, values)


def set_severity(raw, rule_id, severity):
    for c in raw.values():
        for r in c["rules"]:
            if r["id"] == rule_id:
                r["severity"] = severity


class FakeRunner:
    """Answers the git/gh queries the checks make; records every call."""

    def __init__(self, name="octocat", email="123+octocat@users.noreply.github.com",
                 ssh=GOOD_SSH, remotes=None, gh_login="octocat", gh_state="success", branch="main"):
        self.config = {"user.name": name, "user.email": email, "core.sshCommand": ssh}
        self.remotes = {"origin": "git@github.com:octocat/repo.git"} if remotes is None else remotes
        self.gh_login, self.gh_state, self.branch = gh_login, gh_state, branch
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv[0] == "gh":
            if self.gh_login is None:
                return 1, json.dumps({"hosts": {}})
            return 0, json.dumps({"hosts": {"github.com": [
                {"state": self.gh_state, "active": True, "host": "github.com", "login": self.gh_login}]}})
        rest = argv[3:] if argv[:2] == ["git", "-C"] else argv[1:]
        if rest[:2] == ["config", "--get"]:
            val = self.config.get(rest[2])
            return (0, val + "\n") if val else (1, "")
        if rest[:2] == ["remote", "get-url"]:
            name = rest[-1]
            return (0, self.remotes[name] + "\n") if name in self.remotes else (2, "")
        if rest == ["remote"]:
            return 0, "\n".join(self.remotes) + "\n"
        if rest[:1] == ["symbolic-ref"]:
            return (0, self.branch + "\n") if self.branch else (1, "")
        return 1, ""


def evaluate(command, dialect="bash", runner=None, env=None, contracts_obj=None, files=None, cwd=CWD):
    from gitguard.engine import evaluate as _evaluate
    files = files or {}

    def read_file(path):
        for k, v in files.items():
            if os.path.normpath(path).endswith(os.path.normpath(k)):
                return v
        raise FileNotFoundError(path)

    return _evaluate(command, dialect, cwd, contracts_obj or contracts(), run=runner or FakeRunner(),
                     process_env=env if env is not None else {"GH_CONFIG_DIR": os.path.join(CWD, "no-gh")},
                     read_file=read_file)


def ids(verdict):
    return sorted({f.rule_id for f in verdict.findings})
