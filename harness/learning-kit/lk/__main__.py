"""`python -m lk ...` (run from the repo root) — same as `python lk.py ...`."""
import sys

from .cli import main

sys.exit(main())
