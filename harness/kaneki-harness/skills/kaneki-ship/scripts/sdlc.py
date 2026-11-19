#!/usr/bin/env python3
"""Thin entry point: locate the harness home and run the CLI.

Home = $SDLC_HOME, else the path in `.harness-home` next to this file (written by `sdlc install`), else the first
parent folder that holds graph/lifecycle.json and harness/ (running from the harness repository itself).
"""

import os
import sys
from pathlib import Path


def _home() -> Path:
    env = os.environ.get("SDLC_HOME")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    marker = here.parent / ".harness-home"
    if marker.is_file():
        p = Path(marker.read_text(encoding="utf-8").strip())
        if (p / "harness").is_dir():
            return p
    for parent in here.parents:
        if (parent / "graph" / "lifecycle.json").is_file() and (parent / "harness").is_dir():
            return parent
    sys.stdout.write('{"error": "G7: harness home not found; set SDLC_HOME or reinstall with sdlc install"}\n')
    sys.exit(2)


sys.path.insert(0, str(_home()))
from harness.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
