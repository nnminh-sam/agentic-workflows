"""kaneki-ship: the only path that commits and pushes (user decision: nodes never commit).

The user approves one exact request (repository, branch, file list, commit message, PR body, identity, reviewed
tree), identified by its digest. Two ship backends are supported:

- `native-git` (default, agent-agnostic): stages exactly the reviewed files, grants a short-lived approval in
  `.sdlc/ship-approvals.json`, commits via `gitid` (profile identity + SSH command), verifies the commit,
  pushes if configured, and opens the PR/MR via `gh`/`glab` or leaves manual PR instructions.
- `my-git` (opt-in Claude Code plugin backend): stages the files, grants the approval, and emits `/my-git:commit`,
  `/my-git:push`, and `/my-git:pr` next steps; `sdlc ship-record` then verifies and records the commit.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from . import approvals, checks, gitid, mygit
from .errors import HarnessError
from .state import check_receipts, now, sha256_text, worktree_snapshot

VALID_BACKENDS = ("native-git", "my-git")


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


def _cli(name: str) -> list[str]:
    override = os.environ.get(f"SDLC_{name.upper()}")
    return shlex.split(override, posix=os.name != "nt") if override else [name]


def _norm_message(text: str) -> str:
    """git's `whitespace` cleanup (commit -F): trailing spaces off, blank-line runs collapsed, outer blanks dropped."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def resolve_backend(profile: dict[str, Any], delivery: dict[str, Any], override: str | None = None) -> str:
    val = (
        override
        or os.environ.get("SDLC_SHIP_BACKEND")
        or (profile.get("delivery") or {}).get("shipBackend")
        or (delivery.get("ship") or {}).get("backend")
        or "native-git"
    )
    if val not in VALID_BACKENDS:
        raise HarnessError(f"G7: unknown ship backend {val!r}; expected one of {', '.join(VALID_BACKENDS)}")
    return val


def pr_mode(delivery: dict[str, Any], backend: str = "native-git") -> str | None:
    """How the PR/MR is opened after the push, per backend (`native-git` vs `my-git`)."""
    ship = delivery.get("ship") or {}
    if not ship.get("push"):
        return None
    pr = ship.get("pr") or {}
    if backend == "my-git":
        via = pr.get("via") or ("my-git" if pr.get("cli") == "gh" else None)
        return "my-git" if via in ("my-git", "gh") else "manual"
    via = pr.get("via") or pr.get("cli")
    if via == "my-git":
        return "gh"
    return via if via in ("gh", "glab", "manual") else "manual"


def build_request(s: Any, repo_id: str, delivery: dict[str, Any], backend: str = "native-git",
                  mygit_root: Path | None = None) -> dict[str, Any]:
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
    if branch == repo.get("baseBranch", "main"):
        raise HarnessError(f"G7: the checkout is on the base branch {branch}; create the ticket branch first "
                           f"(sdlc git --ticket {s.ctx['ticket']} --repo {repo_id} -- switch -c <branch>)")
    msg_file = s.ticket.dir / "drafts" / "commit-msg.md"
    message = _live(msg_file) if msg_file.is_file() else ""
    if not message:
        raise HarnessError("G7: drafts/commit-msg.md is missing or empty")
    body_file = s.ticket.dir / "drafts" / "pr-body.md"
    body = _live(body_file) if body_file.is_file() else ""
    paths = changed_paths(path, s.excludes)
    if not paths:
        raise HarnessError("G7: nothing to ship: the worktree has no changes")
    if backend == "my-git":
        if mygit_root is None:
            raise HarnessError("G7: my-git backend requires the my-git plugin")
        problems = mygit.check_message(mygit_root, message)
        if problems:
            raise HarnessError("G7: drafts/commit-msg.md fails my-git's commit-message rules (fix it in delivery-drafts): "
                               + "; ".join(problems[:5]))
    else:
        problems = checks.commit_message(message)
        if problems:
            raise HarnessError("G7: drafts/commit-msg.md fails commit-message rules (fix it in delivery-drafts): "
                               + "; ".join(problems[:5]))
    remote = gitid.run(path, ["remote", "get-url", "origin"], ident)
    ship = delivery.get("ship") or {}
    return {"ticket": s.ctx["ticket"], "repo": repo_id, "branch": branch, "base": repo.get("baseBranch", "main"),
            "remote": remote.stdout.strip() if remote.returncode == 0 else None,
            "headOid": _git(path, ["rev-parse", "HEAD"], ident).strip(),
            "subject": s.state["receipts"][repo_id]["subject"], "paths": paths,
            "identity": {"name": ident["name"], "email": ident["email"]},
            "commitMessage": message, "prBodyDigest": sha256_text(body) if body else None,
            "push": bool(ship.get("push", False)), "pr": pr_mode(delivery, backend), "via": backend}


