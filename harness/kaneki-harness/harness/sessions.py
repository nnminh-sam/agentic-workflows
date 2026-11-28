"""Read-only session-log reader: the agents' own transcripts, matched to a ticket's nodes (`sdlc transcripts`).

One reader per agent turns its log into the same normalized session:

    {agent, sessionId, source, startedAt, updatedAt, running, title, cwd, parent,
     steps: [{at, role: user|agent|tool, text, tool: {name, input, output} | None}], stepCount, truncated}

- Claude Code: <root>/projects/<cwd-slug>/<session>.jsonl and <session>/subagents/agent-<id>.jsonl (+ .meta.json);
  root = $CLAUDE_CONFIG_DIR or ~/.claude.
- Codex: <root>/sessions/YYYY/MM/DD/rollout-*.jsonl; root = $CODEX_HOME or ~/.codex.
- Antigravity CLI: <root>/brain/<conversation-id>/.system_generated/logs/transcript_full.jsonl (or transcript.jsonl),
  with title / project / parent from <root>/conversation_summaries.db; root = $ANTIGRAVITY_CLI_ROOT or
  ~/.gemini/antigravity-cli. Unpublished format: best-effort, a file that does not parse yields no session.
- sdlc dispatch logs: <ticketDir>/logs/<dispatchId>.dispatch.log (command line, exit code, stdout, stderr).

Matching to a ticket node:
- unit (exact): the session's first user message carries `Read the packet at <path> (sha256 <digest>)` and <path> is
  <ticketDir>/packets/<dispatchId>.md; the dispatchId names node, generation, unit and round. A dispatch log is
  exact by its file name.
- controller (heuristic): the session ran `sdlc packet|record|gate|plan|dispatch --ticket T --node N`.

Nothing here writes a file. Every reader swallows its own parse errors: a bad log is skipped, never fatal.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

RUNNING_SECONDS = 120     # a log written to in the last two minutes belongs to a session still running
MAX_STEPS = 200           # steps returned per session (the first step + the most recent ones)
TEXT_LIMIT = 4000         # characters kept per text / tool input string / tool output
HEAD_USER_STEPS = 3       # how many leading user messages may carry the packet line
SLACK_SECONDS = 86400     # logs last modified this long before the ticket started cannot be about it

PACKET_RE = re.compile(r"Read the packet at\s+[`'\"]?(?P<path>[^`'\"\n]+?)[`'\"]?\s+\(sha256\s+`?(?P<digest>[0-9a-fA-F]{64})")
# Every launcher name `sdlc install` writes: sdlc, sdlc-<host> (sdlc-claude, sdlc-codex ...), each with or without a
# script extension, bare or as a (quoted) path; and `python -m harness.cli` / harness/cli.py.
# Only commands that change the ticket attribute a session to it: read-only ones (check, status, cost, transcripts,
# digest) are run by anyone looking at a ticket, including sessions long after it shipped.
SDLC_RE = re.compile(r"(?:^|[\s/\\\"'`(&;|])(?:sdlc(?:-[A-Za-z0-9_]+)?(?:\.(?:cmd|ps1|bat|sh|py|exe))?"
                     r"|harness[./\\]cli(?:\.py)?)[\"'`]?\s+"
                     r"(?P<cmd>packet|record|gate|plan|dispatch|invalidate)\b(?P<rest>[^\n;&|]*)")
OPT_RE = re.compile(r"--(?P<key>ticket|node|unit)(?:=|\s+)[\"'`]?(?P<val>[^\s\"'`]+)")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def call_opts(rest: str) -> dict[str, str]:
    """--ticket / --node / --unit of one sdlc call. A value that is not a literal name (a shell variable such as `$n`,
    `${node}`, `%N%`, `$env:N`, a placeholder `<node>`) is dropped: the real value is unknown."""
    return {o.group("key"): o.group("val") for o in OPT_RE.finditer(rest) if NAME_RE.fullmatch(o.group("val"))}
DISPATCH_RE = re.compile(r"^(?P<node>.+)\.g(?P<generation>\d+)\.(?P<unit>[^.]+)\.r(?P<round>\d+)$")


# --- small helpers -----------------------------------------------------------------------------------

def _clip(text: Any, limit: int = TEXT_LIMIT) -> str:
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def _clip_obj(obj: Any, limit: int = TEXT_LIMIT) -> Any:
    if isinstance(obj, str):
        return _clip(obj, limit)
    if isinstance(obj, dict):
        return {str(k): _clip_obj(v, limit) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clip_obj(v, limit) for v in obj]
    return obj


# Tool-input fields that carry file text or prompts, not a command: a file edit that merely mentions
# `sdlc gate --ticket T` (a test, a doc) is not a call.
TEXT_KEYS = frozenset({"content", "new_string", "old_string", "file_text", "new_source", "edits", "prompt",
                       "description", "patch"})


def _strings(obj: Any) -> Iterator[str]:
    """Every command-like string inside a tool input (commands hide in `command`, `cmd`, `CommandLine`, argv lists ...);
    file text and prompts (TEXT_KEYS) are skipped."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in TEXT_KEYS:
                yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        if obj and all(isinstance(v, str) for v in obj):
            yield " ".join(obj)  # an argv list: ["sdlc", "gate", "--ticket", "T"]
        for v in obj:
            yield from _strings(v)


