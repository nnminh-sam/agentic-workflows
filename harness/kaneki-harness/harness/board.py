"""Human views of ticket state: one summary dict per ticket, rendered as STATUS.md (for the editor) and served as JSON
to the web board (`sdlc web`). Read-only: nothing here writes state.json or decisions.jsonl."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .contract import CONTRACT_VERSION
from .errors import HarnessError

HIDDEN_EVENTS = {"state-write"}
STATUS_FILE = "STATUS.md"


def summarize(s: Any, events_limit: int = 40, detail: bool = False) -> dict[str, Any]:
    """Summary of one loaded Session (cli.Session).

    `detail=True` (only `sdlc ticket-detail`) adds the app's `TicketDetail` extras: top-level `trace`, `loopCap`,
    `generationMax`, `loopBacks`, and the `NodeCard` keys on every node (`_node_detail`). The default output is the one
    STATUS.md, `sdlc tickets` and the web board use; it does not pay for the extras."""
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
    # One decision-log read serves every waiting() call of this ticket (problems is passed through).
    wait = waiting(s, problems=problems)
    waits = {n["node"]: waiting(s, n["node"], problems=problems) for n in nodes}
    strip = [{"node": n["node"], "status": n["status"], "verdict": n["verdict"], "generation": n["generation"],
              "boundary": n["boundary"], "waiting": waits[n["node"]]} for n in nodes]
    summary = {
        "ticket": s.ctx["ticket"], "title": _title(s), "kind": s.ctx["kind"], "lane": G.lane_of(s.ctx),
        "reviewRound": s.ctx["reviewRound"], "contractVersion": CONTRACT_VERSION,
        "ticketDir": str(s.ticket.dir), "createdAt": s.state.get("createdAt"),
        "updatedAt": events[-1]["at"] if events else s.state.get("createdAt"),
        "settled": sum(1 for n in s.order if settled(s.state, n)), "traceLength": len(s.order),
        "nextPending": nxt, "next": next_hint(s, nxt), "tokensTotal": tokens_total,
        "waitingOn": wait["on"], "waitingNode": wait["node"], "waitingReason": wait["reason"],
        "lastEventAt": shown[-1]["at"] if shown else None, "lastEventKind": shown[-1]["event"] if shown else None,
        "nodes": nodes, "strip": strip,
        "repositories": [{"id": r["id"], "path": r["path"], "worktree": r.get("worktree"), "base": r.get("baseBranch")}
                         for r in extra],
        "questions": {"path": str(questions), "exists": questions.is_file()},
        "receipts": s.state.get("receipts") or {}, "shipped": s.state.get("shipped") or {},
        "events": shown[max(0, len(shown) - events_limit):], "integrity": problems or None,
    }
    if detail:
        for n in nodes:
            n.update(_node_detail(s, n["node"], waits[n["node"]]))
        summary.update({
            "trace": list(s.order), "loopCap": s.profile.get("gates", {}).get("loopCap", 2),
            "generationMax": max((s.state["nodes"][n]["generation"] for n in s.order), default=1),
            "loopBacks": _loop_backs(s, events),
        })
    return summary


def _node_detail(s: Any, node: str, wait: dict[str, Any]) -> dict[str, Any]:
    """The `NodeCard` extras of one node (P1 plan §4.2). `wait` is this node's `waiting()` result, computed once."""
    from .state import missing_prerequisites
    from .usage import node_usage

    spec = s.spec(node)
    rec = s.state["nodes"][node]
    planned = []
    for u in s.units(node):
        tier, why = s.tier(node, u["id"])  # escalation included, as `sdlc plan` prints it
        row: dict[str, Any] = {"id": u["id"], "role": u.get("role"), "tier": tier, "model": s.model(tier)}
        if why:
            row["escalated"] = {"from": u["tier"], "why": why}
        planned.append(row)
    usage = node_usage(rec)
    return {
        "plannedUnits": planned,
        "unitRecords": [dict(u) for u in rec["units"] if u["generation"] == rec["generation"]],
        "agents": usage["agents"], "tokensAllGenerations": usage["allGenerations"],
        "tokensEarlier": usage["earlierGenerations"],
        "loopBackTo": spec.get("loopBackTo"), "reopens": spec.get("reopens"), "gates": list(spec.get("gates", [])),
        "missing": missing_prerequisites(s.graph, s.order, s.state, node),
        "waiting": wait, "resultFiles": _result_files(s, node),
    }