def request_digest(request: dict[str, Any]) -> str:
    return sha256_text(_canon(request))


def _stage_approved(s: Any, request: dict[str, Any]) -> tuple[str, dict[str, str], list[str], str, Path, Path]:
    repo = next(r for r in s.ctx["repositories"] if r["id"] == request["repo"])
    path = repo.get("worktree") or repo["path"]
    ident = gitid.identity(s.profile, request["repo"])
    already = [p for p in _git(path, ["diff", "--cached", "--name-only", "-z"], ident).split("\0") if p]
    stray = sorted(set(already) - set(request["paths"]))
    if stray:
        raise HarnessError(f"G7: files outside the approved list are already staged: {stray[:5]}; unstage them first")
    _git(path, ["add", "-A", "--", *request["paths"]], ident)
    staged = [p for p in _git(path, ["diff", "--cached", "--name-only", "-z"], ident).split("\0") if p]
    extra = sorted(set(staged) - set(request["paths"]))
    if extra:
        _git(path, ["reset", "-q"], ident)  # unstage only; the working tree is untouched
        raise HarnessError(f"G7: staged files outside the approved list: {extra[:5]}; nothing was staged")
    tree = _git(path, ["write-tree"], ident).strip()  # the tree the commit must have
    ship_dir = s.ticket.dir / "ship"
    ship_dir.mkdir(exist_ok=True)
    msg = ship_dir / f"{request['repo']}-commit-msg.txt"
    msg.write_text(request["commitMessage"] + "\n", encoding="utf-8")
    body_src = s.ticket.dir / "drafts" / "pr-body.md"
    body_file = ship_dir / f"{request['repo']}-pr-body.md"
    body_file.write_text((_live(body_src) if body_src.is_file() else "") + "\n", encoding="utf-8")
    return path, ident, staged, tree, msg, body_file


def _verify_commit(s: Any, repo_id: str, path: str, ident: dict[str, str],
                   expected_head: str, expected_tree: str) -> str:
    sha = _git(path, ["rev-parse", "HEAD"], ident).strip()
    if sha == expected_head:
        raise HarnessError("G7: no new commit yet: run /my-git:commit on the staged files first")
    meta = _git(path, ["log", "-1", "--format=%P%x00%T%x00%an%x00%ae%x00%cn%x00%ce%x00%B"], ident).split("\0")
    parents, tree, an, ae, cn, ce, body = (meta + [""] * 7)[:7]
    problems = []
    if parents.split() != [expected_head]:
        problems.append(f"HEAD {sha[:10]} is not one commit on top of the approved {expected_head[:10]}")
    if tree != expected_tree:
        problems.append("the committed files differ from the approved staged files")
    if not (gitid.same_name(an, ident["name"]) and gitid.same_email(ae, ident["email"]) and gitid.same_email(ce, ident["email"])):
        problems.append(f"author {an} <{ae}> / committer {cn} <{ce}> is not the project identity")
    approved = (s.ticket.dir / "ship" / f"{repo_id}-commit-msg.txt").read_text(encoding="utf-8")
    if _norm_message(body) != _norm_message(approved):
        problems.append("the commit message differs from the approved drafts/commit-msg.md")
    if problems:
        raise HarnessError("G7: the commit is not the approved request (nothing recorded; fix it with "
                           "`sdlc git ... -- reset --soft <approved head>` and commit again): " + "; ".join(problems))
    return sha


