"""Human views of ticket state: one summary dict per ticket, rendered as STATUS.md (for the editor) and served as JSON
to the web board (`sdlc web`). Read-only: nothing here writes state.json or decisions.jsonl."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import CONTRACT_VERSION
from .errors import HarnessError

HIDDEN_EVENTS = {"state-write"}
STATUS_FILE = "STATUS.md"
PROFILE_HOST = "profile"  # a unit recorded on the profile binding stores no `host` (cli.record_unit)


def unit_host(u: dict[str, Any]) -> str:
    """The agent (host) a unit record ran on."""
    return u.get("host") or PROFILE_HOST


def ticket_hosts(state: dict[str, Any]) -> tuple[str | None, list[str]]:
    """`(host, hosts)`: the agent of the most recently recorded unit (any node, any generation), and every agent
    that recorded a unit on this ticket in first-seen order. `(None, [])` before the first record."""
    units = sorted((u for rec in (state.get("nodes") or {}).values() for u in rec.get("units", [])),
                   key=lambda u: u.get("at") or "")
    seen: list[str] = []
    for u in units:
        if unit_host(u) not in seen:
            seen.append(unit_host(u))
    return (unit_host(units[-1]) if units else None), seen


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
            "models": sorted({u["model"] for u in current}), "hosts": sorted({unit_host(u) for u in current}),
            "tokens": toks, "closedAt": rec.get("closedAt"),
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
    host, hosts = ticket_hosts(s.state)
    flying = in_flight(s)
    summary = {
        "ticket": s.ctx["ticket"], "host": host, "hosts": hosts, "title": _title(s), "kind": s.ctx["kind"], "lane": G.lane_of(s.ctx),
        "reviewRound": s.ctx["reviewRound"], "contractVersion": CONTRACT_VERSION,
        "ticketDir": str(s.ticket.dir), "createdAt": s.state.get("createdAt"),
        "updatedAt": events[-1]["at"] if events else s.state.get("createdAt"),
        "settled": sum(1 for n in s.order if settled(s.state, n)), "traceLength": len(s.order),
        "nextPending": nxt, "next": next_hint(s, nxt) + (DECIDE_HINT if wait["on"] == "user" else ""),
        "tokensTotal": tokens_total,
        "waitingOn": wait["on"], "waitingNode": wait["node"], "waitingReason": wait["reason"],
        "lastEventAt": shown[-1]["at"] if shown else None, "lastEventKind": shown[-1]["event"] if shown else None,
        "nodes": nodes, "strip": strip, "inFlight": flying,
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
        row: dict[str, Any] = {"id": u["id"], "role": u.get("role"), "tier": tier, "model": s.model(tier),
                               "host": s.execution(node, u["id"], tier)["host"]}
        if why:
            row["escalated"] = {"from": u["tier"], "why": why}
        planned.append(row)
    usage = node_usage(rec)
    return {
        "plannedUnits": planned,
        "unitRecords": [{**u, "host": unit_host(u)} for u in rec["units"] if u["generation"] == rec["generation"]],
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


DECIDE_HINT = " (or run `sdlc decide`)"


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


def gate_request(s: Any, node: str | None = None) -> dict[str, Any]:
    """What the gate on `node` (default: the next pending node N) would accept, as the desktop app's `GateRequest`.
    Read-only: it runs the same checks `sdlc gate` runs (cli.gate_readiness, shared) but closes nothing.

    `readiness` is the approved gate as the user would call it (`--actor user`, no `--reopen`); `controllerReadiness` is
    the same gate as the controller. Every choice carries both views (`allowed`/`whyNot`, `controllerAllowed`/
    `controllerWhyNot`) and the `argv` of the `sdlc` call that makes it. A node that `reopens` has one approve view per
    `--reopen` target. No node to ask about gives `{contractVersion, ticket, node: None}`."""
    from .cli import _effective_boundary, node_inputs, readiness_views
    from .state import next_pending

    n = node if node is not None else next_pending(s.state, s.order)
    if n is None:
        return {"contractVersion": CONTRACT_VERSION, "ticket": s.ctx["ticket"], "node": None}
    spec = s.spec(n)
    rec = s.state["nodes"][n]
    ticket = s.ctx["ticket"]
    _, log_problems = s.ticket.verify_log()
    wait = waiting(s, n, problems=log_problems)
    memo: dict[str, Any] = {}  # one commit-rule / receipts check per request, however many views ask for it
    current = [u for u in rec["units"] if u["generation"] == rec["generation"]]
    reviewer = next((u for u in reversed(current) if u["unit"] == "reviewer"), None)
    bare = readiness_views(s, n, "approved", None, memo)
    boundary = _effective_boundary(s, n)
    return {
        "contractVersion": CONTRACT_VERSION, "ticket": ticket, "node": n, "generation": rec["generation"],
        "attempts": rec["attempts"], "loopCap": s.profile.get("gates", {}).get("loopCap", 2), "boundary": boundary,
        "gates": list(spec.get("gates", [])), "question": _gate_question(s, n, spec, rec, boundary, wait["reason"]),
        "waitingReason": wait["reason"],
        "readiness": {"ok": not bare["user"], "problems": bare["user"]},
        "controllerReadiness": {"ok": not bare["controller"], "problems": bare["controller"]},
        "reviewer": None if reviewer is None else {
            "unit": reviewer["unit"], "model": reviewer["model"], "tier": reviewer["tier"],
            "verdict": reviewer.get("verdict"), "failureClass": reviewer.get("failureClass"), "at": reviewer["at"],
            "resultFile": reviewer.get("result")},
        "units": [dict(u) for u in current],
        "evidence": _gate_evidence(s, n, reviewer, node_inputs(s, n)),
        "ngItems": [],
        "choices": _gate_choices(s, n, spec, bare, memo),
    }


def _gate_question(s: Any, node: str, spec: dict[str, Any], rec: dict[str, Any], boundary: str, reason: str) -> str:
    """The sentence the gate card asks. No spec fixes the wording; the loop-cap and approval cases follow the gate
    messages (cli.cmd_gate G4 / G0)."""
    if reason == "loop-cap":
        return f"NEEDS CHANGES again after {rec['attempts'] + 1} rounds: loop once more, reopen earlier, or stop?"
    if boundary == "user":
        return "Approve the plan?" if node in ("fix-plan", "feature-plan") else "Proceed, re-run or stop?"
    return spec["purpose"]


def _gate_fields(user: list[str], controller: list[str]) -> dict[str, Any]:
    """`allowed`/`whyNot` for the user and `controllerAllowed`/`controllerWhyNot` for the controller, from the problems
    each would meet (the first one is the reason)."""
    fields: dict[str, Any] = {"allowed": not user}
    if user:
        fields["whyNot"] = user[0]
    fields["controllerAllowed"] = not controller
    if controller:
        fields["controllerWhyNot"] = controller[0]
    return fields


def _gate_evidence(s: Any, node: str, reviewer: dict[str, Any] | None, inputs: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The files behind the decision: the node's artifacts, the last reviewer result, the inputs it read. `headings`
    are the `#` lines of the live text (what a rerun left below the last SUPERSEDED line)."""
    from .cli import live_text

    def entry(name: str, path: Path, source: str) -> dict[str, Any]:
        exists = path.is_file()
        headings = []
        if exists and path.suffix == ".md":
            headings = [line.strip() for line in live_text(path).splitlines() if line.startswith("#")]
        return {"name": name, "path": str(path), "exists": exists, "source": source, "headings": headings}

    out = [entry(a, s.ticket.dir / a, "artifact") for a in s.artifacts(node)]
    if reviewer and reviewer.get("result"):
        out.append(entry(Path(reviewer["result"]).name, Path(reviewer["result"]), "result"))
    out += [entry(Path(i["file"]).name, Path(i["file"]), "input") for i in inputs]
    return out


