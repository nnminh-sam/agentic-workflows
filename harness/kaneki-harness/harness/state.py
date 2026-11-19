"""Ticket state: state.json (current view) + decisions.jsonl (append-only, hash-chained log). The filesystem is truth.

Integrity (both checked on every load):
- every log line carries `prev`, the sha256 of the line before it, so an edited or removed line breaks the chain;
- every state write appends a `state-write` event with the digest of what was written, so a `state.json` edited
  outside the driver no longer matches the last recorded digest.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import HarnessError

GENESIS = "genesis"
LOCK_STALE_SECONDS = 600


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def state_digest(state: dict[str, Any]) -> str:
    return sha256_text(json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def validate_state_shape(state: Any) -> list[str]:
    """Structural check of contracts/state.schema.json (the full schema is checked too when a validator runs)."""
    errs = []
    if not isinstance(state, dict) or state.get("schemaVersion") != 1:
        return ["state.json: schemaVersion must be 1"]
    for key, typ in (("graphId", str), ("profilePath", str), ("context", dict), ("nodes", dict), ("receipts", dict)):
        if not isinstance(state.get(key), typ):
            errs.append(f"state.json: '{key}' missing or not a {typ.__name__}")
    for node, rec in (state.get("nodes") or {}).items():
        if not isinstance(rec, dict) or rec.get("status") not in ("pending", "in_progress", "stale", "done") \
                or not isinstance(rec.get("generation"), int) or not isinstance(rec.get("units"), list):
            errs.append(f"state.json: node record '{node}' is malformed")
    return errs


class Ticket:
    def __init__(self, root: Path, ticket: str):
        self.dir = root / ticket
        self.state_path = self.dir / "state.json"
        self.log_path = self.dir / "decisions.jsonl"
        self.lock_path = self.dir / ".lock"

    def exists(self) -> bool:
        return self.state_path.exists()

    # --- log -----------------------------------------------------------------------------------------
    def _lines(self) -> list[str]:
        if not self.log_path.exists():
            return []
        return [l for l in self.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def verify_log(self) -> tuple[list[dict[str, Any]], list[str]]:
        events, problems, prev = [], [], GENESIS
        for i, line in enumerate(self._lines(), 1):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"decisions.jsonl:{i} is not JSON")
                break
            if "prev" in ev and ev["prev"] != prev:
                problems.append(f"decisions.jsonl:{i} breaks the hash chain (a line before it was edited or removed)")
            prev = sha256_text(line)
            events.append(ev)
        return events, problems

    def log(self, event: dict[str, Any]) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        lines = self._lines()
        prev = sha256_text(lines[-1]) if lines else GENESIS
        event = {"at": now(), **event, "prev": prev}
        from . import schema
        schema.validate(event, "decision-event.schema.json", f"decision event '{event.get('event')}'")
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    # --- state ---------------------------------------------------------------------------------------
    def load(self, verify: bool = True) -> dict[str, Any]:
        if not self.exists():
            raise HarnessError(f"ticket not initialised: {self.dir} (run init)")
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HarnessError(f"G7: state.json is not valid JSON: {exc}") from exc
        problems = validate_state_shape(state)
        if not problems:
            from . import schema
            problems += schema.errors(state, "state.schema.json")
        if verify:
            events, chain = self.verify_log()
            problems += chain
            writes = [e for e in events if e.get("event") == "state-write"]
            if writes and writes[-1].get("stateDigest") != state_digest(state):
                problems.append("state.json changed outside the driver (its digest differs from the last state-write "
                                "in decisions.jsonl)")
        if problems:
            raise HarnessError("G7: ticket integrity check failed: " + "; ".join(problems) +
                               ". If the change is intended, run `status --accept-current-state --reason '<why>'`.")
        return state

    def accept_current(self, reason: str) -> str:
        state = self.load(verify=False)
        digest = state_digest(state)
        self.log({"event": "state-write", "stateDigest": digest, "acceptedByUser": True, "reason": reason})
        return digest

    def save(self, state: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        for attempt in range(20):  # Windows: AV/indexer can hold the target for a moment
            try:
                tmp.replace(self.state_path)
                break
            except PermissionError:
                if attempt == 19:
                    raise HarnessError(f"G7: cannot replace {self.state_path} (file locked)")
                time.sleep(0.05 * (attempt + 1))
        self.log({"event": "state-write", "stateDigest": state_digest(state)})

    # --- lock (one writer per ticket) ----------------------------------------------------------------
    @contextmanager
    def lock(self, command: str) -> Iterator[None]:
        """mkdir is atomic on every OS and filesystem the harness supports. A lock older than
        LOCK_STALE_SECONDS is assumed abandoned (crashed session) and broken, with a log line."""
        self.dir.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.lock_path.mkdir()
                break
            except FileExistsError:
                owner = self.lock_path / "owner.json"
                try:
                    info = json.loads(owner.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    info = {}
                age = time.time() - (info.get("since") or self.lock_path.stat().st_mtime)
                if age < LOCK_STALE_SECONDS:
                    raise HarnessError(f"G7: ticket is locked by another session (`{info.get('command', '?')}`, pid "
                                       f"{info.get('pid', '?')}, {int(age)}s ago); wait for it to finish")
                shutil.rmtree(self.lock_path, ignore_errors=True)
                if self.exists():
                    self.log({"event": "lock-broken", "staleSeconds": int(age), "owner": info})
        else:
            raise HarnessError("G7: could not take the ticket lock")
        (self.lock_path / "owner.json").write_text(json.dumps({"pid": os.getpid(), "command": command, "since": time.time()}),
                                                   encoding="utf-8")
        try:
            yield
        finally:
            shutil.rmtree(self.lock_path, ignore_errors=True)


def new_node_record() -> dict[str, Any]:
    return {"status": "pending", "generation": 1, "attempts": 0, "units": [], "closedAt": None, "verdict": None}


def settled(state: dict[str, Any], node: str) -> bool:
    return state["nodes"].get(node, {}).get("status") == "done"


def next_pending(state: dict[str, Any], order: list[str]) -> str | None:
    for node in order:
        if not settled(state, node):
            return node
    return None


def missing_prerequisites(graph: dict[str, Any], order: list[str], state: dict[str, Any], node: str) -> list[str]:
    """Readiness = every token the node requires is produced by a settled node of the trace.
    A token no node in the trace produces is inert (e.g. a family-only token)."""
    producers: dict[str, list[str]] = {}
    for n in order:
        for tok in graph["nodes"][n].get("produces", []):
            producers.setdefault(tok, []).append(n)
    missing = []
    for tok in graph["nodes"][node].get("requires", []):
        owners = [n for n in producers.get(tok, []) if n != node]
        if owners and not any(settled(state, n) for n in owners):
            missing.append(f"{tok} (from {', '.join(owners)})")
    return missing


# --- subject digests and receipts --------------------------------------------------------------------

def _git(path: str, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True,
                             encoding="utf-8", errors="replace")  # git emits UTF-8 on every OS
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HarnessError(f"G7: git failed in {path}: {' '.join(args)}: {getattr(exc, 'stderr', exc)}") from exc
    return out.stdout


def _excluded(rel: str, prefixes: list[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def worktree_snapshot(path: str, exclude: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Port of wms-flow `gitfacts.worktree_snapshot`: HEAD, the tracked modification (`git diff HEAD`) and the
    CONTENT of every untracked file (ignored paths excluded). Editing a new file changes the snapshot.
    `exclude`: absolute paths of harness folders (ticket root, `.sdlc/`) that live inside the repo; the driver
    writes there continuously, so they are never part of what ships."""
    head = _git(path, "rev-parse", "HEAD").strip()
    root_abs = Path(path).resolve()
    prefixes = []
    for e in exclude:
        try:
            prefixes.append(Path(e).resolve().relative_to(root_abs).as_posix())
        except ValueError:
            pass  # outside this repo
    porcelain = sorted(l for l in _git(path, "status", "--porcelain", "--untracked-files=all").splitlines()
                       if l and not _excluded(l[3:].strip().strip('"'), prefixes))
    tracked_diff = sha256_text(_git(path, "diff", "HEAD", "--binary"))
    untracked: dict[str, str] = {}
    root = Path(path)
    for line in porcelain:
        if not line.startswith("??"):
            continue
        rel = line[3:].strip().strip('"')
        f = root / rel
        if f.is_symlink():
            untracked[rel] = "symlink:" + os.readlink(f)
        elif f.is_file():
            untracked[rel] = sha256_file(f)
    return {"headOid": head, "porcelain": porcelain, "trackedDiffDigest": tracked_diff, "untracked": untracked}


