"""Budgets and gate G8 (plan M3·2): the profile's optional `budgets` block checked against measured usage.

    "budgets": {"ticket": {"usd": 40, "tokens": 60000000},
                "node": {"default": {"usd": 6, "tokens": 15000000}, "code-review": {"usd": 8}},
                "daily": {"usd": 60, "tokens": 120000000},
                "warnAt": 0.8, "action": "park"}

Every key is optional. No block means the built-in DEFAULTS below (A4: a runaway unit stops even in a profile that
never set budgets); an explicit empty block `"budgets": {}` turns every check off (and every cost read). A profile block
replaces the defaults as a whole. Scopes:
- ticket: everything measured for the ticket (units, dispatches, controller, side calls).
- node: the node's exact unit sessions plus the controller work attributed to it (cost.py's node attribution).
  `node.default` applies to every node; a node's own entry overrides it key by key.
- daily: everything measured on this machine's local calendar day, every ticket.

Node caps may also set `minutes`: wall-clock time since the node's current round started (its first packet of the
generation, `startedAt` in state). Over it, `sdlc packet` refuses with G8, the runner stops the session, `sdlc
dispatch` caps its timeout to what is left, and the packet tells the unit its deadline.

Usage comes from `cost.measured()` (the agents' own logs, served from cost.py's fingerprint cache), so `check` is
cheap enough for every runner poll. USD covers only `reported` + `priced` usage; when part of a scope is unpriced
(`unpricedTokens > 0`) the token cap governs that part: an unpriced remainder never refuses on its own, only a token
cap does, and the level is at least `warn` because the USD shown is partial. Known USD at or over a USD cap is over
(the unknown part can only add to it).

Levels: `ok`, `warn` (a scope reached `warnAt` of a cap, or USD is partial), `over` (a scope reached a cap). With
`action: "park"` (default) `over` refuses: `sdlc packet` fails with G8 and the runner parks / stops the node. With
`action: "warn"` nothing is refused. The user lets one node through with `sdlc budget --ticket T --allow-node N
--reason ... --actor user`: a `budget-override` decision event that covers that node's current generation (one
round) for the scopes that were over when it was recorded.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .errors import HarnessError

SCOPES = ("ticket", "node", "daily")
CAP_KEYS = ("usd", "tokens")
NODE_KEYS = (*CAP_KEYS, "minutes")
# Built-in budgets for a profile without a `budgets` block. feature-flags spent 23.6 M on coding and 32.3 M on evidence
# over 5 generations (2 abandoned evidence runs, 2 h); the target is <= 45 M per ticket and <= 2 generations per node.
DEFAULTS: dict[str, Any] = {
    "ticket": {"tokens": 60_000_000},
    "node": {"default": {"tokens": 15_000_000, "minutes": 120},
             "execute-and-evidence": {"tokens": 20_000_000, "minutes": 60}},
    "warnAt": 0.8,
    "action": "park",
}
DEFAULT_WARN_AT = 0.8
MEMO_SECONDS = 2.0  # one packet / status / board pass asks the same scope several times; cost.py caches the rest

_memo: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


# --- profile block -------------------------------------------------------------------------------------------

def effective(prof: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """(the budgets block that applies, "profile" | "default")."""
    block = prof.get("budgets")
    return (DEFAULTS, "default") if block is None else (block, "profile")


def enabled(prof: dict[str, Any]) -> bool:
    """False only for an explicit empty block: no caps, no cost reads."""
    block, _ = effective(prof)
    return bool({k: v for k, v in block.items() if k in SCOPES and v})


def _cap_errors(cap: Any, where: str, keys: tuple[str, ...] = CAP_KEYS) -> list[str]:
    if not isinstance(cap, dict):
        return [f"{where} must be an object like {{\"usd\": 6, \"tokens\": 15000000}}"]
    errs = [f"{where}.{k} is unknown (keys: {', '.join(keys)})" for k in cap if k not in keys]
    mins = cap.get("minutes")
    if mins is not None and (isinstance(mins, bool) or not isinstance(mins, (int, float)) or mins <= 0):
        errs.append(f"{where}.minutes must be a number > 0")
    usd, tok = cap.get("usd"), cap.get("tokens")
    if usd is not None and (isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd < 0):
        errs.append(f"{where}.usd must be a number >= 0")
    if tok is not None and (isinstance(tok, bool) or not isinstance(tok, int) or tok < 0):
        errs.append(f"{where}.tokens must be an integer >= 0")
    return errs


def config_errors(block: Any) -> list[str]:
    """Friendly messages for a profile `budgets` block (load_profile; the schema then checks it again)."""
    if not isinstance(block, dict):
        return ["budgets must be an object"]
    errs = [f"budgets.{k} is unknown (keys: ticket, node, daily, warnAt, action)" for k in block
            if k not in (*SCOPES, "warnAt", "action")]
    for scope in ("ticket", "daily"):
        if scope in block:
            errs += _cap_errors(block[scope], f"budgets.{scope}")
    if "node" in block:
        if not isinstance(block["node"], dict):
            errs.append("budgets.node must be an object: {\"default\": {...}, \"<node>\": {...}}")
        else:
            for name, cap in block["node"].items():
                errs += _cap_errors(cap, f"budgets.node.{name}", NODE_KEYS)
    warn = block.get("warnAt", DEFAULT_WARN_AT)
    if isinstance(warn, bool) or not isinstance(warn, (int, float)) or not 0 <= warn <= 1:
        errs.append("budgets.warnAt must be a number from 0 to 1")
    if block.get("action", "park") not in ("park", "warn"):
        errs.append("budgets.action must be park or warn")
    return errs


def caps_for(block: dict[str, Any], node: str | None) -> dict[str, dict[str, Any] | None]:
    """The caps that apply to one node of a ticket: {ticket, node, daily}, each {usd?, tokens?} or None."""
    def clean(cap: Any) -> dict[str, Any] | None:
        got = {k: cap[k] for k in NODE_KEYS if isinstance(cap, dict) and cap.get(k) is not None}
        return got or None
    nodes = block.get("node") or {}
    merged = {**(nodes.get("default") or {}), **((nodes.get(node) or {}) if node else {})}
    return {"ticket": clean(block.get("ticket")), "node": clean(merged) if node else None,
            "daily": clean(block.get("daily"))}


# --- usage -----------------------------------------------------------------------------------------------------

def _day(now: Any) -> date:
    if now is None:
        return date.today()
    if isinstance(now, datetime):
        return now.astimezone().date() if now.tzinfo else now.date()
    if isinstance(now, date):
        return now
    return datetime.fromtimestamp(float(now)).date()  # epoch seconds -> this machine's local day


def _measure(profile: Path, ticket: str | None, node: str | None, day: date | None) -> dict[str, Any]:
    from . import cost
    key = (str(profile), ticket, node, day)
    hit = _memo.get(key)
    t = time.monotonic()
    if hit and t - hit[0] < MEMO_SECONDS:
        return hit[1]
    m = cost.measured(profile, ticket, node, day)
    if len(_memo) > 256:
        _memo.clear()
    _memo[key] = (t, m)
    return m


def _usage(m: dict[str, Any]) -> dict[str, Any]:
    unpriced = int(m.get("unpricedTokens") or 0)
    return {"usd": m.get("usd"), "tokens": int(m.get("tokens") or 0), "unpricedTokens": unpriced,
            "partial": unpriced > 0}


def fmt_usd(v: float | None) -> str:
    return "$0.00" if v is None else f"${v:,.2f}"


# --- the check -------------------------------------------------------------------------------------------------

def _scope_verdict(scope: str, use: dict[str, Any], cap: dict[str, Any] | None, warn_at: float,
                   label: str) -> dict[str, Any]:
    level, reasons, ratios = "ok", [], {}
    if not cap:
        return {"level": "ok", "reasons": [], "ratios": {}}
    for key in (*CAP_KEYS, "minutes"):
        if cap.get(key) is None or (key == "minutes" and use.get("minutes") is None):
            continue
        used = (use["usd"] or 0.0) if key == "usd" else use[key]
        limit = cap[key]
        ratio = (used / limit) if limit else 1.0  # a zero cap is spent before anything runs
        ratios[key] = round(ratio, 4)
        shown = {"usd": lambda: f"{fmt_usd(used)} of {fmt_usd(limit)}", "tokens": lambda: f"{used:,} of {limit:,} tokens",
                 "minutes": lambda: f"{used:,.0f} of {limit:,} minutes since this round started"}[key]()
        if used >= limit:
            level = "over"
            reasons.append(f"{label}: {shown} ({ {'usd': 'USD', 'tokens': 'tokens', 'minutes': 'time'}[key]} cap reached)")
        elif ratio >= warn_at:
            level = "warn" if level == "ok" else level
            reasons.append(f"{label}: {shown} ({int(round(ratio * 100))}% of the cap)")
    if use["partial"] and cap.get("usd") is not None:
        level = "warn" if level == "ok" else level
        tail = ("the token cap governs that part" if cap.get("tokens") is not None
                else "no token cap, so that part is not capped")
        reasons.append(f"{label}: USD is partial, {use['unpricedTokens']:,} tokens unpriced ({tail})")
    return {"level": level, "reasons": reasons, "ratios": ratios}


def find_override(events: list[dict[str, Any]], node: str | None, generation: int | None) -> dict[str, Any] | None:
    """The user's budget-override of this node at its current generation (the last one), or None."""
    if not node:
        return None
    for ev in reversed(events):
        if (ev.get("event") == "budget-override" and ev.get("node") == node and ev.get("actor") == "user"
                and (generation is None or ev.get("generation") == generation)):
            return ev
    return None