def _gate_choices(s: Any, node: str, spec: dict[str, Any], bare: dict[str, Any], memo: dict[str, Any]) -> list[dict[str, Any]]:
    """approve, needs-changes (only with `loopBackTo`), reject, stop and reopen, each with its `sdlc gate` argv."""
    from .cli import readiness_views, stale_preview, superseded_preview
    from .state import settled

    def argv(status: str, *extra: str) -> list[str]:
        return ["gate", "--ticket", s.ctx["ticket"], "--node", node, "--status", status, "--actor", "user", *extra]

    def choice(cid: str, label: str, fields: dict[str, Any], args: list[str], **more: Any) -> dict[str, Any]:
        return {"id": cid, "label": label, **fields, "argv": args, "reopenTargets": more.pop("reopenTargets", []),
                "staleIfChosen": more.pop("stale", []), "supersededIfChosen": more.pop("superseded", []),
                "needsNote": more.pop("needsNote", False), "needsReason": more.pop("needsReason", False), **more}

    settled_before = [n for n in s.order[:s.order.index(node)] if settled(s.state, n)]
    approve_fields = _gate_fields(bare["user"], bare["controller"])
    targets: list[dict[str, Any]] = []
    if spec.get("reopens"):  # one approve view per --reopen target; nothing is preselected
        for target in settled_before + ["none"]:
            view = readiness_views(s, node, "approved", target, memo)
            targets.append({"node": target, "staleNodes": [] if target == "none" else stale_preview(s, target),
                            **_gate_fields(view["user"], view["controller"]),
                            "argv": argv("approved", "--reopen", target)})
        ok_user = any(t["allowed"] for t in targets)
        ok_controller = any(t["controllerAllowed"] for t in targets)
        approve_fields = {"allowed": ok_user}
        if not ok_user:
            approve_fields["whyNot"] = bare["user"][0]
        approve_fields["controllerAllowed"] = ok_controller
        if not ok_controller:
            approve_fields["controllerWhyNot"] = bare["controller"][0]
    choices = [choice("approve", "Approve", approve_fields, argv("approved"), reopenTargets=targets)]

    back = spec.get("loopBackTo")
    if back:
        view = readiness_views(s, node, "needs-changes", None, memo)
        choices.append(choice("needs-changes", "Needs changes", _gate_fields(view["user"], view["controller"]),
                              argv("needs-changes"), stale=stale_preview(s, back),
                              superseded=superseded_preview(s, back), needsNote=True))
    verdict_only = readiness_views(s, node, "rejected", None, memo)  # rejected and stopped check the same two things
    fields = _gate_fields(verdict_only["user"], verdict_only["controller"])
    choices.append(choice("reject", "Reject", fields, argv("rejected"), needsNote=True))
    choices.append(choice("stop", "Stop", fields, argv("stopped")))
    choices.append(choice("reopen", "Reopen an earlier node", fields, argv("rejected"), needsNote=True, needsReason=True,
                          followUp=[["invalidate", "--ticket", s.ctx["ticket"], "--node", "<node>", "--reason", "<required>"]],
                          reopenTargets=[{"node": n, "staleNodes": stale_preview(s, n)} for n in settled_before]))
    return choices