def repo_subject(path: str, exclude: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    snap = worktree_snapshot(path, exclude)
    return {"head": snap["headOid"], "subject": sha256_text(json.dumps(snap, sort_keys=True))}


def issue_receipts(state: dict[str, Any], ttl_hours: int, exclude: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).replace(microsecond=0).isoformat()
    receipts = {}
    for repo in state["context"]["repositories"]:
        subj = repo_subject(repo.get("worktree") or repo["path"], exclude)
        receipts[repo["id"]] = {**subj, "issuedAt": now(), "expiresAt": expires}
    state["receipts"] = receipts
    return receipts


def check_receipts(state: dict[str, Any], min_hours_left: int, exclude: list[str] | tuple[str, ...] = ()) -> list[str]:
    problems = []
    receipts = state.get("receipts") or {}
    limit = datetime.now(timezone.utc) + timedelta(hours=min_hours_left)
    for repo in state["context"]["repositories"]:
        rec = receipts.get(repo["id"])
        if not rec:
            problems.append(f"{repo['id']}: no receipt (rerun final-review)")
            continue
        if datetime.fromisoformat(rec["expiresAt"]) < limit:
            problems.append(f"{repo['id']}: receipt expires {rec['expiresAt']} (< {min_hours_left}h left) - re-issue from final-review, do not invalidate")
            continue
        current = repo_subject(repo.get("worktree") or repo["path"], exclude)["subject"]
        if current != rec["subject"]:
            problems.append(f"{repo['id']}: checkout changed since the receipt - invalidate the node that owns the change")
    return problems
