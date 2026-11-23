"""`sdlc decide`: a person closes a user gate from a terminal, without an agent session.

It reads what `sdlc gate-request` returns, shows it as plain text, asks for a choice and an optional note, prints the
exact `sdlc gate ... --actor user` call, asks y/N and runs it in-process through `cli.main` (the same lock, checks,
decision log and STATUS.md refresh as the command typed by hand).

Gates are never crossed by automation: the command refuses to run unless stdin and stdout are both interactive
terminals, and no flag turns that off. Only choices that gate-request allows for `--actor user` are offered; the others
are listed with their `whyNot`. Plain `input()`, no curses: PowerShell, cmd, bash and POSIX terminals alike.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .errors import HarnessError

NOT_A_TERMINAL = ("sdlc decide is for a person at a terminal: stdin and stdout must both be interactive. "
                  "Agents ask the user and close the gate with `sdlc gate ... --actor user` after the user answers.")


def interactive() -> bool:
    """Both ends are a terminal. No flag, environment variable or profile key bypasses this."""
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def command_line(argv: list[str]) -> str:
    """How the call reads when typed in this OS's shell."""
    words = ["sdlc", *argv]
    return subprocess.list2cmdline(words) if os.name == "nt" else " ".join(shlex.quote(w) for w in words)


def ask(prompt: str) -> str | None:
    """One line of input, stripped; None on EOF or Ctrl-C (the person walked away: nothing runs)."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


# --- picking a ticket ----------------------------------------------------------------------------------

def waiting_tickets(ppath: Path) -> list[dict[str, Any]]:
    """Tickets the board shows as waiting on the user (`waitingOn == "user"`), newest activity first."""
    from . import board
    return [t for t in board.list_tickets(ppath) if t.get("waitingOn") == "user"]


def pick_ticket(ppath: Path) -> tuple[str, str | None] | None:
    """(ticket, node) the person picked from the waiting list; None when nothing waits or they cancelled."""
    waiting = waiting_tickets(ppath)
    if not waiting:
        print("No ticket is waiting on you.")
        return None
    print("Tickets waiting on you:")
    for i, t in enumerate(waiting, 1):
        title = f" · {t['title']}" if t.get("title") else ""
        print(f"  {i}) {t['ticket']}{title} · {t.get('waitingNode')} ({t.get('waitingReason')})")
    while True:
        raw = ask(f"Ticket [1-{len(waiting)}, Enter to cancel]: ")
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(waiting):
            t = waiting[int(raw) - 1]
            return t["ticket"], t.get("waitingNode")
        print(f"  Type a number from 1 to {len(waiting)}.")


# --- the menu ------------------------------------------------------------------------------------------

class Option:
    """One numbered entry: the `sdlc` calls it makes and what it needs from the person."""

    def __init__(self, label: str, calls: list[list[str]], stale: list[str] | None = None, note: str = "optional"):
        self.label, self.calls, self.stale, self.note = label, calls, stale or [], note


def options(req: dict[str, Any]) -> tuple[list[Option], list[str]]:
    """The allowed choices as options, and one `label: whyNot` line per refused one. Order: proceed, re-run, reject,
    stop, reopen."""
    offered: list[Option] = []
    refused: list[str] = []
    by_id = {c["id"]: c for c in req["choices"]}

    approve = by_id["approve"]
    if approve.get("reopenTargets"):  # triage-review: one proceed per --reopen target the user may pick
        for t in approve["reopenTargets"]:
            label = "Proceed" if t["node"] == "none" else f"Proceed and reopen {t['node']}"
            if t["allowed"]:
                offered.append(Option(label, [t["argv"]], t.get("staleNodes")))
            else:
                refused.append(f"{label}: {t['whyNot']}")
    elif approve["allowed"]:
        offered.append(Option("Proceed (approve)", [approve["argv"]]))
    else:
        refused.append(f"Proceed: {approve['whyNot']}")

    nc = by_id.get("needs-changes")
    if nc:
        back = nc["staleIfChosen"][0] if nc["staleIfChosen"] else "the earlier node"
        label = f"Re-run {back} (needs changes)"
        if nc["allowed"]:
            offered.append(Option(label, [nc["argv"]], nc["staleIfChosen"]))
        else:
            refused.append(f"{label}: {nc['whyNot']}")

    for cid, label in (("reject", "Reject"), ("stop", "Stop")):
        c = by_id[cid]
        if c["allowed"]:
            offered.append(Option(label, [c["argv"]]))
        else:
            refused.append(f"{label}: {c['whyNot']}")

    reopen = by_id["reopen"]
    for t in reopen.get("reopenTargets") or []:
        label = f"Reopen {t['node']} (rejects this round)"
        if reopen["allowed"]:
            follow = [[t["node"] if a == "<node>" else a for a in step] for step in reopen.get("followUp") or []]
            offered.append(Option(label, [reopen["argv"], *follow], t.get("staleNodes"), note="required"))
        else:
            refused.append(f"{label}: {reopen['whyNot']}")
    return offered, refused


def describe(req: dict[str, Any], waiting_on: str | None, title: str | None) -> list[str]:
    """The gate as plain text: what is asked, what the reviewer said, what is not ready, what to read."""
    lines = [f"Ticket {req['ticket']}" + (f" · {title}" if title else ""),
             f"Node:     {req['node']} (generation {req['generation']}, attempts {req['attempts']}/{req['loopCap']}, "
             f"{req['boundary']} boundary)",
             f"Question: {req['question']}"]
    if waiting_on and waiting_on != "user":
        lines.append(f"Note:     this ticket waits on the {waiting_on} ({req['waitingReason']}), not on you.")
    rv = req.get("reviewer")
    if rv:
        lines.append(f"Reviewer: {rv.get('verdict') or 'no verdict'} · {rv['model']} ({rv['tier']})"
                     + (f" · {rv['resultFile']}" if rv.get("resultFile") else ""))
    problems = req["readiness"]["problems"]
    lines.append("Ready:    yes" if not problems else "Ready:    no")
    lines += [f"  - {p}" for p in problems]
    if req["evidence"]:
        lines.append("Read:")
        for e in req["evidence"]:
            lines.append(f"  [{e['source']}] {e['name']}  {e['path']}" + ("" if e["exists"] else "  (missing)"))
    return lines


# --- the command ---------------------------------------------------------------------------------------

def _fingerprint(req: dict[str, Any]) -> tuple[Any, ...]:
    """What must not change between showing the gate and closing it."""
    return (req.get("node"), req.get("generation"), req.get("attempts"), len(req.get("units") or []),
            (req.get("reviewer") or {}).get("at"))


def run(args: argparse.Namespace, request: Callable[[str, str | None], dict[str, Any]],
        execute: Callable[[list[str]], int]) -> int:
    """`request(ticket, node)` is gate-request; `execute(argv)` runs one `sdlc` call and prints its JSON line."""
    from . import cli

    if not interactive():
        raise HarnessError(NOT_A_TERMINAL)
    ppath = cli.profile_path(args)
    ticket, node = args.ticket, args.node
    if not ticket and getattr(args, "path", None):
        ticket = cli.ticket_of_path(ppath, Path(args.path))
        if ticket:
            print(f"Ticket {ticket} (from {args.path})")
    if not ticket:
        picked = pick_ticket(ppath)
        if picked is None:
            cli.out({"decided": False, "reason": "nothing chosen"})
            return 0
        ticket, node = picked[0], node or picked[1]

    from . import board
    summary = board.summarize_ticket(ppath, ticket)
    if summary.get("error"):
        raise HarnessError(summary["error"])
    req = request(ticket, node)
    if not req.get("node"):
        print(f"{ticket}: no node is waiting for a decision.")
        cli.out({"decided": False, "ticket": ticket, "reason": "no pending node"})
        return 0
    waiting_on = summary.get("waitingOn") if summary.get("waitingNode") == req["node"] else None

    print()
    print("\n".join(describe(req, waiting_on, summary.get("title"))))
    offered, refused = options(req)
    print("Choices:")
    for i, o in enumerate(offered, 1):
        print(f"  {i}) {o.label}" + (f"  -> goes stale: {', '.join(o.stale)}" if o.stale else ""))
    if refused:
        print("Not available:")
        print("\n".join(f"  - {r}" for r in refused))
    if not offered:
        cli.out({"decided": False, "ticket": ticket, "node": req["node"], "reason": "no choice is allowed"})
        return 0

    while True:
        raw = ask(f"Choice [1-{len(offered)}, Enter to cancel]: ")
        if not raw:
            cli.out({"decided": False, "ticket": ticket, "node": req["node"], "reason": "cancelled"})
            return 0
        if raw.isdigit() and 1 <= int(raw) <= len(offered):
            chosen = offered[int(raw) - 1]
            break
        print(f"  Type a number from 1 to {len(offered)}.")

    while True:
        note = ask(f"Note ({chosen.note}): ")
        if note is None:
            cli.out({"decided": False, "ticket": ticket, "node": req["node"], "reason": "cancelled"})
            return 0
        if note or chosen.note != "required":
            break
        print("  This choice needs a note: say what is wrong in the earlier node.")

    profile = ["--profile", str(ppath)]
    calls = []
    for call in chosen.calls:
        argv = list(call)
        if argv[0] == "gate" and note:
            argv += ["--note", note]
        if argv[0] == "invalidate":
            argv = [note if a == "<required>" else a for a in argv]
        calls.append(argv + profile)
    print("This will run:")
    print("\n".join(f"  {command_line(c)}" for c in calls))
    if (ask("Run it? [y/N]: ") or "").lower() not in ("y", "yes"):
        cli.out({"decided": False, "ticket": ticket, "node": req["node"], "reason": "cancelled"})
        return 0
    if _fingerprint(request(ticket, req["node"])) != _fingerprint(req):
        raise HarnessError(f"{ticket}/{req['node']} changed while you were deciding; run `sdlc decide` again")
    rc = 0
    for argv in calls:
        rc = execute(argv)
        if rc:
            break
    return rc
