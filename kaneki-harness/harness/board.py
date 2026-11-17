"""Human views of ticket state: one summary dict per ticket, rendered as STATUS.md (for the editor) and served as JSON
to the web board (`sdlc web`). Read-only: nothing here writes state.json or decisions.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .errors import HarnessError

HIDDEN_EVENTS = {"state-write"}
STATUS_FILE = "STATUS.md"


def summarize(s: Any, events_limit: int = 40) -> dict[str, Any]:
    """Summary of one loaded Session (cli.Session)."""
    from . import graph as G
    from .state import next_pending, settled

    nodes = []
    tokens_total = 0
    for node in s.order:
        spec = s.spec(node)
        rec = s.state["nodes"][node]
        current = [u for u in rec["units"] if u["generation"] == rec["generation"]]
        toks = sum(u.get("tokens") or 0 for u in current)
        tokens_total += toks
        arts = []
        for name in s.artifacts(node) + spec.get("optionalArtifacts", []):
            path = s.ticket.dir / name
            arts.append({"name": name, "path": str(path), "exists": path.is_file()})
        planned = [u["id"] for u in spec.get("units", [])] + (["reviewer"] if spec.get("reviewer") else [])
        nodes.append({
            "node": node, "layer": spec["layer"], "purpose": spec["purpose"], "status": rec["status"],
            "generation": rec["generation"], "verdict": rec["verdict"], "attempts": rec["attempts"],
            "boundary": spec["boundary"], "executor": "controller" if not planned else "subagents",
            "units": planned, "unitsDone": sorted({u["unit"] for u in current if u["status"] == "ok"}),
            "lastUnitVerdict": next((u.get("verdict") for u in reversed(current) if u.get("verdict")), None),
            "models": sorted({u["model"] for u in current}), "tokens": toks, "closedAt": rec.get("closedAt"),
            "artifacts": arts,
        })
    nxt = next_pending(s.state, s.order)
    events, problems = s.ticket.verify_log()
    shown = [e for e in events if e.get("event") not in HIDDEN_EVENTS]
    extra = [p for p in (s.state["context"].get("repositories") or [])]
    questions = s.ticket.dir / "questions.md"
    return {
        "ticket": s.ctx["ticket"], "kind": s.ctx["kind"], "lane": G.lane_of(s.ctx), "reviewRound": s.ctx["reviewRound"],
        "ticketDir": str(s.ticket.dir), "createdAt": s.state.get("createdAt"),
        "updatedAt": events[-1]["at"] if events else s.state.get("createdAt"),
        "settled": sum(1 for n in s.order if settled(s.state, n)), "traceLength": len(s.order),
        "nextPending": nxt, "next": next_hint(s, nxt), "tokensTotal": tokens_total, "nodes": nodes,
        "repositories": [{"id": r["id"], "path": r["path"], "worktree": r.get("worktree"), "base": r.get("baseBranch")}
                         for r in extra],
        "questions": {"path": str(questions), "exists": questions.is_file()},
        "receipts": s.state.get("receipts") or {}, "shipped": s.state.get("shipped") or {},
        "events": shown[-events_limit:], "integrity": problems or None,
    }


def next_hint(s: Any, node: str | None) -> str:
    if node is None:
        return "done: ship with /kaneki-ship" if not s.state.get("shipped") else "shipped"
    spec = s.spec(node)
    rec = s.state["nodes"][node]
    planned = [u["id"] for u in spec.get("units", [])] + (["reviewer"] if spec.get("reviewer") else [])
    done = {u["unit"] for u in rec["units"] if u["generation"] == rec["generation"] and u["status"] == "ok"}
    if planned and set(planned) <= done or (not planned and rec["status"] == "in_progress"):
        return f"{node}: work recorded, waiting for " + ("your approval" if spec["boundary"] == "user" else "the gate")
    if rec["status"] in ("in_progress",):
        return f"{node}: running ({len(done)}/{len(planned)} units recorded)"
    who = "controller does it inline" if not planned else f"{len(planned)} subagent unit(s)"
    return f"{node}: next ({who})" + (" — stale, rerun" if rec["status"] == "stale" else "")


def summarize_ticket(profile: Path, ticket: str) -> dict[str, Any]:
    from .cli import Session
    try:
        s = Session(argparse.Namespace(profile=str(profile), ticket=ticket))
    except HarnessError as exc:
        return {"ticket": ticket, "error": str(exc)}
    return summarize(s)


def list_tickets(profile: Path) -> list[dict[str, Any]]:
    """Every initialised ticket under the profile's ticketRoot, newest activity first, without node detail."""
    from .cli import ticket_root
    from .profile import load_profile
    root = ticket_root(load_profile(profile), profile.parent)
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if (d / "state.json").is_file():
                full = summarize_ticket(profile, d.name)
                brief = {k: v for k, v in full.items() if k not in ("nodes", "events", "receipts", "repositories")}
                if "nodes" in full:
                    brief["strip"] = [{"node": n["node"], "status": n["status"], "verdict": n["verdict"]} for n in full["nodes"]]
                out.append(brief)
    out.sort(key=lambda t: t.get("updatedAt") or "", reverse=True)
    return out


