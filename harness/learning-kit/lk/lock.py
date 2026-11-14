"""One writer at a time across processes: the web app, the MCP server and CLI calls from skills.

Atomic replace (store._atomic_write) stops torn files, but not lost updates: two processes that both
load srs.json, change different cards and save would drop one change. Every read-modify-write of
shared state runs inside `locked()`, which holds an OS file lock on <home>/.lk.lock.

- Reentrant within a thread, so a service call can call another one.
- Threads of one process queue on a process-wide RLock first, then on the file lock.
- Never hold it across slow work (LLM grading, network fetches): take it around the load-change-save.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

from . import paths
from .store import LkError

LOCK_NAME = ".lk.lock"
DEFAULT_TIMEOUT = 60.0

_thread_lock = threading.RLock()
_local = threading.local()


def _try_lock(fh) -> bool:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked(timeout: float = DEFAULT_TIMEOUT):
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    if not _thread_lock.acquire(timeout=timeout):
        raise LkError("the data home is busy (another request holds the lock); try again")
    try:
        home = paths.home()
        home.mkdir(parents=True, exist_ok=True)
        with open(home / LOCK_NAME, "a+b") as fh:
            deadline = time.monotonic() + timeout
            while not _try_lock(fh):
                if time.monotonic() > deadline:
                    raise LkError(f"timed out waiting for {home / LOCK_NAME}: another lk process is busy")
                time.sleep(0.05)
            _local.depth = 1
            try:
                yield
            finally:
                _local.depth = 0
                _unlock(fh)
    finally:
        _thread_lock.release()
