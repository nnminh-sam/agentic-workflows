"""Branching model rules BR001-BR005: protected branches, branch naming/base, PR targets."""
from __future__ import annotations

import re

from .commands import GitCall, Invocation, gh_path
from .contracts import Contract
from .decision import Verdict

# runner(argv, cwd) -> (returncode, stdout); same shape as identity.Runner
_ZERO = "0" * 40


def _settings(c: Contract) -> tuple[str, str, str]:
    s = c.settings
    return s.get("production_branch", "main"), s.get("staging_branch", "develop/base"), s.get("remote", "origin")


def protected(c: Contract) -> list[str]:
    prod, staging, _ = _settings(c)
    return [prod, staging]


def current_branch(run, cwd: str) -> str | None:
    code, out = run(["git", "-C", cwd, "symbolic-ref", "--short", "-q", "HEAD"], cwd)
    return out.strip() if code == 0 and out.strip() else None


def _has_commits(run, cwd: str) -> bool:
    code, _ = run(["git", "-C", cwd, "rev-parse", "--verify", "-q", "HEAD"], cwd)
    return code == 0


def _remote_has(run, cwd: str, remote: str, branch: str) -> bool:
    code, _ = run(["git", "-C", cwd, "rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}"], cwd)
    return code == 0


def _short(ref: str) -> str:
    ref = ref.lstrip("+")
    for p in ("refs/heads/",):
        if ref.startswith(p):
            return ref[len(p):]
    return ref


# ---------------------------------------------------------------- BR001
def check_local_commit(call: GitCall, c: Contract, run) -> Verdict:
    v = Verdict()
    r = c.rule("BR001")
    if not r.enabled or call.sub not in r.params.get("subcommands", []):
        return v
    argv = call.argv
    if call.sub == "merge" and ("--ff-only" in argv or "--abort" in argv or "--continue" in argv):
        return v
    if call.sub in ("rebase", "cherry-pick", "revert", "am") and any(
            a in argv for a in ("--abort", "--continue", "--skip", "--quit")):
        return v
    branch = current_branch(run, call.cwd)
    if branch not in protected(c):
        return v
    _, staging, _ = _settings(c)
    if call.sub == "commit" and not _has_commits(run, call.cwd):
        v.add("BR001", r.params.get("initial_commit_severity", "ask"),
              f"initial commit directly on '{branch}' (allowed once to bootstrap the repository)")
        return v
    v.add("BR001", r.severity,
          f"git {call.sub} on protected branch '{branch}': create a work branch first, e.g. "
          f"git switch -c develop/feat/<name> {staging}, and merge through a PR")
    return v


# ---------------------------------------------------------------- BR002
_PUSH_WITH_VALUE = {"--repo", "--receive-pack", "--exec", "-o", "--push-option", "--signed"}


def _push_targets(call: GitCall, run) -> tuple[str | None, list[tuple[str, bool]], bool]:
    """(remote, [(dst branch, is_delete)], touches_everything)."""
    argv = call.argv
    everything = any(a in ("--all", "--mirror", "--branches") for a in argv)
    delete = any(a in ("--delete", "-d") for a in argv)
    pos: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            pos.extend(argv[i + 1:])
            break
        if a in _PUSH_WITH_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    remote = pos[0] if pos else None
    targets: list[tuple[str, bool]] = []
    cur = current_branch(run, call.cwd)
    for spec in pos[1:]:
        if spec.startswith("refs/tags/") or spec == "tag":
            continue
        if ":" in spec:
            src, dst = spec.split(":", 1)
            dst = _short(dst) if dst else _short(src)
            is_del = src.lstrip("+") == ""
        else:
            src = dst = _short(spec)
            is_del = delete
        if dst in ("HEAD", "@") and cur:
            dst = cur
        targets.append((dst, is_del))
    if not pos[1:] and not everything and cur:
        targets.append((cur, False))  # push.default simple/upstream: the current branch
    return remote, targets, everything


def check_push(call: GitCall, c: Contract, run) -> Verdict:
    v = Verdict()
    r = c.rule("BR002")
    if not r.enabled or call.sub != "push":
        return v
    remote, targets, everything = _push_targets(call, run)
    remote = remote or _settings(c)[2]
    prot = protected(c)
    if everything:
        v.add("BR002", r.severity, f"git push --all/--mirror would also update the protected branches {prot}")
        return v
    for dst, is_del in targets:
        if dst not in prot:
            continue
        if is_del:
            v.add("BR002", r.severity, f"deleting protected branch '{dst}' on the remote is not allowed")
        elif not _remote_has(run, call.cwd, remote, dst):
            v.add("BR002", r.params.get("new_branch_severity", "ask"),
                  f"first push of protected branch '{dst}' to {remote} (bootstrap only; later changes go through PRs)")
        else:
            v.add("BR002", r.severity,
                  f"direct push to protected branch '{dst}': push a work branch and open a PR (/my-git:pr)")
    return v


