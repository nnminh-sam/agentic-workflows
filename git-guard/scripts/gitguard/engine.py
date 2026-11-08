"""Evaluate one shell command against all contracts."""
from __future__ import annotations

import os
import re
from typing import Callable

from . import commit_message, gh_policy, identity
from .commands import HTTP_CLIENTS, git_call, parse
from .contracts import Contracts
from .decision import Verdict
from .shellparse import BASH, POWERSHELL

# Cheap pre-filter: skip parsing for commands that cannot involve git, gh or the GitHub API.
_INTERESTING = re.compile(r"\b(git|gh)(\.exe)?\b|github\.com", re.IGNORECASE)


def dialect_for(tool_name: str) -> str:
    return POWERSHELL if tool_name.lower() == "powershell" else BASH


def evaluate(command: str, dialect: str, cwd: str, contracts: Contracts,
             run: identity.Runner = identity.default_runner,
             process_env: dict[str, str] | None = None,
             read_file: Callable[[str], str] | None = None) -> Verdict:
    verdict = Verdict()
    if not command or not _INTERESTING.search(command):
        return verdict
    env = dict(os.environ if process_env is None else process_env)
    for inv in parse(command, dialect, cwd):
        if inv.program == "git":
            call = git_call(inv)
            if call is None:
                continue
            if call.sub == "commit":
                verdict.extend(identity.check_author(call, contracts.identity, run, env))
                verdict.extend(_check_commit_message(call, contracts, read_file))
            verdict.extend(identity.check_network(call, contracts.identity, run, env))
        elif inv.program == "gh":
            if inv.argv[:2] in (["auth", "switch"], ["auth", "login"], ["auth", "logout"]):
                identity.clear_gh_cache()
            verdict.extend(identity.check_gh_account(inv, contracts.identity, run, env))
            verdict.extend(gh_policy.check_gh(inv, contracts.gh, contracts.commit))
        elif inv.program in HTTP_CLIENTS:
            verdict.extend(gh_policy.check_http_client(inv, contracts.gh))
    return verdict


def _check_commit_message(call, contracts: Contracts, read_file) -> Verdict:
    c = contracts.commit
    msg = commit_message.extract(call, read_file)
    v = Verdict()
    if msg.kind == "none":
        v.add("CM001", c.rule("CM001").severity,
              "git commit has no -m/--message or -F/--file; an editor cannot be used here")
    elif msg.kind in ("dynamic", "unreadable"):
        v.add("CM002", c.rule("CM002").severity, f"commit message cannot be checked: {msg.detail}")
    elif msg.kind == "text":
        v.extend(commit_message.check(msg.text, c))
    return v
