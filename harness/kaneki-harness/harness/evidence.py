"""The evidence runner core (plan E4, with the B4 circuit breaker): `sdlc evidence run|status`.

feature-flags' evidence node wrote 12 scripts (~1,000 lines), patched them 37 times and lost two runs to the
environment: 30 run failures, 20 of them infra (12 `too many clients`, 8 Neon control plane), 9 UI steps, 1 login.
Here the driver runs the cases; the agent only supplies them and reads the result.

Input (`--cases <file>`): {"cases": [{"id": "TC-1", "surface": "web", "command": "npx playwright test -g TC-1",
"smoke": false}, ...]}. Each command runs in the evidence repository's worktree with the evidence env loaded
(harness/env.py: the agent never handles the env file), one case per process, its output in `evidence/logs/`.

Rules:
- Smoke first: the cases marked `smoke` (else the first case of each surface) run before the batch; a smoke case that
  does not pass stops the run before the batch is spent on a broken environment.
- Resume: `evidence/run.json` keeps each case's result; a rerun skips finished cases (PASS, FAIL) unless `--rerun`.
- Failure classes, from the exit code and the output:
  - infra (connection limits, refused connections, control-plane / gateway errors): retried with back-off at most
    2 times; 2 cases in a row still infra after their retries open the breaker: the run stops, escalation `infra`.
  - step (selector / locator not found, a wait that timed out): recorded with its log; the agent may fix the step
    data and rerun that case once (`--rerun TC-n`); a second step error escalates.
  - product (anything else that fails): a real FAIL, recorded once, never retried.
- Time cap: the run stops before the node's budget deadline (`budgets.node.*.minutes`), leaving the rest NOT-RUN.
Statuses: PASS, FAIL, STEP-ERROR, INFRA, NOT-RUN.
`ok` means the run is complete: every case PASS or FAIL and no escalation. A FAIL is a result for the evidence
judge, not a failed run; exit 1 means something is left (STEP-ERROR, INFRA, NOT-RUN) or escalated.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import HarnessError

INFRA_RE = re.compile(r"too many clients|remaining connection slots|ECONNREFUSED|ECONNRESET|connection refused|"
                      r"Control plane request failed|getaddrinfo|ENOTFOUND|socket hang up|"
                      r"\b(?:HTTP|status)[ :=]*50[234]\b|Bad Gateway|Service Unavailable|"
                      r"database system is (starting up|shutting down)|terminating connection", re.I)
STEP_RE = re.compile(r"locator\.|waiting for (selector|locator|element)|selector .*not found|"
                     r"element(?: is)? not (visible|found|attached)|TimeoutError|Timeout \d+ms exceeded|"
                     r"strict mode violation|STEP-ERROR:", re.I)
INFRA_RETRIES = 2
BACKOFF_SECONDS = (5, 15)
BREAKER_STREAK = 2
FINISHED = ("PASS", "FAIL")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def classify(exit_code: int | None, output: str) -> str:
    """PASS, or the failure class: infra, step, product."""
    if exit_code == 0:
        return "PASS"
    if INFRA_RE.search(output):
        return "infra"
    if exit_code is None or STEP_RE.search(output):  # None: the case itself timed out
        return "step"
    return "product"


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"G7: cases file {path}: {exc}") from exc
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise HarnessError(f"G7: {path} must be {{\"cases\": [{{\"id\", \"surface\", \"command\"}}, ...]}}")
    seen = set()
    for c in cases:
        if not isinstance(c, dict) or not c.get("id") or not c.get("command"):
            raise HarnessError(f"G7: every case needs an id and a command: {c}")
        if c["id"] in seen:
            raise HarnessError(f"G7: duplicate case id {c['id']}")
        seen.add(c["id"])
    return cases


def smoke_ids(cases: list[dict[str, Any]]) -> list[str]:
    marked = [c["id"] for c in cases if c.get("smoke")]
    if marked:
        return marked
    first: dict[str, str] = {}
    for c in cases:
        first.setdefault(c.get("surface") or "-", c["id"])
    return list(first.values())


def _run_path(ticket_dir: Path) -> Path:
    return ticket_dir / "evidence" / "run.json"


def load_run(ticket_dir: Path) -> dict[str, Any]:
    try:
        return json.loads(_run_path(ticket_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cases": {}}


def _save(ticket_dir: Path, run: dict[str, Any]) -> None:
    p = _run_path(ticket_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def execute(ticket_dir: Path, cases: list[dict[str, Any]], run_case: Callable[[dict[str, Any], Path], dict[str, Any]],
            *, rerun: list[str] | None = None, deadline: float | None = None,
            sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Run the cases by the rules above. `run_case(case, log) -> {exit, seconds}` runs one command (env loaded)."""
    run = load_run(ticket_dir)
    results: dict[str, Any] = run.setdefault("cases", {})
    rerun_set = set(rerun or [])
    escalations: list[dict[str, Any]] = []
    order = smoke_ids(cases)
    smoke = set(order)
    ordered = [c for c in cases if c["id"] in smoke] + [c for c in cases if c["id"] not in smoke]
    streak = 0
    stopped: str | None = None
    logs = ticket_dir / "evidence" / "logs"
    for case in ordered:
        cid = case["id"]
        prev = results.get(cid) or {}
        if stopped:
            results.setdefault(cid, {"status": "NOT-RUN"})
            if results[cid]["status"] not in FINISHED:
                results[cid].update(status="NOT-RUN", reason=stopped)
            continue
        if prev.get("status") in FINISHED and cid not in rerun_set:
            continue
        if prev.get("status") == "STEP-ERROR" and cid not in rerun_set:
            continue  # the agent fixes the step data and asks for this case again
        if deadline is not None and time.time() >= deadline:
            stopped = "time cap reached"
            results[cid] = {**prev, "status": "NOT-RUN", "reason": stopped}
            continue
        step_errors = int(prev.get("stepErrors", 0))
        attempts = 0
        while True:
            attempts += 1
            log = logs / f"{cid}.{attempts}.log"
            res = run_case(case, log)
            out = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
            cls = classify(res.get("exit"), out)
            if cls == "infra" and attempts <= INFRA_RETRIES:
                sleep(BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)])
                continue
            break
        row: dict[str, Any] = {"surface": case.get("surface"), "attempts": attempts, "exit": res.get("exit"),
                               "seconds": res.get("seconds"), "log": str(log), "at": _now(), "smoke": cid in smoke}
        if cls == "PASS":
            row["status"], streak = "PASS", 0
        elif cls == "product":
            row["status"], streak = "FAIL", 0
        elif cls == "step":
            streak = 0
            row.update(status="STEP-ERROR", stepErrors=step_errors + 1)
            if step_errors + 1 >= 2:
                escalations.append({"case": cid, "class": "step", "why": "a second step error after the step fix"})
        else:
            streak += 1
            row["status"] = "INFRA"
            if streak >= BREAKER_STREAK:
                stopped = "breaker: infra failures in a row after retries"
                escalations.append({"case": cid, "class": "infra", "why": stopped})
        results[cid] = row
        if cid in smoke and row["status"] != "PASS" and not stopped:
            stopped = f"smoke case {cid} did not pass ({row['status']})"
            escalations.append({"case": cid, "class": "infra" if row["status"] == "INFRA" else "smoke", "why": stopped})
        _save(ticket_dir, run)
    run.update(at=_now(), stopped=stopped, escalations=escalations, summary=summary(results))
    _save(ticket_dir, run)
    return {"ok": not stopped and not escalations and all(r.get("status") in FINISHED for r in results.values()),
            "stopped": stopped, "escalations": escalations, "summary": run["summary"], "file": str(_run_path(ticket_dir))}