def _ticket_events(profile: Path, prof: dict[str, Any], ticket: str, node: str | None) -> tuple[list[dict[str, Any]], int | None]:
    from .state import Ticket
    root = Path(prof["paths"]["ticketRoot"])
    root = root if root.is_absolute() else (Path(profile).parent / root).resolve()
    t = Ticket(root, ticket)
    try:
        events, _ = t.verify_log()
    except (OSError, ValueError):
        events = []
    gen = None
    try:
        gen = ((t.load().get("nodes") or {}).get(node) or {}).get("generation") if node else None
    except (OSError, ValueError, HarnessError):
        pass
    return events, gen


def node_started(rec: dict[str, Any] | None) -> float | None:
    """Epoch seconds when the node's current round started (its first packet of the generation), or None."""
    st = (rec or {}).get("startedAt") or {}
    if not isinstance(st, dict) or st.get("generation") != (rec or {}).get("generation") or not st.get("at"):
        return None
    try:
        return datetime.fromisoformat(str(st["at"]).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _started_from_state(profile: Path, prof: dict[str, Any], ticket: str, node: str) -> float | None:
    from .state import Ticket
    root = Path(prof["paths"]["ticketRoot"])
    root = root if root.is_absolute() else (Path(profile).parent / root).resolve()
    try:
        return node_started(((Ticket(root, ticket).load().get("nodes") or {}).get(node)))
    except (OSError, ValueError, HarnessError):
        return None


def _now_ts(now: Any) -> float:
    if isinstance(now, datetime):
        return now.timestamp()
    if isinstance(now, (int, float)) and not isinstance(now, bool):
        return float(now)
    return time.time()


def check(profile: Path | str, ticket: str, node: str | None = None, now: Any = None, *,
          prof: dict[str, Any] | None = None, events: list[dict[str, Any]] | None = None,
          generation: int | None = None, started: float | None = None) -> dict[str, Any]:
    """Budget verdict for starting (or continuing) `node` of `ticket`:

    {configured, allowed, level ok|warn|over, action, reasons[], over[scopes], usage {ticket, node, daily:
    {usd, tokens, unpricedTokens, partial}}, caps {ticket, node, daily}, warnAt, day, override}.

    `prof` / `events` / `generation` / `started` (epoch of the node's round start) spare a reload when the caller
    already has them. `source` says whether the caps are the profile's or the built-in DEFAULTS. Never raises on a cost read:
    usage that cannot be measured is a warning, not a refusal."""
    profile = Path(profile)
    if prof is None:
        from .profile import load_profile
        prof = load_profile(profile)
    block, source = effective(prof)
    day = _day(now)
    base: dict[str, Any] = {"configured": enabled(prof), "source": source, "ticket": ticket, "node": node,
                            "day": day.isoformat(), "allowed": True, "level": "ok", "reasons": [], "over": [],
                            "usage": {}, "caps": {}, "override": None}
    if not base["configured"]:
        return base
    warn_at = float(block.get("warnAt", DEFAULT_WARN_AT))
    action = block.get("action", "park")
    caps = caps_for(block, node)
    base.update(action=action, warnAt=warn_at, caps=caps)
    labels = {"ticket": f"ticket {ticket}", "node": f"node {node}", "daily": f"daily {day.isoformat()}"}
    args = {"ticket": (ticket, None, None), "node": (ticket, node, None), "daily": (None, None, day)}
    level_rank = {"ok": 0, "warn": 1, "over": 2}
    level = "ok"
    for scope in SCOPES:
        if scope == "node" and not node:
            continue
        try:
            use = _usage(_measure(profile, *args[scope]))
        except Exception as exc:  # noqa: BLE001 — a budget check never fails on an unreadable log
            base["reasons"].append(f"{labels[scope]}: usage could not be measured ({exc})")
            level = max(level, "warn", key=level_rank.get)
            continue
        base["usage"][scope] = use
        limit_min = (caps.get("node") or {}).get("minutes") if scope == "node" else None
        if limit_min:
            t0 = started if started is not None else _started_from_state(profile, prof, ticket, node)
            if t0 is not None:
                use["minutes"] = round(max(0.0, _now_ts(now) - t0) / 60.0, 1)
                base["deadline"] = datetime.fromtimestamp(t0 + limit_min * 60, timezone.utc).replace(
                    microsecond=0).isoformat()
                base["secondsLeft"] = max(0, int(t0 + limit_min * 60 - _now_ts(now)))
        v = _scope_verdict(scope, use, caps.get(scope), warn_at, labels[scope])
        base["reasons"] += v["reasons"]
        if caps.get(scope):
            use["ratios"] = v["ratios"]
            use["level"] = v["level"]
        if v["level"] == "over":
            base["over"].append(scope)
        level = max(level, v["level"], key=level_rank.get)
    base["level"] = level
    if level == "over" and action == "park":
        if events is None or (generation is None and node):
            ev2, gen2 = _ticket_events(profile, prof, ticket, node)
            events = ev2 if events is None else events
            generation = gen2 if generation is None else generation
        ov = find_override(events, node, generation)
        scopes = set((ov or {}).get("scopes") or [])
        if ov and ("all" in scopes or set(base["over"]) <= scopes):
            base["override"] = {k: ov.get(k) for k in ("at", "node", "generation", "scopes", "reason", "actor")}
        else:
            base["allowed"] = False
    return base


# --- messages ------------------------------------------------------------------------------------------------

def refusal(res: dict[str, Any]) -> str:
    """The G8 error: scope, used vs cap, and how to proceed."""
    over = [r for r in res["reasons"] if "cap reached" in r]
    node = res.get("node") or "<node>"
    scopes = ", ".join(f"budgets.{s}" + (f".{node} (or budgets.node.default)" if s == "node" else "")
                       for s in res["over"])
    return (f"G8: budget over for {res['ticket']}/{node}: " + "; ".join(over)
            + f". To proceed, raise {scopes} in the profile, or let this one node through: "
              f"sdlc budget --ticket {res['ticket']} --allow-node {node} --reason '<why>' --actor user "
              "(the user's decision; it covers this node's current round only). Or stop here.")


def warning(res: dict[str, Any]) -> str | None:
    if res.get("level") == "ok" or not res.get("reasons"):
        return None
    head = "G8 warning" if res["level"] == "warn" else ("G8: over budget (action warn)" if res.get("allowed") and not
                                                        res.get("override") else "G8: over budget, user override")
    return head + ": " + "; ".join(res["reasons"])


def line(res: dict[str, Any]) -> str | None:
    """One STATUS.md line, or None without a budgets block."""
    if not res or not res.get("configured"):
        return None
    parts = []
    for scope in SCOPES:
        cap, use = (res.get("caps") or {}).get(scope), (res.get("usage") or {}).get(scope)
        if not cap or not use:
            continue
        bits = []
        if cap.get("usd") is not None:
            bits.append(f"{fmt_usd(use['usd'])}/{fmt_usd(cap['usd'])}" + (" (partial)" if use["partial"] else ""))
        if cap.get("tokens") is not None:
            bits.append(f"{use['tokens']:,}/{cap['tokens']:,} tokens")
        if cap.get("minutes") is not None and use.get("minutes") is not None:
            bits.append(f"{use['minutes']:,.0f}/{cap['minutes']:,} min")
        parts.append(f"{scope} " + ", ".join(bits))
    icon = {"ok": "", "warn": "⚠️ ", "over": "⛔ "}[res["level"]]
    tail = (" · override by the user" if res.get("override") else "") + (
        " · default caps" if res.get("source") == "default" else "")
    return f"**Budget:** {icon}{res['level']}" + (f" · {'; '.join(parts)}" if parts else "") + tail
