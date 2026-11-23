"""Agent-agnostic git hooks for ticket checkouts: `pre-commit`, `pre-push` and `reference-transaction`.

Claude Code has a PreToolUse guard (skills/kaneki-flow/scripts/guard_git.py); Codex and Antigravity do not. These
hooks are the part of the git-safety rules that holds for every agent, because git itself runs them:

- `pre-commit`: nodes never commit; only kaneki-ship does, inside a short-lived approval (`.sdlc/ship-approvals.json`).
- `pre-push`: the same approval, and even with it: never delete a remote ref, never force (non-fast-forward) a
  branch, never push to `main`, `develop/base` or the repository's base branch.
- `reference-transaction` (prepared phase, Git 2.28+): refuses deleting any `refs/heads/*` ref (`branch -d/-D/
  --delete`, `branch -m`, `update-ref -d`), any update of `main`, `develop/base` or the base branch, and any
  non-fast-forward move of a branch (`reset --hard HEAD~1`, `rebase`, `commit --amend`, `branch -f`) unless an
  approved ship covers that branch in this checkout. Fast-forward moves (the approved ship's commit) pass. Unlike
  `pre-commit` it cannot be skipped with `--no-verify`.

Where they are installed (`sdlc worktree`, and `sdlc init` for in-place lanes):

- Linked ticket worktree (full lane): Kaneki enables `extensions.worktreeConfig` and sets `--worktree core.hooksPath`
  to `<git-dir>/kaneki-hooks`, so the hooks apply only to git commands run in that worktree. Always enforcing.
- In-place checkout (lite lane, or `sdlc worktree --path <the repository>`): the same worktree-scoped `core.hooksPath`
  on the checkout itself, but the hooks enforce only while an in-place ticket is active: `<git-dir>/kaneki-hooks/
  active.json` lists the ticket, and that ticket's `state.json` still binds the repository to this checkout. With no
  active ticket the hooks only pass through to the chained hooks (the shell checks the marker before any Python
  runs), so the owner's git in that checkout behaves as before. `sdlc worktree --release --actor user` drops the
  ticket and, when no ticket is left, restores the checkout's previous `core.hooksPath` exactly. Deleting the ticket
  folder or rebinding the repository to a linked worktree also ends enforcement.

Chaining: the previously effective `core.hooksPath` (repo, global or worktree-scoped), or `<common-dir>/hooks`, is
chained after Kaneki's checks for every standard hook found there at install time (stdin kept for `pre-push` and
`reference-transaction`). A hook added there later is picked up at the next bind.

What hooks cannot catch: they guard commits, pushes and ref updates only. Discarding uncommitted work never touches
a ref: `git clean -f`, `git checkout -- .`, `git restore`, `git reset --hard` with no target (the ref does not move),
`git stash drop/clear`, deleting files, or editing `.git` (including these hooks and the marker) by hand. Those are
covered by the agent-side policies: Claude's PreToolUse guard (guard_git.py / gitpolicy.py), Codex approvals and
sandbox (`sandbox_mode`, `approval_policy`), and Antigravity's terminal command rules (deny list / request review).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

HOOKS_DIRNAME = "kaneki-hooks"
GUARDED = ("pre-commit", "pre-push", "reference-transaction")
ALWAYS_PROTECTED = ("main", "develop/base")
# client-side hooks git runs by name; the ones found in the chained directory get a pass-through forwarder
KNOWN_HOOKS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit", "prepare-commit-msg",
    "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-auto-gc",
    "post-rewrite", "sendemail-validate", "post-index-change", "reference-transaction", "push-to-checkout",
)

_GUARD_PY = '''#!/usr/bin/env python3
"""Kaneki git-safety guard for a ticket checkout (self-contained stdlib Python 3.9+, fail closed).

Run by the sh hooks next to it:
  pre-commit                     -> guard.py commit
  pre-push <remote> <url>        -> guard.py push   (ref lines on stdin)
  reference-transaction prepared -> guard.py ref    (ref lines on stdin)