# --- in flight -----------------------------------------------------------------------------------------------

# The unit-contract line of the packet template (playbooks/packet-template.md): what the packet was rendered with.
PACKET_HEAD_RE = re.compile(r"tier \*\*(?P<tier>[^*\n]+)\*\*, host \*\*(?P<host>[^*\n]+)\*\*, model \*\*(?P<model>[^*\n]+)\*\*")
PACKET_HEAD_BYTES = 4096


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(microsecond=0).isoformat()


def in_flight(s: Any, now: float | None = None) -> list[dict[str, Any]]:
    """Units dispatched but not recorded yet, in trace order: a packet `packets/<node>.g<gen>.<unit>.r<round>.md`
    (cli.build_packet) rendered at the node's current generation whose round has no record. Read-only.

    `build_packet` numbers a round as 1 + the unit's records at that generation, so round R is recorded once the unit
    has R records there. A packet of an earlier generation (superseded by invalidate / needs-changes), of a node that
    is done, or of a node no longer in the trace is not in flight. Per unit only the highest open round is listed.

    tier / host / model are the ones the packet was rendered with (its unit-contract line); a packet without that line
    falls back to the current plan. `now` (epoch seconds) adds `ageSeconds`; without it the result is deterministic,
    as STATUS.md and the ticket summary need."""
    from .sessions import split_dispatch

    folder = s.ticket.dir / "packets"
    try:
        entries = list(folder.iterdir()) if folder.is_dir() else []
    except OSError:
        return []
    latest: dict[tuple[str, str], tuple[dict[str, Any], Path]] = {}
    for path in entries:
        if path.suffix != ".md" or not path.is_file():
            continue
        ident = split_dispatch(path.stem, s.order)
        if not ident or ident["node"] not in s.order:
            continue
        rec = s.state["nodes"][ident["node"]]
        if rec["status"] == "done" or ident["generation"] != rec["generation"]:
            continue
        recorded = sum(1 for u in rec["units"] if u["unit"] == ident["unit"] and u["generation"] == rec["generation"])
        if recorded >= ident["round"]:
            continue
        key = (ident["node"], ident["unit"])
        if key not in latest or ident["round"] > latest[key][0]["round"]:
            latest[key] = (ident, path)

    out = []
    for (node, unit), (ident, path) in latest.items():
        try:
            mtime = path.stat().st_mtime
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(PACKET_HEAD_BYTES)
        except OSError:
            continue
        try:
            role = s.unit(node, unit).get("role")
        except HarnessError:
            role = None  # the graph no longer plans this unit; still a packet nobody recorded
        m = PACKET_HEAD_RE.search(head)
        if m:
            tier, host, model = m.group("tier"), m.group("host"), m.group("model")
        elif role is not None:
            tier = s.tier(node, unit)[0]
            exe = s.execution(node, unit, tier)
            host, model = exe["host"], exe["model"]
        else:
            tier = host = model = None
        dispatch = path.stem
        result = s.ticket.dir / "results" / f"{dispatch}.md"
        row: dict[str, Any] = {
            "ticket": s.ctx["ticket"], "node": node, "unit": unit, "role": role, "generation": ident["generation"],
            "round": ident["round"], "dispatchId": dispatch, "tier": tier, "model": model, "host": host,
            "packetPath": str(path), "packetAt": _iso(mtime), "resultPath": str(result),
            "resultExists": result.is_file(),
        }
        if now is not None:
            row["ageSeconds"] = max(0, int(now - mtime))
        out.append(row)
    out.sort(key=lambda r: (s.order.index(r["node"]), r["unit"]))
    return out