def _iso(value: Any) -> str | None:
    """ISO-8601 UTC from an ISO string, epoch seconds or epoch milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        secs = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(secs, timezone.utc).replace(microsecond=0).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "seconds" in value:  # protobuf Timestamp as JSON
        return _iso(value.get("seconds"))
    return None


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _norm(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _under(path: str | None, roots: Iterable[str]) -> bool:
    if not path:
        return False
    p = _norm(path)
    for r in roots:
        if p == r or p.startswith(r.rstrip("\\/") + os.sep):
            return True
    return False


def _jsonl(path: Path, wanted: Callable[[str], bool] | None = None) -> Iterator[dict[str, Any]]:
    """Objects of a JSONL file, streamed; bad lines are skipped. `wanted` is a cheap pre-filter on the raw line."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or (wanted is not None and not wanted(line)):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


class _Builder:
    """Collects steps for one session: keeps the first step and the last `max_steps - 1`, links tool outputs."""

    def __init__(self, agent: str, source: Path, max_steps: int, running_seconds: int, now: float):
        self.s: dict[str, Any] = {"agent": agent, "sessionId": None, "source": str(source), "startedAt": None,
                                  "updatedAt": None, "running": False, "title": None, "cwd": None, "parent": None}
        self.first: dict[str, Any] | None = None
        self.tail: deque[dict[str, Any]] = deque(maxlen=max(max_steps - 1, 0))
        self.count = 0
        self.tools: dict[str, dict[str, Any]] = {}
        self.head_users: list[str] = []
        self.sdlc: list[dict[str, Any]] = []
        try:
            mtime = source.stat().st_mtime
            self.s["updatedAt"] = _iso(mtime)
            self.s["running"] = now - mtime <= running_seconds
        except OSError:
            pass

    def stamp(self, at: Any) -> str | None:
        at = _iso(at)
        if at and not self.s["startedAt"]:
            self.s["startedAt"] = at
        return at

    def add(self, role: str, text: str = "", at: Any = None, tool: dict[str, Any] | None = None,
            call_id: str | None = None) -> dict[str, Any]:
        step = {"at": self.stamp(at), "role": role, "text": _clip(text), "tool": None}
        if tool is not None:
            step["tool"] = {"name": tool.get("name"), "input": _clip_obj(tool.get("input")),
                            "output": None if tool.get("output") is None else _clip(tool.get("output"))}
            self._scan_sdlc(tool.get("input"), step["at"])
            if call_id:
                self.tools[call_id] = step
        if role == "user" and len(self.head_users) < HEAD_USER_STEPS:
            self.head_users.append(text)
        self.count += 1
        if self.first is None:
            self.first = step
        else:
            self.tail.append(step)
        return step

    def output(self, call_id: str | None, output: Any, at: Any = None) -> None:
        self.stamp(at)
        step = self.tools.get(call_id or "")
        if step is not None and step["tool"] is not None:
            step["tool"]["output"] = _clip(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))

    def _scan_sdlc(self, tool_input: Any, at: str | None) -> None:
        for text in _strings(tool_input):
            if "sdlc" not in text and "cli" not in text:
                continue
            for m in SDLC_RE.finditer(text):
                opts = call_opts(m.group("rest"))
                if opts.get("ticket"):
                    self.sdlc.append({"command": m.group("cmd"), "ticket": opts["ticket"], "node": opts.get("node"),
                                      "unit": opts.get("unit"), "at": at})

    def done(self) -> dict[str, Any] | None:
        if self.count == 0:
            return None
        steps = ([self.first] if self.first else []) + list(self.tail)
        self.s.update(steps=steps, stepCount=self.count, truncated=self.count - len(steps),
                      _headUsers=self.head_users, _sdlc=self.sdlc)
        return self.s


# --- Claude Code ---------------------------------------------------------------------------------------

