#!/usr/bin/env python3
"""Check a commit message (or PR title) against contracts/commit-message.json.

  python check_message.py "feat(api): add retry"
  python check_message.py --file .git/COMMIT_EDITMSG     # also works as a git commit-msg hook
  echo "fix: x" | python check_message.py -
  python check_message.py --title "feat: x"              # header rules only (PR titles)

Exit code: 0 = ok or warnings only, 1 = a deny/ask rule fired, 2 = usage / contract error.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import commit_message, contracts as contracts_mod  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("message", nargs="?", help="message text, or '-' for stdin")
    ap.add_argument("--file", "-F", help="read the message from a file (git commit-msg hook style)")
    ap.add_argument("--title", action="store_true", help="check header rules only (PR titles)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            # git commit-msg hooks receive the raw file: drop comment lines like git's default cleanup
            text = "\n".join(ln for ln in fh.read().splitlines() if not ln.startswith("#"))
    elif args.message == "-" or args.message is None:
        if args.message is None and sys.stdin.isatty():
            ap.print_usage()
            return 2
        text = sys.stdin.read()
    else:
        text = args.message
    try:
        contract = contracts_mod.load().commit
    except contracts_mod.ContractError as e:
        print(e, file=sys.stderr)
        return 2
    verdict = commit_message.check(text, contract, header_only=args.title)
    if not verdict.findings:
        print("ok")
        return 0
    for f in verdict.findings:
        print(f"{f.severity.upper():5} {f.render()}")
    return 1 if verdict.decision in ("deny", "ask") else 0


if __name__ == "__main__":
    sys.exit(main())
