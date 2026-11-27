"""Tester notebook prototype: backfill a ticket's test cases and evidence into one reviewable HTML page.

This is the throwaway prototype of `sdlc report`. It reads today's markdown artifacts (testcases.md, run-report.md,
final-review.md, ticket.md), the ticket worktree and the unit-test log, builds the structured data the future
`testcases.json` / `evidence.json` would hold, and injects it into template.html.

What a backfill cannot know (and the page says so):
- per-assertion verdicts: today's run report has one status per case, so assertions inherit the case status;
- risk tiers: inferred from the test-case section and wording;
- acceptance-criterion mapping: ticket ACs -> requirement ids is given below per ticket (later: written by the plan).

Usage: python -I build.py --ticket-dir <.sdlc/tickets/T> --worktree <worktree> --base <sha> --head <sha> --out <html>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Ticket ACs (ticket.md "Acceptance criteria", in order) -> requirement ids (requirements.md). Backfill-only mapping.
AC_REQS = {
    "AC-1": ["R-9", "R-10", "R-11", "R-12", "R-13", "R-14", "R-15"],
    "AC-2": ["R-16"],
    "AC-3": ["R-6", "R-7", "R-8", "R-20", "R-21"],
    "AC-4": ["R-4"],
    "AC-5": ["R-5"],
    "AC-6": ["R-1", "R-2", "R-3", "R-17", "R-18", "TEST-04"],
    "AC-7": ["R-19"],
}
CRITICAL_SECTIONS = {"F", "G", "H", "I", "J"}
CITE_RE = re.compile(r"((?:[\w.()\[\]-]+/)*[\w.()\[\]-]+\.(?:tsx?|md|sql|example|log|json)):(\d+)(?:-(\d+))?((?:,\s*\d+(?:-\d+)?)*)")
TCID_RE = re.compile(r"TC-\d+")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def latest_generation(text: str) -> str:
    """The text after the last `# ` H1 heading: artifacts append generations below SUPERSEDED markers."""
    heads = [m.start() for m in re.finditer(r"^# ", text, re.M)]
    return text[heads[-1]:] if heads else text


def first_generation(text: str) -> str:
    heads = [m.start() for m in re.finditer(r"^# ", text, re.M)]
    return text[: heads[1]] if len(heads) > 1 else text


def section(text: str, title_prefix: str) -> str:
    m = re.search(rf"^## {re.escape(title_prefix)}.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def strip_md(s: str) -> str:
    return s.replace("`", "")


# --- parsing ------------------------------------------------------------------------------------------------

def parse_acs(ticket: str) -> list[dict]:
    body = section(ticket, "Acceptance criteria")
    items = re.findall(r"^- \[[ x]\] (.+)$", body, re.M)
    return [{"id": f"AC-{i}", "text": strip_md(t), "reqs": AC_REQS.get(f"AC-{i}", [])} for i, t in enumerate(items, 1)]


def parse_testcases(text: str) -> list[dict]:
    gen = latest_generation(text)
    out, sec, sec_name, sec_reqs = [], "", "", []
    for line in gen.splitlines():
        m = re.match(r"^## ([A-Z])\. (.+)$", line)
        if m:
            sec, sec_name = m.group(1), strip_md(m.group(2))
            sec_reqs = re.findall(r"R-\d+|TEST-\d+", m.group(2))
            continue
        if line.startswith("## "):
            sec = ""
        if sec and line.startswith("| TC-"):
            c = cells(line)
            malformed = None
            if len(c) == 6:  # a merged Preconditions/Steps cell: keep the row, say so on the card
                c = c[:4] + ["(see Given: this row has 6 of 7 columns in testcases.md)"] + c[4:]
                malformed = "This row has 6 of the 7 table columns in testcases.md; Given and When are merged. A JSON test case would have failed schema validation here."
            if len(c) < 7:
                continue
            tid, purpose, surface, pre, steps = c[:5]
            expected, in_scope = "|".join(c[5:-1]), c[-1]
            reqs = re.findall(r"R-\d+", purpose) or sec_reqs
            low = purpose.lower()
            risk = "critical" if (sec in CRITICAL_SECTIONS or "negative" in low or "refus" in low) else "normal"
            if "R-19" in reqs and len(reqs) == 1:
                risk = "low"
            out.append({
                "id": tid, "title": strip_md(re.sub(r"\s*\((?:R|TEST)-[^)]*\)\s*$", "", purpose)), "section": sec,
                "sectionName": sec_name, "reqs": reqs, "surface": strip_md(surface), "pre": strip_md(pre),
                "steps": strip_md(steps), "expected": strip_md(expected), "inScope": strip_md(in_scope),
                "assertions": [a.strip() for a in re.split(r";\s+", strip_md(expected)) if a.strip()],
                "risk": risk, "malformed": malformed,
            })
    return out


def parse_report_rows(text: str) -> dict[str, dict]:
    rows = {}
    for line in section(text, "Cases").splitlines():
        if line.startswith("| TC-"):
            c = cells(line)
            rows[c[0]] = {"status": c[1], "method": c[2], "evidence": strip_md("|".join(c[3:]))}
    return rows


def classify(status: str, method: str) -> tuple[str, str]:
    s, m = status.upper(), method.lower()
    if "live-skipped" in status or m.startswith("web") or "live" in m:
        level = "live"
    elif "static" in m or "rg" in m:
        level = "static"
    elif "typecheck" in m or "build" in m:
        level = "build"
    elif "handler" in m or "action" in m:
        level = "handler"
    elif "component" in m:
        level = "component"
    else:
        level = "unit"
    if s.startswith("PASS"):
        return "pass", level
    if s.startswith("FAIL"):
        return "fail", level
    if "SKIPPED" in s or "skipped" in m:
        return "not-run", "live"
    if "(none)" in m:
        return "not-run", level
    return "partial", level


def parse_audit(text: str) -> dict[str, str]:
    """Per-row auditor notes: `TC-02, TC-11: ...` segments inside the 'Per-row outcome' bullets."""
    audit = section(text, "Evidence audit")
    m = re.search(r"Per-row outcome.*?\n(.*?)(?:\n\n|\Z)", audit, re.S)
    notes: dict[str, str] = {}
    if not m:
        return notes
    for bullet in re.findall(r"^- (.+)$", m.group(1), re.M):
        groups = list(re.finditer(r"((?:TC-\d+(?:/\d+)?(?:,\s*)?)+):", bullet))
        for i, g in enumerate(groups):
            seg = bullet[g.end(): groups[i + 1].start() if i + 1 < len(groups) else len(bullet)].strip()
            ids = []
            for tok in re.findall(r"TC-\d+(?:/\d+)?", g.group(1)):
                base = tok.split("/")[0]
                ids.append(base)
                if "/" in tok:
                    ids.append(base[: -len(tok.split("/")[1])] + tok.split("/")[1])
            for tid in ids:
                notes[tid] = (notes.get(tid, "") + " " + strip_md(seg)).strip()
    return notes


# --- evidence from the worktree -----------------------------------------------------------------------------

class Worktree:
    def __init__(self, root: Path, project: Path):
        self.root, self.project = root, project
        files = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, encoding="utf-8").stdout
        self.files = [f for f in files.splitlines() if f]

    def resolve(self, path: str) -> Path | None:
        for base in (self.root, self.project):
            p = base / path
            if p.is_file():
                return p
        hits = [f for f in self.files if f.endswith("/" + path) or f == path]
        return self.root / hits[0] if len(hits) == 1 else None

    def rel(self, p: Path) -> str:
        for base in (self.root, self.project):
            try:
                return p.resolve().relative_to(base.resolve()).as_posix()
            except ValueError:
                pass
        return p.as_posix()

    def excerpt(self, p: Path, a: int, b: int, pad: int = 0) -> dict:
        lines = read(p).splitlines()
        a, b = max(1, a - pad), min(len(lines), b + pad)
        if b - a > 34:
            b = a + 34
        return {"path": self.rel(p), "start": a, "lines": lines[a - 1: b]}

    def it_blocks(self, p: Path) -> list[tuple[str, int, int]]:
        lines = read(p).splitlines()
        out = []
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)(?:it|test)\(\s*([\"'`])(.+?)\2", line)
            if not m:
                continue
            indent, end = m.group(1), i
            for j in range(i + 1, min(len(lines), i + 80)):
                if re.match(rf"^{indent}\}}\)", lines[j]):
                    end = j
                    break
            out.append((m.group(3), i + 1, end + 1))
        return out


def gather_evidence(case: dict, row: dict, note: str, wt: Worktree, log_lines: list[str]) -> list[dict]:
    text = row.get("evidence", "") + " " + note
    ev, seen = [], set()
    # 1. tests named in the evidence cell: find their it() blocks and their `ok` line in the run log
    test_files = {m.group(0) for m in re.finditer(r"src/[\w./()\[\]-]+\.test\.tsx?", text)}
    for tf in sorted(test_files):
        p = wt.resolve(tf)
        if not p:
            continue
        for title, a, b in wt.it_blocks(p):
            if title in text and (p, a) not in seen:
                seen.add((p, a))
                ok = next(((i + 1, l.strip()) for i, l in enumerate(log_lines)
                           if re.search(rf"\bok \d+ - {re.escape(title)}\s*$", l)), None)
                ev.append({"kind": "test", "title": title, **wt.excerpt(p, a, b),
                           "log": {"line": ok[0], "text": ok[1]} if ok else None})
    # 1b. a whole suite cited without test names ("all 5 tests ok"): list each test with its log line
    for tf in sorted(test_files):
        p = wt.resolve(tf)
        if p and not any(e.get("path") == wt.rel(p) for e in ev) and re.search(r"all \d+ tests ok|whole .* suite", text):
            lines = []
            for title, a, _ in wt.it_blocks(p):
                ok = next((f"{l.strip()}   (log:{i + 1})" for i, l in enumerate(log_lines)
                           if re.search(rf"\bok \d+ - {re.escape(title)}\s*$", l)), "no 'ok' line in log")
                lines.append(f"{wt.rel(p)}:{a}  {title}\n    -> {ok}")
            ev.append({"kind": "log", "title": f"suite {wt.rel(p)}", "path": wt.rel(p) + " (suite)", "start": 1,
                       "lines": "\n".join(lines).splitlines()})
    # 1c. run logs cited as the evidence (npm test / typecheck summaries)
    for lg in sorted(set(re.findall(r"(?:logs/)?(evidence[\w.-]*\.(?:unittest|typecheck|build)\.log)", text))):
        p = wt.project / ".sdlc" / "tickets" / case.get("ticket", "") / "logs" / lg if case.get("ticket") else None
        if p and p.is_file():
            body = read(p).splitlines()
            tail = [l for l in body if re.match(r"^# (tests|suites|pass|fail|cancelled|skipped|todo|duration_ms)\b", l)] or body[-8:]
            start = body.index(tail[0]) + 1 if tail and tail[0] in body else max(1, len(body) - 7)
            ev.append({"kind": "log", "title": lg, "path": p.relative_to(wt.project).as_posix(), "start": start,
                       "lines": tail[:12] or ["(empty log: the command printed nothing and exited 0)"]})
    # 2. path:line citations (auditor ranges, docs lines, config lines)
    for m in CITE_RE.finditer(text):
        p = wt.resolve(m.group(1))
        if not p or p.suffix == ".log":
            continue
        ranges = [(int(m.group(2)), int(m.group(3) or m.group(2)))]
        for extra in re.findall(r"\d+(?:-\d+)?", m.group(4) or ""):
            x, _, y = extra.partition("-")
            ranges.append((int(x), int(y or x)))
        for a, b in ranges:
            if any(e.get("path") == wt.rel(p) and e["start"] <= a <= e["start"] + len(e["lines"]) for e in ev):
                continue
            pad = 1 if b - a < 3 else 0
            ev.append({"kind": "cite", "title": f"{wt.rel(p)}:{a}" + (f"-{b}" if b != a else ""), **wt.excerpt(p, a, b, pad)})
    return ev[:4]


def screenshots(ticket_dir: Path, tid: str) -> list[str]:
    hist = ticket_dir / "evidence" / "r1"
    return sorted(p.relative_to(ticket_dir).as_posix() for p in hist.glob(f"{tid}.*") if p.suffix in (".png", ".json", ".txt", ".html"))


# --- coverage of the change ---------------------------------------------------------------------------------

def changed_files(wt: Worktree, base: str, head: str) -> list[dict]:
    out = subprocess.run(["git", "-C", str(wt.root), "diff", "--numstat", base, head],
                         capture_output=True, text=True, encoding="utf-8").stdout
    rows = []
    for line in out.splitlines():
        add, rem, path = line.split("\t", 2)
        rows.append({"path": path, "added": int(add), "removed": int(rem), "isTest": ".test." in path or path.startswith("src/test/")})
    return rows


def imports_of(wt: Worktree, test_path: str) -> set[str]:
    p = wt.root / test_path
    src = read(p)
    out = set()
    for spec in re.findall(r"from\s+[\"']([^\"']+)[\"']|import\(\s*[\"']([^\"']+)[\"']", src):
        s = spec[0] or spec[1]
        if s.startswith("@/"):
            cand = "src/" + s[2:]
        elif s.startswith("."):
            cand = (Path(test_path).parent / s).as_posix()
            cand = str(Path(cand)).replace("\\", "/")
            parts = []
            for seg in cand.split("/"):
                if seg == "..":
                    parts.pop()
                elif seg != ".":
                    parts.append(seg)
            cand = "/".join(parts)
        else:
            continue
        for ext in ("", ".ts", ".tsx", "/index.ts"):
            if (wt.root / (cand + ext)).is_file():
                out.add(cand + ext)
    # structure tests read page files by path segments (fs), not imports
    if "learning-gate.structure" in test_path:
        out |= {f for f in wt.files if f.startswith("src/app/(workspace)/learning/") and f.endswith((".tsx",))}
    return out


# --- main ---------------------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket-dir", required=True)
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    td, wt = Path(a.ticket_dir), Worktree(Path(a.worktree), Path(a.project))
    ticket_id = td.name

    ticket = read(td / "ticket.md")
    report_all = read(td / "run-report.md")
    report = latest_generation(report_all)
    report_prev = first_generation(report_all)
    final = latest_generation(read(td / "final-review.md"))
    log_path = next((td / "logs" / n for n in ("evidence-r4.unittest.log", "evidence.unittest.log") if (td / "logs" / n).is_file()))
    log_lines = read(log_path).splitlines()

    acs = parse_acs(ticket)
    cases = parse_testcases(read(td / "testcases.md"))
    rows, prev_rows, notes = parse_report_rows(report), parse_report_rows(report_prev), parse_audit(report)
    req_to_ac = {r: ac["id"] for ac in acs for r in ac["reqs"]}

    for c in cases:
        c["ticket"] = ticket_id
        row = rows.get(c["id"], {"status": "NOT IN REPORT", "method": "", "evidence": ""})
        c["verdict"], c["level"] = classify(row["status"], row["method"])
        c["statusRaw"], c["method"], c["resultText"] = row["status"], row["method"], row["evidence"]
        c["ac"] = sorted({req_to_ac[r] for r in c["reqs"] if r in req_to_ac}) or (["AC-6"] if c["section"] == "L" else [])
        c["auditNote"] = notes.get(c["id"], "")
        c["evidence"] = gather_evidence(c, row, c["auditNote"], wt, log_lines)
        c["history"] = screenshots(td, c["id"])
        prev = prev_rows.get(c["id"])
        if prev:
            pv, _ = classify(prev["status"], prev["method"])
            if pv != c["verdict"]:
                c["delta"] = {"from": pv, "to": c["verdict"], "fromText": prev["status"]}
        titles = [e["title"] for e in c["evidence"] if e["kind"] == "test"]
        pattern = "|".join(re.sub(r"([.*+?^${}()|\[\]\\])", r"\\\1", t) for t in titles).replace('"', '\\"')
        c["reproduce"] = f'npm test -- --test-name-pattern "{pattern}"' if titles else None

    # change coverage: a changed source file is covered when a case cites it or a cited test imports it
    changed = changed_files(wt, a.base, a.head)
    for f in changed:
        f["cases"] = []
    by_path = {f["path"]: f for f in changed}
    for c in cases:
        touched = set()
        for e in c["evidence"]:
            touched.add(e["path"])
            if e["kind"] == "test":
                touched |= imports_of(wt, e["path"])
        for pth in touched:
            if pth in by_path and c["id"] not in by_path[pth]["cases"]:
                by_path[pth]["cases"].append(c["id"])

    # residual risks: the run report's "Unproven live" bullets, each with the cases it names
    unproven = []
    for b in re.findall(r"^- (.+)$", section(report, "Unproven live"), re.M):
        ids = []
        for m in re.finditer(r"TC-(\d+)(?: to TC-(\d+))?", b):
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            ids += [f"TC-{n:02d}" if n < 100 else f"TC-{n}" for n in range(lo, hi + 1)]
        known = {c["id"] for c in cases}
        unproven.append({"text": strip_md(b), "cases": [i for i in ids if i in known]})

    judgements = [strip_md(b) for b in re.findall(r"^- (TC-.+)$", section(report, "Evidence audit"), re.M)
                  if "accepted" in b.lower() or "Accepted" in b]
    doubts = [strip_md(b) for b in re.findall(r"^- (.+)$", section(final, "Open doubts"), re.M)]
    decisions = [strip_md(re.sub(r"^\d+\.\s*", "", l)) for l in section(final, "User decisions").splitlines() if re.match(r"^\d+\.", l)]
    runs = [cells(l) for l in section(report, "Runs").splitlines() if l.startswith("| npm")]

    data = {
        "ticket": ticket_id,
        "title": strip_md(next((l[2:] for l in ticket.splitlines() if l.startswith("# ")), ticket_id)),
        "goal": strip_md(section(ticket, "Goal")),
        "base": a.base[:7], "head": a.head[:7],
        "generatedAt": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": {"testcases": "testcases.md (generation 2)", "report": "run-report.md (generation 3, round 1)",
                    "log": log_path.relative_to(td).as_posix(), "final": "final-review.md (generation 2)"},
        "acs": acs, "cases": cases, "changed": changed, "unproven": unproven,
        "judgements": judgements, "doubts": doubts, "decisions": decisions,
        "runs": [{"command": r[0], "result": r[1], "log": r[2]} for r in runs if len(r) >= 3],
    }
    html = read(HERE / "template.html").replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    Path(a.out).write_text(html, encoding="utf-8")
    print(json.dumps({"out": a.out, "cases": len(cases), "acs": len(acs), "bytes": len(html.encode("utf-8")),
                      "withEvidence": sum(1 for c in cases if c["evidence"]), "changed": len(changed)}))


if __name__ == "__main__":
    main()