def claude_root() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def claude_slug(cwd: str | os.PathLike[str]) -> str:
    """Claude Code's project folder name: every character that is not a letter or digit becomes '-'
    (so ':' '\\' '/' '.' '_' and spaces all do)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _claude_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(str(b.get("text") or ""))
                elif b.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def _claude_wanted(line: str) -> bool:
    return any(k in line for k in ('"user"', '"assistant"', '"ai-title"', '"summary"', '"custom-title"'))


def read_claude(path: Path, *, max_steps: int = MAX_STEPS, running_seconds: int = RUNNING_SECONDS,
                now: float | None = None) -> dict[str, Any] | None:
    """One Claude Code session (or subagent) log. Several assistant rows may share one message.id (one content
    block each, or a repeated cumulative content list); each block is kept once."""
    try:
        b = _Builder("claude", path, max_steps, running_seconds, time.time() if now is None else now)
        seen: set[tuple[str, str]] = set()
        for row in _jsonl(path, _claude_wanted):
            t = row.get("type")
            if t == "ai-title" and row.get("aiTitle"):
                b.s["title"] = str(row["aiTitle"])
            elif t == "custom-title" and row.get("customTitle"):
                b.s["title"] = str(row["customTitle"])
            elif t == "summary" and row.get("summary") and not b.s["title"]:
                b.s["title"] = str(row["summary"])
            if t not in ("user", "assistant"):
                continue
            b.s["sessionId"] = b.s["sessionId"] or row.get("sessionId")
            b.s["cwd"] = b.s["cwd"] or row.get("cwd")
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            content = msg.get("content")
            at = row.get("timestamp")
            if t == "user":
                if row.get("isMeta"):
                    b.stamp(at)
                    continue
                if isinstance(content, list):
                    text_parts = []
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_result":
                            out = blk.get("content")
                            b.output(blk.get("tool_use_id"), _claude_text(out) if not isinstance(out, str) else out, at)
                        else:
                            text_parts.append(blk)
                    text = _claude_text(text_parts)
                    if text.strip():
                        b.add("user", text, at)
                elif isinstance(content, str) and content.strip():
                    b.add("user", content, at)
                continue
            mid = str(msg.get("id") or row.get("uuid") or "")
            for i, blk in enumerate(content if isinstance(content, list) else [{"type": "text", "text": content}]):
                if not isinstance(blk, dict):
                    continue
                kind = blk.get("type")
                if kind == "text" and str(blk.get("text") or "").strip():
                    key = (mid, "t:" + str(blk.get("text")))
                    if key not in seen:
                        seen.add(key)
                        b.add("agent", str(blk["text"]), at)
                elif kind == "tool_use":
                    key = (mid, "u:" + str(blk.get("id") or i))
                    if key not in seen:
                        seen.add(key)
                        b.add("tool", "", at, {"name": blk.get("name"), "input": blk.get("input")}, blk.get("id"))
        sess = b.done()
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if sess is None:
        return None
    sess["sessionId"] = sess["sessionId"] or path.stem
    return sess


def read_claude_subagent(path: Path, **kw: Any) -> dict[str, Any] | None:
    """A subagent log; its parent is the session that owns the `subagents/` folder."""
    sess = read_claude(path, **kw)
    if sess is None:
        return None
    parent_id = sess.get("sessionId") or path.parent.parent.name
    sess["parent"] = {"agent": "claude", "sessionId": parent_id}
    sess["sessionId"] = path.stem[len("agent-"):] if path.stem.startswith("agent-") else path.stem
    meta = path.with_name(path.stem + ".meta.json")
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
        if isinstance(info, dict):
            sess["title"] = sess["title"] or info.get("description") or info.get("agentType")
            if info.get("toolUseId"):
                sess["parent"]["toolUseId"] = info["toolUseId"]
            if info.get("agentType"):
                sess["agentType"] = info["agentType"]
    except (OSError, ValueError):
        pass
    return sess


def claude_logs(dirs: Iterable[str], since: float | None) -> Iterator[tuple[Path, bool]]:
    """(log, is_subagent) for every Claude Code project folder of the given working directories."""
    projects = claude_root() / "projects"
    seen: set[str] = set()
    for d in dirs:
        folder = projects / claude_slug(d)
        if str(folder) in seen or not folder.is_dir():
            continue
        seen.add(str(folder))
        try:
            for f in folder.glob("*.jsonl"):
                if _fresh(f, since):
                    yield f, False
            for f in folder.glob("*/subagents/*.jsonl"):
                if _fresh(f, since):
                    yield f, True
        except OSError:
            continue


def _fresh(path: Path, since: float | None) -> bool:
    try:
        return since is None or path.stat().st_mtime >= since
    except OSError:
        return False


# --- Codex ---------------------------------------------------------------------------------------------

def codex_root() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


INJECTED = ("<environment_context>", "<user_instructions>", "# AGENTS.md instructions", "<permissions instructions>")


def _codex_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") for c in content
                         if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"))
    return ""


def _codex_args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _codex_output(raw: Any) -> Any:
    """function_call_output.output is a string, a JSON string {"output": ..., "metadata": ...}, or content items."""
    if isinstance(raw, dict):
        return raw.get("content") if "content" in raw else raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and "output" in obj:
                return obj["output"]
        except ValueError:
            pass
        return raw
    if isinstance(raw, list):
        return _codex_text(raw) or raw
    return raw


def codex_meta(path: Path) -> dict[str, Any]:
    """id / cwd / timestamp / parent from the first lines of a rollout file (cheap: stops early)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(5), fh):
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload") if obj.get("type") == "session_meta" else None
                if payload is None and "id" in obj and "type" not in obj:  # legacy first line: bare meta object
                    payload = obj
                if isinstance(payload, dict):
                    if isinstance(payload.get("meta"), dict):  # SessionMetaLine may nest the meta
                        payload = {**payload["meta"], **{k: v for k, v in payload.items() if k != "meta"}}
                    return payload
    except OSError:
        pass
    return {}


