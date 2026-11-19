#!/usr/bin/env python3
"""Claude Code SessionStart hook: refresh rules.html and tell Claude about identity problems up front."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import contracts as contracts_mod  # noqa: E402
from gitguard import identity, paths  # noqa: E402
from render_rules import render  # noqa: E402


def author_dirs(cwd: str, run=identity.default_runner) -> list[str]:
    """Where to read the commit identity from.

    git config is per repository: an `includeIf "gitdir:..."` identity only applies inside a repo. When the session
    starts in a plain folder that holds repos (a workspace), reading `user.name` there returns the global default,
    which is not what any commit in those repos will use. So check the repos directly under cwd instead; fall back
    to cwd itself when it is a repo or holds none.
    """
    code, out = run(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"], cwd)
    if code == 0 and out.strip() == "true":
        return [cwd]
    try:
        children = sorted(e.path for e in os.scandir(cwd)
                          if e.is_dir() and os.path.exists(os.path.join(e.path, ".git")))
    except OSError:
        children = []
    return children or [cwd]


def summary(cwd: str, contracts, run=identity.default_runner, env=None) -> list[str]:
    env = dict(os.environ if env is None else env)
    c = contracts.identity
    notes = []
    r1, r2, r5 = c.rule("ID001"), c.rule("ID002"), c.rule("ID005")
    dirs = author_dirs(cwd, run)
    for d in dirs:
        where = "" if d == cwd else f"[{os.path.basename(d)}] "
        _, name = run(["git", "-C", d, "config", "--get", "user.name"], d)
        _, email = run(["git", "-C", d, "config", "--get", "user.email"], d)
        if r1.enabled:
            p = identity.name_problem(name.strip() or None, r1.params)
            if p:
                notes.append(f"{where}{p} (ID001).")
        if r2.enabled:
            p = identity.email_problem(email.strip() or None, r2.params)
            if p:
                notes.append(f"{where}{p} (ID002).")
    if r5.enabled and pr_via(contracts) != "browser":  # browser mode: gh is never run, so its login is moot
        host = c.settings.get("gh_hostname", "github.com")
        login, note = identity.gh_active_login(host, env, run, cwd, ttl=int(c.settings.get("gh_status_cache_seconds", 300)))
        p = identity.gh_login_problem(login, note, host, r5.params)
        if p:
            notes.append(f"{p} (ID005).")
    if c.rule("ID003").enabled and identity.transport(c) == "gh":
        host = c.settings.get("gh_hostname", "github.com")
        _, helper = run(["git", "-C", cwd, "config", "--get-urlmatch", "credential.helper", f"https://{host}"], cwd)
        helper = helper.strip().splitlines()[-1].strip() if helper.strip() else None
        if not identity.is_gh_helper(helper):
            notes.append(f"git_transport is 'gh' but the git credential helper for {host} is "
                         f"{repr(helper) if helper else 'not set'}; pushes will be blocked. "
                         f"Fix: gh auth setup-git --hostname {host} (ID003).")
    return notes


def pr_via(contracts) -> str:
    v = contracts.profile.get("pr_via")
    return v.value if v is not None and v.value else "gh"


def unset_profile(contracts) -> list[str]:
    skip = {"ssh_key"} if identity.transport(contracts.identity) == "gh" else set()
    skip.add("github_orgs")  # optional: only needed when you push to organisation repos
    skip.add("signing_key")  # optional: only the commit skill reads it (unset = unsigned commits)
    skip.add("pr_via")  # optional: unset means gh
    return [k for k, v in contracts.profile.items() if v.value is None and k not in skip]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    lines = []
    try:
        render()  # cheap; also picks up profile changes made through env vars
        contracts = contracts_mod.load()
        notes = summary(cwd, contracts)
        if notes:
            lines.append("git-guard identity check (matching commits / pushes / gh calls will be blocked until fixed):")
            lines += [f"- {n}" for n in notes]
            lines.append("Do not change git/gh identity yourself; tell the user and let them decide.")
        if pr_via(contracts) == "browser":
            lines.append("git-guard: pr_via is 'browser' here. Do not run gh (GH010 denies it); /my-git:pr prints a "
                         "prefilled compare link for the user to open and submit.")
        missing = unset_profile(contracts)
        if missing:
            lines.append(f"git-guard: profile values not set ({', '.join(missing)}); those identity rules only check "
                         f"presence. Set them with: python3 \"{paths.display(paths.SCRIPTS_DIR / 'setup_profile.py')}\" init --from-current "
                         f"(ask the user first; it records their identity)")
    except contracts_mod.ContractError as e:
        lines.append(f"git-guard: contracts or profile are invalid, git/gh commands will need confirmation: {e}")
    except Exception as e:  # informational hook: never fail the session
        lines.append(f"git-guard: session check skipped ({type(e).__name__}: {e})")
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
