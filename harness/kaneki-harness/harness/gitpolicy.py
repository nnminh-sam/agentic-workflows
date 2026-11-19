"""The git policy of `sdlc git` (the wrapper nodes may use to run git as the project identity).

Rule set (user decision: nodes never commit; only kaneki-ship commits and pushes, through my-git):
- HISTORY: commit, merge, rebase, push, … — refused for nodes; kaneki-ship hands them to /my-git:commit and /my-git:push.
- DESTRUCTIVE: reset --hard, clean -f, branch -D, … — refused (never delete work).
- IDENTITY: setting user.* / core.sshCommand / --author — refused (identity comes from the profile).
Everything else (status, diff, log, show, add, fetch, worktree add, branch, checkout -b, …) is allowed.

The kaneki-flow guard hook (skills/kaneki-flow/scripts/guard_git.py) checks plain `git` in Bash/PowerShell with the
same HISTORY and destructive() rules, self-contained; identity, SSH-key and remote rules there belong to my-git's
guard. tests/test_hardening.py keeps the two in sync.
"""

from __future__ import annotations

import re

HISTORY = {"commit", "merge", "rebase", "cherry-pick", "revert", "am", "tag", "push", "pull", "filter-branch",
           "replace", "notes", "fast-import", "commit-tree"}
IDENTITY_RE = re.compile(r"\b(user\.(name|email|signingkey)|core\.sshCommand|author\.(name|email)|committer\.(name|email))\b", re.I)


def _sub_and_rest(args: list[str]) -> tuple[str, list[str], list[str]]:
    """Skip git's global options (`-C <path>`, `-c k=v`, `--git-dir=…`); return (subcommand, rest, global -c values)."""
    i, configs = 0, []
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            if args[i] == "-c":
                configs.append(args[i + 1])
            i += 2
        else:
            i += 1
    return (args[i] if i < len(args) else ""), args[i + 1:], configs


def destructive(sub: str, rest: list[str]) -> bool:
    return (
        (sub == "reset" and "--hard" in rest)
        or (sub == "clean" and any(a.startswith("-") and "f" in a.lstrip("-") for a in rest if not a.startswith("--")))
        or (sub == "clean" and "--force" in rest)
        or (sub == "checkout" and ("--" in rest or "." in rest) and "-b" not in rest)
        or (sub == "restore" and "--staged" not in rest)
        or (sub == "branch" and any(a in ("-D", "-d", "--delete") for a in rest))
        or (sub == "stash" and rest[:1] in (["drop"], ["clear"]))
        or (sub == "worktree" and rest[:1] == ["remove"] and ("--force" in rest or "-f" in rest))
        or (sub == "update-ref" and "-d" in rest)
        or (sub == "gc" and any(a.startswith("--prune") for a in rest))
    )


def refusal(args: list[str], via_wrapper: bool = True) -> str | None:
    """Why this git command is not allowed for a node, or None if it is."""
    sub, rest, configs = _sub_and_rest(args)
    joined = " ".join(rest)
    if any(IDENTITY_RE.search(c) for c in configs) or (sub == "config" and IDENTITY_RE.search(joined)) \
            or re.search(r"--author\b|--reset-author\b", joined):
        return "identity settings come from the project profile (git.user / git.sshCommand); never override them"
    if sub in HISTORY:
        return f"`git {sub}` is not run by nodes: only kaneki-ship commits and pushes (through my-git, after the user approves)"
    if destructive(sub, rest):
        return f"`git {sub} {joined}` can delete work; nodes never delete"
    if sub == "config" and not any(a in ("--get", "--get-all", "--list", "-l", "--get-regexp") for a in rest):
        return "git config is read-only for nodes (--get / --list)"
    return None
