#!/usr/bin/env python3
"""Set up a repository with the my-git branching model, from templates/repo/.

Branches: <production> (main) for releases, <staging> (develop/base) as the integration branch.
Both only change through pull requests; work branches are develop/<type>/<name> off staging.
Names and patterns come from the branching contract (a project override in
.claude/git-guard/contracts/branching.json wins), so the template follows the guard.

  python3 setup_repo.py apply     [project]           # PR template + .githooks, enable core.hooksPath
  python3 setup_repo.py bootstrap [project]           # apply + git init, initial commit, create the staging branch
  python3 setup_repo.py github    [project] [--repo owner/name] [--create private|public] [--approvals N]
                                                      # push both branches, default branch = staging,
                                                      # ruleset protecting both; merged head branches are
                                                      # kept (auto-delete off) so long-lived branches
                                                      # such as develop/infra survive their PRs
  python3 setup_repo.py all       [project] ...       # apply + bootstrap + github
  python3 setup_repo.py render-ruleset                # print the ruleset JSON

Every command accepts --dry-run (print what would happen). 'github' changes the GitHub repository,
so confirm with the user before running it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import contracts as contracts_mod  # noqa: E402
from gitguard import paths  # noqa: E402

TEMPLATE = paths.PACKAGE_ROOT / "templates" / "repo"
LOCAL_FILES = {  # template path -> project path
    ".github/pull_request_template.md": ".github/pull_request_template.md",
    ".githooks/pre-commit": ".githooks/pre-commit",
    ".githooks/pre-push": ".githooks/pre-push",
    ".githooks/commit-msg": ".githooks/commit-msg",
}


# ---------------------------------------------------------------- config
def model(project: Path) -> dict:
    """Branch names, branch pattern and commit types from the (project-aware) contracts."""
    old = os.environ.get("GIT_GUARD_PROJECT_DIR")
    os.environ["GIT_GUARD_PROJECT_DIR"] = str(project)
    try:
        local = paths.local_dir()
        raw = {name: json.loads(paths.contract_path(fname, local).read_text(encoding="utf-8"))
               for name, fname in contracts_mod.CONTRACT_FILES.items()}
    finally:
        if old is None:
            os.environ.pop("GIT_GUARD_PROJECT_DIR", None)
        else:
            os.environ["GIT_GUARD_PROJECT_DIR"] = old
    br = raw["branching"]
    st = br["settings"]
    pattern = next(r for r in br["rules"] if r["id"] == "BR003")["params"]["patterns"][0]
    types = raw["commit-message"]["settings"]["types"]
    return {"production": st["production_branch"], "staging": st["staging_branch"], "remote": st["remote"],
            "pattern": pattern, "types": types}


def render(text: str, m: dict, approvals: int = 0) -> str:
    example = "develop/feat/<name>" if m["pattern"].startswith("^develop/") else "<work-branch>"
    return (text.replace("{{PRODUCTION}}", m["production"]).replace("{{STAGING}}", m["staging"])
            .replace("{{BRANCH_PATTERN}}", m["pattern"]).replace("{{BRANCH_EXAMPLE}}", example)
            .replace("{{TYPES}}", "|".join(m["types"])).replace("{{TYPES_LIST}}", ", ".join(m["types"]))
            .replace("{{REQUIRED_APPROVALS}}", str(approvals)))


def ruleset(m: dict, approvals: int) -> dict:
    return json.loads(render((TEMPLATE / "github" / "ruleset.json").read_text(encoding="utf-8"), m, approvals))


# ---------------------------------------------------------------- helpers
class Runner:
    def __init__(self, cwd: Path, dry: bool):
        self.cwd, self.dry = cwd, dry

    def __call__(self, argv: list[str], check: bool = True, capture: bool = False, stdin: str | None = None):
        shown = " ".join(a if a and " " not in a else json.dumps(a) for a in argv)
        if self.dry:
            print(f"[dry-run] {shown}")
            return subprocess.CompletedProcess(argv, 0, "", "")
        print(f"$ {shown}")
        p = subprocess.run(argv, cwd=self.cwd, text=True, input=stdin,
                           capture_output=capture or stdin is not None)
        if check and p.returncode != 0:
            raise SystemExit(f"failed ({p.returncode}): {shown}\n{(p.stderr or '').strip()}")
        return p

    def query(self, argv: list[str]) -> tuple[int, str]:
        p = subprocess.run(argv, cwd=self.cwd, text=True, capture_output=True)
        return p.returncode, p.stdout.strip()


def is_repo(project: Path) -> bool:
    return subprocess.run(["git", "-C", str(project), "rev-parse", "--git-dir"],
                          capture_output=True).returncode == 0


def has_commits(project: Path) -> bool:
    return subprocess.run(["git", "-C", str(project), "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True).returncode == 0


def identity_args() -> list[str]:
    """Pin the profile's author on bootstrap commits (falls back to git config)."""
    try:
        prof = contracts_mod.load().profile
    except contracts_mod.ContractError:
        return []
    out = []
    name, email = prof.get("git_name"), prof.get("git_email")
    if name and name.value:
        out += ["-c", f"user.name={name.value}"]
    if email and email.value:
        out += ["-c", f"user.email={email.value[0] if isinstance(email.value, list) else email.value}"]
    return out


