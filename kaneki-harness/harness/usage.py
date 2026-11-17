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


def write_ticket_file(ticket_dir: Path, ticket: str, order: list[str], nodes: dict[str, dict[str, Any]]) -> Path:
    summ = ticket_summary(order, nodes)
    lines = [f"# Token usage — {ticket}", "", "Written by the harness on every `record` and `gate`; do not edit.", "",
             f"**Ticket total:** {_fmt(summ['total'])} tokens (all nodes, all generations)", "",
             "## Per node", "", "| Node | Status | Generation | Agents | Current generation | All generations |",
             "|---|---|---:|---:|---:|---:|"]
    for n in summ["nodes"]:
        lines.append(f"| {n['node']} | {n['status']} | {n['generation']} | {len(n['agents'])} | "
                     f"{_fmt(n['total']) if n['agents'] else '—'} | {_fmt(n['allGenerations']) if n['agents'] or n['allGenerations'] else '—'} |")
    detail = [{**r, "node": n["node"]} for n in summ["nodes"] for r in agent_rows(nodes[n["node"]]["units"])]
    lines += ["", "## Per agent (each node, all generations)", ""]
    lines += _table(detail, with_node=True) if detail else ["No agent results recorded yet."]
    lines += ["", "## Per model", "", "| Model | Tokens |", "|---|---:|"]
    lines += [f"| {m} | {_fmt(t)} |" for m, t in sorted(summ["byModel"].items())] or ["| — | — |"]
    unreported = sum(a["unreported"] for a in summ["agents"])
    if unreported:
        lines += ["", f"_{unreported} round(s) were recorded without `--tokens`; totals are a lower bound._"]
    path = ticket_dir / USAGE_FILE
    path.write_text(NL.join(lines) + NL, encoding="utf-8")
    return path