def ticket_lock(ticket_dir: Path, now: float | None = None) -> dict[str, Any] | None:
    """The writer holding the ticket lock (state.Ticket.lock: `.lock/owner.json`), or None. Read-only; a lock without
    a readable owner file (being taken, or a crash) still counts, aged by the folder's mtime."""
    lock = ticket_dir / ".lock"
    if not lock.is_dir():
        return None
    try:
        info = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    if not isinstance(info, dict):
        info = {}
    try:
        since = float(info.get("since") or lock.stat().st_mtime)
    except (OSError, TypeError, ValueError):
        return None
    out: dict[str, Any] = {"command": info.get("command"), "pid": info.get("pid"), "since": _iso(since)}
    if now is not None:
        out["ageSeconds"] = max(0, int(now - since))
    return out


def inflight_report(profile: Path, now: float | None = None) -> dict[str, Any]:
    """Across every ticket under the profile: the units in flight, the tickets waiting on the user and the ticket locks
    held, for the board's in-flight dock (`GET /api/inflight`). Read-only.

    `tickets` lists each ticket that has a unit in flight, waits on the user, or is locked, with its last event time;
    `needsYou` counts the ones waiting on the user. A ticket that fails to load is listed with its `error`."""
    import time as _time
    from .cli import Session, ticket_root
    from .profile import load_profile

    now = _time.time() if now is None else now
    root = ticket_root(load_profile(profile), profile.parent)
    units: list[dict[str, Any]] = []
    tickets: list[dict[str, Any]] = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not (d / "state.json").is_file():
            continue
        lock = ticket_lock(d, now)
        try:
            s = Session(argparse.Namespace(profile=str(profile), ticket=d.name))
        except HarnessError as exc:
            tickets.append({"ticket": d.name, "error": str(exc), "lock": lock, "inFlight": 0, "waitingOn": None,
                            "waitingNode": None, "waitingReason": None, "lastEventAt": None, "lastEventKind": None})
            continue
        flying = in_flight(s, now)
        units += flying
        wait = waiting(s)
        if not (flying or lock or wait["on"] == "user"):
            continue
        events, _ = s.ticket.verify_log()
        shown = [e for e in events if e.get("event") not in HIDDEN_EVENTS]
        tickets.append({
            "ticket": d.name, "title": _title(s), "waitingOn": wait["on"], "waitingNode": wait["node"],
            "waitingReason": wait["reason"], "inFlight": len(flying), "lock": lock,
            "lastEventAt": shown[-1]["at"] if shown else None, "lastEventKind": shown[-1]["event"] if shown else None,
        })
    units.sort(key=lambda u: u["packetAt"])
    tickets.sort(key=lambda t: t.get("lastEventAt") or "", reverse=True)
    return {"contractVersion": CONTRACT_VERSION, "generatedAt": _iso(now), "units": units, "tickets": tickets,
            "needsYou": sum(1 for t in tickets if t["waitingOn"] == "user")}


