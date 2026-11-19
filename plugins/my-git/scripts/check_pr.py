#!/usr/bin/env python3
"""Check a pull request (title, body, base/head) against the contracts before running gh pr create.

  python3 check_pr.py --title "feat(auth): add login" --body-file pr.md --base develop/base --head develop/feat/login
  python3 check_pr.py --print-template        # the body skeleton the rules expect
  python3 check_pr.py ... --compare-url <repo>  # browser mode (pr_via=browser): also print the prefilled
                                                # GitHub compare link, built from <repo>'s origin remote

Rules: GH005 title (commit header rules), GH008 required body sections, GH009 / CM016 no AI attribution,
BR005 PR target (work branches -> staging or a parent work branch; only staging -> production).
Exit code: 0 = ok or warnings only, 1 = a deny/ask rule fired, 2 = usage / contract error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import branching, commit_message, contracts as contracts_mod, gh_policy, paths  # noqa: E402
from gitguard.decision import Verdict  # noqa: E402


def check(title: str, body: str, base: str | None, head: str | None, contracts) -> Verdict:
    v = Verdict()
    gh, commit, branch = contracts.gh, contracts.commit, contracts.branch
    r5 = gh.rule("GH005")
    if r5.enabled:
        for f in commit_message.check(title, commit, header_only=True, severity_override=r5.severity).findings:
            v.add("GH005", f.severity, f"PR title: {f.message} ({f.rule_id})")
    r8 = gh.rule("GH008")
    if r8.enabled:
        for prob in gh_policy.body_section_problems(body, r8.params):
            v.add("GH008", r8.severity, f"PR body: {prob}")
    r9 = gh.rule("GH009")
    if r9.enabled:
        for prob in commit_message.ai_attribution(body.split("\n"), commit.rule("CM016").params):
            v.add("GH009", r9.severity, f"PR body: {prob}")
    r = branch.rule("BR005")
    problem = branching.pr_target_problem(base, head, branch) if r.enabled else None
    if problem:
        v.add("BR005", r.severity, problem)
    return v


# GitHub rejects very long request lines; past this the body is left out and pasted by hand.
MAX_URL = 8000


def repo_slug(remote_url: str) -> tuple[str, str] | None:
    """(host, 'owner/repo') from an SSH or HTTPS GitHub remote URL."""
    m = (re.match(r"^(?:ssh://)?[\w.-]+@(?P<host>[\w.-]+)[:/](?P<path>[^:]+?)(?:\.git)?/?$", remote_url)
         or re.match(r"^https?://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+?)(?:\.git)?/?$", remote_url))
    if not m or m.group("path").count("/") != 1:
        return None
    return m.group("host").lower(), m.group("path")


def compare_url(remote_url: str, base: str, head: str, title: str, body: str) -> tuple[str, bool]:
    """The prefilled 'open a pull request' link and whether the body fit into it."""
    slug = repo_slug(remote_url)
    if slug is None:
        raise ValueError(f"cannot read owner/repo from remote URL {remote_url!r}")
    host, path = slug
    root = f"https://{host}/{path}/compare/{quote(base, safe='/')}...{quote(head, safe='/')}?"
    url = root + urlencode({"expand": "1", "title": title, "body": body}, quote_via=quote)
    if len(url) <= MAX_URL:
        return url, True
    return root + urlencode({"expand": "1", "title": title}, quote_via=quote), False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title")
    ap.add_argument("--body-file", help="markdown file with the PR body ('-' for stdin)")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--print-template", action="store_true")
    ap.add_argument("--compare-url", metavar="REPO",
                    help="when the checks pass, also print the prefilled compare link for REPO's origin remote")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if args.print_template:
        print((paths.PACKAGE_ROOT / "templates" / "repo" / ".github" / "pull_request_template.md")
              .read_text(encoding="utf-8"), end="")
        return 0
    if not args.title or not args.body_file:
        ap.error("--title and --body-file are required")
    body = sys.stdin.read() if args.body_file == "-" else open(args.body_file, encoding="utf-8").read()
    try:
        contracts = contracts_mod.load()
    except contracts_mod.ContractError as e:
        print(e, file=sys.stderr)
        return 2
    v = check(args.title, body, args.base, args.head, contracts)
    for f in v.findings:
        print(f"{f.severity.upper():5} {f.render()}")
    if v.decision in ("deny", "ask"):
        return 1
    if not v.findings:
        print("ok")
    if args.compare_url:
        if not args.base or not args.head:
            ap.error("--compare-url needs --base and --head")
        p = subprocess.run(["git", "-C", args.compare_url, "remote", "get-url", "origin"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            url, with_body = compare_url(p.stdout.strip(), args.base, args.head, args.title, body)
        except ValueError as e:
            print(f"compare-url: {e}", file=sys.stderr)
            return 2
        print(f"compare-url: {url}")
        if not with_body:
            print(f"compare-url: body too long for the link; paste it from {args.body_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
