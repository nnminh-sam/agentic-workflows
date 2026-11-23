"""Learning (M4·1): the reflect node's signals, its mode, and the proposal format.

Decision D10: learning output needs the user's approval before it changes anything, and the harness never rewrites
its own graph, gates or driver. The reflect node therefore only PRODUCES proposals (`<ticketDir>/proposals.json`,
checked against contracts/proposal.schema.json at its gate). Applying them (`sdlc learn apply / revert`), lessons in
packets and the cross-ticket retro belong to the next track (M4·2 / M4·3), which reads the formats defined here.

Signals are what went wrong on a ticket, read from the hash-chained decision log, the node records and two ticket
files. Every signal has a stable `id`; each closed reflect gate logs the ids it `covered`, so a later round (a PR review
restarts reflect) only counts what is new. `learning.reflect: on-signal` (the default) closes reflect without a unit
when nothing new went wrong; `off` drops reflect from the trace; `always` always dispatches its unit.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import schema

REFLECT = "reflect"
MODES = ("always", "on-signal", "off")
DEFAULT_MODE = "on-signal"
PROPOSAL_TYPES = ("lesson", "rule", "profile", "harness")
PROPOSALS_FILE = "proposals.json"
TIER_RANK = {"light": 0, "routine": 1, "deep": 2}
FAILING = ("FAIL", "NEEDS CHANGES")
# signal kinds, in the order the reflect unit reads them
KINDS = ("loopBacks", "invalidations", "laneChanges", "hostSwitches", "tierEscalations", "reviewerFails",
         "gateFirings", "questions", "prFindings", "citationWarnings")


def reflect_mode(profile: dict[str, Any]) -> str:
    return (profile.get("learning") or {}).get("reflect") or DEFAULT_MODE


def _short(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:10]


def _clip(text: Any, n: int = 240) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --- signals -----------------------------------------------------------------------------------------

def collect(s: Any) -> dict[str, Any]:
    """Every signal of the ticket loaded in Session `s`, each marked `new` unless an earlier reflect close covered it.
    Read-only. `s` is a cli.Session (ticket, state, ctx, graph, order, profile)."""
    events, problems = s.ticket.verify_log()
    covered: set[str] = set()
    last_close = None
    for i, e in enumerate(events, 1):
        if e.get("event") == "gate" and e.get("node") == REFLECT and e.get("verdict") == "approved":
            covered.update(e.get("covered") or [])
            last_close = i
    sig: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
    nodes = s.graph["nodes"]
    for i, e in enumerate(events, 1):
        kind, node = e.get("event"), e.get("node")
        if node == REFLECT:
            continue  # reflect never reflects on itself
        base = {"id": f"ev:{i}", "ref": f"decisions.jsonl#{i}", "at": e.get("at")}
        if kind == "gate":
            if e.get("verdict") == "needs-changes":
                sig["loopBacks"].append({**base, "node": node, "loopBackTo": nodes.get(node, {}).get("loopBackTo"),
                                         "generation": e.get("generation"), "note": _clip(e.get("note"))})
            if e.get("gate") and e.get("gate") != "G0":
                sig["gateFirings"].append({**base, "gate": e["gate"], "node": node, "actor": e.get("actor"),
                                           "verdict": e.get("verdict"), "note": _clip(e.get("note"))})
            if e.get("citationWarnings"):
                sig["citationWarnings"].append({**base, "node": node, "generation": e.get("generation"),
                                                "files": e["citationWarnings"]})
        elif kind == "invalidate":
            reason = str(e.get("reason") or "")
            cause = ("loop-back" if " NEEDS CHANGES" in reason.split(":", 1)[0] else
                     "review-round" if reason.startswith("review round ") else "manual")
            sig["invalidations"].append({**base, "node": node, "stale": list(e.get("stale") or []), "cause": cause,
                                         "reason": _clip(reason)})
        elif kind == "lane":
            sig["laneChanges"].append({**base, "from": e.get("from"), "to": e.get("lane"), "reason": _clip(e.get("reason"))})
        elif kind == "host-switch":
            sig["hostSwitches"].append({**base, "node": node, "unit": e.get("unit"), "from": e.get("from"),
                                        "to": e.get("to"), "previousNode": e.get("previousNode")})
        elif kind == "record":
            if e.get("verdict") in FAILING:
                sig["reviewerFails"].append({**base, "node": node, "unit": e.get("unit"), "verdict": e["verdict"],
                                             "failureClass": e.get("failureClass"), "generation": e.get("generation")})
            planned = planned_tier(s, node, e.get("unit"))
            got = e.get("tier")
            if planned and got in TIER_RANK and TIER_RANK[got] > TIER_RANK[planned]:
                sig["tierEscalations"].append({**base, "node": node, "unit": e.get("unit"), "planned": planned,
                                               "recorded": got, "model": e.get("model")})
    sig["questions"] = answered_high_questions(s.ticket.dir / "questions.md")
    sig["prFindings"] = accepted_findings(s, events)
    new = 0
    for items in sig.values():
        for item in items:
            item["new"] = item["id"] not in covered
            new += item["new"]
    return {
        "ticket": s.ctx["ticket"], "reviewRound": s.ctx.get("reviewRound", 0), "mode": reflect_mode(s.profile),
        "sinceEvent": last_close, "newSignals": new, "empty": new == 0,
        "counts": {k: len(v) for k, v in sig.items()},
        "attempts": attempts(s), "signals": sig,
        "cost": None,  # slot: cost / budget signals arrive with M3 cost (measured vs reported tokens, budget refusals)
        "logIntegrity": problems or None,
    }


def new_ids(report: dict[str, Any]) -> list[str]:
    return [i["id"] for items in report["signals"].values() for i in items if i.get("new")]


def planned_tier(s: Any, node: str | None, unit_id: str | None) -> str | None:
    """The tier a unit was planned on (graph tier with `profile.nodeOverrides`), before review-failure escalation."""
    spec = s.graph["nodes"].get(node or "")
    if not spec:
        return None
    unit = next((u for u in spec.get("units", []) if u["id"] == unit_id), None)
    if unit is None and unit_id == "reviewer" and spec.get("reviewer"):
        unit = {"id": "reviewer", "tier": spec["reviewer"]["tier"]}
    if unit is None:
        return None
    p_node = (s.profile.get("nodeOverrides") or {}).get(node) or {}
    p_unit = (p_node.get("units") or {}).get(unit_id) or {}
    return p_unit.get("tier") or p_node.get("tier") or unit["tier"]


def attempts(s: Any) -> dict[str, Any]:
    """Per node that ran more than once: generation, loop-back attempts, recorded units, failing unit records."""
    out = {}
    for node in s.order:
        if node == REFLECT:
            continue
        rec = s.state["nodes"].get(node) or {}
        units = rec.get("units") or []
        if rec.get("generation", 1) > 1 or rec.get("attempts", 0) > 0:
            out[node] = {"generation": rec.get("generation"), "attempts": rec.get("attempts", 0), "units": len(units),
                         "failing": sum(1 for u in units if u.get("verdict") in FAILING)}
    return out


HEADING_RE = re.compile(r"^#{1,6}\s")
HIGH_RE = re.compile(r"\bHIGH\b")
ANSWERED_RE = re.compile(r"\bANSWERED\b", re.I)


def answered_high_questions(path: Path) -> list[dict[str, Any]]:
    """HIGH questions the user answered, from `questions.md`: a line (list item, table row) carrying both `HIGH` and
    `ANSWERED`, or a heading section that carries both somewhere in it. Best effort; the file has no fixed format."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    out: list[dict[str, Any]] = []
    hit_lines = {n for n, line in enumerate(lines, 1) if HIGH_RE.search(line) and ANSWERED_RE.search(line)}
    for n in sorted(hit_lines):
        out.append({"id": f"q:{_short(lines[n - 1])}", "ref": f"questions.md:{n}", "text": _clip(lines[n - 1])})
    starts = [n for n, line in enumerate(lines, 1) if HEADING_RE.match(line)] + [len(lines) + 1]
    for a, b in zip(starts, starts[1:]):
        block = lines[a - 1:b - 1]
        if any(a <= n < b for n in hit_lines):
            continue
        text = "\n".join(block)
        if HIGH_RE.search(text) and ANSWERED_RE.search(text):
            out.append({"id": f"q:{_short(text)}", "ref": f"questions.md:{a}", "text": _clip(lines[a - 1])})
    return sorted(out, key=lambda q: int(q["ref"].rsplit(":", 1)[1]))