def _codex_parent(meta: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("forked_from_id", "parent_id", "parent_thread_id", "parentId"):
        if isinstance(meta.get(key), str):
            return {"agent": "codex", "sessionId": meta[key]}
    src = meta.get("source")
    if isinstance(src, dict):
        found = _find_key(src, ("parent_thread_id", "parent_id", "parentThreadId"))
        if found:
            return {"agent": "codex", "sessionId": found}
    return None


def _find_key(obj: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            hit = _find_key(v, keys)
            if hit:
                return hit
    return None


def read_codex(path: Path, *, max_steps: int = MAX_STEPS, running_seconds: int = RUNNING_SECONDS,
               now: float | None = None) -> dict[str, Any] | None:
    """One Codex rollout. Envelope lines {timestamp, type, payload}; type session_meta | response_item | event_msg |
    turn_context | compacted. Messages come from response_item (event_msg user/agent messages repeat them and are
    used only when a file has no response_item messages). The legacy format (bare items, no envelope) also reads."""
    try:
        b = _Builder("codex", path, max_steps, running_seconds, time.time() if now is None else now)
        meta = codex_meta(path)
        b.s["sessionId"] = meta.get("id")
        b.s["cwd"] = meta.get("cwd")
        b.s["parent"] = _codex_parent(meta)
        b.stamp(meta.get("timestamp"))
        events: list[tuple[str, str, Any]] = []
        has_messages = False
        for row in _jsonl(path):
            at = row.get("timestamp")
            kind = row.get("type")
            if kind in ("session_meta", "turn_context", "compacted"):
                if kind == "turn_context" and isinstance(row.get("payload"), dict):
                    b.s["cwd"] = b.s["cwd"] or row["payload"].get("cwd")
                continue
            if kind == "event_msg":
                p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if p.get("type") in ("user_message", "agent_message") and p.get("message"):
                    events.append(("user" if p["type"] == "user_message" else "agent", str(p["message"]), at))
                continue
            item = row.get("payload") if kind == "response_item" else row
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "message":
                text = _codex_text(item.get("content"))
                role = item.get("role")
                if role not in ("user", "assistant") or not text.strip():
                    continue
                if role == "user" and text.lstrip().startswith(INJECTED):
                    b.stamp(at)
                    continue
                has_messages = True
                b.add("user" if role == "user" else "agent", text, at)
            elif itype in ("function_call", "custom_tool_call"):
                args = _codex_args(item.get("arguments") if itype == "function_call" else item.get("input"))
                b.add("tool", "", at, {"name": item.get("name"), "input": args}, item.get("call_id") or item.get("id"))
            elif itype == "local_shell_call":
                action = item.get("action") if isinstance(item.get("action"), dict) else {}
                b.add("tool", "", at, {"name": "local_shell", "input": action}, item.get("call_id") or item.get("id"))
            elif itype in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
                b.output(item.get("call_id"), _codex_output(item.get("output")), at)
            elif itype == "web_search_call":
                b.add("tool", "", at, {"name": "web_search", "input": item.get("action")})
        if not has_messages:
            for role, text, at in events:
                b.add(role, text, at)
        sess = b.done()
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if sess is None:
        return None
    sess["sessionId"] = sess["sessionId"] or path.stem
    return sess


def codex_logs(dirs: list[str], since: float | None) -> Iterator[Path]:
    """Rollouts whose session cwd is one of the workspace folders (or below one)."""
    files: list[Path] = []
    for root in (codex_root() / "sessions", codex_root() / "archived_sessions"):
        if not root.is_dir():
            continue
        try:  # date folders (current) and flat (very old builds); `.jsonl.zst` (compressed) is not read
            files += list(root.glob("*/*/*/rollout-*.jsonl")) + list(root.glob("rollout-*.jsonl"))
        except OSError:
            continue
    for f in files:
        if _fresh(f, since) and _under(codex_meta(f).get("cwd"), dirs):
            yield f


# --- Antigravity CLI -------------------------------------------------------------------------------------

def antigravity_root() -> Path:
    env = os.environ.get("ANTIGRAVITY_CLI_ROOT")
    return Path(env) if env else Path.home() / ".gemini" / "antigravity-cli"


def _file_uri_path(uri: str) -> str:
    if not uri.startswith("file://"):
        return uri
    from urllib.parse import unquote, urlparse
    p = unquote(urlparse(uri).path)
    return os.path.normpath(p[1:] if re.match(r"^/[A-Za-z]:", p) else p)


def antigravity_summaries(root: Path) -> dict[str, dict[str, Any]]:
    """conversation id -> {title, projects, parent, agentName, createdAt} from conversation_summaries.db, opened
    read-only. Known table: conversation_summaries(conversation_id, title, workspace_uris (JSON list of file://
    URIs), parent_conversation_id ('' for a root conversation), agent_name, status, ...). The schema is unpublished,
    so columns are found by name in every table; any failure gives what was read so far (or nothing)."""
    db = root / "conversation_summaries.db"
    if not db.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    except (sqlite3.Error, ValueError, OSError):
        return {}
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            lower = {c.lower(): c for c in cols}

            def pick(*names: str) -> str | None:
                return next((lower[n] for n in names if n in lower), None)

            cid = pick("conversation_id", "conversationid", "cascade_id")
            if not cid:
                continue
            fields = {"title": pick("title", "summary"),
                      "projects": pick("workspace_uris", "workspace_uri", "workspace_folders", "project_dir",
                                       "project_path", "workspace_path", "workspace", "cwd"),
                      "parent": pick("parent_conversation_id", "parentconversationid", "parent_id"),
                      "agentName": pick("agent_name"),
                      "createdAt": pick("created_at", "createdat", "create_time", "start_time")}
            sel = [cid] + [c for c in fields.values() if c]
            query = "SELECT " + ", ".join('"' + c + '"' for c in sel) + f' FROM "{table}"'
            for row in con.execute(query):
                rec = dict(zip(sel, row))
                entry = out.setdefault(str(rec[cid]), {})
                for name, col in fields.items():
                    val = rec.get(col) if col else None
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", "replace")
                    if val in (None, "") or name in entry:
                        continue
                    if name == "projects":
                        try:
                            parsed = json.loads(val) if isinstance(val, str) and val.lstrip().startswith("[") else [val]
                        except ValueError:
                            parsed = [val]
                        val = [_file_uri_path(str(u)) for u in parsed if u]
                    entry[name] = val
    except sqlite3.Error:
        return out
    finally:
        con.close()
    return out


AG_SKIP_TYPES = ("SYSTEM_MESSAGE", "CONVERSATION_HISTORY", "CHECKPOINT")


def _ag_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for k in ("text", "content", "message", "markdown"):
            if isinstance(content.get(k), str):
                return content[k]
        return ""
    if isinstance(content, list):
        return "\n".join(t for t in (_ag_text(c) for c in content) if t)
    return ""


def _ag_args(raw: Any) -> Any:
    """Tool args: PascalCase keys whose values are themselves JSON-encoded strings."""
    raw = _codex_args(raw)
    if isinstance(raw, dict):
        return {k: (_codex_args(v) if isinstance(v, str) and v[:1] in ('"', "{", "[") else v) for k, v in raw.items()}
    return raw


def read_antigravity(path: Path, *, conversation_id: str | None = None, summary: dict[str, Any] | None = None,
                     max_steps: int = MAX_STEPS, running_seconds: int = RUNNING_SECONDS,
                     now: float | None = None) -> dict[str, Any] | None:
    """One Antigravity transcript: a step per line with step_index, source (USER_EXPLICIT | MODEL | SYSTEM), type
    (USER_INPUT | PLANNER_RESPONSE | GENERIC | RUN_COMMAND ...), status, created_at, content, tool_calls [{name, args}]
    and error. Tool calls carry no id and no result: the non-planner MODEL steps after a planner step are their
    results, in order. Best-effort; None when nothing parses."""
    try:
        b = _Builder("antigravity", path, max_steps, running_seconds, time.time() if now is None else now)
        rows = list(_jsonl(path))
        rows.sort(key=lambda r: r.get("step_index") if isinstance(r.get("step_index"), int) else 0)
        pending: deque[dict[str, Any]] = deque()
        for row in rows:
            at = row.get("created_at") or row.get("createdAt") or row.get("timestamp")
            source = str(row.get("source") or "").upper()
            kind = str(row.get("type") or "").upper()
            text = _ag_text(row.get("content"))
            if "USER" in source or kind == "USER_INPUT":
                text = re.sub(r"</?USER_REQUEST>", "", text).strip()
                if text:
                    b.add("user", text, at)
                else:
                    b.stamp(at)
                continue
            if source == "SYSTEM" or kind in AG_SKIP_TYPES:
                b.stamp(at)
                continue
            calls = row.get("tool_calls") or row.get("toolCalls") or []
            if kind == "PLANNER_RESPONSE" or calls:
                if text.strip():
                    b.add("agent", text, at)
                for call in calls if isinstance(calls, list) else []:
                    if isinstance(call, dict):
                        step = b.add("tool", "", at, {"name": call.get("name"), "input": _ag_args(call.get("args"))})
                        pending.append(step)
                if not text.strip() and not calls:
                    b.stamp(at)
                continue
            result = text or (str(row["error"]) if row.get("error") else "")
            if pending:
                step = pending.popleft()
                b.stamp(at)
                if step["tool"] is not None:
                    step["tool"]["output"] = _clip(result)
            elif result.strip():
                b.add("tool", "", at, {"name": kind.lower() or None, "input": None, "output": result})
        sess = b.done()
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if sess is None:
        return None
    info = summary or {}
    sess["sessionId"] = conversation_id or path.parents[2].name
    sess["title"] = info.get("title")
    projects = info.get("projects") or []
    sess["cwd"] = projects[0] if projects else None
    if info.get("parent"):
        sess["parent"] = {"agent": "antigravity", "sessionId": str(info["parent"])}
    if info.get("agentName"):
        sess["agentType"] = info["agentName"]
    if info.get("createdAt") and not sess["startedAt"]:
        sess["startedAt"] = _iso(info["createdAt"])
    return sess


def antigravity_logs(dirs: list[str], since: float | None) -> Iterator[tuple[Path, str, dict[str, Any]]]:
    """(transcript, conversation id, summary) for conversations whose workspace is a workspace folder (or a
    subagent of one); without a summaries row every recent transcript is read and matching alone decides."""
    root = antigravity_root()
    brain = root / "brain"
    if not brain.is_dir():
        return
    summaries = antigravity_summaries(root)
    try:
        convs = [d for d in brain.iterdir() if d.is_dir()]
    except OSError:
        return

    def relevant(cid: str, depth: int = 0) -> bool:
        info = summaries.get(cid)
        if not info or depth > 8:
            return True  # unknown: let matching decide
        if info.get("projects"):
            return any(_under(p, dirs) for p in info["projects"])
        return relevant(str(info["parent"]), depth + 1) if info.get("parent") else True

    for conv in convs:
        if not relevant(conv.name):
            continue
        logs = conv / ".system_generated" / "logs"
        for name in ("transcript_full.jsonl", "transcript.jsonl"):
            f = logs / name
            if f.is_file():
                if _fresh(f, since):
                    yield f, conv.name, summaries.get(conv.name, {})
                break


# --- sdlc dispatch logs --------------------------------------------------------------------------------

def read_dispatch_log(path: Path, *, running_seconds: int = RUNNING_SECONDS, now: float | None = None) -> dict[str, Any] | None:
    """`$ <argv>` / `exit=<code>` (or `TIMEOUT after <n>s`) / `--- STDOUT ---` / `--- STDERR ---`, as written by
    harness/dispatch.py."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.split("\n")
    if not lines or not lines[0].startswith("$ "):
        return None
    command = lines[0][2:]
    status = lines[1].strip() if len(lines) > 1 else ""
    exit_code: int | None = None
    m = re.match(r"exit=(-?\d+)", status)
    if m:
        exit_code = int(m.group(1))
    o, e = text.find("\n--- STDOUT ---\n"), text.find("\n\n--- STDERR ---\n")
    stdout = text[o + len("\n--- STDOUT ---\n"):e] if o >= 0 and e > o else ""
    stderr = text[e + len("\n\n--- STDERR ---\n"):].rstrip("\n") if e >= 0 else ""
    b = _Builder("dispatch", path, 3, running_seconds, time.time() if now is None else now)
    at = b.s["updatedAt"]
    b.add("tool", "", at, {"name": "dispatch", "input": {"command": command}, "output": status})
    if stdout.strip():
        b.add("agent", stdout.strip("\n"), at)
    if stderr.strip():
        b.add("tool", "", at, {"name": "stderr", "input": None, "output": stderr})
    b.s["running"] = False  # the log is written once, after the runner exits
    sess = b.done()
    if sess is None:
        return None
    dispatch_id = path.name[:-len(".dispatch.log")] if path.name.endswith(".dispatch.log") else path.stem
    sess.update(sessionId=dispatch_id, title=f"sdlc dispatch {dispatch_id}", exitCode=exit_code,
                timedOut=status.startswith("TIMEOUT"), runner=os.path.basename(command.split(" ", 1)[0]) or None)
    return sess


# --- matching ------------------------------------------------------------------------------------------

def split_dispatch(dispatch_id: str, nodes: Iterable[str] = ()) -> dict[str, Any] | None:
    """node / generation / unit / round of `<node>.g<gen>.<unit>.r<round>`; known node names win over the regex
    (a node name could contain `.g1.`)."""
    for node in sorted(nodes, key=len, reverse=True):
        rest = dispatch_id[len(node):] if dispatch_id.startswith(node + ".g") else None
        m = re.match(r"^\.g(\d+)\.([^.]+)\.r(\d+)$", rest) if rest else None
        if m:
            return {"node": node, "generation": int(m.group(1)), "unit": m.group(2), "round": int(m.group(3))}
    m = DISPATCH_RE.match(dispatch_id)
    if not m:
        return None
    return {"node": m.group("node"), "generation": int(m.group("generation")), "unit": m.group("unit"),
            "round": int(m.group("round"))}


def packet_of(sess: dict[str, Any], packets_dir: Path) -> tuple[str, str] | None:
    """(dispatchId, digest) when a leading user message hands over a packet of this ticket."""
    pdir = _norm(packets_dir)
    for text in sess.get("_headUsers", []):
        for m in PACKET_RE.finditer(text or ""):
            raw = m.group("path").strip()
            p = _norm(raw)
            if os.path.dirname(p) == pdir and p.endswith(".md"):
                return os.path.basename(raw.replace("\\", "/"))[:-3], m.group("digest").lower()
    return None


def _record_for(state: dict[str, Any], ticket_dir: Path, ident: dict[str, Any], dispatch_id: str) -> dict[str, Any] | None:
    rec = (state.get("nodes") or {}).get(ident["node"]) or {}
    # `result` may be absolute or relative to the profile, with either separator: compare the file name
    want = f"{dispatch_id}.md"
    for u in rec.get("units") or []:
        name = u.get("result").replace("\\", "/").rsplit("/", 1)[-1] if isinstance(u, dict) and isinstance(
            u.get("result"), str) else None
        if name == want:
            return {k: u.get(k) for k in ("status", "verdict", "model", "tier", "tokens", "host", "at") if k in u}
    return None


def _public(sess: dict[str, Any], with_steps: bool) -> dict[str, Any]:
    out = {k: v for k, v in sess.items() if not k.startswith("_")}
    if not with_steps:
        out.pop("steps", None)
    return out


def match_sessions(sessions: list[dict[str, Any]], ticket: str, ticket_dir: Path, state: dict[str, Any],
                   node: str | None = None, with_steps: bool = False) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per node: `units` (exact, by packet path or dispatch-log name) and `controller` (heuristic, by sdlc calls)."""
    node_names = list((state.get("nodes") or {}).keys())
    nodes: dict[str, dict[str, list[dict[str, Any]]]] = {
        n: {"units": [], "controller": []} for n in node_names if node is None or n == node}
    if node is not None and node not in nodes:
        nodes[node] = {"units": [], "controller": []}
    packets = ticket_dir / "packets"
    for sess in sessions:
        exact = None
        if sess["agent"] == "dispatch":
            exact = (sess["sessionId"], None)
        else:
            exact = packet_of(sess, packets)
        if exact:
            ident = split_dispatch(exact[0], node_names)
            if ident and ident["node"] in nodes:
                item = _public(sess, with_steps)
                item.update(match="exact", dispatchId=exact[0], **ident,
                            matchedBy=f"packet {exact[0]}" if exact[1] else f"dispatch log {exact[0]}")
                if exact[1]:
                    item["packetDigest"] = exact[1]
                record = _record_for(state, ticket_dir, ident, exact[0])
                if record:
                    item["record"] = record
                nodes[ident["node"]]["units"].append(item)
            continue
        calls: dict[str, list[str]] = {}
        for c in sess.get("_sdlc", []):
            if c["ticket"] == ticket and c.get("node") and c["node"] in nodes:
                calls.setdefault(c["node"], [])
                if c["command"] not in calls[c["node"]]:
                    calls[c["node"]].append(c["command"])
        for n, cmds in calls.items():
            item = _public(sess, with_steps)
            item.update(match="heuristic", node=n, sdlcCommands=cmds, matchedBy=f"sdlc {', '.join(cmds)} for {n}")
            nodes[n]["controller"].append(item)
    for entry in nodes.values():
        for key in ("units", "controller"):
            entry[key].sort(key=lambda s: s.get("startedAt") or s.get("updatedAt") or "")
    return nodes


# --- ticket entry point --------------------------------------------------------------------------------

def workspace_dirs(profile_dir: Path, state: dict[str, Any]) -> list[str]:
    """The profile folder and each ticket worktree recorded in state (resolved as the driver does)."""
    dirs = [str(profile_dir.resolve())]
    for repo in ((state.get("context") or {}).get("repositories") or []):
        if not isinstance(repo, dict):
            continue
        if repo.get("worktreeRel"):
            wt = Path(repo["worktreeRel"])
            dirs.append(str(wt if wt.is_absolute() else (profile_dir / wt).resolve()))
        elif repo.get("worktree"):
            dirs.append(str(Path(repo["worktree"])))
    out: list[str] = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def _ticket_started(ticket_dir: Path) -> float | None:
    try:
        with (ticket_dir / "decisions.jsonl").open("r", encoding="utf-8") as fh:
            first = json.loads(fh.readline())
        return _epoch(first.get("at"))
    except (OSError, ValueError, AttributeError):
        try:
            return (ticket_dir / "state.json").stat().st_ctime
        except OSError:
            return None


def collect(ticket_dir: Path, dirs: list[str], *, max_steps: int = MAX_STEPS, running_seconds: int = RUNNING_SECONDS,
            since: float | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """Every readable session relevant to these workspace folders, from all four sources."""
    kw = {"max_steps": max_steps, "running_seconds": running_seconds, "now": now}
    norm_dirs = [_norm(d) for d in dirs]
    sessions: list[dict[str, Any]] = []
    for f, sub in claude_logs(dirs, since):
        s = read_claude_subagent(f, **kw) if sub else read_claude(f, **kw)
        if s:
            sessions.append(s)
    for f in codex_logs(norm_dirs, since):
        s = read_codex(f, **kw)
        if s:
            sessions.append(s)
    for f, cid, info in antigravity_logs(norm_dirs, since):
        s = read_antigravity(f, conversation_id=cid, summary=info, **kw)
        if s:
            sessions.append(s)
    logs = ticket_dir / "logs"
    if logs.is_dir():
        for f in sorted(logs.glob("*.dispatch.log")):
            s = read_dispatch_log(f, running_seconds=running_seconds, now=now)
            if s:
                sessions.append(s)
    return sessions


def log_fingerprint(profile_path: Path, ticket: str) -> str:
    """A digest of (path, mtime, size) of every file `transcripts` could read for this ticket: state.json, the
    decision log, the Claude Code / Codex / Antigravity logs of its workspace folders and its dispatch logs. Stat only:
    no log is opened, so a poller can call it cheaply and re-read only when it changes. It covers a superset of what
    `collect` reads (Codex rollouts of any cwd, Antigravity conversations of any project), which costs at most an extra
    refresh, never a stale answer."""
    import hashlib
    from .cli import ticket_root
    from .profile import load_profile
    profile = load_profile(profile_path)
    tdir = ticket_root(profile, profile_path.parent) / ticket
    try:
        state = json.loads((tdir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    files: list[Path] = [tdir / "state.json", tdir / "decisions.jsonl"]
    projects = claude_root() / "projects"
    globs: list[tuple[Path, tuple[str, ...]]] = []
    for d in workspace_dirs(profile_path.parent, state if isinstance(state, dict) else {}):
        globs.append((projects / claude_slug(d), ("*.jsonl", "*/subagents/*.jsonl", "*/subagents/*.meta.json")))
    for root in (codex_root() / "sessions", codex_root() / "archived_sessions"):
        globs.append((root, ("*/*/*/rollout-*.jsonl", "rollout-*.jsonl")))
    agy = antigravity_root()
    files.append(agy / "conversation_summaries.db")
    globs.append((agy / "brain", ("*/.system_generated/logs/transcript*.jsonl",)))
    globs.append((tdir / "logs", ("*.dispatch.log",)))
    for folder, patterns in globs:
        if not folder.is_dir():
            continue
        for pattern in patterns:
            try:
                files += list(folder.glob(pattern))
            except OSError:
                continue
    h = hashlib.sha256()
    for f in sorted(set(files), key=str):
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f}\0{st.st_mtime_ns}\0{st.st_size}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def running_until(result: dict[str, Any], running_seconds: int = RUNNING_SECONDS) -> float | None:
    """The earliest epoch at which a session of a `transcripts` result flagged `running` stops being running without
    its log changing (no write for `running_seconds`); None when no session is running."""
    until: float | None = None
    for entry in (result.get("nodes") or {}).values():
        for sess in (entry.get("units") or []) + (entry.get("controller") or []):
            if sess.get("running"):
                at = _epoch(sess.get("updatedAt"))
                if at is not None:
                    end = at + running_seconds + 1
                    until = end if until is None else min(until, end)
    return until


def transcripts(profile_path: Path, ticket: str, node: str | None = None, *, max_steps: int = MAX_STEPS,
                running_seconds: int = RUNNING_SECONDS) -> dict[str, Any]:
    """`sdlc transcripts`: sessions per node of one ticket; steps only with `node`. Read-only."""
    from .cli import ticket_root
    from .errors import HarnessError
    from .profile import load_profile
    profile = load_profile(profile_path)
    tdir = ticket_root(profile, profile_path.parent) / ticket
    try:
        state = json.loads((tdir / "state.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HarnessError(f"ticket not initialised: {tdir} (run init)")
    except ValueError as exc:
        raise HarnessError(f"G7: state.json is not valid JSON: {exc}")
    if node is not None and node not in (state.get("nodes") or {}):
        raise HarnessError(f"{node} is not a node of {ticket}: {list((state.get('nodes') or {}).keys())}")
    started = _ticket_started(tdir)
    since = started - SLACK_SECONDS if started else None
    sessions = collect(tdir, workspace_dirs(profile_path.parent, state), max_steps=max_steps,
                       running_seconds=running_seconds, since=since)
    nodes = match_sessions(sessions, ticket, tdir, state, node=node, with_steps=node is not None)
    return {"ok": True, "ticket": ticket, "nodes": nodes}
