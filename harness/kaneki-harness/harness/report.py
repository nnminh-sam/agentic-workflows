"""`sdlc report`: the tester notebook, a read-only page per ticket (plan C2).

Built from the ticket's data, not from prose: testcases.json (criteria, cases, checks, risk), evidence.json (an
observed value and a source per check), evidence/run.json (how each case ran, its log), questions.json (decisions and
open questions) and the repository's change against the ticket's start commit. It replaces the backfill prototype
in docs/prototypes/tester-notebook/build.py, which scraped testcases.md and run-report.md.

The page is a view for looking back: no buttons that decide anything, nothing stored but the theme. The judges
decide test and evidence validity (review authority); the page shows what they decided from.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import HarnessError

TEMPLATE = Path(__file__).resolve().parent / "report_template.html"
REF_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)$")
MAX_EXCERPT = 34


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout if p.returncode == 0 else ""


def _excerpt(root: Path, rel: str, line: int, pad: int = 4) -> dict[str, Any] | None:
    p = root / rel
    if not p.is_file():
        return None
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    a, b = max(1, line - pad), min(len(lines), line + pad)
    b = min(b, a + MAX_EXCERPT)
    return {"path": rel, "start": a, "lines": lines[a - 1:b]}


def verdict_of(statuses: list[str]) -> str:
    """One case's verdict from its checks' statuses (pass, partial, not-run, fail)."""
    if any(st == "FAIL" for st in statuses):
        return "fail"
    if statuses and all(st == "PASS" for st in statuses):
        return "pass"
    if any(st in ("PASS", "UNOBSERVED") for st in statuses):
        return "partial"
    return "not-run"


def level_of(case: dict[str, Any]) -> str:
    kind = case["run"]["kind"]
    if kind == "steps" or case["surface"] in ("web", "api", "mobile", "device"):
        return "live" if kind != "manual" else "static"
    return "unit" if kind == "repo-test" else "static"


def build(s: Any) -> dict[str, Any]:
    tdir = s.ticket.dir
    tc = _read_json(tdir / "testcases.json")
    if not tc:
        raise HarnessError("G7: no testcases.json in the ticket: the notebook is built from data, not from prose")
    ev = (_read_json(tdir / "evidence.json") or {}).get("checks") or {}
    run = (_read_json(tdir / "evidence" / "run.json") or {}).get("cases") or {}
    questions = (_read_json(tdir / "questions.json") or {}).get("questions") or []
    repo = s.ctx["repositories"][0]
    root = Path(repo.get("worktree") or repo["path"])
    base = repo.get("startHead") or _git(root, "rev-parse", "HEAD~1").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    ticket_md = (tdir / "ticket.md").read_text(encoding="utf-8") if (tdir / "ticket.md").is_file() else ""

    cases = []
    for c in tc["cases"]:
        rows = [(chk, ev.get(chk["id"]) or {"status": "NOT-RUN"}) for chk in c["checks"]]
        statuses = [r["status"] for _, r in rows]
        res = run.get(c["id"]) or {}
        evidence: list[dict[str, Any]] = []
        notes = []
        for chk, r in rows:
            src = r.get("source") or {}
            m = REF_RE.match(str(src.get("ref") or ""))
            if m and src.get("kind") == "test":
                ex = _excerpt(root, m.group("path"), int(m.group("line")))
                if ex:
                    evidence.append({"kind": "test", "title": f"{chk['id']}: observed {json.dumps(r.get('observed'))}",
                                     **ex, "log": None})
            elif src.get("kind") == "step" and src.get("ref"):
                f = tdir / str(src["ref"]).split("#")[0]
                if f.is_file():
                    evidence.append({"kind": "cite", "title": f"{chk['id']}: {src['ref']}", "path": src["ref"],
                                     "start": 1, "lines": f.read_text(encoding="utf-8").splitlines()[:MAX_EXCERPT]})
            if r.get("note"):
                notes.append(f"{chk['id']}: {r['note']}")
        if res.get("log") and Path(res["log"]).is_file():
            tail = Path(res["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            evidence.append({"kind": "log", "title": Path(res["log"]).name, "path": Path(res["log"]).name, "start": 1,
                             "lines": tail or ["(empty log)"]})
        cases.append({
            "id": c["id"], "ticket": s.ctx["ticket"], "title": c["title"], "section": c["surface"],
            "sectionName": c["surface"], "reqs": c["criteria"], "surface": c["surface"],
            "pre": "; ".join(c.get("preconditions") or []), "steps": "; ".join(c.get("steps") or []),
            "expected": "; ".join(f"{k['id']}: {k['expect']}" for k in c["checks"]),
            "inScope": "yes" if c["inScope"] else f"no ({c.get('reason') or 'no reason given'})",
            "assertions": [f"{k['id']}: {k['expect']}" for k in c["checks"]], "risk": c["risk"], "malformed": False,
            "verdict": verdict_of(statuses), "level": level_of(c), "statusRaw": res.get("status") or "not run",
            "method": c["run"]["kind"] + (f": {c['run'].get('command') or c['run'].get('file')}" if c["run"]["kind"] != "manual" else ""),
            "resultText": "; ".join(f"{chk['id']} {r['status']}" + (f" (observed {json.dumps(r['observed'])})"
                                                                     if "observed" in r else "") for chk, r in rows),
            "ac": c["criteria"], "auditNote": " ".join(notes), "evidence": evidence, "history": [],
            "reproduce": c["run"].get("command"),
        })

    changed = []
    for line in _git(root, "diff", "--numstat", f"{base}..{head}" if base and head else "HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add, rem, path = parts
            changed.append({"path": path, "added": int(add) if add.isdigit() else 0,
                            "removed": int(rem) if rem.isdigit() else 0,
                            "isTest": bool(re.search(r"(\.test\.|\.spec\.|(^|/)tests?/)", path)), "cases": []})
    by_path = {f["path"]: f for f in changed}
    for c in cases:
        for e in c["evidence"]:
            if e["kind"] == "test" and e["path"] in by_path and c["id"] not in by_path[e["path"]]["cases"]:
                by_path[e["path"]]["cases"].append(c["id"])

    unproven = [{"text": f"{cid}: {r.get('note') or r['status']}", "cases": [r["case"]]}
                for cid, r in ev.items() if r["status"] in ("UNOBSERVED", "NOT-RUN") and r.get("case")]
    decisions = [f"{q['id']} ({q['class']}, {q['by']}): {q['question']} → {q['answer']}" for q in questions
                 if q.get("status") in ("answered", "adopted")]
    doubts = [f"{q['id']} ({q['severity']}, {q['class']}): {q['question']}" for q in questions if q.get("status") == "open"]
    title = next((ln[2:].strip() for ln in ticket_md.splitlines() if ln.startswith("# ")), s.ctx["ticket"])
    return {
        "ticket": s.ctx["ticket"], "title": title, "goal": "", "base": (base or "")[:7], "head": (head or "")[:7],
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": {"testcases": "testcases.json", "report": "evidence.json", "log": "evidence/logs/",
                    "final": "questions.json"},
        "acs": [{"id": a["id"], "text": a["text"], "reqs": a.get("requirements") or []} for a in tc["criteria"]],
        "cases": cases, "changed": changed, "unproven": unproven, "judgements": [], "doubts": doubts,
        "decisions": decisions,
        "runs": [{"command": cid, "result": r.get("status"), "log": r.get("log")} for cid, r in run.items()],
    }


def write(s: Any, out: Path | None = None) -> dict[str, Any]:
    data = build(s)
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null",
                                                         json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    dest = out or (s.ticket.dir / "tester-notebook.html")
    dest.write_text(page, encoding="utf-8")
    verdicts: dict[str, int] = {}
    for c in data["cases"]:
        verdicts[c["verdict"]] = verdicts.get(c["verdict"], 0) + 1
    return {"ok": True, "path": str(dest), "cases": len(data["cases"]), "criteria": len(data["acs"]),
            "verdicts": verdicts, "changed": len(data["changed"]), "bytes": len(page.encode("utf-8"))}
