#!/usr/bin/env python3
"""Suggest the PR base for a work branch: the staging branch, or the parent work branch it was forked from.

  python3 pr_base.py <repo> <head>

Read-only (git only, no network beyond what is already fetched). Candidates are the staging branch and, when
settings.nested_work_branches is true, every other work branch on the remote. For each candidate it counts the
head's commits since their merge base; the closest candidate is the parent. Output, one line each:

  base <branch>                       the suggestion
  tie <branch> <branch> ...           only when several candidates are equally close (ask the user)
  candidate <branch> <commits ahead>  every candidate, closest first

Exit code: 0 = suggestion printed, 2 = usage / git error.
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import branching, contracts as contracts_mod  # noqa: E402


def _git(repo: str, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


def ranked(repo: str, head: str, c, git=_git) -> list[tuple[str, int]]:
    """[(candidate, commits on head since the merge base)], closest first; staging wins ties against parents."""
    _, staging, remote = branching._settings(c)
    head_ref = f"refs/remotes/{remote}/{head}"
    if git(repo, "rev-parse", "--verify", "-q", head_ref)[0] != 0:
        head_ref = head
    names = [staging]
    if branching.nested(c):
        _, out = git(repo, "for-each-ref", "--format=%(refname)", f"refs/remotes/{remote}/")
        for ref in out.split():
            name = branching.local_name(ref, remote)
            if name != head and name not in names and branching.is_work_branch(name, c):
                names.append(name)
    out_list = []
    for name in names:
        code, mb = git(repo, "merge-base", head_ref, f"refs/remotes/{remote}/{name}")
        if code != 0 or not mb.strip():
            continue
        code, n = git(repo, "rev-list", "--count", f"{mb.strip()}..{head_ref}")
        # 0 means the candidate already contains every head commit: a child (or a copy) of the head, not its base.
        if code == 0 and n.strip().isdigit() and int(n.strip()) > 0:
            out_list.append((name, int(n.strip())))
    # Closest first; on equal distance staging comes first so a fresh branch off staging is never mistaken for a child.
    out_list.sort(key=lambda t: (t[1], t[0] != staging))
    return out_list


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    repo, head = argv
    try:
        c = contracts_mod.load().branch
    except contracts_mod.ContractError as e:
        print(e, file=sys.stderr)
        return 2
    rows = ranked(repo, head, c)
    if not rows:
        print(f"no candidate base found for '{head}' (fetch origin first?)", file=sys.stderr)
        return 2
    best = rows[0][1]
    closest = [n for n, d in rows if d == best]
    print(f"base {closest[0]}")
    if len(closest) > 1 and branching._settings(c)[1] not in closest:
        print("tie " + " ".join(closest))
    for name, dist in rows:
        print(f"candidate {name} {dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