def stage(s: Any, request: dict[str, Any], mygit_root: Path) -> dict[str, Any]:
    """`my-git` backend: stage approved files, write message/body, grant approval, and emit /my-git:* steps."""
    path, _, staged, tree, msg, body_file = _stage_approved(s, request)
    digest = request_digest(request)
    approval = approvals.grant(s.pdir, s.ctx["ticket"], request["repo"], path, request["branch"], digest)
    pending = {"requestDigest": digest, "headOid": request["headOid"], "tree": tree,
               "branch": request["branch"], "stagedAt": now()}
    s.state.setdefault("shipPending", {})[request["repo"]] = pending
    title = request["commitMessage"].splitlines()[0][:72]
    steps = [f'/my-git:commit "{path}" commit exactly what is staged, with the message in "{msg}" (do not stage '
             f"anything else, do not change the message)"]
    if request["push"]:
        steps.append(f'/my-git:push "{path}" --upstream')
    if request["pr"] == "my-git":
        steps.append(f'/my-git:pr "{path}" title "{title}", body from "{body_file}" (base {request["base"]})')
    elif request["pr"] == "manual":
        steps.append(f"open the PR/MR {request['branch']} -> {request['base']} by hand with the body in {body_file}")
    steps.append(f"sdlc ship-record --ticket {s.ctx['ticket']} --repo {request['repo']} [--pr <url>]")
    return {"repo": request["repo"], "via": "my-git", "staged": staged, "tree": tree, "branch": request["branch"],
            "commitMessageFile": str(msg), "prBodyFile": str(body_file), "pr": request["pr"],
            "myGit": {"root": str(mygit_root), "version": mygit.version(mygit_root)},
            "guardApprovalExpiresAt": approval["expiresAt"], "next": steps}