# ---------------------------------------------------------------- BR003 / BR004
_BRANCH_LIST_FLAGS = {"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-l", "--list", "-a",
                      "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--show-current", "--contains",
                      "--no-contains", "--merged", "--no-merged", "--points-at", "--edit-description",
                      "-u", "--set-upstream-to", "--unset-upstream", "--sort", "--format", "--column"}


def _new_branch(call: GitCall) -> tuple[str, str | None] | None:
    """(name, start point or None) for commands that create a branch."""
    argv = call.argv
    create = {"checkout": ("-b", "-B"), "switch": ("-c", "-C", "--create", "--force-create")}
    if call.sub in create:
        for i, a in enumerate(argv):
            flag, _, inline = a.partition("=")
            if flag in create[call.sub] and (inline or i + 1 < len(argv)):
                name = inline or argv[i + 1]
                rest = [x for x in argv[i + 2 if not inline else i + 1:] if not x.startswith("-")]
                return name, (rest[0] if rest else None)
        return None
    if call.sub == "branch":
        if any(a.split("=", 1)[0] in _BRANCH_LIST_FLAGS for a in argv):
            return None
        pos = [a for a in argv if not a.startswith("-")]
        if not pos:
            return None
        return pos[0], (pos[1] if len(pos) > 1 else None)
    return None


def check_new_branch(call: GitCall, c: Contract, run) -> Verdict:
    v = Verdict()
    nb = _new_branch(call)
    if nb is None:
        return v
    name, start = nb
    prod, staging, remote = _settings(c)
    if name in (prod, staging):
        return v
    r3 = c.rule("BR003")
    if r3.enabled:
        pats = r3.params.get("patterns", [])
        if pats and not any(re.search(p, name) for p in pats):
            v.add("BR003", r3.severity, f"branch name '{name}' must match {' or '.join(pats)} "
                                        f"(e.g. develop/feat/<feature>)")
    r4 = c.rule("BR004")
    if r4.enabled:
        allowed = {staging, f"{remote}/{staging}", f"refs/heads/{staging}", f"refs/remotes/{remote}/{staging}",
                   *r4.params.get("extra_base_refs", [])}
        base = start if start is not None else current_branch(run, call.cwd)
        if base not in allowed:
            shown = f"'{start}'" if start is not None else f"the current branch '{base}'"
            v.add("BR004", r4.severity, f"work branch '{name}' must start from {staging}, not {shown}: "
                                        f"git switch -c {name} {remote}/{staging}")
    return v


def check_git(call: GitCall, c: Contract, run) -> Verdict:
    v = Verdict()
    v.extend(check_local_commit(call, c, run))
    v.extend(check_push(call, c, run))
    v.extend(check_new_branch(call, c, run))
    return v


# ---------------------------------------------------------------- BR005
def _flag(argv: list[str], short: str, long: str) -> str | None:
    val = None
    for i, a in enumerate(argv):
        if a in (short, long) and i + 1 < len(argv):
            val = argv[i + 1]
        elif a.startswith(long + "="):
            val = a.split("=", 1)[1]
    return val


def check_gh(inv: Invocation, c: Contract, run) -> Verdict:
    v = Verdict()
    r = c.rule("BR005")
    path = gh_path(inv)
    if not r.enabled or path not in (["pr", "create"], ["pr", "edit"]):
        return v
    prod, staging, _ = _settings(c)
    argv = inv.argv
    base = _flag(argv, "-B", "--base")
    if base is None:
        if path == ["pr", "create"]:
            v.add("BR005", r.severity, f"gh pr create must pass --base ({staging} for work, {prod} for a release)")
        return v
    head = _flag(argv, "-H", "--head")
    if head is None and path == ["pr", "create"]:
        head = current_branch(run, inv.cwd)
    if path == ["pr", "edit"] and base == prod:
        v.add("BR005", "ask", f"changing a PR's base to {prod}: only {staging} may target {prod}")
        return v
    problem = pr_target_problem(base, head, c)
    if problem:
        v.add("BR005", r.severity, problem)
    return v


def pr_target_problem(base: str | None, head: str | None, c: Contract) -> str | None:
    """Why a PR from head into base breaks the branching model, or None."""
    prod, staging, _ = _settings(c)
    if head and ":" in head:
        head = head.split(":", 1)[1]
    if not base:
        return f"--base is required ({staging} for work, {prod} for a release)"
    if base == prod and head != staging:
        return f"only {staging} may open a PR into {prod} (release); '{head}' must target {staging}"
    if base not in (prod, staging):
        return f"PR base '{base}' is not allowed; use {staging} (or {prod} for a release)"
    return None