ICON = {"done": "✅", "in_progress": "🔄", "stale": "♻️", "pending": "⏳"}


def render_status_md(summary: dict[str, Any]) -> str:
    """STATUS.md: what the ticket looks like right now, with relative links an editor can follow."""
    t = summary
    tdir = Path(t["ticketDir"])
    lines = [
        f"# {t['ticket']} · {t['kind']} · lane `{t['lane']}`",
        "",
        "> Written by the driver after every change. Do not edit: it is regenerated.",
        "",
        f"**Progress:** {t['settled']}/{t['traceLength']} nodes · **Next:** {t['next']} · **Tokens:** {t['tokensTotal']}"
        + (f" · **Review round:** {t['reviewRound']}" if t["reviewRound"] else ""),
        "",
    ]
    if t.get("integrity"):
        lines += ["> ⚠️ **Integrity problem:** " + "; ".join(t["integrity"]), ""]
    lines += ["| # | Node | Status | Verdict | Who | Model | Tokens | Artifacts |",
              "|---|------|--------|---------|-----|-------|--------|-----------|"]
    for i, n in enumerate(t["nodes"], 1):
        arts = ", ".join(f"[{a['name']}]({_rel(a['path'], tdir)})" for a in n["artifacts"] if a["exists"]) or "—"
        who = ("you approve · " if n["boundary"] == "user" else "") + ("inline" if n["executor"] == "controller" else
                                                                       f"{len(n['unitsDone'])}/{len(n['units'])} units")
        verdict = n["verdict"] or n["lastUnitVerdict"] or ""
        gen = f" (gen {n['generation']})" if n["generation"] > 1 else ""
        lines.append(f"| {i} | {n['node']}{gen} | {ICON.get(n['status'], '')} {n['status']} | {verdict} | {who} | "
                     f"{', '.join(n['models']) or '—'} | {n['tokens'] or ''} | {arts} |")
    if (tdir / "token-usage.md").is_file():
        lines += ["", "**Token usage per node and agent:** [token-usage.md](token-usage.md)"]
    if t["questions"]["exists"]:
        lines += ["", f"**Open questions:** [questions.md]({_rel(t['questions']['path'], tdir)})"]
    lines += ["", "## Recent decisions", ""]
    for e in reversed(t["events"][-12:]):
        lines.append(f"- `{e['at'][:19].replace('T', ' ')}` **{e['event']}** " + _event_text(e))
    return "\n".join(lines) + "\n"


def _event_text(e: dict[str, Any]) -> str:
    parts = []
    for key in ("node", "unit", "verdict", "actor", "lane", "from", "repo"):
        if e.get(key):
            parts.append(f"{key}={e[key]}")
    if e.get("stale"):
        parts.append("stale=" + ",".join(e["stale"]))
    for key in ("note", "reason"):
        if e.get(key):
            parts.append(f"— {str(e[key])[:160]}")
    return " ".join(parts)


def _rel(path: str, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def write_status(s: Any) -> Path:
    path = s.ticket.dir / STATUS_FILE
    path.write_text(render_status_md(summarize(s)), encoding="utf-8", newline="\n")
    return path


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")