def _open_pr_cli(pr_tool: str, path: str, ident: dict[str, str], base: str, branch: str,
                 title: str, body_file: Path) -> str | None:
    if pr_tool == "gh":
        cmd = _cli("gh") + ["pr", "create", "--base", base, "--head", branch, "--title", title,
                             "--body-file", str(body_file)]
    else:
        cmd = _cli("glab") + ["mr", "create", "--target-branch", base, "--source-branch", branch,
                              "--title", title, "--description", body_file.read_text(encoding="utf-8"), "--yes"]
    try:
        res = subprocess.run(cmd, cwd=path, env=gitid.env(ident), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except OSError as exc:
        raise HarnessError(f"{pr_tool} execution failed: {exc}") from exc
    if res.returncode != 0:
        raise HarnessError((res.stderr or res.stdout).strip()[:300] or f"{pr_tool} exited {res.returncode}")
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else None


def execute_native(s: Any, request: dict[str, Any]) -> dict[str, Any]:
    """`native-git` backend: stage, commit as profile identity, verify, push, and open PR/MR if configured."""
    path, ident, staged, tree, msg, body_file = _stage_approved(s, request)
    digest = request_digest(request)
    repo_id = request["repo"]
    approvals.grant(s.pdir, s.ctx["ticket"], repo_id, path, request["branch"], digest)
    try:
        _git(path, ["commit", "-F", str(msg)], ident)
        sha = _verify_commit(s, repo_id, path, ident, request["headOid"], tree)
        shipped = s.state.setdefault("shipped", {}).setdefault(repo_id, {"commits": []})
        if sha not in shipped["commits"]:
            shipped["commits"].append(sha)
        shipped["branch"] = request["branch"]
        shipped["lastApproval"] = digest
        if s.state.get("shipPending"):
            s.state["shipPending"].pop(repo_id, None)
            if not s.state["shipPending"]:
                s.state.pop("shipPending")
        pushed = False
        if request["push"]:
            res = gitid.run(path, ["push", "-u", "origin", f"HEAD:refs/heads/{request['branch']}"], ident)
            if res.returncode != 0:
                s.save()
                s.ticket.log({"event": "ship", "repo": repo_id, "approval": digest, "tree": tree, "via": "native-git"})
                s.ticket.log({"event": "ship-record", "repo": repo_id, "commit": sha, "approval": digest,
                              "pushed": False, "pr": shipped.get("pr")})
                raise HarnessError(f"G7: commit {sha[:10]} succeeded and was recorded, but git push failed: "
                                   f"{(res.stderr or res.stdout).strip()[:300]}")
            pushed = True
            shipped["pushedAt"] = now()
        if pushed and request["pr"] in ("gh", "glab") and not shipped.get("pr"):
            title = request["commitMessage"].splitlines()[0][:72]
            try:
                pr_url = _open_pr_cli(request["pr"], path, ident, request["base"], request["branch"], title, body_file)
            except HarnessError as exc:
                s.save()
                s.ticket.log({"event": "ship", "repo": repo_id, "approval": digest, "tree": tree, "via": "native-git"})
                s.ticket.log({"event": "ship-record", "repo": repo_id, "commit": sha, "approval": digest,
                              "pushed": True, "pr": shipped.get("pr")})
                raise HarnessError(f"G7: commit {sha[:10]} pushed and recorded, but {request['pr']} PR creation failed: "
                                   f"{exc}") from exc
            if pr_url:
                shipped["pr"] = pr_url
        next_steps = []
        if request["pr"] == "manual" and not shipped.get("pr"):
            next_steps.append(f"open the PR/MR {request['branch']} -> {request['base']} by hand with the body in {body_file}")
            next_steps.append(f"sdlc ship-record --ticket {s.ctx['ticket']} --repo {repo_id} --pr <url>")
    finally:
        approvals.revoke(s.pdir, s.ctx["ticket"], repo_id)
    return {"repo": repo_id, "via": "native-git", "staged": staged, "tree": tree, "commit": sha,
            "branch": request["branch"], "pushed": pushed, "pr": shipped.get("pr"), "prMode": request["pr"],
            "commitMessageFile": str(msg), "prBodyFile": str(body_file), "approval": digest, "next": next_steps}


def record_ship(s: Any, repo_id: str, pr: str | None) -> dict[str, Any]:
    """Verify the commit is the approved one (or confirm remote state on an existing native ship) and record it."""
    repo = next(r for r in s.ctx["repositories"] if r["id"] == repo_id)
    path = repo.get("worktree") or repo["path"]
    ident = gitid.identity(s.profile, repo_id)
    pending = (s.state.get("shipPending") or {}).get(repo_id)
    if not pending:
        shipped = (s.state.get("shipped") or {}).get(repo_id)
        if not (shipped and shipped.get("commits")):
            raise HarnessError(f"G7: nothing staged for {repo_id}: run ship-request, approval and ship first")
        sha = shipped["commits"][-1]
        branch = shipped.get("branch", "")
        remote = gitid.run(path, ["ls-remote", "origin", f"refs/heads/{branch}"], ident) if branch else None
        pushed = bool(remote and remote.returncode == 0 and remote.stdout.split()[:1] == [sha])
        if pushed:
            shipped["pushedAt"] = now()
        if pr:
            shipped["pr"] = pr
        approvals.revoke(s.pdir, s.ctx["ticket"], repo_id)
        return {"repo": repo_id, "commit": sha, "branch": branch, "pushed": pushed,
                "pr": shipped.get("pr"), "approval": shipped.get("lastApproval", "recorded")}

    sha = _verify_commit(s, repo_id, path, ident, pending["headOid"], pending["tree"])
    shipped = s.state.setdefault("shipped", {}).setdefault(repo_id, {"commits": []})
    if sha not in shipped["commits"]:
        shipped["commits"].append(sha)
    shipped["branch"] = pending["branch"]
    shipped["lastApproval"] = pending["requestDigest"]
    remote = gitid.run(path, ["ls-remote", "origin", f"refs/heads/{pending['branch']}"], ident)
    pushed = remote.returncode == 0 and remote.stdout.split()[:1] == [sha]
    if pushed:
        shipped["pushedAt"] = now()
    if pr:
        shipped["pr"] = pr
    s.state["shipPending"].pop(repo_id, None)
    if not s.state["shipPending"]:
        s.state.pop("shipPending")
    approvals.revoke(s.pdir, s.ctx["ticket"], repo_id)
    return {"repo": repo_id, "commit": sha, "branch": pending["branch"], "pushed": pushed, "pr": shipped.get("pr"),
            "approval": pending["requestDigest"]}


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
