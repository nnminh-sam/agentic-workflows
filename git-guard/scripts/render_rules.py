#!/usr/bin/env python3
"""Render the contracts into the self-contained rules viewer (rules.html).

Output: <project>/.claude/git-guard/rules.html (plugin) or next to the code (vendored).

  python render_rules.py           # write rules.html
  python render_rules.py --open    # write and open in the browser
  python render_rules.py --check   # exit 1 if rules.html is missing or older than a contract
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitguard import __version__  # noqa: E402
from gitguard import paths  # noqa: E402
from gitguard import profile as profile_mod  # noqa: E402
from gitguard.contracts import CONTRACT_FILES, contract_paths, load_raw, validate_all  # noqa: E402

SHELL = paths.VIEWER_SHELL
OUTPUT = paths.RULES_HTML
PLACEHOLDER = "/*__GIT_GUARD_DATA__*/null"


def _profile_rows(raw: dict, problems: list[str], env=None, files=None) -> list[dict]:
    decls = raw.get("identity", {}).get("profile_variables", {}) or {}
    try:
        kw = {} if files is None else {"files": files}
        prof = profile_mod.load(decls, env, **kw)
    except profile_mod.ProfileError as e:
        problems.append(f"profile: {e}")
        prof = {}
    rows = []
    for name, decl in decls.items():
        v = prof.get(name)
        rows.append({"name": name, "type": decl.get("type"), "env": decl.get("env"),
                     "description": decl.get("description", ""), "example": decl.get("example"),
                     "value": v.value if v else None, "source": v.source if v else "unset"})
    return rows


def build_html(raw: dict | None = None, env=None, profile_files=None) -> str:
    raw = raw if raw is not None else load_raw()
    problems = validate_all(raw)
    data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "version": __version__,
        "order": list(CONTRACT_FILES),
        "files": CONTRACT_FILES,
        "contracts": raw,
        "profile": _profile_rows(raw, problems, env, profile_files),
        "problems": problems,
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    shell = SHELL.read_text(encoding="utf-8")
    if PLACEHOLDER not in shell:
        raise RuntimeError(f"{SHELL} has no data placeholder {PLACEHOLDER!r}")
    return shell.replace(PLACEHOLDER, blob)


def render(output=OUTPUT) -> str:
    html = build_html()
    if output == OUTPUT:
        paths.ensure_local_dir(output.parent)
    output.write_text(html, encoding="utf-8")
    return str(output)


def is_stale(output=OUTPUT) -> bool:
    if not output.exists():
        return True
    out_m = output.stat().st_mtime
    sources = list(contract_paths().values()) + [SHELL, profile_mod.PROJECT_PROFILE,
                                                                       profile_mod.USER_PROFILE]
    return any(p.exists() and p.stat().st_mtime > out_m for p in sources)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--open", action="store_true", help="open the viewer after rendering")
    ap.add_argument("--check", action="store_true", help="only report whether rules.html is up to date")
    args = ap.parse_args(argv)
    if args.check:
        stale = is_stale()
        print("stale" if stale else "up to date")
        return 1 if stale else 0
    path = render()
    print(path)
    if args.open:
        webbrowser.open(OUTPUT.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
