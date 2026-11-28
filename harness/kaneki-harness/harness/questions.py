"""Questions for the user as data, asked in one batch (D7).

feature-flags asked the user 26 times over 129 minutes, one doubt at a time, from free-form `questions.md` files. Here
every question is a row of `<ticketDir>/questions.json`, added with `sdlc ask` by a unit or the controller:

    {"id": "Q-3", "node": "requirement-analysis", "class": "business", "severity": "HIGH",
     "question": "...", "why": "...", "recommendation": "...", "options": ["...", "..."],
     "status": "open" | "answered" | "adopted", "answer": "...", "by": "user" | "controller", "at": "...",
     "late": false}

Classes: `business` (what the product should do), `infra` (environment, access, accounts) and `critical-logic`
(important logic with no citation to decide on). Questions a judge can settle from the requirements and the evidence
(test validity, partial coverage, wording) are not questions for the user: `sdlc ask` refuses them (review authority).

`sdlc questions --ticket T` returns every open question as one batch for a single ASK_USER round (recommended option
first). `sdlc answer --ticket T --id Q-n --answer ... --actor user` records the user's answer; a LOW question may be
closed by the controller with `--adopt` (its recommendation is adopted and says so). The plan node (`fix-plan`,
`feature-plan`, `scope`) cannot close while a HIGH question is open (G1). A business question asked after the plan
was approved is marked `late`: it reaches reflect as a signal, because the plan batch should have carried it.

`questions.md` is rendered from the JSON after every change (the board links it; reflect reads answered HIGH lines).
A ticket without `questions.json` keeps its hand-written `questions.md` untouched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import HarnessError

FILE = "questions.json"
CLASSES = ("business", "infra", "critical-logic")
SEVERITIES = ("HIGH", "LOW")
PLAN_NODES = ("fix-plan", "feature-plan", "scope")
JUDGEABLE_HINT = ("test validity, coverage, wording and other points a judge can settle from the requirements and the "
                  "evidence are decided by the reviewing unit with citations, not asked (review authority)")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def path(ticket_dir: Path) -> Path:
    return ticket_dir / FILE


def load(ticket_dir: Path) -> list[dict[str, Any]]:
    p = path(ticket_dir)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"G7: {p} is not valid JSON: {exc}") from exc
    rows = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise HarnessError(f"G7: {p} must be {{\"questions\": [...]}}")
    return rows


def save(ticket_dir: Path, rows: list[dict[str, Any]], ticket: str) -> None:
    path(ticket_dir).write_text(json.dumps({"questions": rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ticket_dir / "questions.md").write_text(render(rows, ticket), encoding="utf-8")


def render(rows: list[dict[str, Any]], ticket: str) -> str:
    lines = [f"# Questions for the user ({ticket})", "",
             "Generated from `questions.json` by `sdlc ask` / `sdlc answer`; edit through those commands.", ""]
    for state, title in (("open", "Open"), ("answered", "Answered"), ("adopted", "Adopted recommendations")):
        part = [q for q in rows if q.get("status") == state]
        if not part:
            continue
        lines += [f"## {title}", ""]
        for q in part:
            head = f"- {q['id']} · {q['severity']} · {q['class']} · {q['node']}"
            if state == "answered":
                head += f" · ANSWERED ({q.get('by')}, {q.get('at')})"
            elif state == "adopted":
                head += f" · ADOPTED ({q.get('by')}, {q.get('at')})"
            if q.get("late"):
                head += " · late (after the plan batch)"
            lines.append(f"{head}: {q['question']}")
            if q.get("why"):
                lines.append(f"  - Why it matters: {q['why']}")
            lines.append(f"  - Recommendation: {q['recommendation']}")
            for opt in q.get("options") or []:
                lines.append(f"  - Option: {opt}")
            if q.get("answer"):
                lines.append(f"  - Answer: {q['answer']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def plan_done(state: dict[str, Any]) -> bool:
    return any((state.get("nodes") or {}).get(n, {}).get("status") == "done" for n in PLAN_NODES)


def add(ticket_dir: Path, ticket: str, state: dict[str, Any], *, node: str, cls: str, severity: str, question: str,
        recommendation: str, why: str | None = None, options: list[str] | None = None) -> dict[str, Any]:
    if cls == "judgeable":
        raise HarnessError(f"not a question for the user: {JUDGEABLE_HINT}")
    if cls not in CLASSES:
        raise HarnessError(f"--class must be one of {', '.join(CLASSES)} ({JUDGEABLE_HINT})")
    if severity not in SEVERITIES:
        raise HarnessError("--severity must be HIGH (changes what, where, tests or evidence) or LOW")
    if not question.strip() or not recommendation.strip():
        raise HarnessError("--question and --recommend are required: every question carries a recommendation")
    rows = load(ticket_dir)
    same = next((q for q in rows if q["question"].strip() == question.strip() and q["status"] == "open"), None)
    if same:
        return {**same, "duplicate": True}
    n = 1 + max((int(str(q["id"]).split("-")[-1]) for q in rows if str(q.get("id", "")).startswith("Q-")), default=0)
    row = {"id": f"Q-{n}", "node": node, "class": cls, "severity": severity, "question": question.strip(),
           "why": (why or "").strip() or None, "recommendation": recommendation.strip(),
           "options": [o for o in (options or []) if o.strip()] or None, "status": "open", "askedAt": _now(),
           "late": cls == "business" and plan_done(state)}
    rows.append(row)
    save(ticket_dir, rows, ticket)
    return row


def answer(ticket_dir: Path, ticket: str, qid: str, *, text: str | None, actor: str, adopt: bool) -> dict[str, Any]:
    rows = load(ticket_dir)
    q = next((r for r in rows if r.get("id") == qid), None)
    if q is None:
        raise HarnessError(f"unknown question {qid}: {[r.get('id') for r in rows]}")
    if q["status"] != "open":
        raise HarnessError(f"{qid} is already {q['status']}")
    if adopt:
        if q["severity"] == "HIGH" and actor != "user":
            raise HarnessError(f"G1: {qid} is HIGH: only the user answers it (repeat with --actor user after asking)")
        q.update(status="adopted", answer=f"recommendation adopted: {q['recommendation']}", by=actor, at=_now())
    else:
        if actor != "user":
            raise HarnessError(f"G1: an answer is the user's: ask, then record it with --actor user "
                               f"(a LOW question may be closed with --adopt)")
        if not (text or "").strip():
            raise HarnessError("--answer is required (or --adopt to take the recommendation)")
        q.update(status="answered", answer=text.strip(), by="user", at=_now())
    save(ticket_dir, rows, ticket)
    return q


def batch(ticket_dir: Path) -> dict[str, Any]:
    """Every open question, HIGH first, as one batch for a single ASK_USER round."""
    rows = load(ticket_dir)
    open_rows = sorted((q for q in rows if q.get("status") == "open"),
                       key=lambda q: (q["severity"] != "HIGH", CLASSES.index(q["class"]) if q["class"] in CLASSES else 9,
                                      q["id"]))
    return {"open": open_rows, "openHigh": [q["id"] for q in open_rows if q["severity"] == "HIGH"],
            "answered": sum(1 for q in rows if q.get("status") == "answered"),
            "adopted": sum(1 for q in rows if q.get("status") == "adopted"),
            "late": [q["id"] for q in rows if q.get("late")],
            "how": ("Ask every open question in ONE ASK_USER round (recommended option first; split only where the "
                    "tool caps the number per call), then record each with `sdlc answer --id Q-n --answer ... --actor "
                    "user`. LOW questions may be closed with `--adopt`.") if open_rows else None}


def open_high(ticket_dir: Path) -> list[str]:
    try:
        return [q["id"] for q in load(ticket_dir) if q.get("status") == "open" and q.get("severity") == "HIGH"]
    except HarnessError:
        return []