def _result_files(s: Any, node: str) -> list[dict[str, Any]]:
    """Existing `results/<node>.g<gen>.<unit>.r<n>.md` files of every generation (naming: cli.cmd_packet), oldest
    generation first, then unit, then round."""
    folder = s.ticket.dir / "results"
    if not folder.is_dir():
        return []
    pattern = re.compile(re.escape(node) + r"\.g(\d+)\.(.+)\.r(\d+)\.md")
    found = []
    for path in folder.iterdir():
        m = pattern.fullmatch(path.name)
        if m and path.is_file():
            found.append({"name": path.name, "path": str(path), "generation": int(m.group(1)), "unit": m.group(2),
                          "round": int(m.group(3))})
    return sorted(found, key=lambda f: (f["generation"], f["unit"], f["round"]))


def _loop_backs(s: Any, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One `{at, node, stale, reason, byGateVerdict, byNode}` per `invalidate` event. The gate that caused it logs its
    own event right after (cli.cmd_gate: `_invalidate` first, then the gate): a `needs-changes` gate on the node that
    loops back to the reopened node, or an `approved` gate on a node that `reopens` (triage-review). A manual
    `sdlc invalidate` has no such gate: both null.

    The following gate alone is not proof: a triage gate closed `--reopen none` invalidates nothing, so a manual
    invalidate just before it would look caused by it. The invalidate's reason must also carry the text that gate
    writes (cli.cmd_gate: `<node> NEEDS CHANGES...` / `review round <n>:...`)."""
    out = []
    for i, e in enumerate(events):
        if e.get("event") != "invalidate":
            continue
        after = next((x for x in events[i + 1:] if x.get("event") not in HIDDEN_EVENTS), None)
        by_verdict = by_node = None
        if after and after.get("event") == "gate":
            gate_node = after.get("node") or ""
            spec = s.graph["nodes"].get(gate_node, {})
            reason = str(e.get("reason") or "")
            if ((after.get("verdict") == "needs-changes" and spec.get("loopBackTo") == e.get("node")
                 and reason.startswith(f"{gate_node} NEEDS CHANGES"))
                    or (after.get("verdict") == "approved" and spec.get("reopens")
                        and reason.startswith("review round "))):
                by_verdict, by_node = after["verdict"], gate_node
        out.append({"at": e.get("at"), "node": e.get("node"), "stale": list(e.get("stale") or []),
                    "reason": e.get("reason"), "byGateVerdict": by_verdict, "byNode": by_node})
    return out


def _title(s: Any) -> str | None:
    """First `# ` line of the ticket text minus a leading `<ticket>:`: `ticket.md` in the ticket folder (full-lane
    intake copies it there), else the local-file tracker's `{dir}/{ticket}.md` next to the profile; None when neither."""
    candidates = [s.ticket.dir / "ticket.md"]
    tracker = s.profile.get("tracker") or {}
    if tracker.get("adapter") == "local-file":
        folder = (tracker.get("config") or {}).get("dir") or "tickets"
        candidates.append(s.pdir / folder / f"{s.ctx['ticket']}.md")
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# "):
                text = line[2:].strip()
                prefix = s.ctx["ticket"] + ":"
                return (text[len(prefix):] if text.startswith(prefix) else text).strip() or None
        return None
    return None


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


WAITING_REASONS = ("integrity", "done", "shipped", "rejected-open", "loop-cap", "reviewer-fail", "needs-run", "running",
                   "user-boundary", "awaiting-gate")
FAILING_VERDICTS = ("FAIL", "NEEDS CHANGES")


def waiting(s: Any, node: str | None = None, problems: list[str] | None = None) -> dict[str, Any]:
    """Who the ticket waits on: `{"on": "user"|"agent"|"none", "node": str|None, "reason": str}`. Read-only.

    `node` defaults to the next pending node N. The rules are tried in this order; the first match wins:

    1. the decision log fails its hash chain                         -> none,  integrity
    2. no pending node (or the given node is done)                   -> none,  shipped if the ticket shipped, else done
    3. N's verdict is `rejected` (not yet invalidated)                -> user,  rejected-open
    4. N loops back, its last unit verdict is FAIL / NEEDS CHANGES
       and `attempts + 1 >= gates.loopCap` (G4 needs --actor user)   -> user,  loop-cap
    5. N has a reviewer whose last verdict is FAIL (every unit ok)   -> agent, reviewer-fail (the controller re-records
                                                                        or re-dispatches the worker; no user decision)
    6. N's round is not complete                                      -> agent, running if in_progress, else needs-run
    7. N closes on the user's approval (not a plan auto-skip)         -> user,  user-boundary
    8. otherwise                                                      -> agent, awaiting-gate (the controller closes it)

    Explicit decisions (3-5) come before "work left to do" (6) because the gate refuses the normal close in each case;
    loop-cap comes before 6 because a staged review that FAILs its first unit never runs the next one.

    `problems` is the result of `s.ticket.verify_log()` when the caller already has it (summarize reads the log once
    for every node of a ticket); None reads the log here.

    A round is complete when every planned unit (reviewer included) has an `ok` record at the current generation, or,
    on a loop-back node, when the last recorded unit verdict is FAIL / NEEDS CHANGES (the controller closes it
    needs-changes). A node without units (lite scope/change, inline nodes, deliverable-ready) never reaches
    in_progress, so its round is complete when every artifact exists with live text; one without artifacts is
    complete as soon as it is next.
    """
    from .cli import live_text
    from .state import next_pending

    n = node if node is not None else next_pending(s.state, s.order)
    if problems is None:
        _, problems = s.ticket.verify_log()
    if problems:
        return {"on": "none", "node": n, "reason": "integrity"}
    if n is None or s.state["nodes"][n]["status"] == "done":
        return {"on": "none", "node": n, "reason": "shipped" if s.state.get("shipped") else "done"}

    spec = s.spec(n)
    rec = s.state["nodes"][n]
    current = [u for u in rec["units"] if u["generation"] == rec["generation"]]
    planned = [u["id"] for u in s.units(n)]
    last_verdict = next((u.get("verdict") for u in reversed(current) if u.get("verdict")), None)
    loops_back = bool(spec.get("loopBackTo"))
    cap = s.profile.get("gates", {}).get("loopCap", 2)

    if rec["verdict"] == "rejected":
        return {"on": "user", "node": n, "reason": "rejected-open"}
    if loops_back and last_verdict in FAILING_VERDICTS and rec["attempts"] + 1 >= cap:
        return {"on": "user", "node": n, "reason": "loop-cap"}

    if planned:
        done = {u["unit"] for u in current if u["status"] == "ok"}
        complete = set(planned) <= done or (loops_back and last_verdict in FAILING_VERDICTS)
        reviews = [u for u in current if u["unit"] == "reviewer"]
        if complete and reviews and reviews[-1].get("verdict") == "FAIL":
            return {"on": "agent", "node": n, "reason": "reviewer-fail"}
    else:
        paths = [s.ticket.dir / a for a in s.artifacts(n)]  # the gate's own test (cli.cmd_gate: absent, not_rewritten)
        complete = all(p.is_file() and (p.suffix != ".md" or live_text(p).strip()) for p in paths)
    if not complete:
        return {"on": "agent", "node": n, "reason": "running" if rec["status"] == "in_progress" else "needs-run"}

    boundary = spec["boundary"]
    if boundary == "user" and n in ("fix-plan", "feature-plan") and s.profile.get("gates", {}).get("autoSkipPlanApproval"):
        boundary = "controller"
    if boundary == "user":
        return {"on": "user", "node": n, "reason": "user-boundary"}
    return {"on": "agent", "node": n, "reason": "awaiting-gate"}


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
                # contractVersion goes once on the wrapper (cmd_tickets, /api/tickets), not on every item.
                brief = {k: v for k, v in full.items()
                         if k not in ("nodes", "events", "receipts", "repositories", "contractVersion")}
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
