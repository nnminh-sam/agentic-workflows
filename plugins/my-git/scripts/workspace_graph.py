#!/usr/bin/env python3
"""Workspace git graph: every repo of a workspace, every checkout (base clone + worktrees) and the branch each one
has checked out, how far each local branch is ahead/behind its remote branch and its base branch, and a commit graph
of those branches.

  python workspace_graph.py [workspace] [--open | --html [FILE] | --json | --text] [--all] [--fetch]

  --open   write the HTML view (viewer/graph.html filled with the data) and open it in the browser
  --html   write the HTML view only;  --json  print the data;  --text (default)  terminal view

Workspace discovery (works for any workspace):
  1. The workspace is the given path (default: cwd). If a parent folder holds sdlc.profile.json, that folder is used.
  2. Repos = profile.repositories[].path (relative to the workspace, or anywhere else on disk),
     plus every git repo directly under the workspace, plus the owner repo of every checkout under
     profile.paths.worktreeRoot (default "worktrees").
  3. Checkouts of a repo come from `git worktree list`, so worktrees outside the workspace show up too.

Read-only: it never changes a repo. --fetch runs `git fetch --prune` per repo (with the repo's own core.sshCommand).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROFILE = "sdlc.profile.json"
DEFAULT_WT_ROOT = "worktrees"
DEFAULT_PROD, DEFAULT_STAGING = "main", "develop/base"
LIST_MAX = 30
TEMPLATE = Path(__file__).resolve().parent.parent / "viewer" / "graph.html"
DATA_MARK = "/*__GRAPH_DATA__*/null"


# --- git ------------------------------------------------------------------------------------------------------

def git(repo: Path | str, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {res.stderr.strip()}")
    return res.stdout if res.returncode == 0 else ""


def norm(p: str | Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(str(p))))


def ahead_behind(repo: Path, left: str, right: str) -> tuple[int, int] | None:
    """(commits in left not in right, commits in right not in left), or None if a ref is missing."""
    out = git(repo, "rev-list", "--left-right", "--count", f"{left}...{right}", check=False).split()
    return (int(out[0]), int(out[1])) if len(out) == 2 else None


def commit_list(repo: Path, rng: str) -> list[dict]:
    out = git(repo, "log", f"-n{LIST_MAX}", "--format=%h\x1f%cr\x1f%s", rng, check=False)
    return [dict(zip(("sha", "age", "subject"), ln.split("\x1f", 2))) for ln in out.splitlines() if ln]


def collect_dag(repo: Path, refs: list[str], remote: str, max_commits: int) -> tuple[list[dict], bool]:
    """Every commit of `refs` back to (and including) their common merge base, topo order, newest first."""
    if not refs:
        return [], False
    stop: list[str] = []
    mb = git(repo, "merge-base", "--octopus", *refs, check=False).strip()
    if mb:
        stop = git(repo, "rev-parse", f"{mb}^@", check=False).split()
    out = git(repo, "log", "--topo-order", f"-n{max_commits + 1}", "--decorate-refs=refs/heads",
              f"--decorate-refs=refs/remotes/{remote}", "--decorate-refs-exclude=refs/remotes/*/HEAD",
              "--format=%H\x1f%P\x1f%D\x1f%an\x1f%cr\x1f%cI\x1f%s", *refs, "--not", *stop, "--", check=False)
    rows = []
    for ln in out.splitlines():
        sha, parents, deco, author, age, date, subj = (ln.split("\x1f") + [""] * 7)[:7]
        rows.append({"sha": sha, "parents": parents.split(), "refs": [d.strip().removeprefix("HEAD -> ")
                     for d in deco.split(",") if d.strip()], "author": author, "age": age, "date": date,
                     "subject": subj})
    return rows[:max_commits], len(rows) > max_commits


# --- model ----------------------------------------------------------------------------------------------------

@dataclass
class Checkout:
    path: str
    rel: str            # display path (relative to the workspace, or absolute when outside)
    kind: str           # base | worktree | external
    branch: str | None  # None = detached
    head: str
    dirty: int = 0
    untracked: int = 0
    missing: bool = False


@dataclass
class Branch:
    name: str
    sha: str
    subject: str
    age: str
    upstream: str | None
    gone: bool
    vs_remote: tuple[int, int] | None   # (ahead, behind) vs upstream
    base: str | None                    # ref compared against
    vs_base: tuple[int, int] | None
    role: str                           # production | staging | work
    checkout: str | None = None         # display path of the checkout holding it
    # the commits behind each count (newest first, at most LIST_MAX): {sha, subject, age}
    ahead_remote: list[dict] = field(default_factory=list)
    behind_remote: list[dict] = field(default_factory=list)
    ahead_base: list[dict] = field(default_factory=list)
    behind_base: list[dict] = field(default_factory=list)


@dataclass
class Repo:
    name: str
    path: str
    rel: str
    remote_url: str
    fetched: str | None
    production: str
    staging: str
    checkouts: list[Checkout] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    remote_only: list[str] = field(default_factory=list)
    graph: list[str] = field(default_factory=list)       # `git log --graph` lines for the text view
    remote: str = "origin"
    graph_refs: list[str] = field(default_factory=list)  # refs the graph is drawn for
    commits: list[dict] = field(default_factory=list)    # DAG for the HTML view, topo order
    truncated: bool = False


# --- discovery ------------------------------------------------------------------------------------------------

def find_workspace(start: Path) -> tuple[Path, dict]:
    for d in [start, *start.parents]:
        f = d / PROFILE
        if f.is_file():
            try:
                return d, json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                return d, {}
    return start, {}


def common_dir(path: Path) -> Path | None:
    out = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False).strip()
    return norm(out) if out else None


def main_worktree(path: Path) -> Path | None:
    """The main checkout of the repo that owns `path` (works from a linked worktree too)."""
    out = git(path, "worktree", "list", "--porcelain", check=False)
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line[9:])
    return None


def discover_repos(ws: Path, profile: dict) -> list[tuple[str, Path]]:
    found: dict[Path, tuple[str, Path]] = {}

    def add(p: Path, name: str | None = None) -> None:
        if not (p / ".git").exists():
            return
        main = main_worktree(p)
        if main is None:
            return
        cd = common_dir(main)
        if cd and cd not in found:
            found[cd] = (name or main.name, main)

    for r in profile.get("repositories") or []:
        p = Path(r.get("path", ""))
        add(p if p.is_absolute() else (ws / p).resolve(), r.get("id"))
    if (ws / ".git").exists():
        add(ws)
    wt_root = ws / ((profile.get("paths") or {}).get("worktreeRoot") or DEFAULT_WT_ROOT)
    for parent in (ws, wt_root):
        if parent.is_dir():
            for child in sorted(parent.iterdir()):
                if child.is_dir():
                    add(child)
    return list(found.values())


# --- collection -----------------------------------------------------------------------------------------------

def display(path: Path, ws: Path) -> str:
    try:
        return path.resolve().relative_to(ws.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()


def load_branching():
    """The branching contract (the project's ejected copy when present) or None when it can't be loaded."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gitguard import contracts as contracts_mod
        return contracts_mod.load().branch
    except Exception:  # noqa: BLE001 - the graph still works with the default model
        return None


def branching(c) -> tuple[str, str, str | None]:
    """production / staging / remote names from the contract, else main / develop/base / None."""
    if c is None:
        return DEFAULT_PROD, DEFAULT_STAGING, None
    from gitguard import branching as br
    return br._settings(c)


def is_work(name: str, c) -> bool:
    if c is None:
        return name.startswith("develop/")
    from gitguard import branching as br
    return br.is_work_branch(name, c)


def nested_enabled(c) -> bool:
    if c is None:
        return True
    from gitguard import branching as br
    return br.nested(c)


def count(repo: Path, rng: str) -> int | None:
    out = git(repo, "rev-list", "--count", rng, check=False).strip()
    return int(out) if out.isdigit() else None


def work_parent(main: Path, head: str, staging_ref: str, open_parents: list[str]) -> str:
    """The base a work branch is measured against: staging, or a not-yet-merged parent work branch that is strictly
    closer (fewer head commits since the merge base). Merged branches never count as parents, so a merge-commit
    history does not make every older branch look like a parent."""
    mb = git(main, "merge-base", head, staging_ref, check=False).strip()
    best, best_d = staging_ref, count(main, f"{mb}..{head}") if mb else None
    if not best_d:
        return staging_ref
    for p in open_parents:
        if p.partition("/")[2] == head:
            continue  # its own remote branch
        mb = git(main, "merge-base", head, p, check=False).strip()
        d = count(main, f"{mb}..{head}") if mb else None
        if d and d < best_d:
            best, best_d = p, d
    return best


def display(path: Path, ws: Path) -> str:
    try:
        return path.resolve().relative_to(ws.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()


def load_branching():
    """The branching contract (the project's ejected copy when present) or None when it can't be loaded."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gitguard import contracts as contracts_mod
        return contracts_mod.load().branch
    except Exception:  # noqa: BLE001 - the graph still works with the default model
        return None


def branching(c) -> tuple[str, str, str | None]:
    """production / staging / remote names from the contract, else main / develop/base / None."""
    if c is None:
        return DEFAULT_PROD, DEFAULT_STAGING, None
    from gitguard import branching as br
    return br._settings(c)


def collect_checkouts(main: Path, ws: Path, wt_root: Path) -> list[Checkout]:
    out, cur = [], {}
    for line in git(main, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
        else:
            k, _, v = line.partition(" ")
            cur[k] = v or True
    res = []
    for i, c in enumerate(out):
        p = Path(c["worktree"])
        if i == 0:
            kind = "base"
        else:
            try:
                p.resolve().relative_to(wt_root.resolve())
                kind = "worktree"
            except ValueError:
                kind = "external"
        br = c.get("branch")
        co = Checkout(path=p.as_posix(), rel=display(p, ws), kind=kind,
                      branch=br.removeprefix("refs/heads/") if isinstance(br, str) else None,
                      head=str(c.get("HEAD", ""))[:7], missing=not p.is_dir() or "prunable" in c)
        if not co.missing:
            for ln in git(p, "status", "--porcelain", check=False).splitlines():
                if ln.startswith("??"):
                    co.untracked += 1
                else:
                    co.dirty += 1
        res.append(co)
    return res


def collect_repo(name: str, main: Path, ws: Path, wt_root: Path, remote: str | None, show_all: bool, limit: int,
                 contract=None, lists: bool = False, max_commits: int = 0) -> Repo:
    prod, staging, c_remote = branching(contract)
    remote = remote or c_remote or "origin"
    cd = common_dir(main)
    fh = cd / "FETCH_HEAD" if cd else None
    fetched = (dt.datetime.fromtimestamp(fh.stat().st_mtime).isoformat(timespec="minutes")
               if fh and fh.is_file() else None)
    repo = Repo(name=name, path=main.as_posix(), rel=display(main, ws),
                remote_url=git(main, "remote", "get-url", remote, check=False).strip(),
                fetched=fetched, production=prod, staging=staging, remote=remote)
    repo.checkouts = collect_checkouts(main, ws, wt_root)
    held = {c.branch: c.rel for c in repo.checkouts if c.branch}

    remote_refs = set(git(main, "for-each-ref", f"refs/remotes/{remote}", "--format=%(refname:short)").split())
    r_prod, r_staging = f"{remote}/{prod}", f"{remote}/{staging}"
    open_parents = []
    if nested_enabled(contract) and r_staging in remote_refs:
        open_parents = [r for r in git(main, "for-each-ref", f"--no-merged={r_staging}", f"refs/remotes/{remote}",
                                       "--format=%(refname:short)").split()
                        if is_work(r.removeprefix(f"{remote}/"), contract)]
    fmt = "%(refname:short)\x1f%(objectname:short)\x1f%(upstream:short)\x1f%(upstream:track)\x1f%(committerdate:relative)\x1f%(contents:subject)"
    locals_ = set()
    for line in git(main, "for-each-ref", "refs/heads", f"--format={fmt}").splitlines():
        b, sha, up, track, age, subj = line.split("\x1f")
        locals_.add(b)
        role = "production" if b == prod else "staging" if b == staging else "work"
        if role == "work" and r_staging in remote_refs and is_work(b, contract):
            base = work_parent(main, b, r_staging, open_parents)
        elif role == "work":
            base = r_staging
        else:
            base = r_staging if role == "production" else r_prod
        if base not in remote_refs:
            base = base.removeprefix(f"{remote}/")
        gone = "[gone]" in track
        repo.branches.append(Branch(
            name=b, sha=sha, subject=subj, age=age, upstream=up or None, gone=gone,
            vs_remote=ahead_behind(main, b, up) if up and not gone else None,
            base=base if base != b else None, vs_base=ahead_behind(main, b, base) if base != b else None,
            role=role, checkout=held.get(b)))
        br = repo.branches[-1]
        if lists:
            if br.vs_remote and br.vs_remote[0]:
                br.ahead_remote = commit_list(main, f"{up}..{b}")
            if br.vs_remote and br.vs_remote[1]:
                br.behind_remote = commit_list(main, f"{b}..{up}")
            if br.vs_base and br.vs_base[0]:
                br.ahead_base = commit_list(main, f"{base}..{b}")
            if br.vs_base and br.vs_base[1]:
                br.behind_base = commit_list(main, f"{b}..{base}")
    order = {"production": 0, "staging": 1, "work": 2}
    repo.branches.sort(key=lambda x: (order[x.role], x.checkout is None, x.name))
    tracked = {b.upstream for b in repo.branches if b.upstream}
    repo.remote_only = sorted(r for r in remote_refs if r not in tracked and r != f"{remote}/HEAD"
                              and r != remote and r.removeprefix(f"{remote}/") not in locals_)

    # graph: trunks + checked-out branches (+ every local branch with --all) and their upstreams
    refs = {prod, staging, r_prod, r_staging}
    for b in repo.branches:
        if show_all or b.checkout or b.role != "work":
            refs.add(b.name)
            if b.upstream and not b.gone:
                refs.add(b.upstream)
    refs = sorted(r for r in refs if git(main, "rev-parse", "--verify", "-q", r, check=False).strip())
    # decorate (and so keep, under --simplify-by-decoration) only the selected refs
    deco = [f"--decorate-refs={'refs/remotes/' if r in remote_refs else 'refs/heads/'}{r}" for r in refs]
    repo.graph = git(main, "log", "--graph", "--simplify-by-decoration", *deco,
                     f"-n{limit}", "--format=%h\x1f%D\x1f%cr\x1f%s", *refs).splitlines()
    repo.graph_refs = refs
    if max_commits:
        repo.commits, repo.truncated = collect_dag(main, refs, remote, max_commits)
    return repo


def collect(ws_arg: Path, remote: str | None, show_all: bool, limit: int, fetch: bool,
            lists: bool = False, max_commits: int = 0) -> dict:
    ws, profile = find_workspace(ws_arg.resolve())
    wt_root = ws / ((profile.get("paths") or {}).get("worktreeRoot") or DEFAULT_WT_ROOT)
    contract = load_branching()
    remote = remote or branching(contract)[2] or "origin"
    repos = []
    for name, main in discover_repos(ws, profile):
        if fetch:
            git(main, "fetch", "--prune", remote, check=False)
        repos.append(collect_repo(name, main, ws, wt_root, remote, show_all, limit, contract, lists, max_commits))
    return {"workspace": ws.as_posix(), "profile": PROFILE if profile else None,
            "worktreeRoot": display(wt_root, ws), "remote": remote, "repos": repos,
            "generated": dt.datetime.now().isoformat(timespec="seconds"), "fetchedNow": fetch}


# --- render ---------------------------------------------------------------------------------------------------

class Style:
    def __init__(self, color: bool, ascii_: bool):
        self.color, self.a = color, ascii_

    def c(self, code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.color else s

    def g(self, uni: str, asc: str) -> str:
        return asc if self.a else uni


def fmt_ab(ab: tuple[int, int] | None, st: Style, sync: str) -> str:
    if ab is None:
        return st.c("2", "-")
    a, b = ab
    if a == 0 and b == 0:
        return st.c("32", sync)
    parts = []
    if a:
        parts.append(st.c("32", f"{st.g('↑', '+')}{a}"))
    if b:
        parts.append(st.c("33", f"{st.g('↓', '-')}{b}"))
    return " ".join(parts)


def vis_len(s: str) -> int:
    import re
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - vis_len(s))


def table(rows: list[list[str]], head: list[str], st: Style, indent: str) -> list[str]:
    w = [max(vis_len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    out = [indent + "  ".join(st.c("2", pad(h, w[i])) for i, h in enumerate(head)).rstrip()]
    out += [indent + "  ".join(pad(c, w[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    return out


def render_graph_line(line: str, repo: Repo, remote: str, st: Style, width: int) -> str:
    if "\x1f" not in line:
        return "    " + st.c("2", line)
    pre, rest = line.split("\x1f", 1)[0], line.split("\x1f", 1)[1]
    graph, _, sha = pre.rpartition(" ") if " " in pre.strip() else ("", "", pre)
    graph = pre[: len(pre) - len(sha)]
    deco, age, subj = (rest.split("\x1f") + ["", ""])[:3]
    held = {b.name: b.checkout for b in repo.branches if b.checkout}
    names = [d.strip().removeprefix("HEAD -> ") for d in deco.split(",") if d.strip()]
    locs = [n for n in names if not n.startswith(f"{remote}/")]
    rems = [n for n in names if n.startswith(f"{remote}/")]
    labels = []
    for n in locs:
        lab = st.c("1;32", n)
        if f"{remote}/{n}" in rems:
            rems.remove(f"{remote}/{n}")
            lab += st.c("2", f" {st.g('≡', '=')}{remote}")
        if n in held:
            lab = st.c("1;36", f"{st.g('◀', '<')}{held[n]} ") + lab
        labels.append(lab)
    labels += [st.c("31", n) for n in rems]
    head = f"{graph}{st.c('33', sha)} "
    tail = "  ".join(labels)
    budget = width - vis_len(head) - vis_len(tail) - 4
    s = subj if len(subj) <= budget else subj[: max(0, budget - 1)] + st.g("…", "~")
    return "    " + head + (tail + "  " if tail else "") + st.c("2", s if budget > 8 else "")


def notes(b: Branch, repo: Repo) -> list[tuple[str, str]]:
    """Short, actionable readings of the numbers as (kind, text); kind is a style key shared with the HTML view."""
    out = []
    if b.gone:
        out.append(("bad", "remote deleted"))
    elif b.vs_remote and b.vs_remote[0] and b.vs_remote[1]:
        out.append(("bad", "diverged"))
    elif b.vs_remote and b.vs_remote[1]:
        out.append(("warn", "pull (ff)"))
    elif b.vs_remote and b.vs_remote[0]:
        out.append(("good", "push"))
    if b.vs_base:
        ahead, behind = b.vs_base
        if b.role == "production" and behind:
            out.append(("release", f"{behind} unreleased on {repo.staging}"))
        elif b.role == "work" and ahead == 0 and behind:
            out.append(("muted", "merged"))
        elif b.role == "work" and behind:
            out.append(("muted", "base moved"))
    return out


NOTE_COLORS = {"bad": "31", "warn": "33", "good": "32", "release": "35", "muted": "2"}


def hint(b: Branch, repo: Repo, st: Style) -> str:
    return ", ".join(st.c(NOTE_COLORS[k], t) for k, t in notes(b, repo))


def render(data: dict, st: Style, width: int) -> str:
    L: list[str] = []
    rule = st.g("─", "-")
    remote = data["remote"]
    L.append(st.c("1", f"Workspace  {data['workspace']}"))
    L.append(st.c("2", f"profile: {data['profile'] or 'none (scanned the folder)'}   worktreeRoot: {data['worktreeRoot']}"
                       f"   repos: {len(data['repos'])}"))
    for repo in data["repos"]:
        L.append("")
        L.append(st.c("1;35", f"{rule * 2} {repo.name} ") + st.c("2", rule * max(4, width - len(repo.name) - 4)))
        L.append(f"  repo     {repo.rel}   {st.c('2', repo.remote_url or '(no ' + remote + ' remote)')}")
        L.append(f"  model    production {st.c('1', repo.production)}  {st.g('←', '<-')}  staging "
                 f"{st.c('1', repo.staging)}  {st.g('←', '<-')}  work branches")
        when = repo.fetched.replace("T", " ") if repo.fetched else "never"
        L.append(f"  fetched  {when}   " + st.c("2", "(remote counts are as of the last fetch; --fetch refreshes)"))

        L.append("")
        L.append(st.c("1", "  CHECKOUTS"))
        rows = []
        bmap = {b.name: b for b in repo.branches}
        for c in repo.checkouts:
            icon = {"base": st.g("●", "*"), "worktree": st.g("◆", "+"), "external": st.g("◇", "o")}[c.kind]
            b = bmap.get(c.branch or "")
            state = []
            if c.missing:
                state.append(st.c("31", "missing (prunable)"))
            if c.dirty:
                state.append(st.c("33", f"{c.dirty} changed"))
            if c.untracked:
                state.append(st.c("2", f"{c.untracked} untracked"))
            rows.append([f"{icon} {c.rel}", c.kind,
                         st.c("1;32", c.branch) if c.branch else st.c("31", f"detached @{c.head}"),
                         fmt_ab(b.vs_remote, st, "= synced") if b and b.upstream else
                         (st.c("31", "upstream gone") if b and b.gone else st.c("2", "no upstream")),
                         (fmt_ab(b.vs_base, st, "= even") + st.c("2", f" vs {b.base}")) if b and b.base else "",
                         ", ".join(state) or st.c("2", "clean")])
        L += table(rows, ["checkout", "kind", "branch", "local vs remote", "vs base", "working tree"], st, "    ")

        L.append("")
        L.append(st.c("1", "  BRANCHES") + st.c("2", f"  ({st.g('↑', '+')} ahead / {st.g('↓', '-')} behind)"))
        rows = []
        for b in repo.branches:
            mark = st.c("1;36", f"{st.g('◀', '<')} {b.checkout}") if b.checkout else ""
            if b.gone:
                remote_col = st.c("31", f"{b.upstream} gone")
            elif b.upstream:
                remote_col = fmt_ab(b.vs_remote, st, "= synced") + st.c("2", f"  {b.upstream}")
            else:
                remote_col = st.c("2", "local only")
            base_col = fmt_ab(b.vs_base, st, "= even") if b.base else ""
            note = hint(b, repo, st)
            rows.append([st.c("1;32" if b.checkout else "32", b.name), mark, remote_col,
                         st.c("2", b.base or ""), base_col, note, st.c("2", b.age)])
        L += table(rows, ["branch", "checked out in", "local vs remote", "base", "vs base", "note", "last commit"],
                   st, "    ")
        if repo.remote_only:
            shown = ", ".join(repo.remote_only[:6]) + (f", +{len(repo.remote_only) - 6} more" if len(repo.remote_only) > 6 else "")
            L.append(st.c("2", f"    remote only ({len(repo.remote_only)}): {shown}"))

        L.append("")
        L.append(st.c("1", "  GRAPH") + st.c("2", f"  (trunks + checked-out branches{''}; {st.g('◀', '<')}checkout, "
                                                 f"{st.g('≡', '=')}{remote} = remote at same commit)"))
        L += [render_graph_line(ln, repo, remote, st, width) for ln in repo.graph]
    return "\n".join(L)


def to_json(data: dict) -> dict:
    """The collected data as plain JSON, with each branch's notes added (the HTML view and --json share it)."""
    repos = []
    for r in data["repos"]:
        d = asdict(r)
        d.pop("graph", None)
        for bd, b in zip(d["branches"], r.branches):
            bd["notes"] = [{"kind": k, "text": t} for k, t in notes(b, r)]
        repos.append(d)
    return {**data, "repos": repos}


def default_html_path(ws: str) -> Path:
    import hashlib
    import tempfile
    tag = hashlib.sha1(ws.lower().encode("utf-8")).hexdigest()[:8]
    return Path(tempfile.gettempdir()) / "my-git-graph" / f"{Path(ws).name or 'workspace'}-{tag}.html"


def write_html(data: dict, path: Path, refresh_args: list[str]) -> Path:
    payload = to_json(data)
    payload["command"] = " ".join(["python3", f'"{Path(__file__).resolve().as_posix()}"', *refresh_args])
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8")
    if DATA_MARK not in page:
        raise SystemExit(f"{TEMPLATE}: data marker {DATA_MARK} not found")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page.replace(DATA_MARK, blob, 1), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workspace", nargs="?", default=".")
    ap.add_argument("--remote", default=None, help="remote to compare against (default: the branching contract's, origin)")
    ap.add_argument("--all", action="store_true", help="graph every local branch, not only checked-out ones")
    ap.add_argument("--limit", type=int, default=40, help="max graph rows per repo (default 40)")
    ap.add_argument("--fetch", action="store_true", help="git fetch --prune each repo first (network)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--html", nargs="?", const="", default=None, metavar="FILE",
                    help="write the HTML view (default file: <temp>/my-git-graph/<workspace>-<hash>.html)")
    ap.add_argument("--open", action="store_true", help="write the HTML view and open it in the browser")
    ap.add_argument("--max-commits", type=int, default=400, help="max commits drawn per repo in the HTML view")
    ap.add_argument("--text", action="store_true", help="terminal view (the default when neither --html nor --open)")
    ap.add_argument("--ascii", action="store_true")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    ap.add_argument("--width", type=int, default=0)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    html = (a.open or a.html is not None) and not a.text
    rich = a.json or html
    data = collect(Path(a.workspace), a.remote, a.all, a.limit, a.fetch,
                   lists=rich, max_commits=a.max_commits if rich else 0)
    if a.json:
        print(json.dumps(to_json(data), indent=2, ensure_ascii=False))
        return 0
    if html:
        out = Path(a.html) if a.html else default_html_path(data["workspace"])
        refresh = [f'"{data["workspace"]}"', "--html", f'"{out.as_posix()}"'] + (["--all"] if a.all else [])
        write_html(data, out, refresh)
        print(f"wrote {out.as_posix()}")
        for r in data["repos"]:
            print(f"  {r.name}: {len(r.checkouts)} checkouts, {len(r.branches)} branches, "
                  f"{len(r.commits)} commits drawn" + (" (truncated: raise --max-commits)" if r.truncated else ""))
        if a.open:
            import webbrowser
            if not webbrowser.open(out.resolve().as_uri()):
                print("could not open a browser; open the file above")
        return 0
    color = a.color == "always" or (a.color == "auto" and sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    width = a.width or min(140, max(80, (os.get_terminal_size().columns if sys.stdout.isatty() else 120)))
    print(render(data, Style(color, a.ascii), width))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