Rules (owner's hard rule: branches are never deleted, nothing is force-pushed, history is never rewritten outside
an approved ship):
  commit  needs an approved ship for this checkout.
  push    needs an approved ship for this checkout; never deletes a remote ref, never forces (non-fast-forward)
          a branch, never pushes to main, develop/base or the base branch.
  ref     never deletes refs/heads/*; never updates main, develop/base or the base branch; never moves a branch
          to a commit that does not contain its current one (reset to an older commit, rebase, amend, branch -f)
          unless an approved ship covers that branch here. Fast-forward moves pass.

In-place mode (lite lane in the repository's own checkout) enforces only while active.json names a ticket whose
state.json still binds the repository to this checkout; otherwise every action passes.

Not covered by any hook (no ref changes): git clean -f, checkout -- ., restore, reset --hard without a target,
stash drop/clear, deleting files, editing .git by hand. The agent-side policies cover those: Claude's PreToolUse
guard, Codex approvals/sandbox, Antigravity terminal rules.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

APPROVAL_PATHS = (Path(".sdlc") / "ship-approvals.json", Path(".claude") / "kaneki-ship-approvals.json")
ZERO_RE = re.compile(r"^0+$")
HERE = Path(__file__).resolve().parent
PREFIX = "Blocked by kaneki worktree hook"


class Blocked(Exception):
    pass


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise Blocked(f"unreadable {what} ({exc})")


def _active_projects(cfg: dict) -> list:
    """The projects whose approvals count here; empty when an in-place checkout has no active ticket."""
    if cfg.get("mode", "worktree") != "in-place":
        return [str(cfg["project"])]
    marker = HERE / "active.json"
    if not marker.is_file():
        return []
    data = _read_json(marker, "active-ticket marker")
    out = []
    for ticket, entry in sorted((data.get("tickets") or {}).items()):
        state_path = Path(entry["ticketDir"]) / "state.json"
        if not state_path.is_file():
            continue  # the ticket is gone: it no longer holds this checkout
        state = _read_json(state_path, f"state.json of ticket {ticket}")
        repos = ((state.get("context") or {}).get("repositories") or [])
        repo = next((r for r in repos if isinstance(r, dict) and r.get("id") == entry.get("repo")), None)
        if repo is None:
            continue
        bound = repo.get("worktree")
        if repo.get("worktreeRel"):
            rel = Path(repo["worktreeRel"])
            bound = str(rel if rel.is_absolute() else Path(entry["project"]) / rel)
        if bound and _same_path(bound, cfg["worktree"]):
            out.append(str(entry["project"]))
    return out


def _approved(cfg: dict, projects: list, action: str, branch=None) -> bool:
    now = time.time()
    for project in projects:
        for rel in APPROVAL_PATHS:
            app_path = Path(project) / rel
            if not app_path.is_file():
                continue
            data = _read_json(app_path, "ship approvals file")
            for entry in (data.get("approvals", []) if isinstance(data, dict) else []):
                if not isinstance(entry, dict) or entry.get("expiresAt", 0) <= now:
                    continue
                if action not in entry.get("allow", []) or not _same_path(str(entry.get("path", "")), cfg["worktree"]):
                    continue
                if branch is None or entry.get("branch") == branch:
                    return True
    return False


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def _exists(oid: str) -> bool:
    return _git("cat-file", "-e", oid + "^{commit}").returncode == 0


def _is_ancestor(old: str, new: str) -> bool:
    return _git("merge-base", "--is-ancestor", old, new).returncode == 0  # 1 = no, other = error: both refuse


def _protected(cfg: dict) -> set:
    return set(cfg.get("protected") or ["main", "develop/base"])


def _check_refs(cfg: dict, projects: list, lines: list) -> None:
    for line in lines:
        parts = line.split()
        if len(parts) != 3 or not parts[2].startswith("refs/heads/"):
            continue
        old, new, ref = parts
        if old.startswith("ref:") or new.startswith("ref:"):
            continue  # symbolic-ref update (HEAD), not a branch move
        branch = ref[len("refs/heads/"):]
        if ZERO_RE.match(new):
            raise Blocked(f"rule: branches are never deleted. Refusing to delete {branch} "
                          "(git branch -d/-D/--delete, branch -m and update-ref -d all delete a branch); "
                          "ask the user to delete it themselves.")
        if branch in _protected(cfg):
            raise Blocked(f"rule: {branch} is protected; agents never move main, develop/base or the base branch. "
                          "Work on the ticket branch; kaneki-ship delivers it.")
        cur = old
        if ZERO_RE.match(old):  # unverified update: compare with the branch's current value
            res = _git("rev-parse", "--verify", "--quiet", ref)
            cur = res.stdout.strip() if res.returncode == 0 else ""
        if not cur or cur == new or _is_ancestor(cur, new):
            continue  # new branch, no-op, or fast-forward
        if _approved(cfg, projects, "commit", branch):
            continue
        raise Blocked(f"rule: history is never rewritten outside an approved ship. {branch} would move from "
                      f"{cur[:10]} to {new[:10]}, which drops commits (reset to an older commit, rebase, "
                      "commit --amend, branch -f).")


def _check_push(cfg: dict, projects: list, lines: list) -> None:
    if not _approved(cfg, projects, "push"):
        raise Blocked("nodes never push; only kaneki-ship does (after user approval).")
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _lref, lsha, rref, rsha = parts
        if ZERO_RE.match(lsha):
            raise Blocked(f"rule: branches are never deleted. Refusing to delete {rref} on the remote.")
        if not rref.startswith("refs/heads/"):
            continue
        branch = rref[len("refs/heads/"):]
        if branch in _protected(cfg):
            raise Blocked(f"rule: {branch} is protected; agents never push to main, develop/base or the base branch.")
        if ZERO_RE.match(rsha):
            continue
        if not _exists(rsha):
            raise Blocked(f"rule: nothing is force-pushed. The remote {branch} has commits this checkout does not "
                          "have; fetch them first (a push that drops them is never allowed).")
        if not _is_ancestor(rsha, lsha):
            raise Blocked(f"rule: nothing is force-pushed. Pushing {branch} would drop remote commits "
                          f"({rsha[:10]} is not in {lsha[:10]}).")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("commit", "push", "ref"):
        raise Blocked("invalid hook action")
    action = sys.argv[1]
    cfg = _read_json(HERE / "config.json", "hook config")
    if not isinstance(cfg, dict) or "worktree" not in cfg:
        raise Blocked("hook config has no worktree")
    projects = _active_projects(cfg)
    if cfg.get("mode") == "in-place" and not projects:
        return 0  # no active in-place ticket: the owner's checkout is left alone
    if action == "commit":
        if not _approved(cfg, projects, "commit"):
            raise Blocked("nodes never commit; only kaneki-ship does (after user approval).")
        return 0
    lines = sys.stdin.read().splitlines()
    if action == "push":
        _check_push(cfg, projects, lines)
    else:
        _check_refs(cfg, projects, lines)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Blocked as exc:
        print(f"{PREFIX}: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"{PREFIX}: internal error ({type(exc).__name__}: {exc}).", file=sys.stderr)
        sys.exit(1)
'''

_SH_HEAD = (
    "#!/bin/sh\n"
    "# {name}: written by kaneki (harness/worktree_hooks.py); rewritten at every `sdlc worktree` bind.\n"
    'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
    "mode={mode}\n"
    "chain={chain}\n"
    "ok() {{ \"$@\" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }}\n"
    "guard() {{\n"
    '  if [ -n "$SDLC_PYTHON" ] && ok $SDLC_PYTHON; then $SDLC_PYTHON "$here/guard.py" "$@"; return $?; fi\n'
    "  for py in python3 python; do\n"
    '    if command -v "$py" >/dev/null 2>&1 && ok "$py"; then "$py" "$here/guard.py" "$@"; return $?; fi\n'
    "  done\n"
    '  if command -v py >/dev/null 2>&1 && ok py -3; then py -3 "$here/guard.py" "$@"; return $?; fi\n'
    '  echo "Blocked by kaneki worktree hook: Python 3.9+ not found." >&2\n'
    "  return 1\n"
    "}}\n"
    '# in-place checkouts enforce only while an in-place ticket marker exists (guard.py checks the ticket too)\n'
    'enforcing() {{ [ "$mode" = worktree ] || [ -f "$here/active.json" ]; }}\n'
)

_SH_BODY = {
    "pre-commit": (
        "if enforcing; then guard commit </dev/null || exit $?; fi\n"
        'if [ -n "$chain" ] && [ -x "$chain" ]; then exec "$chain" "$@"; fi\n'
        "exit 0\n"
    ),
    "pre-push": (
        "input=$(cat; echo x); input=${input%x}\n"
        "if enforcing; then printf '%s' \"$input\" | guard push \"$@\" || exit $?; fi\n"
        'if [ -n "$chain" ] && [ -x "$chain" ]; then printf \'%s\' "$input" | "$chain" "$@"; exit $?; fi\n'
        "exit 0\n"
    ),
    "reference-transaction": (
        "input=$(cat; echo x); input=${input%x}\n"
        'if [ "$1" = prepared ] && enforcing; then\n'
        "  case \"$input\" in *refs/heads/*) printf '%s' \"$input\" | guard ref || exit $? ;; esac\n"
        "fi\n"
        'if [ -n "$chain" ] && [ -x "$chain" ]; then printf \'%s\' "$input" | "$chain" "$@"; exit $?; fi\n'
        "exit 0\n"
    ),
}


def _hook_script(name: str, mode: str, chain_hook: Path | None) -> str:
    chain = shlex.quote(chain_hook.as_posix()) if chain_hook else "''"
    return _SH_HEAD.format(name=name, mode=mode, chain=chain) + _SH_BODY[name]


def _forwarder(name: str, chain_hook: Path) -> str:
    chain = shlex.quote(chain_hook.as_posix())
    return (f"#!/bin/sh\n# {name}: kaneki pass-through to the chained hook.\n"
            f"if [ -x {chain} ]; then exec {chain} \"$@\"; fi\nexit 0\n")


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _resolve_git_path(base: Path, raw: str) -> Path:
    p = Path(raw.strip())
    return (p if p.is_absolute() else (base / p)).resolve()


def _write_lf(path: Path, text: str, executable: bool = False) -> None:
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    if executable:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _git_dirs(checkout: Path) -> tuple[Path, Path] | None:
    gd_res = _git(checkout, "rev-parse", "--git-dir")
    cd_res = _git(checkout, "rev-parse", "--git-common-dir")
    if gd_res.returncode != 0 or cd_res.returncode != 0:
        return None
    return _resolve_git_path(checkout, gd_res.stdout), _resolve_git_path(checkout, cd_res.stdout)


def hooks_dir_of(checkout: Path) -> Path | None:
    dirs = _git_dirs(checkout.resolve())
    return (dirs[0] / HOOKS_DIRNAME).resolve() if dirs else None


def install(worktree: Path, project: Path, *, in_place: bool = False, ticket: str | None = None,
            ticket_dir: Path | None = None, repo_id: str | None = None,
            base_branch: str | None = None) -> dict[str, Any]:
    """Install the guarded hooks (+ pass-through forwarders for chained hooks) for one ticket checkout.

    Linked worktree (`in_place=False`): always enforcing; a main checkout is skipped. In-place (`in_place=True`,
    needs `ticket`, `ticket_dir`, `repo_id`): enforcing only while this ticket stays active (see module docstring)."""
    wt_abs = worktree.resolve()
    proj_abs = project.resolve()
    dirs = _git_dirs(wt_abs)
    if dirs is None:
        return {"installed": False, "reason": "not-a-git-worktree"}
    git_dir, common_dir = dirs
    if git_dir == common_dir and not in_place:
        return {"installed": False, "reason": "main-checkout"}
    if in_place and not (ticket and ticket_dir and repo_id):
        raise ValueError("in-place hooks need ticket, ticket_dir and repo_id")

    hooks_dir = (git_dir / HOOKS_DIRNAME).resolve()
    old_cfg = _read_json(hooks_dir / "config.json") if (hooks_dir / "config.json").is_file() else {}
    chain_dir: Path | None = None
    cur_res = _git(wt_abs, "config", "--get", "core.hooksPath")
    ours = cur_res.returncode == 0 and cur_res.stdout.strip() and _resolve_git_path(wt_abs, cur_res.stdout) == hooks_dir
    if ours:
        chain_dir = Path(old_cfg["chainDir"]).resolve() if old_cfg.get("chainDir") else None
    elif cur_res.returncode == 0 and cur_res.stdout.strip():
        chain_dir = _resolve_git_path(wt_abs, cur_res.stdout)
    else:
        chain_dir = (common_dir / "hooks").resolve()

    ext_res = _git(wt_abs, "config", "extensions.worktreeConfig", "true")
    if ext_res.returncode != 0:
        return {"installed": False, "warning": "git does not support worktreeConfig; worktree hooks skipped"}
    # what this checkout's own (worktree-scoped) hooksPath was, so an in-place release restores it exactly
    restore = old_cfg.get("restoreWorktreeHooksPath")
    if not ours:
        prev = _git(wt_abs, "config", "--worktree", "--get", "core.hooksPath")
        restore = prev.stdout.strip() if prev.returncode == 0 and prev.stdout.strip() else None
    cfg_res = _git(wt_abs, "config", "--worktree", "core.hooksPath", hooks_dir.as_posix())
    if cfg_res.returncode != 0:
        return {"installed": False, "warning": "git does not support worktreeConfig; worktree hooks skipped"}

    hooks_dir.mkdir(parents=True, exist_ok=True)
    protected = list(dict.fromkeys([*ALWAYS_PROTECTED, *([base_branch] if base_branch else []),
                                    *(old_cfg.get("protected") or [])]))
    mode = "in-place" if in_place else "worktree"
    cfg = {"schemaVersion": 2, "mode": mode, "worktree": str(wt_abs), "project": str(proj_abs),
           "chainDir": str(chain_dir) if chain_dir else None, "protected": protected,
           "restoreWorktreeHooksPath": restore}
    _write_lf(hooks_dir / "config.json", json.dumps(cfg, indent=2) + "\n")
    _write_lf(hooks_dir / "guard.py", _GUARD_PY, executable=True)
    legacy = hooks_dir / "check_approval.py"
    if legacy.is_file():
        legacy.unlink()
    for name in KNOWN_HOOKS:
        target = hooks_dir / name
        chained = chain_dir / name if chain_dir else None
        if name in GUARDED:
            _write_lf(target, _hook_script(name, mode, chained), executable=True)
        elif chained is not None and chained.is_file():
            _write_lf(target, _forwarder(name, chained), executable=True)
        elif target.is_file():
            target.unlink()  # a forwarder for a hook the chained directory no longer has
    if in_place:
        marker = hooks_dir / "active.json"
        data = _read_json(marker)
        tickets = data.get("tickets") if isinstance(data.get("tickets"), dict) else {}
        tickets[ticket] = {"ticketDir": str(Path(ticket_dir).resolve()), "repo": repo_id, "project": str(proj_abs)}
        _write_lf(marker, json.dumps({"schemaVersion": 1, "tickets": tickets}, indent=2) + "\n")
    return {"installed": True, "mode": mode, "hooksDir": str(hooks_dir),
            "chained": str(chain_dir) if chain_dir else None, "protected": protected}


def release(checkout: Path, ticket: str) -> dict[str, Any]:
    """End `ticket`'s in-place protection of `checkout`. With no ticket left, restore the checkout's hooksPath."""
    co = checkout.resolve()
    hooks_dir = hooks_dir_of(co)
    if hooks_dir is None or not (hooks_dir / "config.json").is_file():
        return {"released": False, "reason": "no kaneki hooks in this checkout"}
    cfg = _read_json(hooks_dir / "config.json")
    if cfg.get("mode") != "in-place":
        return {"released": False, "reason": "linked worktrees keep their hooks (remove the worktree with "
                                             "`git worktree remove` when the ticket is done)"}
    marker = hooks_dir / "active.json"
    tickets = _read_json(marker).get("tickets") or {}
    was_active = tickets.pop(ticket, None) is not None
    if tickets:
        _write_lf(marker, json.dumps({"schemaVersion": 1, "tickets": tickets}, indent=2) + "\n")
        return {"released": was_active, "remaining": sorted(tickets), "hooksPathRestored": False}
    if marker.is_file():
        marker.unlink()
    cur = _git(co, "config", "--worktree", "--get", "core.hooksPath")
    restored = False
    if cur.returncode == 0 and _resolve_git_path(co, cur.stdout) == hooks_dir:
        if cfg.get("restoreWorktreeHooksPath"):
            _git(co, "config", "--worktree", "core.hooksPath", cfg["restoreWorktreeHooksPath"])
        else:
            _git(co, "config", "--worktree", "--unset", "core.hooksPath")
        restored = True
    return {"released": was_active, "remaining": [], "hooksPathRestored": restored}