# --- one node ------------------------------------------------------------------------------------------------

def node_view(s: Any, node: str) -> dict[str, Any]:
    """One node of one ticket for the board's node inspector (`GET /api/node`). Read-only.

    `units`: one row per planned unit at the current generation (planned tier/model/host, the last record's verdict,
    status, model, host and result, tokens and rounds recorded at this generation, and whether a round is in flight);
    `records`: every unit record of every generation; `events`: the decision events about this node (record, gate,
    invalidate, host-switch, ...; an invalidate elsewhere that made it stale included); `gateRequest`: the
    `gate_request` view when the node waits on the user, else None. The `NodeCard` keys of `sdlc ticket-detail` are
    included as well."""
    spec = s.spec(node)  # HarnessError when the node is not in this ticket's trace
    rec = s.state["nodes"][node]
    events, problems = s.ticket.verify_log()
    wait = waiting(s, node, problems=problems)
    detail = _node_detail(s, node, wait)
    flying = {r["unit"]: r for r in in_flight(s) if r["node"] == node}
    current = [u for u in rec["units"] if u["generation"] == rec["generation"]]
    units = []
    for planned in detail["plannedUnits"]:
        mine = [u for u in current if u["unit"] == planned["id"]]
        last = mine[-1] if mine else None
        units.append({
            "unit": planned["id"], "role": planned["role"], "tier": planned["tier"], "model": planned["model"],
            "host": planned["host"], "escalated": planned.get("escalated"), "generation": rec["generation"],
            "rounds": len(mine), "tokens": sum(u.get("tokens") or 0 for u in mine),
            "status": last["status"] if last else None, "verdict": last.get("verdict") if last else None,
            "recordedModel": last["model"] if last else None, "recordedHost": unit_host(last) if last else None,
            "result": last.get("result") if last else None,
            "resultExists": bool(last and last.get("result") and Path(last["result"]).is_file()),
            "at": last.get("at") if last else None, "inFlight": flying.get(planned["id"]),
        })
    arts = [{"name": a, "path": str(s.ticket.dir / a), "exists": (s.ticket.dir / a).is_file()}
            for a in s.artifacts(node) + spec.get("optionalArtifacts", [])]
    about = [e for e in events if e.get("event") not in HIDDEN_EVENTS
             and (e.get("node") == node or node in (e.get("stale") or []) or e.get("previousNode") == node)]
    return {
        "contractVersion": CONTRACT_VERSION, "ticket": s.ctx["ticket"], "node": node, "layer": spec["layer"],
        "purpose": spec["purpose"], "status": rec["status"], "generation": rec["generation"], "verdict": rec["verdict"],
        "attempts": rec["attempts"], "boundary": spec["boundary"], "closedAt": rec.get("closedAt"),
        "units": units, "records": [{**u, "host": unit_host(u)} for u in rec["units"]],
        "artifacts": arts, "events": about, "inFlight": list(flying.values()),
        "gateRequest": gate_request(s, node) if wait["on"] == "user" else None,
        **detail,
    }


def summarize_ticket(profile: Path, ticket: str) -> dict[str, Any]:
    from .cli import Session
    try:
        s = Session(argparse.Namespace(profile=str(profile), ticket=ticket))
    except HarnessError as exc:
        return {"ticket": ticket, "error": str(exc)}
    return summarize(s)


def list_tickets(profile: Path, measured: bool = False) -> list[dict[str, Any]]:
    """Every initialised ticket under the profile's ticketRoot, newest activity first, without node detail.
    `measured=True` (`sdlc tickets`) adds each ticket's measured usage (cost.status_field), from one cached read."""
    from .cli import ticket_root
    from .profile import load_profile
    root = ticket_root(load_profile(profile), profile.parent)
    fields: dict[str, Any] = {}
    if measured:
        from . import cost
        fields = cost.all_status_fields(profile)
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if (d / "state.json").is_file():
                full = summarize_ticket(profile, d.name)
                # contractVersion goes once on the wrapper (cmd_tickets, /api/tickets), not on every item.
                brief = {k: v for k, v in full.items()
                         if k not in ("nodes", "events", "receipts", "repositories", "contractVersion")}
                if measured and "error" not in brief:
                    brief["measured"] = fields.get(d.name)
                out.append(brief)
    out.sort(key=lambda t: t.get("updatedAt") or "", reverse=True)
    return out


