"""Fake `claude` / `codex` for the runner tests: never starts a real agent.

    python fake_agent.py claude --bg -n kaneki:<ticket>:<node>:<runId> [args...] <prompt>
    python fake_agent.py claude agents --json --all | stop <id> | logs <id>
    python fake_agent.py codex exec [-m model] [-c ...] <prompt>

State lives in $FAKE_AGENT_DIR (sessions.json, calls.jsonl, counters.json). What a node session does comes from
$FAKE_AGENT_SCENARIO, a JSON object {node: action | [action per call]} (default action `gate`):
  gate     do the node through the real driver (artifacts, unit records, `gate --status approved`), then end (done)
  ask      end without a gate, waiting for input (claude state `blocked`; codex exits 0)
  vanish   print an id but never appear in `agents --json` (claude) / exit 0 without a gate (codex)
  usage    end with a usage-limit message (claude: in `logs`; codex: on stdout, exit 1)
  hang     keep running (claude state `running`)
  nostart  claude --bg fails with a usage-limit error and prints no id
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import secrets
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("FAKE_AGENT_DIR") or ".")
USAGE_TEXT = "Claude AI usage limit reached. Your limit resets in 30 minutes."


def load(name: str, default):
    try:
        return json.loads((STATE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(name: str, data) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def log_call(argv: list[str]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "host": os.environ.get("SDLC_HOST")}) + "\n")


def action_for(node: str) -> str:
    scenario = json.loads(os.environ.get("FAKE_AGENT_SCENARIO") or "{}")
    plan = scenario.get(node, "gate")
    if isinstance(plan, str):
        return plan
    counters = load("counters.json", {})
    i = counters.get(node, 0)
    counters[node] = i + 1
    save("counters.json", counters)
    return plan[min(i, len(plan) - 1)] if plan else "gate"


def driver(*argv: str) -> dict:
    sys.path.insert(0, str(HOME))
    from harness.cli import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main([*argv, "--profile", os.environ["SDLC_PROFILE"]])
    res = json.loads(buf.getvalue().strip().splitlines()[-1])
    if code != 0:
        raise SystemExit(f"driver {argv[0]} failed: {res}")
    return res


def complete_node(ticket: str, node: str) -> None:
    """What a controller does for one node, as the lifecycle tests' Base.complete does it."""
    plan = driver("plan", "--ticket", ticket, "--node", node)
    status = driver("ticket-detail", "--ticket", ticket, "--events", "0")
    tdir = Path(status["ticketDir"])
    rules = plan.get("ruleIds") or []
    for art in plan["artifacts"]:
        check = plan.get("checks", {}).get(art, {})
        lines = [f"# {node}", "", f"Work for {node}."]
        for sec in check.get("sections", []):
            lines += ["", sec, "Content."]
            if "Condition" in sec:
                lines += ["| condition | positive | negative |", "|---|---|---|", "| a > 0 | 1 | 0 |"]
            if sec in ("## Design rules", "## Rule self-check"):
                lines += [f"- {rid}: PASS" for rid in rules]
        parsers = check.get("parsers", [])
        if "confidence" in parsers:
            lines.append("Confidence: High")
        if "rule-verdicts" in parsers:
            lines += [f"- {rid}: PASS" for rid in rules]
        if "verdict" in parsers:
            lines.append("Verdict: APPROVED")
        if "reopen" in parsers:
            lines.append("Reopen: none")
        path = tdir / art
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for unit in plan["units"]:
        pk = driver("packet", "--ticket", ticket, "--node", node, "--unit", unit["id"])
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n", encoding="utf-8")
        verdict = ["--verdict", "PASS"] if unit["role"] == "reviewer" or plan["pattern"].startswith("staged") else []
        driver("record", "--ticket", ticket, "--node", node, "--unit", unit["id"], "--result", pk["resultPath"],
               "--model", unit["model"], "--tokens", "1000", *verdict)
    driver("gate", "--ticket", ticket, "--node", node, "--status", "approved")


def claude(argv: list[str]) -> int:
    sessions = load("sessions.json", {})
    if argv[:1] == ["agents"]:
        listing = list(sessions.values()) if "--all" in argv else \
            [s for s in sessions.values() if s["state"] not in ("done", "stopped")]
        print(json.dumps(listing))
        return 0
    if argv[:1] == ["stop"]:
        sid = argv[1]
        if sid in sessions:
            sessions[sid]["state"] = "stopped"
            save("sessions.json", sessions)
        return 0
    if argv[:1] == ["logs"]:
        print(sessions.get(argv[1], {}).get("_logs", ""))
        return 0
    if "--bg" not in argv:
        print("fake claude: unsupported call", file=sys.stderr)
        return 1
    name = argv[argv.index("-n") + 1]
    _, ticket, node, run_id = name.split(":")
    act = action_for(node)
    if act == "nostart":
        print("Error: " + USAGE_TEXT, file=sys.stderr)
        return 1
    sid = secrets.token_hex(4)
    entry = {"id": sid, "sessionId": sid + "-0000-4000-8000-000000000000", "name": name, "cwd": os.getcwd(),
             "kind": "background", "state": "running", "_logs": "working"}
    if act == "gate":
        complete_node(ticket, node)
        entry.update(state="done", _logs="node closed")
    elif act == "ask":
        entry.update(state="blocked", _logs="Which option do you want? (waiting for you)")
    elif act == "usage":
        entry.update(state="done", _logs=USAGE_TEXT)
    elif act == "hang":
        entry.update(state="running")
    if act != "vanish":
        sessions[sid] = entry
        save("sessions.json", sessions)
    print(f"Started background session {sid}")
    return 0


def codex(argv: list[str]) -> int:
    if argv[:1] != ["exec"]:
        return 1
    prompt = argv[-1]
    ticket = re.search(r"for ticket (\S+)\. Run exactly one node: ([a-z0-9-]+)\.", prompt)
    if not ticket:
        print("fake codex: prompt is not the runner template", file=sys.stderr)
        return 1
    tid, node = ticket.group(1), ticket.group(2)
    act = action_for(node)
    if act == "gate":
        complete_node(tid, node)
        print("tokens used\n1234")
        return 0
    if act == "usage":
        print("ERROR: You've hit your usage limit. Try again in 2 hours.")
        return 1
    print("I have a question for the user.")
    return 0


def main() -> int:
    who, argv = sys.argv[1], sys.argv[2:]
    log_call([who, *argv])
    return claude(argv) if who == "claude" else codex(argv)


if __name__ == "__main__":
    sys.exit(main())
