"""kaneki-ship: the only path that commits and pushes (user decision: nodes never commit).

Modelled on wms-ship: the user approves one exact request (repository, branch, file list, commit message, PR body,
identity, reviewed tree), identified by its digest. `ship` rebuilds the request, refuses if anything changed since
the approval, stages exactly the reviewed files, commits as the project identity, pushes with the project SSH
command, and opens the PR/MR with the delivery adapter's CLI.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from . import gitid
from .errors import HarnessError
from .state import check_receipts, now, sha256_text, worktree_snapshot


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def changed_paths(path: str, excludes: list[str]) -> list[str]:
    """Every path the reviewed change touches (tracked edits, deletions, renames, new files), harness folders excluded."""
    snap = worktree_snapshot(path, excludes)
    out = set()
    for line in snap["porcelain"]:
        body = line[3:].strip()
        for part in body.split(" -> "):
            out.add(part.strip().strip('"'))
    return sorted(out)


def _git(path: str, args: list[str], ident: dict[str, str]) -> str:
    res = gitid.run(path, args, ident)
    if res.returncode != 0:
        raise HarnessError(f"G7: git {' '.join(args[:3])} failed: {(res.stderr or res.stdout).strip()[:300]}")
    return res.stdout


def _live(path: Path) -> str:
    from .cli import live_text  # the SUPERSEDED rule: only the live round of a draft is shipped
    return live_text(path).strip()


def build_request(s: Any, repo_id: str, delivery: dict[str, Any]) -> dict[str, Any]:
    if s.state["nodes"].get("deliverable-ready", {}).get("status") != "done":
        raise HarnessError("G7: ship needs deliverable-ready closed (receipts checked, drafts approved by the user)")
    problems = check_receipts(s.state, s.profile["delivery"].get("minHoursLeft", 24), s.excludes)
    if problems:
        raise HarnessError("G7: receipts not shippable: " + "; ".join(problems))
    repo = next((r for r in s.ctx["repositories"] if r["id"] == repo_id), None)
    if repo is None:
        raise HarnessError(f"unknown repo {repo_id}")
    path = repo.get("worktree") or repo["path"]
    ident = gitid.identity(s.profile, repo_id)
    branch = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"], ident).strip()
    if branch == "HEAD":
        raise HarnessError("G7: the worktree is on a detached HEAD; ship needs the ticket branch")
    msg_file = s.ticket.dir / "drafts" / "commit-msg.md"
    message = _live(msg_file) if msg_file.is_file() else ""
    if not message:
        raise HarnessError("G7: drafts/commit-msg.md is missing or empty")
    body_file = s.ticket.dir / "drafts" / "pr-body.md"
    body = _live(body_file) if body_file.is_file() else ""
    paths = changed_paths(path, s.excludes)
    if not paths:
        raise HarnessError("G7: nothing to ship: the worktree has no changes")
    remote = gitid.run(path, ["remote", "get-url", "origin"], ident)
    ship = delivery.get("ship") or {}
    return {"ticket": s.ctx["ticket"], "repo": repo_id, "branch": branch, "base": repo.get("baseBranch", "main"),
            "remote": remote.stdout.strip() if remote.returncode == 0 else None,
            "headOid": _git(path, ["rev-parse", "HEAD"], ident).strip(),
            "subject": s.state["receipts"][repo_id]["subject"], "paths": paths,
            "identity": {"name": ident["name"], "email": ident["email"]}, "sshCommand": bool(ident.get("sshCommand")),
            "commitMessage": message, "prBodyDigest": sha256_text(body) if body else None,
            "push": bool(ship.get("push", False)), "pr": (ship.get("pr") or {}).get("cli")}


def request_digest(request: dict[str, Any]) -> str:
    return sha256_text(_canon(request))


def _cli(name: str) -> list[str]:
    override = os.environ.get(f"SDLC_{name.upper()}")
    return shlex.split(override, posix=os.name != "nt") if override else [name]


def run_ship(s: Any, request: dict[str, Any], delivery: dict[str, Any], open_pr: bool) -> dict[str, Any]:
    repo = next(r for r in s.ctx["repositories"] if r["id"] == request["repo"])
    path = repo.get("worktree") or repo["path"]
    ident = gitid.identity(s.profile, request["repo"])
    _git(path, ["add", "-A", "--", *request["paths"]], ident)
    staged = [p for p in _git(path, ["diff", "--cached", "--name-only", "-z"], ident).split("\0") if p]
    extra = sorted(set(staged) - set(request["paths"]))
    if extra:
        _git(path, ["reset", "-q"], ident)  # unstage only; the working tree is untouched
        raise HarnessError(f"G7: staged files outside the approved list: {extra[:5]}; nothing was committed")
    ship_dir = s.ticket.dir / "ship"
    ship_dir.mkdir(exist_ok=True)
    msg = ship_dir / f"{request['repo']}-commit-msg.txt"
    msg.write_text(request["commitMessage"] + "\n", encoding="utf-8")
    _git(path, ["-c", f"user.name={ident['name']}", "-c", f"user.email={ident['email']}", "commit", "-q", "-F", str(msg)], ident)
    sha = _git(path, ["rev-parse", "HEAD"], ident).strip()
    result: dict[str, Any] = {"repo": request["repo"], "commit": sha, "branch": request["branch"], "pushed": False, "pr": None}
    shipped = s.state.setdefault("shipped", {}).setdefault(request["repo"], {"commits": []})
    shipped["commits"].append(sha)
    shipped["branch"] = request["branch"]
    if request["push"]:
        res = gitid.run(path, ["push", "origin", f"HEAD:refs/heads/{request['branch']}"], ident)
        if res.returncode != 0:
            result["pushError"] = (res.stderr or res.stdout).strip()[:300]
            return result
        result["pushed"] = True
        shipped["pushedAt"] = now()
    if request["push"] and request["pr"] and open_pr and not shipped.get("pr"):
        result["pr"] = shipped["pr"] = _open_pr(s, request, path)
    elif shipped.get("pr"):
        result["pr"] = shipped["pr"]  # a review round: pushing updated the existing PR
    return result


def _open_pr(s: Any, request: dict[str, Any], path: str) -> str | None:
    body_file = s.ticket.dir / "ship" / f"{request['repo']}-pr-body.md"
    body_src = s.ticket.dir / "drafts" / "pr-body.md"
    body = _live(body_src) if body_src.is_file() else ""
    body_file.write_text(body + "\n", encoding="utf-8")
    title = request["commitMessage"].splitlines()[0][:120]
    cli = request["pr"]
    if cli == "gh":
        argv = _cli("gh") + ["pr", "create", "--base", request["base"], "--head", request["branch"], "--title", title,
                             "--body-file", str(body_file)]
    elif cli == "glab":
        argv = _cli("glab") + ["mr", "create", "--source-branch", request["branch"], "--target-branch", request["base"],
                               "--title", title, "--description", body, "--yes"]
    else:
        raise HarnessError(f"unknown PR cli {cli}")
    try:
        res = subprocess.run(argv, cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(f"G7: pushed, but `{cli}` could not open the PR/MR: {exc}") from exc
    if res.returncode != 0:
        raise HarnessError(f"G7: pushed, but `{cli}` failed to open the PR/MR: {(res.stderr or res.stdout).strip()[:300]}")
    urls = re.findall(r"https?://\S+", res.stdout)
    return urls[-1] if urls else res.stdout.strip() or None


def fetch_review(s: Any, repo_id: str, pr: str | None) -> Path:
    """GitHub: export the PR's reviews and line comments to a markdown file for `handback`."""
    shipped = (s.state.get("shipped") or {}).get(repo_id, {})
    number = pr or (re.findall(r"/pull/(\d+)", shipped.get("pr") or "") or [None])[-1]
    if not number:
        raise HarnessError("G7: no PR number: pass --pr, or ship first so the PR URL is recorded")
    repo = next(r for r in s.ctx["repositories"] if r["id"] == repo_id)
    path = repo.get("worktree") or repo["path"]
    remote = gitid.run(path, ["remote", "get-url", "origin"], gitid.identity(s.profile, repo_id)).stdout.strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", remote)
    if not m:
        raise HarnessError(f"G7: origin is not a GitHub remote ({remote or 'none'})")
    slug = m.group(1)

    def gh(args: list[str]) -> Any:
        res = subprocess.run(_cli("gh") + args, cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if res.returncode != 0:
            raise HarnessError(f"G7: gh {' '.join(args[:2])} failed: {(res.stderr or res.stdout).strip()[:300]}")
        return json.loads(res.stdout or "null")

    view = gh(["pr", "view", str(number), "--repo", slug, "--json", "url,headRefOid,reviews,comments"])
    lines_ = gh(["api", f"repos/{slug}/pulls/{number}/comments"])
    out = [f"# PR review — {view.get('url', slug + ' #' + str(number))}", "", f"Head: `{view.get('headRefOid', '?')}`", ""]
    n = 0
    for rv in view.get("reviews") or []:
        if (rv.get("body") or "").strip():
            n += 1
            out += [f"## RC-{n} · review by {((rv.get('author') or {}).get('login')) or '?'} · {rv.get('state', '')}",
                    f"commit `{rv.get('commit', {}).get('oid', '?') if isinstance(rv.get('commit'), dict) else '?'}`", "", rv["body"].strip(), ""]
    for c in view.get("comments") or []:
        n += 1
        out += [f"## RC-{n} · comment by {((c.get('author') or {}).get('login')) or '?'}", "", (c.get("body") or "").strip(), ""]
    for c in lines_ or []:
        n += 1
        out += [f"## RC-{n} · {c.get('path')}:{c.get('line') or c.get('original_line')} by {((c.get('user') or {}).get('login')) or '?'}",
                f"commit `{c.get('commit_id', '?')}`", "", (c.get("body") or "").strip(), ""]
    dest = s.ticket.dir / "reviews" / f"fetched-pr-{number}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    return dest