ICON = {"done": "✅", "in_progress": "🔄", "stale": "♻️", "pending": "⏳"}


def render_status_md(summary: dict[str, Any], mismatched_nodes: set[str] | None = None) -> str:
    """STATUS.md: what the ticket looks like right now, with relative links an editor can follow."""
    t = summary
    tdir = Path(t["ticketDir"])
    mismatched = mismatched_nodes or set()
    lines = [
        f"# {t['ticket']} · {t['kind']} · lane `{t['lane']}`",
        "",
        "> Written by the driver after every change. Do not edit: it is regenerated.",
        "",
        f"**Progress:** {t['settled']}/{t['traceLength']} nodes · **Next:** {t['next']} · **Tokens:** {t['tokensTotal']}"
        + (f" · **Agent:** {t['host']}" if t.get("host") else "")
        + (f" (also {', '.join(h for h in t['hosts'] if h != t['host'])})" if len(t.get("hosts") or []) > 1 else "")
        + (f" · **Review round:** {t['reviewRound']}" if t["reviewRound"] else ""),
        "",
    ]
    if t.get("integrity"):
        lines += ["> ⚠️ **Integrity problem:** " + "; ".join(t["integrity"]), ""]
    lines += ["| # | Node | Status | Verdict | Who | Agent | Model | Tokens | Artifacts |",
              "|---|------|--------|---------|-----|-------|-------|--------|-----------|"]
    for i, n in enumerate(t["nodes"], 1):
        arts = ", ".join(f"[{a['name']}]({_rel(a['path'], tdir)})" for a in n["artifacts"] if a["exists"]) or "—"
        who = ("you approve · " if n["boundary"] == "user" else "") + ("inline" if n["executor"] == "controller" else
                                                                       f"{len(n['unitsDone'])}/{len(n['units'])} units")
        verdict = n["verdict"] or n["lastUnitVerdict"] or ""
        gen = f" (gen {n['generation']})" if n["generation"] > 1 else ""
        model_col = ", ".join(n["models"]) or "—"
        if n["node"] in mismatched and model_col != "—":
            model_col += " ⚠"
        agent_col = ", ".join(n.get("hosts") or []) or "—"
        lines.append(f"| {i} | {n['node']}{gen} | {ICON.get(n['status'], '')} {n['status']} | {verdict} | {who} | "
                     f"{agent_col} | {model_col} | {n['tokens'] or ''} | {arts} |")
    if mismatched:
        lines += ["", "> ⚠️ **Model verification:** one or more current-round units ran on an inherited or unverified host model."]
    if t.get("inFlight"):
        lines += ["", "**In flight** (packet rendered, no result recorded): " + "; ".join(
            f"{r['node']}/{r['unit']} r{r['round']} on {r['host'] or '?'} ({r['model'] or '?'}) since "
            f"`{r['packetAt'][:19].replace('T', ' ')}`" for r in t["inFlight"])]
    m = t.get("measured")
    if m:
        from .cost import usd_label
        usd = "USD unknown" if m["usd"] is None else f"${m['usd']:,.2f} ({usd_label(m)})"
        lines += ["", f"**Measured** (agents' own logs, each API message once): {m['tokens']:,} tokens · {usd}"
                  + f" · reported {m['reportedTokens']:,}" + (f" (x{m['ratio']})" if m.get("ratio") is not None else "")
                  + " · `sdlc cost --ticket " + t["ticket"] + "`"]
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
    for key in ("node", "unit", "host", "verdict", "actor", "lane", "from", "to", "repo"):
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
    mismatched = {
        node for node, rec in (s.state.get("nodes") or {}).items()
        if any(u.get("generation") == rec.get("generation") and u.get("modelMismatch") for u in rec.get("units", []))
    }
    summary = summarize(s)
    from . import cost
    summary["measured"] = cost.status_field(s.ppath, s.ctx["ticket"])  # cached by log fingerprint; None if unreadable
    path.write_text(render_status_md(summary, mismatched_nodes=mismatched), encoding="utf-8", newline="\n")
    return path


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")
