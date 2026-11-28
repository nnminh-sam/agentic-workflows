"""Trusted evidence judges (plan J1-J5).

feature-flags asked the user to judge test validity (10 partial cases at final review, 20 minutes; weak tests found
in code-review generations 4 and 5). Here the review of evidence is a judge with authority, kept honest by the driver:

- J1 independence: the judges are the evidence node's reviewer units, fresh read-only sessions that never wrote the
  tests or ran them. Judge A (`reviewer`, deep tier) rules on every check; judge B (`reviewer-2`, routine tier) on
  the critical ones.
- J2 machine facts first: `facts()` settles what is determinable (rules EVD-A..E and EVD-M below). A judge cannot
  overrule them: a check the driver saw FAIL is FAIL; one that was not observed, did not run, has no source or whose
  test survived its mutation cannot be PASS.
- J3 mutation proof: for each critical check `mutations.json` (written in coding) names the line that implements it;
  `sdlc mutate` breaks that line, runs the check's tests, and restores the file. A test that still passes is vacuous.
- J4 two judges: A and B must agree on every critical check; a disagreement is a critical-logic question for the user.
- J5 calibration: approved verdicts go to `<project>/.sdlc/judge-calibration.jsonl`; `sdlc judge-miss` records one a
  later stage overturned. Above MISS_RATE_STRICT, a PASS below High confidence must ESCALATE instead.

Rules (the judge sees each in judge-facts.json):
  EVD-A  the check has an observed value (not UNOBSERVED / NOT-RUN)
  EVD-B  observed = expected (the driver's FAIL is final)
  EVD-C  the case ran (run.json PASS or FAIL)
  EVD-D  a critical check names the test line or step that observed it
  EVD-M  a critical check's mutation was killed (or mutations.json says why there is none)
  EVD-E  every changed source file is observed by some case, or the judge says why not (uncoveredFiles)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import HarnessError

FILES = {"a": "evidence-judgement.json", "b": "evidence-judgement-2.json"}
UNIT_JUDGE = {"reviewer": "a", "reviewer-2": "b"}
CALIBRATION = "judge-calibration.jsonl"
MISS_RATE_STRICT = 0.05
CALIBRATION_WINDOW = 200
REF_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?:-(?P<end>\d+))?$")
# Test files by the common conventions: JS/TS (.test. / .spec. / __tests__), Python (test_x.py, x_test.py),
# Go (x_test.go), JVM (XTest.java/kt), and test / e2e / fixture folders.
TEST_PATH_RE = re.compile(r"(\.test\.|\.spec\.|(^|/)tests?/|(^|/)__tests__/|(^|/)e2e/|(^|/)fixtures?/|"
                          r"(^|/)test_[^/]+\.py$|_test\.(py|go)$|Tests?\.(java|kt)$)")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git(root: Path, *args: str) -> str:
    import subprocess
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout if p.returncode == 0 else ""


# --- J2: facts ---------------------------------------------------------------------------------------------------

def facts(s: Any) -> dict[str, Any]:
    from . import evidence as EV
    tdir = s.ticket.dir
    tc = EV.load_testcases(tdir)
    if tc is None:
        raise HarnessError("G7: no testcases.json: the judges rule on its checks")
    ev = (_json(tdir / "evidence.json") or {}).get("checks") or {}
    run = (_json(tdir / "evidence" / "run.json") or {}).get("cases") or {}
    muts = (_json(tdir / "evidence" / "mutations.json") or {}).get("checks") or {}
    targets = _json(tdir / "mutations.json") or {}
    out: dict[str, Any] = {}
    observed_files: set[str] = set()
    for c in tc["cases"]:
        for chk in c["checks"]:
            e = ev.get(chk["id"]) or {"status": "NOT-RUN"}
            critical = c["risk"] == "critical" and c["inScope"]
            rules: list[str] = []
            if e["status"] in ("UNOBSERVED", "NOT-RUN"):
                rules.append("EVD-A: no observed value")
            if e["status"] == "FAIL":
                rules.append("EVD-B: observed differs from expected")
            if e["status"] != "MANUAL" and (run.get(c["id"]) or {}).get("status") not in ("PASS", "FAIL"):
                rules.append("EVD-C: the case did not run to a result")
            src = e.get("source") or {}
            if critical and e["status"] != "MANUAL" and not src.get("ref"):
                rules.append("EVD-D: no test line or step names this observation")
            if src.get("ref"):
                m = REF_RE.match(str(src["ref"]))
                if m:
                    observed_files.add(m.group("path"))
            if critical and c["run"]["kind"] == "repo-test":
                t = targets.get(chk["id"]) if isinstance(targets, dict) else None
                mres = (muts.get(chk["id"]) or {}).get("status")
                if isinstance(t, dict) and t.get("none"):
                    pass  # coding said why this check has no mutation target
                elif mres == "survived":
                    rules.append("EVD-M: the test still passes with the guarded line broken (vacuous test)")
                elif mres != "killed":
                    rules.append("EVD-M: no killed mutation for this critical check (`sdlc mutate`)")
            determined = "FAIL" if e["status"] == "FAIL" else ("NOT-PASS" if rules else "OPEN")
            out[chk["id"]] = {"case": c["id"], "critical": critical, "expect": chk["expect"], "status": e["status"],
                              "observed": e.get("observed"), "expected": e.get("expected"), "source": src,
                              "rules": rules, "determined": determined,
                              "mutation": (muts.get(chk["id"]) or {}).get("status")}
    return {"ticket": s.ctx["ticket"], "at": _now(), "checks": out, "uncoveredFiles": uncovered_files(s, observed_files)}


def uncovered_files(s: Any, observed: set[str]) -> list[str]:
    """EVD-E: changed non-test source files that no observing test or step imports or names."""
    repo = s.ctx["repositories"][0]
    root = Path(repo.get("worktree") or repo["path"])
    base = repo.get("startHead")
    if not base:
        return []
    changed = [ln.strip() for ln in _git(root, "diff", "--name-only", base).splitlines() if ln.strip()]
    changed += [ln[3:].strip() for ln in _git(root, "status", "--porcelain").splitlines() if ln.startswith("??")]
    sources = [f for f in dict.fromkeys(changed) if not TEST_PATH_RE.search(f) and re.search(r"\.(ts|tsx|js|jsx|mjs|py|php|dart|go|rb|java|kt|cs)$", f)]
    texts = []
    for rel in observed:
        p = root / rel
        if p.is_file():
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
    blob = "\n".join(texts)
    out = []
    for f in sources:
        stem = Path(f).with_suffix("").as_posix()
        name = Path(f).stem
        if f in observed or stem in blob or (name not in ("index", "route", "page", "layout") and re.search(rf"[/\"']{re.escape(name)}[\"'./]", blob)):
            continue
        out.append(f)
    return out


def write_facts(s: Any) -> dict[str, Any]:
    data = facts(s)
    p = s.ticket.dir / "evidence" / "judge-facts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for f in data["checks"].values():
        counts[f["determined"]] = counts.get(f["determined"], 0) + 1
    return {"ok": True, "file": str(p), "determined": counts, "uncoveredFiles": data["uncoveredFiles"]}


# --- citations -----------------------------------------------------------------------------------------------------

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def citation_problem(ref: str, quote: str, roots: list[Path]) -> str | None:
    """None when `quote` is found verbatim (whitespace-insensitive) within 2 lines of `ref` (path:line[-end])."""
    m = REF_RE.match(ref or "")
    if not m:
        return f"`{ref}` is not file:line"
    path = next((r / m.group("path") for r in roots if (r / m.group("path")).is_file()), None)
    if path is None:
        return f"`{ref}` does not resolve"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    a = int(m.group("line"))
    b = int(m.group("end") or a)
    window = _norm(" ".join(lines[max(0, a - 3):min(len(lines), b + 2)]))
    if _norm(quote) not in window:
        return f"the quote is not at `{ref}`"
    return None


# --- J5: calibration -------------------------------------------------------------------------------------------------

def _cal_path(s: Any) -> Path:
    return s.pdir / ".sdlc" / CALIBRATION


def miss_rate(s: Any) -> tuple[float, int]:
    rows = []
    p = _cal_path(s)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    verdicts = [r for r in rows if r.get("kind") == "verdict"][-CALIBRATION_WINDOW:]
    keys = {(r["ticket"], r["check"]) for r in verdicts}
    misses = {(r["ticket"], r["check"]) for r in rows if r.get("kind") == "miss"} & keys
    return (len(misses) / len(verdicts) if verdicts else 0.0), len(verdicts)


def record_verdicts(s: Any) -> int:
    """At the evidence gate: every approved verdict of both judges into the calibration log."""
    p = _cal_path(s)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("a", encoding="utf-8") as fh:
        for judge, name in FILES.items():
            data = _json(s.ticket.dir / name) or {}
            for cid, v in (data.get("verdicts") or {}).items():
                fh.write(json.dumps({"kind": "verdict", "at": _now(), "ticket": s.ctx["ticket"], "check": cid,
                                     "judge": judge, "verdict": v.get("verdict"), "confidence": v.get("confidence")}) + "\n")
                n += 1
    return n


def record_miss(s: Any, check: str, reason: str, by: str) -> dict[str, Any]:
    p = _cal_path(s)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": "miss", "at": _now(), "ticket": s.ctx["ticket"], "check": check, "reason": reason, "by": by}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    rate, n = miss_rate(s)
    return {**row, "missRate": round(rate, 4), "window": n, "strict": rate > MISS_RATE_STRICT}


# --- J2/J4: validation and agreement --------------------------------------------------------------------------------

def _questions(s: Any) -> list[dict[str, Any]]:
    from . import questions as Q
    try:
        return Q.load(s.ticket.dir)
    except HarnessError:
        return []


def _asked(qs: list[dict[str, Any]], check: str, answered: bool) -> bool:
    return any(check in q.get("question", "") and (q.get("status") in ("answered", "adopted") if answered else True)
               for q in qs)


def judgement_problems(s: Any, judge: str, fx: dict[str, Any] | None = None) -> list[str]:
    from . import schema
    fx = fx or facts(s)
    name = FILES[judge]
    data = _json(s.ticket.dir / name)
    if data is None:
        return [f"{name} is missing or not JSON (judge {judge.upper()} writes it)"]
    errs = schema.errors(data, "judgement.schema.json")
    if errs:
        return [f"{name}: schema: {e}" for e in errs[:10]]
    from .cli import citation_roots
    roots = citation_roots(s)
    strict = miss_rate(s)[0] > MISS_RATE_STRICT
    qs = _questions(s)
    nodes = set(s.graph["nodes"])
    out: list[str] = []
    want = [cid for cid, f in fx["checks"].items() if judge == "a" or f["critical"]]
    missing = [cid for cid in want if cid not in data["verdicts"]]
    if missing:
        out.append(f"{name}: no verdict for {missing[:15]}")
    for cid, v in data["verdicts"].items():
        f = fx["checks"].get(cid)
        if f is None:
            out.append(f"{name}: {cid} is not a check of testcases.json")
            continue
        verdict = v["verdict"]
        if f["determined"] == "FAIL" and verdict != "FAIL":
            out.append(f"{name}: {cid}: the driver observed a FAIL (EVD-B); the verdict must be FAIL")
        if verdict == "PASS" and f["rules"]:
            out.append(f"{name}: {cid}: PASS is not allowed: {'; '.join(f['rules'])}")
        if verdict in ("PASS", "FAIL"):
            for slot in ("requirement", "evidence"):
                c = v.get(slot)
                if not c:
                    out.append(f"{name}: {cid}: {verdict} needs a {slot} citation (file:line + verbatim quote)")
                    continue
                prob = citation_problem(c["ref"], c["quote"], roots)
                if prob:
                    out.append(f"{name}: {cid}: {slot} citation: {prob}")
        if verdict == "FAIL" and v.get("routeTo") not in nodes:
            out.append(f"{name}: {cid}: FAIL needs routeTo, the node that owns the fix")
        if verdict == "PASS" and (v["confidence"] == "Low" or (strict and v["confidence"] != "High")):
            out.append(f"{name}: {cid}: PASS at {v['confidence']} confidence must ESCALATE"
                       + (" (judge miss rate is high: only High-confidence PASS stands)" if strict else ""))
        if verdict == "ESCALATE":
            if v.get("class") not in ("business", "infra", "critical-logic"):
                out.append(f"{name}: {cid}: ESCALATE is for business, infra or critical-logic; a judgeable point is decided")
            elif not _asked(qs, cid, answered=False):
                out.append(f"{name}: {cid}: escalated but not asked: `sdlc ask --class {v['class']} --severity HIGH "
                           f"--question '{cid}: ...'`")
    if judge == "a":
        uncov = set(fx["uncoveredFiles"]) - set((data.get("uncoveredFiles") or {}).keys())
        if uncov:
            out.append(f"{name}: changed files no case observes (EVD-E) need a reason in uncoveredFiles: {sorted(uncov)}")
    return out


def gate_problems(s: Any) -> list[str]:
    """What stops the evidence node from closing: both judgements valid, the judges agree on every critical check
    (else the user answered a critical-logic question about it), no FAIL left unrouted, every escalation answered."""
    try:
        fx = facts(s)
    except HarnessError as exc:
        return [str(exc)]
    out = judgement_problems(s, "a", fx) + judgement_problems(s, "b", fx)
    if out:
        return out
    a = _json(s.ticket.dir / FILES["a"])["verdicts"]
    b = _json(s.ticket.dir / FILES["b"])["verdicts"]
    qs = _questions(s)
    for cid, f in fx["checks"].items():
        if f["critical"] and cid in b and a[cid]["verdict"] != b[cid]["verdict"] and not _asked(qs, cid, answered=True):
            out.append(f"G1: judges disagree on {cid} (A {a[cid]['verdict']}, B {b[cid]['verdict']}): a critical-logic "
                       f"question for the user: `sdlc ask --class critical-logic --severity HIGH --question '{cid}: ...'`, "
                       "then `sdlc answer ... --actor user`")
    fails = {cid: v.get("routeTo") for cid, v in a.items() if v["verdict"] == "FAIL"}
    if fails:
        out.append(f"FAIL verdicts route to their owning node: {fails}. Reopen it with "
                   "`sdlc invalidate --node <routeTo> --reason '<check>: <why>'` (counts toward the loop cap)")
    for cid, v in a.items():
        if v["verdict"] == "ESCALATE" and not _asked(qs, cid, answered=True):
            out.append(f"G1: {cid} was escalated ({v.get('class')}) and is not answered yet")
    return out


# --- J3: mutation ---------------------------------------------------------------------------------------------------

def _backup_dir(s: Any) -> Path:
    return s.ticket.dir / "evidence" / "mutation-backup"


def restore_leftovers(s: Any) -> list[str]:
    """A mutation interrupted mid-run left its backup: put the original back first."""
    restored = []
    d = _backup_dir(s)
    if not d.is_dir():
        return restored
    for meta in d.glob("*.json"):
        info = _json(meta) or {}
        orig = d / (meta.stem + ".orig")
        if info.get("path") and orig.is_file():
            Path(info["path"]).write_bytes(orig.read_bytes())
            restored.append(info["path"])
        meta.unlink(missing_ok=True)
        orig.unlink(missing_ok=True)
    return restored


def mutate(s: Any, only: str | None = None) -> dict[str, Any]:
    """Break each critical check's guarded line (mutations.json), run that case's tests, restore the file."""
    from . import env as E
    from . import evidence as EV
    restored = restore_leftovers(s)
    tc = EV.load_testcases(s.ticket.dir)
    if tc is None:
        raise HarnessError("G7: no testcases.json")
    targets = _json(s.ticket.dir / "mutations.json")
    if not isinstance(targets, dict):
        raise HarnessError("G7: no mutations.json in the ticket (coding names, per critical check, the line that "
                           "implements it: {\"TC-1.a1\": {\"file\": \"src/x.ts\", \"line\": 41, \"find\": \"...\", "
                           "\"replace\": \"...\"}} or {\"TC-1.a1\": {\"none\": \"why\"}})")
    cfg = E.config(s.profile) or {}
    repo = E._repo(s, cfg, None) if cfg else s.ctx["repositories"][0]
    root = E._checkout(repo)
    env = E.process_env(s, cfg) if cfg else None
    vals = E.values(s, cfg, repo) if cfg else {}
    results = (_json(s.ticket.dir / "evidence" / "mutations.json") or {}).get("checks") or {}
    case_of = {k["id"]: c for c in tc["cases"] for k in c["checks"]}
    logs = s.ticket.dir / "evidence" / "logs"
    for cid, t in targets.items():
        if only and cid != only:
            continue
        c = case_of.get(cid)
        if c is None or not isinstance(t, dict):
            results[cid] = {"status": "error", "detail": "not a check of testcases.json"}
            continue
        if t.get("none"):
            results[cid] = {"status": "none", "detail": t["none"]}
            continue
        if c["run"]["kind"] != "repo-test":
            results[cid] = {"status": "error", "detail": "mutation needs a repo-test case"}
            continue
        path = (root / t["file"]).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            results[cid] = {"status": "error", "detail": f"{t['file']} is not a file of the checkout"}
            continue
        original = path.read_bytes()
        lines = original.decode("utf-8").split("\n")
        i = int(t["line"]) - 1
        if not 0 <= i < len(lines) or (t.get("find") and t["find"] not in lines[i]):
            results[cid] = {"status": "error", "detail": f"line {t['line']} of {t['file']} does not hold the guard "
                                                         f"{t.get('find')!r} (update mutations.json)"}
            continue
        mutated = list(lines)
        mutated[i] = (lines[i].replace(t["find"], t.get("replace", "")) if t.get("find") else "")
        bdir = _backup_dir(s)
        bdir.mkdir(parents=True, exist_ok=True)
        key = re.sub(r"[^\w.-]", "_", cid)
        (bdir / f"{key}.orig").write_bytes(original)
        (bdir / f"{key}.json").write_text(json.dumps({"path": str(path)}), encoding="utf-8")
        try:
            path.write_bytes("\n".join(mutated).encode("utf-8"))
            case_env = {**(env or {}), "KANEKI_EVIDENCE_FILE": str(s.ticket.dir / "evidence" / "mutation-obs.jsonl")}
            r = E.run(E.fmt(c["run"]["command"], {**vals, "case": c["id"]}), root, case_env,
                      logs / f"mutation-{key}.log", int(t.get("timeoutSeconds") or 600))
        finally:
            path.write_bytes(original)
            (bdir / f"{key}.orig").unlink(missing_ok=True)
            (bdir / f"{key}.json").unlink(missing_ok=True)
        cls = EV.classify(r["exit"], Path(r["log"]).read_text(encoding="utf-8", errors="replace"))
        status = "survived" if r["exit"] == 0 else ("error" if cls == "infra" else "killed")
        results[cid] = {"status": status, "file": t["file"], "line": t["line"], "exit": r["exit"], "log": r["log"]}
    out = s.ticket.dir / "evidence" / "mutations.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": _now(), "checks": results}, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"ok": not any(r["status"] in ("survived", "error") for r in results.values()), "results": counts,
            "survived": [k for k, r in results.items() if r["status"] == "survived"], "restored": restored,
            "file": str(out)}