def summary(results: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results.values():
        out[r.get("status", "NOT-RUN")] = out.get(r.get("status", "NOT-RUN"), 0) + 1
    return out


def env_runner(s: Any, repo_id: str | None = None) -> Callable[[dict[str, Any], Path], dict[str, Any]]:
    """Each case's command in the evidence worktree with the env loaded (harness/env.py)."""
    from . import env as E
    cfg = E.config(s.profile) or {}
    import os
    from . import steps as ST
    if cfg:
        repo = E._repo(s, cfg, repo_id)
        env = E.process_env(s, cfg)
        vals = E.values(s, cfg, repo)
        env.setdefault("BASE_URL", vals["baseUrl"])
        cwd = E._checkout(repo)
    else:
        repo = s.ctx["repositories"][0]
        env, vals, cwd = dict(os.environ), {"baseUrl": os.environ.get("BASE_URL", "")}, E._checkout(repo)
    kit = ST.load_kit(ST.kit_path(s))
    obs_dir = s.ticket.dir / "evidence" / "observations"

    def run_case(case: dict[str, Any], log: Path) -> dict[str, Any]:
        obs = obs_dir / f"{case['id']}.jsonl"
        obs.parent.mkdir(parents=True, exist_ok=True)
        obs.unlink(missing_ok=True)  # only the last attempt's observations count
        case_env = {**(env or {}), "KANEKI_EVIDENCE_FILE": str(obs), "KANEKI_CASE": case["id"]}
        if case.get("stepsFile"):
            t0 = time.monotonic()
            path = Path(case["stepsFile"])
            code = ST.run_file(path, kit, vals.get("baseUrl") or "", case_env, log, obs, ST.shell_helper(cwd, case_env),
                               rel=case.get("stepsRef"))
            return {"exit": code, "seconds": round(time.monotonic() - t0, 1)}
        timeout = int(case.get("timeoutSeconds") or 600)
        return E.run(E.fmt(case["command"], {**vals, "case": case["id"]}), cwd, case_env, log, timeout)
    return run_case


# --- testcases.json -> cases, and observations -> evidence.json (plan C1 / C3) ----------------------------------------

def load_testcases(ticket_dir: Path) -> dict[str, Any] | None:
    p = ticket_dir / "testcases.json"
    if not p.is_file():
        return None
    from . import schema
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HarnessError(f"G7: testcases.json is not valid JSON: {exc}") from exc
    schema.validate(data, "testcases.schema.json", "testcases.json")
    return data


def cases_from_testcases(ticket_dir: Path, tc: dict[str, Any], kit: dict[str, Any]) -> list[dict[str, Any]]:
    """The runnable cases (in scope, not manual), each step file validated first: one bad file refuses the whole run
    before anything is spent on it."""
    from . import steps as ST
    out, problems = [], []
    for c in tc["cases"]:
        if not c["inScope"] or c["run"]["kind"] == "manual":
            continue
        row: dict[str, Any] = {"id": c["id"], "surface": c["surface"], "smoke": False}
        if c["run"]["kind"] == "repo-test":
            row["command"] = c["run"]["command"]
        else:
            f = ticket_dir / c["run"]["file"]
            if not f.is_file():
                problems.append(f"{c['id']}: step file {c['run']['file']} is missing")
                continue
            doc = ST.load_steps(f)
            for p in ST.validate(doc, kit, [k["id"] for k in c["checks"]]):
                problems.append(f"{c['id']} ({c['run']['file']}): {p}")
            row.update(stepsFile=str(f), stepsRef=c["run"]["file"])
        out.append(row)
    risky = {}
    for c in tc["cases"]:  # smoke: the first critical case of each surface (else the first of the surface)
        if any(r["id"] == c["id"] for r in out):
            if c["surface"] not in risky or (c["risk"] == "critical" and risky[c["surface"]][1] != "critical"):
                risky[c["surface"]] = (c["id"], c["risk"])
    for r in out:
        r["smoke"] = any(v[0] == r["id"] for v in risky.values())
    if problems:
        raise HarnessError("G7: step files refused before the run (fix the data, never patch a script):\n- "
                           + "\n- ".join(problems))
    return out


def _observations(ticket_dir: Path, case: str) -> list[dict[str, Any]]:
    p = ticket_dir / "evidence" / "observations" / f"{case}.jsonl"
    rows = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def build_evidence(ticket_dir: Path, ticket: str, tc: dict[str, Any]) -> dict[str, Any]:
    """evidence.json: per check of testcases.json, the observed value and its source, from the last run."""
    run = load_run(ticket_dir)
    out: dict[str, Any] = {}
    for c in tc["cases"]:
        res = (run.get("cases") or {}).get(c["id"]) or {}
        obs = _observations(ticket_dir, c["id"])
        for chk in c["checks"]:
            mine = [o for o in obs if o.get("check") == chk["id"]]
            row: dict[str, Any] = {"case": c["id"]}
            if "value" in chk:
                row["expected"] = chk["value"]
            if not c["inScope"] or c["run"]["kind"] == "manual":
                row.update(status="MANUAL" if c["inScope"] else "NOT-RUN", note=c.get("reason") or "manual case")
            elif not mine:
                ran = res.get("status") in ("PASS", "FAIL")
                row["status"] = "UNOBSERVED" if ran else "NOT-RUN"
                row["note"] = ("the case ran but recorded nothing for this check: a passing test is not a passing "
                               "check" if ran else f"case {res.get('status', 'not run')}")
            else:
                failed = [o for o in mine if o.get("pass") is False]
                unknown = [o for o in mine if o.get("pass") is None]
                pick = failed[0] if failed else mine[-1]
                row["status"] = "FAIL" if failed else ("UNOBSERVED" if unknown else "PASS")
                if unknown and not failed:
                    row["note"] = "observed without an expected value: the judge compares it"
                row["observed"] = pick.get("observed")
                if "expected" in pick:
                    row["expected"] = pick["expected"]
                row["observations"] = len(mine)
                row["source"] = {"kind": "step" if "#expect[" in str(pick.get("ref")) else "test",
                                 **({"ref": pick["ref"]} if pick.get("ref") else {}),
                                 **({"log": res["log"]} if res.get("log") else {}),
                                 **({"param": pick["param"]} if "param" in pick else {})}
            out[chk["id"]] = row
    data = {"schemaVersion": 1, "ticket": ticket, "at": _now(), "runFile": str(_run_path(ticket_dir)), "checks": out}
    from . import schema
    schema.validate(data, "evidence.schema.json", "evidence.json")
    (ticket_dir / "evidence.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def evidence_summary(data: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in data["checks"].values():
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def deadline_of(s: Any, node: str = "execute-and-evidence") -> float | None:
    from . import budget as B
    if not B.enabled(s.profile) or node not in s.state["nodes"]:
        return None
    rec = s.state["nodes"][node]
    res = B.check(s.ppath, s.ctx["ticket"], node, prof=s.profile, events=[], generation=rec["generation"],
                  started=B.node_started(rec))
    if not res.get("deadline"):
        return None
    return datetime.fromisoformat(res["deadline"]).timestamp() - 60  # stop a minute early to write the record


# --- gate parsers (graph checks `testcases` / `evidence`) -----------------------------------------------------------

def testcases_problems(text: str) -> list[str]:
    """The testcase node's artifact: schema valid, unique ids, every check belongs to its case, every criterion has a
    case, every in-scope critical case runs (not manual)."""
    from . import schema
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    errs = schema.errors(data, "testcases.schema.json")
    if errs:
        return [f"schema: {e}" for e in errs[:10]]
    out = []
    ids = [c["id"] for c in data["cases"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        out.append(f"duplicate case ids {dup}")
    crit = {a["id"] for a in data["criteria"]}
    covered: set[str] = set()
    for c in data["cases"]:
        wrong = [k["id"] for k in c["checks"] if not k["id"].startswith(c["id"] + ".")]
        if wrong:
            out.append(f"{c['id']}: checks {wrong} belong to another case")
        unknown = [a for a in c["criteria"] if a not in crit]
        if unknown:
            out.append(f"{c['id']}: unknown criteria {unknown}")
        if c["inScope"]:
            covered |= set(c["criteria"])
        if not c["inScope"] and not (c.get("reason") or "").strip():
            out.append(f"{c['id']}: out of scope without a reason")
        if c["inScope"] and c["risk"] == "critical" and c["run"]["kind"] == "manual":
            out.append(f"{c['id']}: a critical case must run (repo-test or steps), not manual")
    missing = sorted(crit - covered)
    if missing:
        out.append(f"criteria with no in-scope case: {missing}")
    return out


def evidence_problems(text: str, ticket_dir: Path) -> list[str]:
    """The evidence node's artifact: schema valid, written for the current testcases.json (every check present)."""
    from . import schema
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    errs = schema.errors(data, "evidence.schema.json")
    if errs:
        return [f"schema: {e}" for e in errs[:10]]
    try:
        tc = load_testcases(ticket_dir)
    except HarnessError as exc:
        return [str(exc)]
    if tc is None:
        return ["no testcases.json to check it against"]
    want = {k["id"] for c in tc["cases"] for k in c["checks"]}
    missing = sorted(want - set(data["checks"]))
    return [f"checks missing (rerun `sdlc evidence run`): {missing[:20]}"] if missing else []
