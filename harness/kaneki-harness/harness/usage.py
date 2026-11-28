"""Token usage written back into the ticket's outputs.

- Each node's markdown artifacts end with a `## Token usage` block when the node closes: one row per agent (unit),
  with its model, rounds and tokens at the closing generation, and the node total. The block sits between markers
  so re-closing a node replaces it instead of stacking a second one; blocks above a `## SUPERSEDED:` line stay as
  the history of that round.
- `token-usage.md` in the ticket folder is the ticket-wide view: every node, every agent, all generations, totals.
  It is rewritten on every `record` and `gate`.

Drafts (`drafts/*.md`) and the PR reply are shipped to people outside the ticket, so they never get a block; their
usage is still in `token-usage.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BEGIN = "<!-- kaneki:token-usage -->"
END = "<!-- /kaneki:token-usage -->"
USAGE_FILE = "token-usage.md"
SUPERSEDED_HEADER = "## SUPERSEDED:"
EXTERNAL_NODES = {"respond-review"}  # its artifact is posted to the PR as-is
NL = chr(10)


def _fmt(n: int | None) -> str:
    return f"{n:,}" if n is not None else "—"


def agent_rows(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group unit records by agent (unit id + model), in first-seen order. `tokens` is None when no round of
    that agent reported any; `unreported` counts the rounds recorded without `--tokens`."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for u in units:
        row = rows.setdefault((u["unit"], u["model"]), {"unit": u["unit"], "model": u["model"], "rounds": 0,
                                                        "tokens": None, "unreported": 0})
        row["rounds"] += 1
        if u.get("tokens") is None:
            row["unreported"] += 1
        else:
            row["tokens"] = (row["tokens"] or 0) + u["tokens"]
    return list(rows.values())


def node_usage(rec: dict[str, Any], generation: int | None = None) -> dict[str, Any]:
    gen = rec["generation"] if generation is None else generation
    current = [u for u in rec["units"] if u["generation"] == gen]
    earlier = [u for u in rec["units"] if u["generation"] != gen]
    rows = agent_rows(current)
    return {"generation": gen, "agents": rows, "total": sum(r["tokens"] or 0 for r in rows),
            "unreported": sum(r["unreported"] for r in rows),
            "earlierGenerations": sum(u.get("tokens") or 0 for u in earlier),
            "allGenerations": sum(u.get("tokens") or 0 for u in rec["units"])}


def _table(rows: list[dict[str, Any]], with_node: bool = False) -> list[str]:
    head = ("| Node " if with_node else "") + "| Agent | Model | Rounds | Tokens |"
    sep = ("|---" if with_node else "") + "|---|---|---:|---:|"
    lines = [head, sep]
    for r in rows:
        toks = _fmt(r["tokens"]) + (f" ({r['unreported']} round(s) unreported)" if r["unreported"] else "")
        lines.append((f"| {r['node']} " if with_node else "") + f"| {r['unit']} | {r['model']} | {r['rounds']} | {toks} |")
    return lines


def node_block(node: str, usage: dict[str, Any]) -> str:
    lines = [BEGIN, "## Token usage", ""]
    if not usage["agents"]:
        lines.append(f"Done by the controller inline; no agent tokens recorded for `{node}`.")
    else:
        lines += _table(usage["agents"])
        lines += ["", f"**Node total:** {_fmt(usage['total'])} tokens (generation {usage['generation']})"]
        if usage["earlierGenerations"]:
            lines.append(f"**Earlier generations:** {_fmt(usage['earlierGenerations'])} tokens · "
                         f"**All generations:** {_fmt(usage['allGenerations'])} tokens")
        if usage["unreported"]:
            lines.append(f"_{usage['unreported']} round(s) were recorded without `--tokens`; the total is a lower bound._")
    lines.append(END)
    return NL.join(lines)


def strip_live_block(text: str) -> str:
    """Remove the usage block from the live round only (after the last SUPERSEDED line)."""
    head, sep, live = text.rpartition(SUPERSEDED_HEADER) if SUPERSEDED_HEADER in text else ("", "", text)
    if BEGIN in live and END in live:
        before, _, rest = live.partition(BEGIN)
        _, _, after = rest.partition(END)
        live = before.rstrip() + (NL + after.lstrip(NL) if after.strip() else NL)
    return head + sep + live


def write_node_block(path: Path, node: str, usage: dict[str, Any]) -> bool:
    if not path.is_file() or path.suffix != ".md":
        return False
    text = strip_live_block(path.read_text(encoding="utf-8")).rstrip()
    path.write_text(text + NL + NL + node_block(node, usage) + NL, encoding="utf-8")
    return True


def writes_block(node: str, artifact: str) -> bool:
    return artifact.endswith(".md") and not artifact.startswith("drafts/") and node not in EXTERNAL_NODES


def ticket_summary(order: list[str], nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out, total, agents = [], 0, {}
    for node in order:
        rec = nodes[node]
        u = node_usage(rec)
        out.append({"node": node, "status": rec["status"], **u})
        total += u["allGenerations"]
        for r in agent_rows(rec["units"]):
            a = agents.setdefault((r["unit"], r["model"]), {"unit": r["unit"], "model": r["model"], "rounds": 0,
                                                             "tokens": None, "unreported": 0})
            a["rounds"] += r["rounds"]
            a["unreported"] += r["unreported"]
            if r["tokens"] is not None:
                a["tokens"] = (a["tokens"] or 0) + r["tokens"]
    by_model: dict[str, int] = {}
    for a in agents.values():
        by_model[a["model"]] = by_model.get(a["model"], 0) + (a["tokens"] or 0)
    return {"nodes": out, "agents": list(agents.values()), "byModel": by_model, "total": total}


def ticket_tokens(profile_path: Path | None, ticket: str, order: list[str], nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The one ticket total that STATUS.md, token-usage.md and `sdlc status` show.

    `source: measured` — cost.ticket_totals: every API message of the units, the controller and side calls, once, from
    the agents' own logs, split into fresh / cache write / cache read / output, plus USD.
    `source: recorded` — no readable log (another agent's machine, a fresh ticket): the unit records' `tokens`, all
    generations, a lower bound with no class split and no USD.
    Either way `total.tokens` equals the sum of `nodes[n].tokens` (node "" is usage outside any node)."""
    from . import cost
    m = cost.ticket_totals(profile_path, ticket) if profile_path is not None else None
    if m and m["total"]["tokens"]:
        return {"source": "measured", **m}
    per = {n: sum(u.get("tokens") or 0 for u in nodes[n]["units"]) for n in order}
    return {"source": "recorded", "total": {"tokens": sum(per.values()), "usd": None},
            "nodes": {n: {"tokens": t} for n, t in per.items()},
            "reportedTokens": sum(cost.reported_tokens(u) or 0 for n in order for u in nodes[n]["units"])}


