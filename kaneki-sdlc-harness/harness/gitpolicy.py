"""The git policy, shared by `sdlc git` (the wrapper every node uses) and the guard hook (which stops plain `git`).

Rule set (user decision: nodes never commit; only kaneki-ship commits and pushes):
- HISTORY: commit, merge, rebase, push, … — refused for nodes everywhere; kaneki-ship does them itself.
- DESTRUCTIVE: reset --hard, clean -f, branch -D, … — refused everywhere (never delete work).
- IDENTITY: setting user.* / core.sshCommand / --author — refused everywhere (identity comes from the profile).
- NETWORK: fetch, ls-remote, clone, … — allowed through the wrapper only (it applies the project SSH command);
  the hook refuses them as plain git.
Everything else (status, diff, log, show, add, stash, worktree add, branch, checkout -b, …) is allowed.
"""

from __future__ import annotations

import re
import shlex

HISTORY = {"commit", "merge", "rebase", "cherry-pick", "revert", "am", "tag", "push", "pull", "filter-branch",
           "replace", "notes", "fast-import", "commit-tree"}
NETWORK = {"fetch", "ls-remote", "clone", "submodule", "remote"}
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


def refusal(args: list[str], via_wrapper: bool) -> str | None:
    """Why this git command is not allowed for a node, or None if it is."""
    sub, rest, configs = _sub_and_rest(args)
    joined = " ".join(rest)
    if any(IDENTITY_RE.search(c) for c in configs) or (sub == "config" and IDENTITY_RE.search(joined)) \
            or re.search(r"--author\b|--reset-author\b", joined):
        return "identity settings come from the project profile (git.user / git.sshCommand); never override them"
    if sub in HISTORY:
        return f"`git {sub}` is not run by nodes: only kaneki-ship commits and pushes (after the user approves)"
    destructive = (
        (sub == "reset" and "--hard" in rest)
        or (sub == "clean" and any(a.startswith("-") and "f" in a.lstrip("-") for a in rest if not a.startswith("--")) )
        or (sub == "clean" and "--force" in rest)
        or (sub == "checkout" and ("--" in rest or "." in rest) and "-b" not in rest)
        or (sub == "restore" and "--staged" not in rest)
        or (sub == "branch" and any(a in ("-D", "-d", "--delete") for a in rest))
        or (sub == "stash" and rest[:1] in (["drop"], ["clear"]))
        or (sub == "worktree" and rest[:1] == ["remove"] and ("--force" in rest or "-f" in rest))
        or (sub == "update-ref" and "-d" in rest)
        or (sub == "gc" and any(a.startswith("--prune") for a in rest))
    )
    if destructive:
        return f"`git {sub} {joined}` can delete work; nodes never delete"
    if sub == "config" and not any(a in ("--get", "--get-all", "--list", "-l", "--get-regexp") for a in rest):
        return "git config is read-only for nodes (--get / --list)"
    if not via_wrapper and (sub in NETWORK and not (sub == "remote" and rest[:1] in ([], ["-v"], ["get-url"], ["show"]))):
        return f"`git {sub}` talks to the remote: run it through `sdlc git` so the project SSH key and account are used"
    return None


WRAPPER_RE = re.compile(r"""sdlc(?:\.py)?["']?(?:\s+--profile\s+(?:"[^"]*"|'[^']*'|\S+))?\s+git\b""")
GIT_RE = re.compile(r"""(?:^|[\s;|&(`]|\$\()(?:[^\s;|&()`'"]*/)?git(?:\.exe)?(?=\s)""")


def shell_refusals(command: str) -> list[str]:
    """For the guard hook: every plain `git …` in a shell command line that the policy refuses.
    Calls of the wrapper (`… sdlc … git --ticket …`) are skipped; their arguments are checked by the wrapper."""
    cleaned = WRAPPER_RE.sub("sdlc SDLCWRAPPEDGIT", command)
    out = []
    for m in GIT_RE.finditer(cleaned):
        tail = cleaned[m.end():]
        segment = re.split(r"[;|&]|\$\(|`|\n", tail, maxsplit=1)[0]
        try:
            args = shlex.split(segment, posix=True)
        except ValueError:
            args = segment.split()
        why = refusal(args, via_wrapper=False)
        if why:
            out.append(f"git {segment.strip()[:80]}: {why}")
    return out