# ---------------------------------------------------------------- commands
def cmd_apply(project: Path, m: dict, force: bool, dry: bool, init_follows: bool = False) -> None:
    for src_rel, dst_rel in LOCAL_FILES.items():
        dst = project / dst_rel
        if dst.exists() and not force:
            print(f"kept {dst_rel} (exists; --force to replace)")
            continue
        text = render((TEMPLATE / src_rel).read_text(encoding="utf-8"), m)
        if dry:
            print(f"[dry-run] write {dst_rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        if dst_rel.startswith(".githooks/"):
            dst.chmod(0o755)
        print(f"wrote {dst_rel}")
    if is_repo(project):
        Runner(project, dry)(["git", "config", "core.hooksPath", ".githooks"])
    elif not init_follows:
        print("not a git repository yet: run 'bootstrap' (or git init, then: git config core.hooksPath .githooks)")


def cmd_bootstrap(project: Path, m: dict, dry: bool) -> None:
    run = Runner(project, dry)
    prod, staging = m["production"], m["staging"]
    if not is_repo(project):
        run(["git", "init", "-b", prod])
    run(["git", "config", "core.hooksPath", ".githooks"])
    if not has_commits(project):
        files = [p for p in (".github", ".githooks", ".gitignore", ".claude/settings.json") if (project / p).exists()]
        if files:
            run(["git", "add", "--", *files])
        run(["git", *identity_args(), "commit", "--allow-empty", "-m", "chore: initialize repository"])
    else:
        code, cur = run.query(["git", "symbolic-ref", "--short", "-q", "HEAD"])
        print(f"repository already has commits (on '{cur}'); not creating an initial commit")
    if run.query(["git", "rev-parse", "-q", "--verify", f"refs/heads/{prod}"])[0] != 0:
        print(f"note: no local '{prod}' branch; create it from your release line before running 'github'")
    elif run.query(["git", "rev-parse", "-q", "--verify", f"refs/heads/{staging}"])[0] != 0:
        run(["git", "branch", staging, prod])
    print(f"\nWork branches: git switch -c develop/feat/<name> {staging}")


def cmd_github(project: Path, m: dict, repo: str | None, create: str | None, approvals: int, dry: bool) -> None:
    run = Runner(project, dry)
    prod, staging, remote = m["production"], m["staging"], m["remote"]
    if not is_repo(project) and not dry:
        raise SystemExit("not a git repository: run 'bootstrap' first")
    code, url = run.query(["git", "remote", "get-url", remote])
    if code != 0:
        if not create or not repo:
            raise SystemExit(f"no '{remote}' remote. Add one, or pass --repo owner/name --create private|public")
        run(["gh", "repo", "create", repo, f"--{create}", "--source", ".", "--remote", remote])
    if not repo:
        code, out = run.query(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        if code != 0 or not out:
            raise SystemExit("could not determine the GitHub repository; pass --repo owner/name")
        repo = out
    # publish the two long-lived branches (the pre-push hook allows a first push)
    for b in (prod, staging):
        exists_remote = run.query(["git", "ls-remote", "--exit-code", "--heads", remote, b])[0] == 0
        if exists_remote:
            print(f"{remote}/{b} already exists")
        elif run.query(["git", "rev-parse", "-q", "--verify", f"refs/heads/{b}"])[0] == 0 or dry:
            run(["git", "push", "-u", remote, b])
        else:
            print(f"warning: local branch '{b}' missing; not pushed")
    # auto-delete stays off: it would also delete long-lived work branches (e.g. develop/infra) on merge
    run(["gh", "repo", "edit", repo, "--default-branch", staging, "--delete-branch-on-merge=false"])
    body = json.dumps(ruleset(m, approvals))
    name = ruleset(m, approvals)["name"]
    code, out = run.query(["gh", "api", f"repos/{repo}/rulesets", "--jq", f'.[] | select(.name=="{name}") | .id'])
    existing = out.splitlines()[0] if code == 0 and out else None
    method, endpoint = ("PUT", f"repos/{repo}/rulesets/{existing}") if existing else ("POST", f"repos/{repo}/rulesets")
    p = run(["gh", "api", "-X", method, endpoint, "--input", "-"], check=False, stdin=body)
    if dry:
        print(f"\n[dry-run] nothing was changed on GitHub. Without --dry-run this would "
              f"{'update' if existing else 'create'} ruleset '{name}' on {repo}.")
        return
    if p.returncode != 0:
        err = (p.stderr or "") + (p.stdout or "")
        print(err.strip(), file=sys.stderr)
        if "403" in err or "Upgrade" in err or "upgrade" in err:
            print("\nGitHub refused the ruleset. Rulesets on PRIVATE repositories need GitHub Pro/Team/Enterprise;\n"
                  "on a free plan make the repository public or upgrade, then re-run 'github'.", file=sys.stderr)
        raise SystemExit(1)
    print(f"\nruleset '{name}' {'updated' if existing else 'created'} on {repo}: "
          f"{prod} and {staging} need a PR (approvals: {approvals}), no force-push, no deletion")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "bootstrap", "github", "all"):
        s = sub.add_parser(name)
        s.add_argument("project", nargs="?", default=".")
        s.add_argument("--dry-run", action="store_true")
        if name in ("apply", "bootstrap", "all"):
            s.add_argument("--force", action="store_true", help="overwrite existing template files")
        if name in ("github", "all"):
            s.add_argument("--repo", help="owner/name (default: from the remote)")
            s.add_argument("--create", choices=("private", "public"), help="create the GitHub repo if there is no remote")
            s.add_argument("--approvals", type=int, default=0,
                           help="required approving reviews (default 0: a solo owner cannot approve their own PR)")
    r = sub.add_parser("render-ruleset")
    r.add_argument("project", nargs="?", default=".")
    r.add_argument("--approvals", type=int, default=0)
    args = ap.parse_args(argv)

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        ap.error(f"not a directory: {project}")
    if shutil.which("git") is None:
        ap.error("git is not on PATH")
    m = model(project)
    if args.cmd == "render-ruleset":
        print(json.dumps(ruleset(m, args.approvals), indent=2))
        return 0
    if args.cmd in ("apply", "bootstrap", "all"):
        cmd_apply(project, m, args.force, args.dry_run, init_follows=args.cmd != "apply")
    if args.cmd in ("bootstrap", "all"):
        cmd_bootstrap(project, m, args.dry_run)
    if args.cmd in ("github", "all"):
        if shutil.which("gh") is None:
            ap.error("gh is not on PATH")
        cmd_github(project, m, args.repo, args.create, args.approvals, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