def total_line(tt: dict[str, Any]) -> str:
    """`113,735,785 tokens · $38.68 (reported)`, or `1,000 tokens (recorded by the agents, lower bound)`."""
    from . import cost
    if tt["source"] == "measured":
        return cost.fmt_total(tt["total"])
    return f"{_fmt(tt['total']['tokens'])} tokens (recorded by the agents, lower bound)"


def compact(tt: dict[str, Any]) -> dict[str, Any]:
    """The `tokens` field of `sdlc status`: source, total, class split and USD (classes only when measured)."""
    t = tt["total"]
    out = {"source": tt["source"], "total": t["tokens"], "usd": t.get("usd")}
    if tt["source"] == "measured":
        out.update({k: t[k] for k in ("input", "cacheWrite", "cacheRead", "output")},
                   units=tt["units"]["tokens"], controller=tt["controller"]["tokens"])
    return out


def write_ticket_file(ticket_dir: Path, ticket: str, order: list[str], nodes: dict[str, dict[str, Any]],
                      totals: dict[str, Any] | None = None) -> Path:
    """token-usage.md. `totals` is ticket_tokens(...); without it the unit records are the only source."""
    from . import cost
    tt = totals or ticket_tokens(None, ticket, order, nodes)
    summ = ticket_summary(order, nodes)
    measured = tt["source"] == "measured"
    lines = [f"# Token usage — {ticket}", "", "Written by the harness on every `record` and `gate`; do not edit.", "",
             f"**Ticket total:** {total_line(tt)} (all nodes, all generations"
             + ("; units + controller, measured from the agents' own logs, each API message once)" if measured else ")")]
    if measured:
        lines += ["", f"- Token classes: {cost.fmt_split(tt['total'])}",
                  f"- Units {_fmt(tt['units']['tokens'])} · controller {_fmt(tt['controller']['tokens'])}",
                  f"- The agents' own `--tokens` (last-turn context size, not consumption): {_fmt(tt['reportedTokens'])}",
                  f"- Details: `sdlc cost --ticket {ticket}`"]
        lines += ["", "## Per node", "", "| Node | Status | Generation | Units | Controller | Total | USD |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for name in order + [n for n in tt["nodes"] if n not in order]:
            n = tt["nodes"].get(name)
            rec = nodes.get(name)
            if n is None:
                continue
            usd = "—" if n["usd"] is None else f"${n['usd']:,.2f}"
            lines.append(f"| {name or '(outside any node)'} | {rec['status'] if rec else '—'} | "
                         f"{rec['generation'] if rec else '—'} | {_fmt(n['units']['tokens'])} | "
                         f"{_fmt(n['controller']['tokens'])} | {_fmt(n['tokens'])} | {usd} |")
    else:
        lines += ["", "## Per node", "", "| Node | Status | Generation | Agents | Current generation | All generations |",
                  "|---|---|---:|---:|---:|---:|"]
        for n in summ["nodes"]:
            lines.append(f"| {n['node']} | {n['status']} | {n['generation']} | {len(n['agents'])} | "
                         f"{_fmt(n['total']) if n['agents'] else '—'} | "
                         f"{_fmt(n['allGenerations']) if n['agents'] or n['allGenerations'] else '—'} |")
    detail = [{**r, "node": n["node"]} for n in summ["nodes"] for r in agent_rows(nodes[n["node"]]["units"])]
    lines += ["", "## Per agent (each node, all generations; measured per dispatch when its log was readable)", ""]
    lines += _table(detail, with_node=True) if detail else ["No agent results recorded yet."]
    gone = [(n, u) for n in order for u in nodes[n]["units"] if u.get("status") == "abandoned" or u.get("abandonedAttempts")]
    if gone:
        lines += ["", "## Abandoned dispatches", "", "| Node | Dispatch | Sessions given up | Tokens | Why |",
                  "|---|---|---:|---:|---|"]
        for n, u in gone:
            if u.get("status") == "abandoned":
                lines.append(f"| {n} | {u.get('dispatchId') or u['unit']} | "
                             f"{(u.get('tokensMeasured') or {}).get('sessions', '—')} | {_fmt(u.get('tokens'))} | "
                             f"{str(u.get('reason') or '').replace('|', '/')[:120]} |")
            else:
                att = u["abandonedAttempts"]
                lines.append(f"| {n} | {u.get('dispatchId') or u['unit']} | {len(att)} | "
                             f"{_fmt(sum(a.get('tokens') or 0 for a in att))} | the same packet was dispatched again |")
    if measured:
        lines += ["", "## Per model", "", "| Agent / model | Fresh | Cache write | Cache read | Output | Total | USD |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for m in tt["byModel"]:
            usd = "—" if m["usd"] is None else f"${m['usd']:,.2f}"
            lines.append(f"| {m['agent']} / {m['model'] or '?'} | {_fmt(m['input'])} | {_fmt(m['cacheWrite'])} | "
                         f"{_fmt(m['cacheRead'])} | {_fmt(m['output'])} | {_fmt(m['tokens'])} | {usd} |")
    else:
        lines += ["", "## Per model", "", "| Model | Tokens |", "|---|---:|"]
        lines += [f"| {m} | {_fmt(t)} |" for m, t in sorted(summ["byModel"].items())] or ["| — | — |"]
        unreported = sum(a["unreported"] for a in summ["agents"])
        if unreported:
            lines += ["", f"_{unreported} round(s) were recorded without `--tokens`; totals are a lower bound._"]
    path = ticket_dir / USAGE_FILE
    path.write_text(NL.join(lines) + NL, encoding="utf-8")
    return path
