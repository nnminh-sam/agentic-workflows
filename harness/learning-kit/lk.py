#!/usr/bin/env python3
"""Entry point: python lk.py <command> ...  (see `python lk.py --help`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lk.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