ACCEPT_RE = re.compile(r"\bACCEPT(ED)?\b")
REOPEN_RE = re.compile(r"^\s*\**Reopen:?\**\s*:?\s*`?([a-z0-9-]+)`?", re.M | re.I)


def accepted_findings(s: Any, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accepted PR review findings of the current round (live text of `triage.md`), with the node triage reopened."""
    rnd = s.ctx.get("reviewRound", 0)
    path = s.ticket.dir / "triage.md"
    if rnd < 1 or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max((n for n, line in enumerate(lines, 1) if line.startswith("## SUPERSEDED:")), default=0)
    gate = next((e for e in reversed(events) if e.get("event") == "gate" and e.get("node") == "triage-review"
                 and e.get("verdict") == "approved"), None)
    reopened = (gate or {}).get("reopen")
    if reopened is None:
        found = REOPEN_RE.findall("\n".join(lines[start:]))
        reopened = found[-1] if found else None
    out = []
    for n in range(start + 1, len(lines) + 1):
        line = lines[n - 1]
        if ACCEPT_RE.search(line) and not line.lstrip().startswith("#"):
            out.append({"id": f"rc:{rnd}:{_short(line)}", "ref": f"triage.md:{n}", "round": rnd, "text": _clip(line),
                        "reopened": reopened})
    return out


# --- proposals ---------------------------------------------------------------------------------------

FENCE_RE = re.compile(r"```|~~~")
TICK_RE = re.compile(r"`([^`\n]+)`")
COMMANDS = {"sudo", "rm", "del", "rmdir", "mv", "cp", "curl", "wget", "git", "gh", "glab", "npm", "npx", "yarn", "pnpm",
            "pip", "pipx", "python", "python3", "py", "node", "bash", "sh", "zsh", "pwsh", "powershell", "cmd", "sdlc",
            "make", "docker", "kubectl", "chmod", "chown", "ssh", "scp", "composer", "php", "artisan", "flutter", "dart",
            "go", "cargo", "dotnet", "mvn", "gradle", "claude", "codex", "gemini", "agy", "opencode", "zed", "set",
            "export", "eval", "exec", "invoke-expression", "iex", "start-process", "remove-item"}
TOOL_RE = re.compile(r"\bmcp__\w+|\b(Bash|PowerShell|WebFetch|WebSearch|NotebookEdit|TodoWrite|ToolSearch|"
                     r"SendMessage|AskUserQuestion)\b|\b(Read|Write|Edit|Glob|Grep|Agent|Task)\s+tool\b")
TOOL_NAMES = {"Read", "Write", "Edit", "Glob", "Grep", "Agent", "Task", "Bash", "PowerShell"}
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
UNC_RE = re.compile(r"\\\\[\w.$-]+\\")
ABS_RE = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]|~[\\/]|/(?:etc|usr|var|home|tmp|opt|bin|Users|root)\b)")
PATH_RE = re.compile(r"(?<![\w@:/\\.-])(?:\.{1,2}[\\/])?(?:[\w.*-]+[\\/])+[\w.*-]*")
FILE_RE = re.compile(r"^[\w.*-]+\.[A-Za-z0-9]{1,8}$")
ID_RE = re.compile(r"^decisions\.jsonl#(\d+)$")
CITE_RE = re.compile(r"^([A-Za-z0-9_.@/\\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?$")
TEXT_FIELDS = ("title", "body", "metric", "baseline")


def _path_like(token: str) -> bool:
    """A separator-bearing token is a path when it names a file (extension), a folder (trailing slash) or a glob;
    prose such as `and/or` or `PASS/FAIL` is not."""
    last = re.split(r"[\\/]", token.rstrip("\\/"))[-1]
    return token.endswith(("/", "\\")) or "*" in token or "." in last.strip(".")


def _in_scope(path: str, patterns: list[str]) -> bool:
    p = path.replace("\\", "/")
    p = p[2:] if p.startswith("./") else p
    for pat in patterns:
        q = pat.replace("\\", "/").rstrip("/")
        if p == q or p.rstrip("/") == q or p.startswith(q + "/") or fnmatch.fnmatchcase(p, q) or fnmatch.fnmatchcase(p, q + "/*"):
            return True
    return False


def _bad_scope_path(path: str) -> str | None:
    p = path.replace("\\", "/")
    if not p.strip():
        return "is empty"
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or p.startswith("~"):
        return "is absolute (scope paths are repository-relative)"
    if ".." in p.split("/"):
        return "leaves the repository (`..`)"
    if URL_RE.search(p):
        return "is a URL"
    return None


def text_problems(text: str, scope_paths: list[str], ticket_files: set[str]) -> list[str]:
    """Forbidden content in a proposal's prose: commands, tool names, destinations, paths outside scope. Heuristic
    (see playbooks/nodes/reflect.md): backticked spans and fenced blocks are judged strictly, plain prose loosely."""
    out = []
    if FENCE_RE.search(text):
        out.append("contains a code block (no commands in a proposal)")
    for line in text.splitlines():
        if re.match(r"^\s*(\$|>|PS [A-Z]:\\.*>)\s", line):
            out.append(f"contains a shell prompt line: {_clip(line, 60)!r}")
    for span in TICK_RE.findall(text):
        first = span.strip().split()[0].lower() if span.strip() else ""
        if first in COMMANDS and (len(span.split()) > 1 or first in ("sdlc", "git", "rm", "curl", "wget")):
            out.append(f"contains a command: `{_clip(span, 60)}`")
        elif re.search(r"(^|\s)--?[a-z][\w-]*", span) and len(span.split()) > 1:
            out.append(f"contains a command line with flags: `{_clip(span, 60)}`")
        elif span.strip() in TOOL_NAMES:
            out.append(f"names a tool: `{span.strip()}`")
    for m in TOOL_RE.finditer(text):
        out.append(f"names a tool: {m.group(0)!r}")
    for rx, what in ((URL_RE, "a URL"), (EMAIL_RE, "an address"), (IP_RE, "an IP address"), (UNC_RE, "a network share")):
        if rx.search(text):
            out.append(f"contains a destination ({what})")
    if ABS_RE.search(text):
        out.append("contains an absolute path")
    candidates = {m.group(0).rstrip(".,;:") for m in PATH_RE.finditer(text) if _path_like(m.group(0).rstrip(".,;:"))}
    candidates |= {s.strip() for s in TICK_RE.findall(text) if FILE_RE.match(s.strip()) or PATH_RE.fullmatch(s.strip())}
    for cand in sorted(candidates):
        c = cand.replace("\\", "/")
        c = c[2:] if c.startswith("./") else c
        if c.split("#")[0] in ticket_files or c.split(":")[0] in ticket_files:
            continue  # a ticket-folder file (evidence), not a target
        if ".." in c.split("/"):
            out.append(f"path {cand!r} leaves the repository")
        elif not _in_scope(c, scope_paths):
            out.append(f"path {cand!r} is outside scope.paths {scope_paths}")
    return list(dict.fromkeys(out))


def proposal_problems(text: str, ctx: dict[str, Any]) -> list[str]:
    """Gate check of `proposals.json`. ctx: ticket, nodes (graph node ids), stacks (known stack ids), logLines (lines
    in decisions.jsonl), roots (citation roots), ticketFiles (file names in the ticket folder)."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"not valid JSON ({exc.msg}, line {exc.lineno})"]
    if not isinstance(doc, dict):
        return ['must be an object {"schemaVersion": 1, "ticket": "<id>", "proposals": [...]}']
    out = []
    extra = sorted(set(doc) - {"schemaVersion", "ticket", "proposals"})
    if extra:
        out.append(f"unknown top-level keys {extra}")
    if doc.get("schemaVersion") != 1:
        out.append("schemaVersion must be 1")
    if doc.get("ticket") != ctx["ticket"]:
        out.append(f"ticket must be {ctx['ticket']!r}, not {doc.get('ticket')!r}")
    props = doc.get("proposals")
    if not isinstance(props, list):
        return out + ["proposals must be a list (empty when nothing is worth proposing)"]
    seen: set[str] = set()
    from . import checks as C
    for i, p in enumerate(props):
        where = f"proposals[{i}]" + (f" ({p.get('id')})" if isinstance(p, dict) and p.get("id") else "")
        if isinstance(p, dict) and p.get("type") not in PROPOSAL_TYPES:
            out.append(f"{where}: unknown type {p.get('type')!r} (types: {', '.join(PROPOSAL_TYPES)})")
        errs = schema.errors(p, "proposal.schema.json")
        out += [f"{where}: {e}" for e in errs if not (e.startswith("type") and "is not one of" in e)]
        if errs or not isinstance(p, dict):
            continue
        if p["id"] in seen:
            out.append(f"{where}: duplicate id")
        seen.add(p["id"])
        scope = p["scope"]
        unknown = [n for n in scope["nodes"] if n not in ctx["nodes"]]
        if unknown:
            out.append(f"{where}: scope.nodes not in the graph: {unknown}")
        stacks = [x for x in scope["stacks"] if x not in ctx["stacks"]]
        if stacks:
            out.append(f"{where}: scope.stacks unknown: {stacks} (known: {sorted(ctx['stacks'])})")
        for path in scope["paths"]:
            bad = _bad_scope_path(path)
            if bad:
                out.append(f"{where}: scope path {path!r} {bad}")
        own = [e for e in p["evidence"] if e["ticket"] == ctx["ticket"]]
        if not own:
            out.append(f"{where}: needs at least one evidence item from ticket {ctx['ticket']}")
        for e in own:
            ref = e["ref"].strip()
            m = ID_RE.match(ref)
            if m:
                if not 1 <= int(m.group(1)) <= ctx["logLines"]:
                    out.append(f"{where}: evidence {ref} is not a line of decisions.jsonl (1-{ctx['logLines']})")
            elif CITE_RE.match(ref):
                broken = C.citations(f"`{ref}`", ctx["roots"])
                if broken:
                    out.append(f"{where}: evidence {broken[0]}")
            else:
                out.append(f"{where}: evidence ref {ref!r} must be decisions.jsonl#<line> or <path>:<line>[-<line>]")
        for field in TEXT_FIELDS:
            if isinstance(p.get(field), str):
                out += [f"{where}.{field}: {x}" for x in text_problems(p[field], scope["paths"], ctx["ticketFiles"])]
    return out
