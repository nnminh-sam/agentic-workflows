"""Measured cost per ticket from the agents' own session logs (`sdlc cost`, `/api/cost`, the STATUS.md field). Read-only
with respect to tickets: it reads the logs `sessions.py` reads and writes only its own cache under the user config dir.

What is counted
- Claude Code: every assistant API message once (rows that share `message.id` are one message; the last row's
  `usage` wins), subagent logs included. A message id seen in two logs (a resumed or forked session) counts once.
  Claude Code's own `cost-state` rows (cumulative `totalCostUSD` and `modelUsage[model].costUSD` of a session and its
  subagents) are its REPORTED cost: the last row of a session is spread over that session family's messages of the
  same model (weighted by priced cost when the model has a price, else by tokens), so it is attributed to nodes and
  days without being added twice. Reported cost of a model with no logged message (side calls) becomes one `side`
  record with the tokens Claude Code reported for it.
- Codex (unverified, not installed here): `event_msg` / `token_count` totals in the rollout, as deltas between
  successive `total_token_usage` values (OpenAI `input_tokens` includes `cached_input_tokens`; they are split).
- Antigravity (unpublished format): a `usage` / `usage_metadata` object on a transcript line when there is one;
  otherwise the session's tokens are unknown and only counted as such (never guessed).
- sdlc dispatch logs: the runner's stdout (`claude -p --output-format json` result, `codex exec --json`
  `turn.completed` usage, else `dispatch.extract_tokens`). A dispatch whose agent session log was also found counts
  once, from the session log.

Attribution (reusing sessions.py's matching)
- unit (exact): the session's leading user message hands over a packet of the ticket; every message is that unit's.
- controller (heuristic): each message belongs to the (ticket, node) of the latest `sdlc packet|record|gate|plan|
  dispatch --ticket T --node N` call before it in that session (messages before the first call go to the first).
  A subagent with neither a packet nor its own calls follows its parent session's calls by time.

USD: `reported` (the agent's own number), else `priced` from the user price table (unverified unless the entry says
`"verified": true`), else `unknown` (tokens are still shown). Prices live in the USER config, never in the repo:
$KANEKI_PRICES, else $KANEKI_HOME/prices.json, else ~/.kaneki/prices.json (`sdlc cost --init-prices` writes a template
with null prices). Days are local calendar days of this machine.

Caching: each log's parse is kept in $KANEKI_HOME/cache/cost/files keyed by path and validated by (mtime, size); a
Claude log that only grew is read from where the last read stopped. The aggregated rows of a scope (one ticket, or all
tickets) are kept keyed by a stat-only fingerprint of every file they depend on (logs, state, decision logs, the price
file). Budgets (next track) ask `measured(profile, ticket, node, day)`, which is served from that cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import sessions as S

CACHE_VERSION = 6                     # 5: host launchers match SDLC_RE; edit text is not a call; rows per session
                                      # 6: read-only commands (check) do not attribute; `--node $n` has no node
HEAD_TEXT = 4000                      # characters kept per leading user message (the packet line is near the start)
TOKEN_KEYS = ("input", "output", "cacheRead", "cacheWrite")
PRICE_KEYS = TOKEN_KEYS               # USD per million tokens, one per token class
TEMPLATE_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")


# --- user config: home, price file, cache --------------------------------------------------------------

def kaneki_home() -> Path:
    env = os.environ.get("KANEKI_HOME")
    return Path(env).expanduser() if env else Path.home() / ".kaneki"


def price_path() -> Path:
    env = os.environ.get("KANEKI_PRICES")
    return Path(env).expanduser() if env else kaneki_home() / "prices.json"


def cache_dir() -> Path:
    return kaneki_home() / "cache" / "cost"


def price_template() -> dict[str, Any]:
    entry = {**{k: None for k in PRICE_KEYS}, "verified": False, "source": None}
    return {"version": 1, "currency": "USD", "unit": "USD per 1,000,000 tokens",
            "_comment": "Fill in prices from the provider's price page and set verified to true only after checking "
                        "them. A model is matched by exact name, else by the longest name it starts with. A null "
                        "price leaves USD unknown for that model (tokens are still counted).",
            "models": {m: dict(entry) for m in TEMPLATE_MODELS}}


def init_prices(path: Path | None = None) -> dict[str, Any]:
    path = path or price_path()
    if path.exists():
        return {"ok": True, "path": str(path), "written": False, "note": "price file already exists; left unchanged"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(price_template(), indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "written": True,
            "note": "template with null prices; fill them in from the provider's price page"}


def load_prices(path: Path | None = None) -> dict[str, Any]:
    """{path, exists, error, models: {name: {input, output, cacheRead, cacheWrite, verified, source}}}."""
    path = path or price_path()
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "error": None, "models": {}}
    if not out["exists"]:
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        models = raw.get("models") if isinstance(raw, dict) else None
        if not isinstance(models, dict):
            raise ValueError("no `models` object")
    except (OSError, ValueError) as exc:
        out["error"] = f"cannot read price file: {exc}"
        return out
    for name, e in models.items():
        if not isinstance(e, dict):
            continue
        clean = {}
        for k in PRICE_KEYS:
            v = e.get(k)
            clean[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None
        clean["verified"] = e.get("verified") is True
        clean["source"] = e.get("source") if isinstance(e.get("source"), str) else None
        out["models"][str(name)] = clean
    return out


def price_for(prices: dict[str, Any], model: str | None) -> tuple[str, dict[str, Any]] | None:
    if not model:
        return None
    models = prices.get("models") or {}
    if model in models:
        return model, models[model]
    hits = [k for k in models if model.startswith(k)]
    if hits:
        k = max(hits, key=len)
        return k, models[k]
    return None


def priced_usd(entry: dict[str, Any] | None, tok: dict[str, int]) -> float | None:
    """USD for these tokens, or None when a token class that has tokens has no price."""
    if entry is None:
        return None
    usd = 0.0
    for k in PRICE_KEYS:
        n = tok.get(k) or 0
        if not n:
            continue
        if entry.get(k) is None:
            return None
        usd += n * entry[k] / 1_000_000
    return usd


# --- small helpers ----------------------------------------------------------------------------------------

def _int(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def _calls(tool_input: Any, at: float | None, seq: int) -> list[list[Any]]:
    """sdlc calls of one tool input, as [at, ticket, node, unit, command, seq] (sessions._Builder._scan_sdlc's rule).
    `seq` is the index of the first message of the log that comes after the call: the message that issues a call still
    belongs to the work before it."""
    found = []
    for text in S._strings(tool_input):
        if "sdlc" not in text and "cli" not in text:
            continue
        for m in S.SDLC_RE.finditer(text):
            opts = S.call_opts(m.group("rest"))
            if opts.get("ticket"):
                found.append([at, opts["ticket"], opts.get("node"), opts.get("unit"), m.group("cmd"), seq])
    return found


def _at(value: Any) -> float | None:
    return S._epoch(S._iso(value)) if value not in (None, "") else None


def _local_day(epoch: float | None) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d") if epoch is not None else "unknown"


def _new_summary(agent: str, path: Path) -> dict[str, Any]:
    return {"v": CACHE_VERSION, "agent": agent, "path": str(path), "sessionId": None, "cwd": None, "head": [],
            "calls": [], "msgs": [], "costState": None, "unknownSteps": 0, "offset": 0, "size": -1, "mtimeNs": -1}


# --- per-agent scanners ------------------------------------------------------------------------------------
# A summary: msgs = [[id, at, model, input, output, cacheRead, cacheWrite], ...]; calls = [[at, ticket, node, unit,
# command], ...]; head = leading user texts; costState = Claude Code's last cost-state row (or None).

def scan_claude(path: Path, prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """Claude Code session or subagent log; continues from `prev` when the file only grew."""
    st = path.stat()
    s = prev if (prev and prev.get("v") == CACHE_VERSION and prev.get("agent") == "claude"
                 and 0 < prev.get("offset", 0) <= st.st_size) else _new_summary("claude", path)
    index = {m[0]: i for i, m in enumerate(s["msgs"])}
    with path.open("rb") as fh:
        fh.seek(s["offset"])
        offset = s["offset"]
        for raw in fh:
            if not raw.endswith(b"\n"):
                break  # a line still being written: read it next time
            offset += len(raw)
            line = raw.decode("utf-8", "replace")
            if '"assistant"' not in line and '"cost-state"' not in line and (
                    '"user"' not in line or len(s["head"]) >= S.HEAD_USER_STEPS):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            t = row.get("type")
            if t == "cost-state":
                if isinstance(row.get("totalCostUSD"), (int, float)):
                    s["costState"] = {"totalCostUSD": float(row["totalCostUSD"]), "at": _at(row.get("timestamp")),
                                      "modelUsage": row.get("modelUsage") if isinstance(row.get("modelUsage"), dict) else {},
                                      "hasUnknownModelCost": bool(row.get("hasUnknownModelCost"))}
                continue
            if t not in ("user", "assistant"):
                continue
            s["sessionId"] = s["sessionId"] or row.get("sessionId")
            s["cwd"] = s["cwd"] or row.get("cwd")
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            content = msg.get("content")
            at = _at(row.get("timestamp"))
            if t == "user":
                if row.get("isMeta") or len(s["head"]) >= S.HEAD_USER_STEPS:
                    continue
                if isinstance(content, list):
                    text = S._claude_text([b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")])
                else:
                    text = content if isinstance(content, str) else ""
                if text.strip():
                    s["head"].append(text[:HEAD_TEXT])
                continue
            usage = msg.get("usage")
            model = msg.get("model")
            seq = len(s["msgs"])
            if isinstance(usage, dict) and model != "<synthetic>":
                mid = str(msg.get("id") or row.get("requestId") or row.get("uuid") or "")
                rec = [mid, at, model, _int(usage.get("input_tokens")), _int(usage.get("output_tokens")),
                       _int(usage.get("cache_read_input_tokens")), _int(usage.get("cache_creation_input_tokens"))]
                if mid in index:
                    old = s["msgs"][index[mid]]
                    rec[1] = old[1] if old[1] is not None else at  # first row's time, last row's usage
                    s["msgs"][index[mid]] = rec
                else:
                    index[mid] = len(s["msgs"])
                    s["msgs"].append(rec)
                seq = index[mid] + 1
            for blk in content if isinstance(content, list) else []:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    s["calls"] += _calls(blk.get("input"), at, seq)
    s.update(offset=offset, size=st.st_size, mtimeNs=st.st_mtime_ns)
    return s


def scan_codex(path: Path, prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """Codex rollout (UNVERIFIED: Codex is not installed where this was written). Token deltas from token_count events."""
    st = path.stat()
    s = _new_summary("codex", path)
    meta = S.codex_meta(path)
    s["sessionId"], s["cwd"] = meta.get("id") or path.stem, meta.get("cwd")
    model = meta.get("model")
    last_total: dict[str, int] | None = None
    events_head: list[str] = []
    n = 0
    for row in S._jsonl(path):
        at = _at(row.get("timestamp"))
        kind = row.get("type")
        p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if kind == "turn_context":
            model = p.get("model") or model
            s["cwd"] = s["cwd"] or p.get("cwd")
            continue
        if kind == "event_msg":
            if p.get("type") == "token_count" and isinstance(p.get("info"), dict):
                info = p["info"]
                total, last = info.get("total_token_usage"), info.get("last_token_usage")
                if isinstance(total, dict):
                    cur = {k: _int(total.get(k)) for k in ("input_tokens", "cached_input_tokens", "output_tokens")}
                    if last_total is not None and cur == last_total:
                        continue  # the same totals repeated (a rate-limit update)
                    base = last_total or {k: 0 for k in cur}
                    delta = {k: max(cur[k] - base.get(k, 0), 0) for k in cur}
                    last_total = cur
                elif isinstance(last, dict):
                    delta = {k: _int(last.get(k)) for k in ("input_tokens", "cached_input_tokens", "output_tokens")}
                else:
                    continue
                n += 1
                cached = delta["cached_input_tokens"]
                s["msgs"].append([f"tc{n}", at, model, max(delta["input_tokens"] - cached, 0), delta["output_tokens"],
                                  cached, 0])
            elif p.get("type") == "user_message" and p.get("message") and len(events_head) < S.HEAD_USER_STEPS:
                events_head.append(str(p["message"])[:HEAD_TEXT])
            continue
        item = p if kind == "response_item" else row
        itype = item.get("type") if isinstance(item, dict) else None
        if itype == "message" and item.get("role") == "user" and len(s["head"]) < S.HEAD_USER_STEPS:
            text = S._codex_text(item.get("content"))
            if text.strip() and not text.lstrip().startswith(S.INJECTED):
                s["head"].append(text[:HEAD_TEXT])
        elif itype in ("function_call", "custom_tool_call"):
            s["calls"] += _calls(S._codex_args(item.get("arguments") if itype == "function_call" else item.get("input")), at,
                                 len(s["msgs"]) + 1)  # the token_count that follows belongs to the issuing response
        elif itype == "local_shell_call":
            s["calls"] += _calls(item.get("action"), at, len(s["msgs"]) + 1)
    if not s["head"]:
        s["head"] = events_head
    s.update(size=st.st_size, mtimeNs=st.st_mtime_ns)
    return s


AG_USAGE_KEYS = ("usage", "usage_metadata", "usageMetadata", "token_usage", "tokenUsage")


def _ag_usage(row: dict[str, Any]) -> dict[str, int] | None:
    for key in AG_USAGE_KEYS:
        u = row.get(key)
        if isinstance(u, dict):
            def g(*names: str) -> int:
                return next((_int(u[n]) for n in names if n in u), 0)
            prompt = g("prompt_token_count", "promptTokenCount", "input_tokens", "inputTokens")
            cached = g("cached_content_token_count", "cachedContentTokenCount", "cache_read_input_tokens")
            out = g("candidates_token_count", "candidatesTokenCount", "output_tokens", "outputTokens") + \
                g("thoughts_token_count", "thoughtsTokenCount")
            if prompt or out or cached:
                gemini = "prompt_token_count" in u or "promptTokenCount" in u  # Gemini counts cached inside prompt
                return {"input": max(prompt - cached, 0) if gemini else prompt, "output": out, "cacheRead": cached}
    return None


def scan_antigravity(path: Path, prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """Antigravity transcript: tokens only where a line carries a usage object; other model steps are `unknownSteps`."""
    st = path.stat()
    s = _new_summary("antigravity", path)
    s["sessionId"] = path.parents[2].name
    for i, row in enumerate(S._jsonl(path)):
        at = _at(row.get("created_at") or row.get("createdAt") or row.get("timestamp"))
        source = str(row.get("source") or "").upper()
        kind = str(row.get("type") or "").upper()
        if "USER" in source or kind == "USER_INPUT":
            text = re.sub(r"</?USER_REQUEST>", "", S._ag_text(row.get("content"))).strip()
            if text and len(s["head"]) < S.HEAD_USER_STEPS:
                s["head"].append(text[:HEAD_TEXT])
            continue
        calls = row.get("tool_calls") or row.get("toolCalls") or []
        u = _ag_usage(row)
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict):
                s["calls"] += _calls(S._ag_args(call.get("args")), at, len(s["msgs"]) + (1 if u else 0))
        if u:
            s["msgs"].append([f"s{row.get('step_index', i)}", at, row.get("model") or row.get("model_name"),
                              u["input"], u["output"], u["cacheRead"], 0])
        elif kind == "PLANNER_RESPONSE" or calls:
            s["unknownSteps"] += 1
    s.update(size=st.st_size, mtimeNs=st.st_mtime_ns)
    return s


def scan_dispatch(path: Path, prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """sdlc dispatch log: usage and the agent's reported USD from the runner's stdout."""
    from .dispatch import extract_tokens
    st = path.stat()
    s = _new_summary("dispatch", path)
    did = path.name[:-len(".dispatch.log")] if path.name.endswith(".dispatch.log") else path.stem
    s["sessionId"] = did
    text = path.read_text(encoding="utf-8", errors="replace")
    o, e = text.find("\n--- STDOUT ---\n"), text.find("\n\n--- STDERR ---\n")
    stdout = text[o + len("\n--- STDOUT ---\n"):e] if o >= 0 and e > o else ""
    stderr = text[e + len("\n\n--- STDERR ---\n"):] if e >= 0 else ""
    objs: list[dict[str, Any]] = []
    try:
        whole = json.loads(stdout.strip())
        objs = [whole] if isinstance(whole, dict) else [x for x in whole if isinstance(x, dict)] if isinstance(whole, list) else []
    except ValueError:
        for line in stdout.splitlines():
            if line.strip().startswith("{"):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        objs.append(obj)
                except ValueError:
                    pass
    at = st.st_mtime
    tok = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    model, usd, found = None, None, False
    for obj in objs:
        if obj.get("type") == "result" or "total_cost_usd" in obj:  # claude -p --output-format json|stream-json
            if isinstance(obj.get("total_cost_usd"), (int, float)):
                usd = float(obj["total_cost_usd"])
            mu = obj.get("modelUsage")
            if isinstance(mu, dict) and mu:
                model = max(mu, key=lambda k: (mu[k] or {}).get("costUSD") or 0 if isinstance(mu[k], dict) else 0)
        u = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
        if u and (obj.get("type") in ("result", "turn.completed") or "total_cost_usd" in obj):
            found = True
            cached = _int(u.get("cached_input_tokens"))
            tok["input"] += max(_int(u.get("input_tokens")) - cached, 0)
            tok["output"] += _int(u.get("output_tokens"))
            tok["cacheRead"] += _int(u.get("cache_read_input_tokens")) + cached
            tok["cacheWrite"] += _int(u.get("cache_creation_input_tokens"))
        model = obj.get("model") if isinstance(obj.get("model"), str) else model
    if not found:
        total = extract_tokens(stdout, stderr)
        if total:  # a bare total: class unknown, kept as input so totals add up; USD then needs `reported`
            tok["input"], found = total, True
            s["totalOnly"] = True
    if found or usd is not None:
        s["msgs"].append([did, at, model, tok["input"], tok["output"], tok["cacheRead"], tok["cacheWrite"]])
    s["reportedUSD"] = usd
    s.update(size=st.st_size, mtimeNs=st.st_mtime_ns)
    return s


AGENTS = ("claude", "codex", "antigravity", "dispatch")  # each has scan_<agent>(path, prev)


# --- per-file cache -----------------------------------------------------------------------------------------

def _file_cache_path(path: Path) -> Path:
    return cache_dir() / "files" / (hashlib.sha256(str(path).encode("utf-8", "surrogateescape")).hexdigest()[:32] + ".json")


def summary_of(agent: str, path: Path) -> dict[str, Any] | None:
    """The file's parse, from cache when (mtime, size) still match; a Claude log that grew continues from its offset."""
    try:
        st = path.stat()
    except OSError:
        return None
    cpath = _file_cache_path(path)
    prev = None
    try:
        prev = json.loads(cpath.read_text(encoding="utf-8"))
        if not (isinstance(prev, dict) and prev.get("v") == CACHE_VERSION and prev.get("path") == str(path)
                and prev.get("agent") == agent):
            prev = None
    except (OSError, ValueError):
        prev = None
    if prev and prev.get("mtimeNs") == st.st_mtime_ns and prev.get("size") == st.st_size:
        return prev
    try:
        s = globals()["scan_" + agent](path, prev if agent == "claude" and prev and st.st_size >= prev.get("size", 0) else None)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None
    _write_json(cpath, s)
    return s


def _write_json(path: Path, obj: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a cache that cannot be written only costs a re-read


# --- scope: tickets, logs, fingerprint -------------------------------------------------------------------

def _tickets(profile_path: Path, only: str | None) -> tuple[Path, dict[str, dict[str, Any]]]:
    from .cli import ticket_root
    from .errors import HarnessError
    from .profile import load_profile
    root = ticket_root(load_profile(profile_path), profile_path.parent)
    names = [only] if only else (sorted(d.name for d in root.iterdir() if (d / "state.json").is_file())
                                 if root.is_dir() else [])
    out: dict[str, dict[str, Any]] = {}
    for t in names:
        tdir = root / t
        try:
            state = json.loads((tdir / "state.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise HarnessError(f"ticket not initialised: {tdir} (run init)")
        except ValueError as exc:
            if only:
                raise HarnessError(f"G7: state.json is not valid JSON: {exc}")
            continue
        out[t] = {"dir": tdir, "state": state if isinstance(state, dict) else {}}
    return root, out


def _sources(profile_path: Path, tickets: dict[str, dict[str, Any]]) -> tuple[list[tuple[str, Path, Any]], list[Path]]:
    """(agent, log, extra) for every log that can hold usage of these tickets, and the extra files the answer
    depends on (for the fingerprint)."""
    dirs: list[str] = []
    started: list[float] = []
    deps: list[Path] = [price_path()]
    for t in tickets.values():
        for d in S.workspace_dirs(profile_path.parent, t["state"]):
            if d not in dirs:
                dirs.append(d)
        st = S._ticket_started(t["dir"])
        if st:
            started.append(st)
        deps += [t["dir"] / "state.json", t["dir"] / "decisions.jsonl"]
    since = min(started) - S.SLACK_SECONDS if started and len(started) == len(tickets) else None
    norm = [S._norm(d) for d in dirs]
    out: list[tuple[str, Path, Any]] = []
    for f, sub in S.claude_logs(dirs, since):
        out.append(("claude", f, sub))
    for f in S.codex_logs(norm, since):
        out.append(("codex", f, None))
    agy = S.antigravity_root()
    deps.append(agy / "conversation_summaries.db")
    for f, cid, info in S.antigravity_logs(norm, since):
        out.append(("antigravity", f, {"cid": cid, "info": info}))
    for name, t in tickets.items():
        logs = t["dir"] / "logs"
        if logs.is_dir():
            for f in sorted(logs.glob("*.dispatch.log")):
                out.append(("dispatch", f, name))
    return out, deps


def _fingerprint(srcs: list[tuple[str, Path, Any]], deps: list[Path], scope: str) -> str:
    h = hashlib.sha256(f"{CACHE_VERSION}\0{scope}\0{time.strftime('%z')}\0".encode())
    for f in sorted({str(p) for _, p, _ in srcs} | {str(p) for p in deps}):
        try:
            st = os.stat(f)
            h.update(f"{f}\0{st.st_mtime_ns}\0{st.st_size}\n".encode("utf-8", "surrogateescape"))
        except OSError:
            h.update(f"{f}\0-\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


# --- attribution ---------------------------------------------------------------------------------------------

def _segmenter(calls: list[list[Any]], known: set[str]):
    """(at, index) -> (ticket, node) of the latest sdlc call before that message: by message index within the same log
    (`index` given), else by time (a subagent following its parent). Messages before the first call go to the first
    call. None when that call names a ticket outside `known`."""
    if not calls:
        return None
    marks: list[tuple[int, float, tuple[str, str | None] | None]] = []
    last_node: dict[str, str | None] = {}
    for c in calls:  # in log order
        at, ticket, node = c[0], c[1], c[2]
        node = node or last_node.get(ticket)
        last_node[ticket] = node
        marks.append((c[5] if len(c) > 5 else 0, at if at is not None else 0.0,
                      (ticket, node) if ticket in known else None))
    by_time = sorted(marks, key=lambda m: m[1])

    def assign(at: float | None, index: int | None = None) -> tuple[str, str | None] | None:
        if index is not None:
            cur = marks[0][2]
            for seq, _when, who in marks:
                if seq <= index:
                    cur = who
            return cur
        if at is None:
            return marks[-1][2]
        cur = by_time[0][2]
        for _seq, when, who in by_time:
            if when <= at:
                cur = who
            else:
                break
        return cur
    return assign


def _exact(summary: dict[str, Any], tickets: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if summary["agent"] == "dispatch":
        return None
    fake = {"_headUsers": summary.get("head") or []}
    for name, t in tickets.items():
        hit = S.packet_of(fake, t["dir"] / "packets")
        if hit:
            ident = S.split_dispatch(hit[0], list((t["state"].get("nodes") or {}).keys()))
            if ident:
                return {"ticket": name, "node": ident["node"], "unit": ident["unit"], "dispatchId": hit[0]}
    return None


def _records(srcs: list[tuple[str, Path, Any]], tickets: dict[str, dict[str, Any]], prices: dict[str, Any]) -> list[dict[str, Any]]:
    """One record per counted message (or dispatch, or side call), attributed and priced."""
    known = set(tickets)
    summaries: list[tuple[str, Path, Any, dict[str, Any]]] = []
    for agent, path, extra in srcs:
        s = summary_of(agent, path)
        if s is not None:
            summaries.append((agent, path, extra, s))
    # Claude families: main log + subagents/ (parent = <folder>/<session>.jsonl of <folder>/<session>/subagents/x.jsonl)
    mains = {str(p): s for a, p, x, s in summaries if a == "claude" and not x}
    recs: list[dict[str, Any]] = []
    seen_claude: set[str] = set()
    exact_dispatch: set[tuple[str, str]] = set()
    families: dict[str, list[dict[str, Any]]] = {}
    rank = {"claude": 0, "codex": 1, "antigravity": 2, "dispatch": 3}  # dispatch last: a session log wins
    order = sorted(summaries, key=lambda r: (rank.get(r[0], 9), bool(r[2]) if r[0] == "claude" else False, str(r[1])))
    for agent, path, extra, s in order:
        parent_key = str(path.parent.parent.with_suffix(".jsonl")) if agent == "claude" and extra else None
        exact = _exact(s, tickets)
        if agent == "dispatch":
            t = extra
            ident = S.split_dispatch(s["sessionId"], list((tickets[t]["state"].get("nodes") or {}).keys()))
            if not ident or (t, s["sessionId"]) in exact_dispatch:
                continue
            exact = {"ticket": t, "node": ident["node"], "unit": ident["unit"], "dispatchId": s["sessionId"]}
        if exact and s.get("msgs"):
            exact_dispatch.add((exact["ticket"], exact["dispatchId"]))
        seg, own = None, True
        if not exact:
            seg = _segmenter(s.get("calls") or [], known)
            if seg is None and parent_key and parent_key in mains:
                parent = mains[parent_key]
                pexact = _exact(parent, tickets)
                if pexact:
                    exact = pexact
                else:
                    seg, own = _segmenter(parent.get("calls") or [], known), False
        # every Claude message joins its family (even one outside this scope) so reported cost is shared fairly
        fam = families.setdefault(parent_key or str(path), []) if agent == "claude" else None
        if not exact and seg is None and fam is None:
            continue
        for idx, (mid, at, model, i, o, cr, cw) in enumerate(s.get("msgs") or []):
            if agent == "claude":
                if mid in seen_claude:
                    continue
                seen_claude.add(mid)
            if exact:
                who = (exact["ticket"], exact["node"])
                kind = "dispatch" if agent == "dispatch" else "unit"
            else:
                who = (seg(at, idx) if own else seg(at)) if seg else None
                kind = "controller"
            r = {"ticket": who[0] if who else None, "node": who[1] if who else None, "kind": kind,
                 "dispatchId": exact["dispatchId"] if exact else None, "session": path.stem,
                 "agent": agent, "model": model, "at": at if at is not None else s.get("mtimeNs", 0) / 1e9,
                 "input": i, "output": o, "cacheRead": cr, "cacheWrite": cw, "usd": None, "src": "unknown", "msgs": 1}
            if agent == "dispatch" and s.get("reportedUSD") is not None:
                r["usd"], r["src"] = s["reportedUSD"], "reported"
            if agent == "dispatch" and s.get("totalOnly"):
                r["totalOnly"] = True
            if who:
                recs.append(r)
            if fam is not None:
                fam.append(r)
        if not exact and seg is None:
            continue
        if agent == "antigravity" and s.get("unknownSteps") and not s.get("msgs"):
            who = (exact["ticket"], exact["node"]) if exact else seg(None)
            if who:
                recs.append({"ticket": who[0], "node": who[1], "kind": "unit" if exact else "controller",
                             "dispatchId": exact["dispatchId"] if exact else None, "session": path.stem,
                             "agent": agent, "model": None,
                             "at": s.get("mtimeNs", 0) / 1e9, "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                             "usd": None, "src": "unknown", "msgs": 0, "unknownSteps": s["unknownSteps"]})
    # Claude Code's reported cost, spread over each family's messages of the same model
    for key, members in families.items():
        main = mains.get(key)
        cs = (main or {}).get("costState")
        if not cs:
            continue
        for model, mu in (cs.get("modelUsage") or {}).items():
            if not isinstance(mu, dict) or not isinstance(mu.get("costUSD"), (int, float)):
                continue
            usd = float(mu["costUSD"])
            mine = [r for r in members if r["model"] == model]
            if not mine:
                if usd <= 0 or not members:
                    continue
                last = max(members, key=lambda r: r["at"])
                if last["ticket"] is None:
                    continue  # the session's latest work is outside this scope
                recs.append({**last, "model": model, "input": _int(mu.get("inputTokens")),
                             "output": _int(mu.get("outputTokens")), "cacheRead": _int(mu.get("cacheReadInputTokens")),
                             "cacheWrite": _int(mu.get("cacheCreationInputTokens")), "usd": usd, "src": "reported",
                             "msgs": 0, "side": True})
                continue
            hit = price_for(prices, model)
            weights = []
            for r in mine:
                w = priced_usd(hit[1], r) if hit else None
                weights.append(w if w is not None else float(_tokens(r)))
            total = sum(weights)
            for r, w in zip(mine, weights):
                r["usd"] = usd * (w / total) if total else usd / len(mine)
                r["src"] = "reported"
    for r in recs:
        if r["src"] == "unknown" and not r.get("unknownSteps"):
            hit = price_for(prices, r["model"])
            usd = priced_usd(hit[1], r) if hit and not r.get("totalOnly") else None
            if usd is not None:
                r["usd"], r["src"] = usd, "priced"
    return recs


def _tokens(r: dict[str, Any]) -> int:
    return sum(r.get(k) or 0 for k in TOKEN_KEYS)


def _rows(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records grouped by (ticket, node, kind, dispatchId, session, agent, model, src, local day): the cached form.
    `session` is the log's file stem (a Claude subagent: agent-<id>), so the attempts of one dispatch stay apart;
    `first` / `last` are the epoch times of the group's first and last message."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in recs:
        key = (r["ticket"], r["node"], r["kind"], r["dispatchId"], r.get("session"), r["agent"], r["model"], r["src"],
               _local_day(r["at"]))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"ticket": key[0], "node": key[1], "kind": key[2], "dispatchId": key[3], "session": key[4],
                               "agent": key[5], "model": key[6], "src": key[7], "day": key[8], "msgs": 0, "usd": None,
                               "unknownSteps": 0, "first": r["at"], "last": r["at"], **{k: 0 for k in TOKEN_KEYS}}
        g["first"] = min(g["first"], r["at"])
        g["last"] = max(g["last"], r["at"])
        for k in TOKEN_KEYS:
            g[k] += r.get(k) or 0
        g["msgs"] += r.get("msgs") or 0
        g["unknownSteps"] += r.get("unknownSteps") or 0
        if r.get("usd") is not None:
            g["usd"] = (g["usd"] or 0.0) + r["usd"]
    return list(groups.values())


def rows(profile_path: Path, ticket: str | None = None) -> dict[str, Any]:
    """{fingerprint, rows, prices, tickets} for one ticket (or every ticket), from cache while nothing changed."""
    profile_path = Path(profile_path).resolve()
    _root, tickets = _tickets(profile_path, ticket)
    srcs, deps = _sources(profile_path, tickets)
    scope = f"{profile_path}\0{ticket or '*'}"
    fp = _fingerprint(srcs, deps, scope)
    cpath = cache_dir() / "scopes" / (hashlib.sha256(scope.encode("utf-8", "surrogateescape")).hexdigest()[:32] + ".json")
    try:
        hit = json.loads(cpath.read_text(encoding="utf-8"))
        if isinstance(hit, dict) and hit.get("fingerprint") == fp and hit.get("v") == CACHE_VERSION:
            return hit
    except (OSError, ValueError):
        pass
    prices = load_prices()
    recs = _records(srcs, tickets, prices)
    reported = {t: _reported(v["state"]) for t, v in tickets.items()}
    res = {"v": CACHE_VERSION, "fingerprint": fp, "rows": _rows(recs), "prices": prices, "reported": reported,
           "tickets": sorted(tickets), "computedAt": time.time()}
    _write_json(cpath, res)
    return res


def reported_tokens(u: dict[str, Any]) -> int | None:
    """What a unit record says the agent reported with `sdlc record --tokens`: `contextAtEnd` (records since the driver
    measures), else `tokens` of an older record without a measurement."""
    if "contextAtEnd" in u:
        return u["contextAtEnd"]
    return None if u.get("tokensSource") == "measured" else u.get("tokens")


def _reported(state: dict[str, Any]) -> dict[str, Any]:
    """Self-reported tokens (`sdlc record --tokens`), per node over all generations. That number is the agent's last-turn
    context size, not what it consumed: it is kept only for comparison with the measured numbers."""
    out: dict[str, Any] = {}
    for node, rec in (state.get("nodes") or {}).items():
        units = [u for u in ((rec.get("units") or []) if isinstance(rec, dict) else []) if isinstance(u, dict)]
        out[node] = {"tokens": sum(reported_tokens(u) or 0 for u in units),
                     "rounds": len(units), "unreported": sum(1 for u in units if reported_tokens(u) is None)}
    return out


# --- reports ---------------------------------------------------------------------------------------------

def _blank() -> dict[str, Any]:
    return {"messages": 0, **{k: 0 for k in TOKEN_KEYS}, "tokens": 0, "usd": None,
            "usdBySource": {"reported": 0.0, "priced": 0.0}, "unpricedTokens": 0, "unknownSteps": 0, "source": "none"}


def _add(acc: dict[str, Any], row: dict[str, Any]) -> None:
    acc["messages"] += row["msgs"]
    for k in TOKEN_KEYS:
        acc[k] += row[k]
    t = sum(row[k] for k in TOKEN_KEYS)
    acc["tokens"] += t
    acc["unknownSteps"] += row.get("unknownSteps") or 0
    if row["src"] in ("reported", "priced") and row["usd"] is not None:
        acc["usdBySource"][row["src"]] += row["usd"]
        acc["usd"] = (acc["usd"] or 0.0) + row["usd"]
    else:
        acc["unpricedTokens"] += t
    acc.setdefault("_src", set()).add(row["src"] if (row["usd"] is not None or row["src"] == "unknown") else "unknown")


def _finish(acc: dict[str, Any]) -> dict[str, Any]:
    srcs = acc.pop("_src", set())
    if acc["unknownSteps"]:
        srcs.add("unknown")
    acc["sources"] = sorted(srcs)
    acc["source"] = ("none" if not srcs else next(iter(srcs)) if len(srcs) == 1 else "mixed")
    if acc["usd"] is not None:
        acc["usd"] = round(acc["usd"], 6)
    acc["usdBySource"] = {k: round(v, 6) for k, v in acc["usdBySource"].items()}
    return acc


def _total(rws: Iterable[dict[str, Any]]) -> dict[str, Any]:
    acc = _blank()
    for r in rws:
        _add(acc, r)
    return _finish(acc)


def parse_day(text: str | None, today: date | None = None) -> date | None:
    if text in (None, ""):
        return None
    today = today or date.today()
    t = text.strip().lower()
    if t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        from .errors import HarnessError
        raise HarnessError(f"not a date: {text!r} (use YYYY-MM-DD, today or yesterday)")


def _in_window(r: dict[str, Any], since: date | None, until: date | None) -> bool:
    if since is None and until is None:
        return True
    if r["day"] == "unknown":
        return False
    d = r["day"]
    return (since is None or d >= since.isoformat()) and (until is None or d <= until.isoformat())


def _prices_used(prices: dict[str, Any], models: Iterable[str | None]) -> dict[str, Any]:
    used, missing = [], []
    for m in sorted({m for m in models if m}):
        hit = price_for(prices, m)
        if hit and any(hit[1].get(k) is not None for k in PRICE_KEYS):
            used.append({"model": m, "entry": hit[0], **{k: hit[1][k] for k in PRICE_KEYS}, "verified": hit[1]["verified"]})
        else:
            missing.append(m)
    return {"file": prices.get("path"), "exists": prices.get("exists"), "error": prices.get("error"), "used": used,
            "missing": missing, "allVerified": bool(used) and all(u["verified"] for u in used),
            "note": "prices are user-supplied and unverified unless an entry says verified: true; reported USD is the "
                    "agent's own number"}


def _by(rws: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in rws:
        groups.setdefault(tuple(r[k] for k in keys), []).append(r)
    out = [{**dict(zip(keys, k)), **_total(v)} for k, v in groups.items()]
    out.sort(key=lambda x: (-(x["usd"] or 0), -x["tokens"]))
    return out


def ticket_report(profile_path: Path, ticket: str, node: str | None = None, *, since: str | None = None,
                  until: str | None = None) -> dict[str, Any]:
    """`sdlc cost --ticket T [--node N] [--since D [--until D]]`."""
    from .errors import HarnessError
    data = rows(profile_path, ticket)
    _root, tickets = _tickets(Path(profile_path).resolve(), ticket)
    order = list((tickets[ticket]["state"].get("nodes") or {}).keys())
    if node is not None and node not in order:
        raise HarnessError(f"{node} is not a node of {ticket}: {order}")
    d0, d1 = parse_day(since), parse_day(until)
    rws = [r for r in data["rows"] if r["ticket"] == ticket and (node is None or r["node"] == node) and _in_window(r, d0, d1)]
    rep = data["reported"].get(ticket) or {}
    nodes = []
    for n in ([node] if node else order + sorted({r["node"] for r in rws if r["node"] not in order}, key=str)):
        mine = [r for r in rws if r["node"] == n]
        units = _total(r for r in mine if r["kind"] in ("unit", "dispatch"))
        reported = (rep.get(n) or {}).get("tokens", 0) if n else 0
        nodes.append({"node": n, "measured": _total(mine), "units": units,
                      "controller": _total(r for r in mine if r["kind"] == "controller"),
                      "reported": {"tokens": reported, "unreportedRounds": (rep.get(n) or {}).get("unreported", 0) if n else 0},
                      "ratio": round(units["tokens"] / reported, 3) if reported else None,
                      "agents": _by(mine, ("kind", "agent", "model"))})
    units_all = _total(r for r in rws if r["kind"] in ("unit", "dispatch"))
    reported_all = sum((rep.get(n["node"]) or {}).get("tokens", 0) for n in nodes if n["node"])
    return {"ok": True, "ticket": ticket, "node": node,
            "window": {"since": d0.isoformat() if d0 else None, "until": d1.isoformat() if d1 else None},
            "total": _total(rws), "units": units_all, "controller": _total(r for r in rws if r["kind"] == "controller"),
            "reported": {"tokens": reported_all,
                         "note": "tokens the agents reported with `sdlc record --tokens` (a lower bound)"},
            "ratio": round(units_all["tokens"] / reported_all, 3) if reported_all else None,
            "nodes": nodes, "byModel": _by(rws, ("agent", "model")), "byDay": sorted(_by(rws, ("day",)), key=lambda x: x["day"]),
            "prices": _prices_used(data["prices"], (r["model"] for r in rws)),
            "fingerprint": data["fingerprint"], "computedAt": data.get("computedAt")}


def range_report(profile_path: Path, since: str, until: str | None = None) -> dict[str, Any]:
    """`sdlc cost --since D [--until D]`: every ticket's measured usage on those local days."""
    data = rows(profile_path, None)
    d0, d1 = parse_day(since), parse_day(until)
    rws = [r for r in data["rows"] if _in_window(r, d0, d1)]
    return {"ok": True, "window": {"since": d0.isoformat() if d0 else None, "until": d1.isoformat() if d1 else None},
            "total": _total(rws), "tickets": _by(rws, ("ticket",)), "byModel": _by(rws, ("agent", "model")),
            "byDay": sorted(_by(rws, ("day",)), key=lambda x: x["day"]),
            "prices": _prices_used(data["prices"], (r["model"] for r in rws)),
            "fingerprint": data["fingerprint"], "computedAt": data.get("computedAt")}


def measured(profile_path: Path, ticket: str | None = None, node: str | None = None,
             day: str | date | None = None) -> dict[str, Any]:
    """The budget question: measured tokens / USD of a ticket (or all tickets), optionally one node and one local day.
    Served from the scope cache; `usd` covers only `reported` + `priced` usage (see `unpricedTokens`)."""
    data = rows(profile_path, ticket)
    d = parse_day(day) if isinstance(day, str) else day
    return _total(r for r in data["rows"] if (ticket is None or r["ticket"] == ticket)
                  and (node is None or r["node"] == node) and _in_window(r, d, d))


def dispatch_usage(profile_path: Path, ticket: str, dispatch_id: str) -> dict[str, Any] | None:
    """What one dispatch (one packet: `<node>.g<gen>.<unit>.r<round>`) consumed, from the unit logs that carry its
    packet line (or its dispatch log): the total per token class plus USD, and one entry per attempt (agent session),
    oldest first. A packet handed to two sessions (a run abandoned, then dispatched again) has two attempts. None when
    no log of this dispatch was found (an agent whose logs are not readable here)."""
    data = rows(profile_path, ticket)
    mine = [r for r in data["rows"] if r["ticket"] == ticket and r["dispatchId"] == dispatch_id
            and r["kind"] in ("unit", "dispatch")]
    if not mine:
        return None
    by_session: dict[str, list[dict[str, Any]]] = {}
    for r in mine:
        by_session.setdefault(str(r.get("session") or r["dispatchId"]), []).append(r)
    attempts = []
    for sess, rws in by_session.items():
        tot = _total(rws)
        attempts.append({"session": sess, "first": min(r["first"] for r in rws), "last": max(r["last"] for r in rws),
                         "tokens": tot["tokens"], "usd": tot["usd"], "messages": tot["messages"],
                         "models": sorted({str(r["model"]) for r in rws if r["model"]})})
    attempts.sort(key=lambda a: a["first"])
    return {"dispatchId": dispatch_id, **classes(_total(mine)), "attempts": attempts}


def classes(total: dict[str, Any]) -> dict[str, Any]:
    """The one shape every view shows: fresh input, cache write, cache read and output tokens, their sum, and USD."""
    return {"input": total["input"], "cacheWrite": total["cacheWrite"], "cacheRead": total["cacheRead"],
            "output": total["output"], "tokens": total["tokens"], "usd": total["usd"], "sources": total["sources"],
            "unpricedTokens": total["unpricedTokens"], "messages": total["messages"]}


def ticket_totals(profile_path: Path, ticket: str) -> dict[str, Any] | None:
    """The single ticket total that STATUS.md, token-usage.md and `sdlc status` show: measured from the logs (units,
    controller and side calls), per node and per model, each split by token class plus USD. Node "" holds usage
    outside any node. None when the logs cannot be read (a view never fails on it)."""
    try:
        data = rows(profile_path, ticket)
    except Exception:  # noqa: BLE001 — a status view never fails on a cost read
        return None
    mine = [r for r in data["rows"] if r["ticket"] == ticket]

    def unit_part(rws: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return classes(_total(r for r in rws if r["kind"] in ("unit", "dispatch")))

    def ctl_part(rws: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return classes(_total(r for r in rws if r["kind"] == "controller"))

    nodes: dict[str, Any] = {}
    for n in sorted({r["node"] or "" for r in mine}):
        part = [r for r in mine if (r["node"] or "") == n]
        nodes[n] = {**classes(_total(part)), "units": unit_part(part), "controller": ctl_part(part)}
    return {"total": classes(_total(mine)), "units": unit_part(mine), "controller": ctl_part(mine), "nodes": nodes,
            "byModel": [{"agent": m["agent"], "model": m["model"], **classes(m)} for m in _by(mine, ("agent", "model"))],
            "reportedTokens": sum(v.get("tokens", 0) for v in (data["reported"].get(ticket) or {}).values())}


def fmt_split(c: dict[str, Any]) -> str:
    """`fresh 1,234 · cache write 5,678 · cache read 9,012 · output 345` for a `classes()` dict."""
    return (f"fresh {c['input']:,} · cache write {c['cacheWrite']:,} · cache read {c['cacheRead']:,} · "
            f"output {c['output']:,}")


def fmt_total(c: dict[str, Any]) -> str:
    """`113,735,785 tokens · $38.68 (reported)` for a `classes()` dict."""
    usd = "USD unknown" if c["usd"] is None else f"${c['usd']:,.2f} ({usd_label(c)})"
    return f"{c['tokens']:,} tokens · {usd}"


def status_field(profile_path: Path, ticket: str) -> dict[str, Any] | None:
    """The additive `measured` field of STATUS.md / `sdlc tickets`; None when the logs cannot be read."""
    try:
        data = rows(profile_path, ticket)
    except Exception:  # noqa: BLE001 — a status view never fails on a cost read
        return None
    return _status_from(data, ticket)


def _status_from(data: dict[str, Any], ticket: str) -> dict[str, Any]:
    mine = [r for r in data["rows"] if r["ticket"] == ticket]
    tot = _total(mine)
    reported = sum(v.get("tokens", 0) for v in (data["reported"].get(ticket) or {}).values())
    units = _total(r for r in mine if r["kind"] in ("unit", "dispatch"))
    return {"tokens": tot["tokens"], "usd": tot["usd"], "source": tot["source"], "sources": tot["sources"],
            "unpricedTokens": tot["unpricedTokens"],
            "reportedTokens": reported, "ratio": round(units["tokens"] / reported, 3) if reported else None}


def all_status_fields(profile_path: Path) -> dict[str, dict[str, Any]]:
    """`measured` for every ticket from one read of the all-tickets scope (for `sdlc tickets`)."""
    try:
        data = rows(profile_path, None)
    except Exception:  # noqa: BLE001
        return {}
    return {t: _status_from(data, t) for t in data["tickets"]}


# --- text output -------------------------------------------------------------------------------------------

def fmt_tokens(n: int | None) -> str:
    return "—" if n is None else f"{n:,}"


def fmt_usd(total: dict[str, Any]) -> str:
    return f"${total['usd']:,.4f} ({usd_label(total)})" if total["usd"] is not None else "USD unknown"


def usd_label(total: dict[str, Any]) -> str:
    """`reported`, `priced, unverified`, `reported + priced (unverified)`, plus `; N tokens unpriced`."""
    parts = [{"reported": "reported", "priced": "priced, unverified"}[s] for s in ("reported", "priced")
             if s in (total.get("sources") or [])]
    tail = f"; {total['unpricedTokens']:,} tokens unpriced" if total["unpricedTokens"] else ""
    return (" + ".join(parts) or "unknown") + tail


def render_text(rep: dict[str, Any]) -> str:
    lines = []
    w = rep["window"]
    span = (f" · {w['since']}" + (f" .. {w['until']}" if w["until"] and w["until"] != w["since"] else "")) if w["since"] else ""
    t = rep["total"]
    if rep.get("ticket"):
        lines.append(f"{rep['ticket']}{' / ' + rep['node'] if rep.get('node') else ''}{span}: "
                     f"{fmt_tokens(t['tokens'])} tokens measured · {fmt_usd(t)}")
        lines.append(f"  units {fmt_tokens(rep['units']['tokens'])} vs reported {fmt_tokens(rep['reported']['tokens'])}"
                     + (f" (x{rep['ratio']})" if rep["ratio"] is not None else "")
                     + f" · controller {fmt_tokens(rep['controller']['tokens'])}")
        lines.append("")
        lines.append(f"{'node':<22}{'measured':>16}{'units':>14}{'reported':>12}  usd")
        for n in rep["nodes"]:
            if not n["measured"]["tokens"] and not n["reported"]["tokens"] and not n["measured"]["unknownSteps"]:
                continue
            lines.append(f"{str(n['node'] or '(no node)'):<22}{fmt_tokens(n['measured']['tokens']):>16}"
                         f"{fmt_tokens(n['units']['tokens']):>14}{fmt_tokens(n['reported']['tokens']):>12}  {fmt_usd(n['measured'])}")
    else:
        lines.append(f"all tickets{span}: {fmt_tokens(t['tokens'])} tokens measured · {fmt_usd(t)}")
        for x in rep["tickets"]:
            lines.append(f"  {x['ticket']:<24}{fmt_tokens(x['tokens']):>16}  {fmt_usd(x)}")
    lines.append("")
    lines.append(f"{'agent / model':<36}{'input':>12}{'output':>12}{'cache read':>14}{'cache write':>13}  usd")
    for m in rep["byModel"]:
        lines.append(f"{(m['agent'] + ' / ' + str(m['model'] or '?')):<36}{m['input']:>12,}{m['output']:>12,}"
                     f"{m['cacheRead']:>14,}{m['cacheWrite']:>13,}  {fmt_usd(m)}")
    p = rep["prices"]
    lines.append("")
    if not p["exists"]:
        lines.append(f"prices: no price file at {p['file']} (sdlc cost --init-prices writes a template); "
                     "USD shown is only what the agents reported")
    else:
        lines.append(f"prices: {p['file']}" + (f" — {p['error']}" if p["error"] else ""))
        for u in p["used"]:
            lines.append(f"  {u['model']} -> {u['entry']}: in {u['input']} out {u['output']} cache-read {u['cacheRead']} "
                         f"cache-write {u['cacheWrite']} USD/Mtok · {'verified' if u['verified'] else 'UNVERIFIED'}")
        if p["missing"]:
            lines.append("  no price: " + ", ".join(p["missing"]))
    lines.append("  " + p["note"])
    return "\n".join(lines)
