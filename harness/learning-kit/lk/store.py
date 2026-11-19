"""Small file-store helpers: atomic JSON, JSONL, ids, dates.

Everything the kit persists is plain JSON / JSONL / Markdown so it can be read in
an editor, diffed in git and repaired by hand.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable


class LkError(Exception):
    """User-facing error: printed without a traceback by the CLI."""


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _replace(tmp: str, path: Path) -> None:
    # os.replace is atomic on POSIX and Windows, but on Windows it fails while another process
    # (antivirus, search indexer, an editor or a sync client) briefly holds the target open. Retry.
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def _atomic_write(path: Path, writer) -> None:
    """Write via temp file + replace so a crash never leaves half a file. Always UTF-8 + LF."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            writer(fh)
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: Path, data: Any) -> None:
    def w(fh):
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    _atomic_write(path, w)


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, lambda fh: fh.write(text))


def append_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def append_jsonl(path: Path, record: dict) -> None:
    append_text(path, json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# "today" is injectable so tests (and `LK_TODAY`) can time-travel the scheduler.
def today() -> _dt.date:
    forced = os.environ.get("LK_TODAY")
    if forced:
        return _dt.date.fromisoformat(forced)
    return _dt.date.today()


def parse_date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s[:10])


_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Device names Windows refuses as file or folder names (with any extension).
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def slugify(text: str) -> str:
    """Lowercase ASCII kebab-case: safe as a folder name on every OS and on case-insensitive
    filesystems (Windows, macOS), and identical when typed in any case."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", folded.lower()).strip("-")[:60].strip("-")
    if not slug:
        raise LkError(f"cannot make a slug from {text!r}")
    if slug in _WIN_RESERVED:
        slug += "-topic"
    return slug


def short_token(n: int = 4) -> str:
    return secrets.token_hex(n // 2 if n > 1 else 1)


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def first(iterable: Iterable, default=None):
    for x in iterable:
        return x
    return default
