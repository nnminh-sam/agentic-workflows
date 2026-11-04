#!/usr/bin/env python3
"""Show or write your git-guard profile (the account values the shared contracts refer to).

  python3 setup_profile.py show
  python3 setup_profile.py init --from-current                 # fill from current git config + gh login
  python3 setup_profile.py init --github-user octocat --git-email me@x.io --ssh-key ~/.ssh/id_gh
  python3 setup_profile.py init --scope user ...               # ~/.claude/git-guard/profile.json (all projects)
  python3 setup_profile.py unset github_orgs
  python3 setup_profile.py eject commit-message                # copy a shipped contract into the project to edit it

The project profile is <project>/.claude/git-guard/profile.local.json (git-ignored);
the project is $GIT_GUARD_PROJECT_DIR, else $CLAUDE_PROJECT_DIR, else the current directory.
Environment variables (GIT_GUARD_GITHUB_USER, ...) override both files.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import contracts as contracts_mod, identity, paths  # noqa: E402
from gitguard import profile as profile_mod  # noqa: E402
from gitguard.shellparse import BASH, lex  # noqa: E402

FILES = {"project": profile_mod.PROJECT_PROFILE, "user": profile_mod.USER_PROFILE}


def variables() -> dict:
    return contracts_mod.load_raw()["identity"]["profile_variables"]


def detect_current(cwd: str, run=identity.default_runner, env=None) -> dict:
    """Best-effort values from the machine's current git and gh configuration."""
    env = dict(os.environ if env is None else env)
    out = {}
    code, name = run(["git", "-C", cwd, "config", "--get", "user.name"], cwd)
    if code == 0 and name.strip():
        out["git_name"] = name.strip()
    code, email = run(["git", "-C", cwd, "config", "--get", "user.email"], cwd)
    if code == 0 and email.strip():
        out["git_email"] = [email.strip()]
    code, ssh = run(["git", "-C", cwd, "config", "--get", "core.sshCommand"], cwd)
    if code == 0 and ssh.strip():
        segs = lex(ssh.strip(), BASH)
        words = segs[0].values if segs else []
        for i, w in enumerate(words):
            if w == "-i" and i + 1 < len(words):
                out["ssh_key"] = words[i + 1]
    login, _ = identity.gh_active_login("github.com", env, run, cwd, use_cache=False)
    if login:
        out["github_user"] = login
    return out


def cmd_show(_args) -> int:
    decls = variables()
    try:
        prof = profile_mod.load(decls, files=(FILES["project"], FILES["user"]))
    except profile_mod.ProfileError as e:
        print(f"invalid profile: {e}", file=sys.stderr)
        return 2
    width = max(len(k) for k in decls)
    for k, decl in decls.items():
        v = prof[k]
        shown = json.dumps(v.value) if v.value is not None else "(unset)"
        print(f"{k:<{width}}  {shown:<45} {v.source}")
    print(f"\nproject file: {FILES['project']}\nuser file:    {FILES['user']}")
    print("contracts:")
    for name, path in contracts_mod.contract_paths().items():
        tag = "shipped" if path.parent == paths.DEFAULT_CONTRACTS_DIR else "project override"
        print(f"  {name:<15} {path}  ({tag})")
    return 0


def cmd_eject(args) -> int:
    """Copy shipped contracts into <project>/.claude/git-guard/contracts/ so this project can edit them."""
    if paths.is_vendored():
        print(f"vendored install: edit the contracts in place under {paths.DEFAULT_CONTRACTS_DIR}")
        return 0
    names = list(contracts_mod.CONTRACT_FILES) if "all" in args.names else args.names
    target = paths.ensure_local_dir() / "contracts"
    target.mkdir(exist_ok=True)
    for name in names:
        fname = contracts_mod.CONTRACT_FILES[name]
        dest = target / fname
        if dest.exists() and not args.force:
            print(f"exists, kept: {dest} (use --force to replace it)")
            continue
        dest.write_text((paths.DEFAULT_CONTRACTS_DIR / fname).read_text(encoding="utf-8"), encoding="utf-8")
        print(f"wrote {dest}")
    print("Commit these files to share the project's rules; delete one to go back to the shipped default.")
    return 0


def cmd_init(args) -> int:
    decls = variables()
    path = FILES[args.scope]
    try:
        data = profile_mod._read(path)
    except profile_mod.ProfileError as e:
        print(e, file=sys.stderr)
        return 2
    if args.from_current:
        for k, v in detect_current(os.getcwd()).items():
            if args.overwrite or k not in data:
                data[k] = v
    given = {"github_user": args.github_user, "github_orgs": args.github_orgs, "git_name": args.git_name,
             "git_email": args.git_email, "ssh_key": args.ssh_key}
    for k, v in given.items():
        if v is not None and k in decls:
            data[k] = profile_mod._norm(decls[k].get("type", "string"), v)
    unknown = sorted(set(data) - set(decls))
    if unknown:
        print(f"warning: unknown profile keys ignored by the hook: {unknown}", file=sys.stderr)
    if args.scope == "project":
        paths.ensure_local_dir(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in data.items() if v is not None}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return cmd_show(args)


def cmd_unset(args) -> int:
    path = FILES[args.scope]
    data = profile_mod._read(path)
    for k in args.keys:
        data.pop(k, None)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"updated {path}")
    return cmd_show(args)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print every profile variable, its value and where it came from")
    p = sub.add_parser("init", help="create or update a profile file")
    p.add_argument("--scope", choices=FILES, default="project")
    p.add_argument("--from-current", action="store_true", help="fill unset values from current git/gh config")
    p.add_argument("--overwrite", action="store_true", help="with --from-current, replace existing values")
    p.add_argument("--github-user")
    p.add_argument("--github-orgs", help="comma-separated")
    p.add_argument("--git-name")
    p.add_argument("--git-email", help="comma-separated")
    p.add_argument("--ssh-key")
    u = sub.add_parser("unset", help="remove keys from a profile file")
    u.add_argument("keys", nargs="+")
    u.add_argument("--scope", choices=FILES, default="project")
    e = sub.add_parser("eject", help="copy shipped contracts into the project so they can be customized")
    e.add_argument("names", nargs="+", choices=[*contracts_mod.CONTRACT_FILES, "all"])
    e.add_argument("--force", action="store_true", help="replace an existing project copy")
    args = ap.parse_args(argv)
    return {"show": cmd_show, "init": cmd_init, "unset": cmd_unset, "eject": cmd_eject}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
