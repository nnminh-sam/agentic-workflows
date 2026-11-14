"""Project git identity: every git command the harness or a unit runs goes through here.

The identity comes from the profile (`git.user`, optional `git.sshCommand`, optional per-repository `git`
override). It is applied per command through environment variables, so neither the repository config nor the
user's global config is changed.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from . import gitpolicy
from .errors import HarnessError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
def identity(profile: dict[str, Any], repo_id: str | None = None) -> dict[str, str]:
    git = profile.get("git") or {}
    out = {"name": (git.get("user") or {}).get("name", ""), "email": (git.get("user") or {}).get("email", ""),
           "sshCommand": git.get("sshCommand", "")}
    if repo_id:
        repo = next((r for r in profile.get("repositories", []) if r.get("id") == repo_id), {})
        over = repo.get("git") or {}
        out["name"] = (over.get("user") or {}).get("name", out["name"])
        out["email"] = (over.get("user") or {}).get("email", out["email"])
        out["sshCommand"] = over.get("sshCommand", out["sshCommand"])
    return out


def validate(profile: dict[str, Any]) -> list[str]:
    errors = []
    git = profile.get("git")
    if not isinstance(git, dict) or not isinstance(git.get("user"), dict):
        return ["missing 'git.user' {name, email}: the account every git command of this project uses"]
    for scope, block in [("git", git)] + [(f"repositories.{r.get('id')}.git", r["git"]) for r in profile.get("repositories", []) if r.get("git")]:
        user = block.get("user")
        if scope == "git" or user:
            if not (user or {}).get("name", "").strip():
                errors.append(f"{scope}.user.name is empty")
            if not EMAIL_RE.match((user or {}).get("email", "")):
                errors.append(f"{scope}.user.email is not an email address")
        if "sshCommand" in block and not str(block["sshCommand"]).strip():
            errors.append(f"{scope}.sshCommand is empty (omit it to use the default ssh)")
    return errors


def env(ident: dict[str, str], base: dict[str, str] | None = None) -> dict[str, str]:
    e = dict(os.environ if base is None else base)
    if ident.get("name"):
        e["GIT_AUTHOR_NAME"] = e["GIT_COMMITTER_NAME"] = ident["name"]
    if ident.get("email"):
        e["GIT_AUTHOR_EMAIL"] = e["GIT_COMMITTER_EMAIL"] = ident["email"]
    if ident.get("sshCommand"):
        e["GIT_SSH_COMMAND"] = ident["sshCommand"]
    return e


def check_args(args: list[str]) -> None:
    """The wrapper's gate on a git command a node asked for (policy in gitpolicy.py)."""
    if not args:
        raise HarnessError("no git command given (use: sdlc git --ticket <id> --repo <repo> -- <git args>)")
    why = gitpolicy.refusal(args, via_wrapper=True)
    if why:
        raise HarnessError(f"G7: refused `git {' '.join(args)}`: {why}")


def run(path: str, args: list[str], ident: dict[str, str], capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", path, *args], env=env(ident), capture_output=capture, text=True,
                          encoding="utf-8", errors="replace")


def same_name(a: str, b: str) -> bool:
    """git config and commit metadata may store one name in different Unicode forms (NFC vs NFD: "Nguyễn")."""
    import unicodedata
    norm = lambda x: unicodedata.normalize("NFC", x or "").strip().casefold()  # noqa: E731
    return norm(a) == norm(b)


def same_email(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def commits_since(path: str, since: str | None, ident: dict[str, str]) -> list[dict[str, str]]:
    if not since:
        return []
    res = run(path, ["log", f"{since}..HEAD", "--format=%H%x09%an%x09%ae"], ident)
    if res.returncode != 0:
        return []
    out = []
    for line in res.stdout.splitlines():
        sha, an, ae = (line.split("\t") + ["", ""])[:3]
        out.append({"sha": sha, "name": an, "email": ae})
    return out


def foreign_commits(path: str, since: str | None, ident: dict[str, str]) -> list[str]:
    """Commits after `since` (the HEAD recorded at init) whose author or committer is not the project identity."""
    if not since:
        return []
    res = run(path, ["log", f"{since}..HEAD", "--format=%h%x09%an%x09%ae%x09%cn%x09%ce"], ident)
    if res.returncode != 0:
        return [f"cannot read history since {since[:10]}: {res.stderr.strip()[:120]}"]
    bad = []
    for line in res.stdout.splitlines():
        sha, an, ae, cn, ce = (line.split("\t") + [""] * 5)[:5]
        if not (same_email(ae, ident["email"]) and same_email(ce, ident["email"]) and same_name(an, ident["name"])):
            bad.append(f"{sha} by {an} <{ae}> (committer {cn} <{ce}>)")
    return bad
