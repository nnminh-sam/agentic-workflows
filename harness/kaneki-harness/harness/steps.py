"""Declarative evidence cases (plan E2) and the project's evidence kit (plan E5).

A case that does not belong in the repository's tests is data (contracts/steps.schema.json) that this tested runner
executes. Before anything runs, `validate` checks the file against the schema, every `use` against the kit's helpers,
`as` / `login` against the kit's accounts and every expect's check id against the case's checks in testcases.json, so
a typo never costs a run and no script is patched in place.

The runner works at the HTTP level with one cookie session per (case, param): `login`, `http`, `page: goto` (a GET,
the status and the body as the browser would receive them before scripts run), kit helpers (`use`), and expectations
on the last response (status, textPresent, textAbsent, json path, header). Interactions that need a browser (click,
fill, soft navigation) are refused at validation: write those cases as repository e2e tests (plan E1).

Each expectation is recorded like an evidence() call (one JSON line in $KANEKI_EVIDENCE_FILE, source
`<step file>#expect[i]`), so the merge into evidence.json is the same for tests and step files.

Kit: `<project>/.sdlc/adapters/evidence/<project id>/kit.json` (contracts/evidence-kit.schema.json), or the path in
`environment.evidence.kit`.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import HarnessError

VAR_RE = re.compile(r"\$\{([A-Za-z_][\w.]*)\}")
STEP_KINDS = ("http", "page", "use", "login", "capture")
PAGE_ACTIONS = ("goto",)


# --- loading ---------------------------------------------------------------------------------------------------

def kit_path(s: Any) -> Path:
    cfg = (s.profile.get("environment") or {}).get("evidence") or {}
    if cfg.get("kit"):
        p = Path(cfg["kit"])
        return p if p.is_absolute() else s.pdir / p
    return s.pdir / ".sdlc" / "adapters" / "evidence" / str(s.profile["project"]["id"]) / "kit.json"


def load_kit(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"accounts": {}, "helpers": {}}
    from . import schema
    try:
        kit = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HarnessError(f"G7: evidence kit {path} is not valid JSON: {exc}") from exc
    schema.validate(kit, "evidence-kit.schema.json", f"evidence kit {path}")
    return {"accounts": {}, "helpers": {}, **kit}


def load_steps(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"G7: step file {path}: {exc}") from exc


# --- validation ------------------------------------------------------------------------------------------------

def _check_steps(steps: list[dict[str, Any]], kit: dict[str, Any], where: str, out: list[str]) -> None:
    for i, st in enumerate(steps or []):
        here = f"{where}[{i}]"
        kinds = [k for k in st if k in STEP_KINDS]
        if len(kinds) != 1:
            out.append(f"{here}: one of {', '.join(STEP_KINDS)} per step (got {sorted(st)})")
            continue
        kind = kinds[0]
        if kind == "page" and st["page"] not in PAGE_ACTIONS:
            out.append(f"{here}: page `{st['page']}` needs a browser: write this case as a repository e2e test (E1)")
        elif kind == "page" and not st.get("url"):
            out.append(f"{here}: page goto needs a url")
        elif kind == "http" and (not isinstance(st["http"], dict) or not st["http"].get("url")):
            out.append(f"{here}: http needs an object with a url")
        elif kind == "use" and st["use"] not in (kit.get("helpers") or {}):
            out.append(f"{here}: the kit has no helper `{st['use']}` (add it to the project's evidence kit, tested "
                       "once; reflect proposes it)")
        elif kind == "use":
            want = set((kit["helpers"][st["use"]].get("params") or []))
            got = set((st.get("with") or {}).keys())
            if want - got:
                out.append(f"{here}: helper `{st['use']}` needs {sorted(want - got)}")
        elif kind == "login" and st["login"] not in (kit.get("accounts") or {}):
            out.append(f"{here}: the kit has no account `{st['login']}`")
        elif kind == "capture" and not (isinstance(st["capture"], dict) and st["capture"].get("name")
                                        and st["capture"].get("json")):
            out.append(f"{here}: capture needs {{\"name\", \"json\": <path>}}")


def validate(doc: dict[str, Any], kit: dict[str, Any], checks: list[str] | None = None) -> list[str]:
    """Every problem that would make the run fail before it observes anything. [] = runnable."""
    from . import schema
    out = [f"schema: {e}" for e in schema.errors(doc, "steps.schema.json")]
    if out:
        return out
    if doc.get("as") and doc["as"] not in (kit.get("accounts") or {}):
        out.append(f"as: the kit has no account `{doc['as']}`")
    if doc.get("as") and not kit.get("login"):
        out.append("as: the kit has no `login` steps")
    for part in ("setup", "steps", "teardown"):
        _check_steps(doc.get(part) or [], kit, part, out)
    for name, helper in (kit.get("helpers") or {}).items():
        if bool(helper.get("steps")) == bool(helper.get("command")):
            out.append(f"kit helper `{name}`: give either steps or a command")
    if checks is not None:
        for i, ex in enumerate(doc["expect"]):
            cid = full_check(doc["case"], ex["check"])
            if cid not in checks:
                out.append(f"expect[{i}]: {cid} is not a check of {doc['case']} in testcases.json ({checks})")
    return out


def full_check(case: str, check: str) -> str:
    return check if check.startswith("TC-") else f"{case}.{check}"


# --- running ---------------------------------------------------------------------------------------------------

def _sub(value: Any, vars_: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = VAR_RE.fullmatch(value)
        if whole and whole.group(1) in vars_:
            return vars_[whole.group(1)]  # keeps the type (a number param stays a number)
        return VAR_RE.sub(lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), value)
    if isinstance(value, dict):
        return {k: _sub(v, vars_) for k, v in value.items()}
    if isinstance(value, list):
        return [_sub(v, vars_) for v in value]
    return value


def json_path(data: Any, path: str) -> Any:
    cur = data
    for part in [p for p in re.split(r"\.|\[(\d+)\]", path.lstrip("$.")) if p]:
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


class Session:
    def __init__(self, base_url: str, log: Any, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.log = log
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                  _NoRedirect())
        self.last: dict[str, Any] | None = None

    def request(self, method: str, url: str, body: Any = None, headers: dict[str, str] | None = None,
                is_json: bool = False) -> dict[str, Any]:
        full = url if re.match(r"^https?://", url) else self.base + "/" + url.lstrip("/")
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            if is_json or not isinstance(body, str):
                data = json.dumps(body).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            else:
                data = body.encode("utf-8")
        req = urllib.request.Request(full, data=data, method=method.upper(), headers=hdrs)
        try:
            with self.opener.open(req, timeout=self.timeout) as res:
                status, text, rh = res.status, res.read().decode("utf-8", "replace"), dict(res.headers)
        except urllib.error.HTTPError as exc:
            status, text, rh = exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers or {})
        except (urllib.error.URLError, OSError) as exc:
            self.log.write(f"{method} {full} -> connection refused / failed: {exc}\n")
            raise ConnectionError(f"{method} {full}: connection refused or failed ({exc})") from exc
        self.log.write(f"{method} {full} -> {status} ({len(text)} bytes)\n")
        self.last = {"status": status, "text": text, "headers": {k.lower(): v for k, v in rh.items()}, "url": full}
        return self.last


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep 3xx as the observed status: a redirect is something a check may assert."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def _run_steps(steps: list[dict[str, Any]], sess: Session, kit: dict[str, Any], vars_: dict[str, Any],
               exec_helper: Any) -> None:
    for st in steps or []:
        st = _sub(st, vars_)
        if "http" in st:
            h = st["http"]
            sess.request(h.get("method", "GET"), h["url"], h.get("json", h.get("body")), h.get("headers"),
                         is_json="json" in h)
        elif "page" in st:
            sess.request("GET", st["url"], headers={"Accept": "text/html"})
        elif "login" in st:
            acct = kit["accounts"][st["login"]]
            env = vars_.get("_env") or {}
            creds = {**vars_, "email": env.get(acct["emailVar"], ""), "password": env.get(acct["passwordVar"], "")}
            _run_steps(kit.get("login") or [], sess, kit, creds, exec_helper)
        elif "use" in st:
            helper = kit["helpers"][st["use"]]
            inner = {**vars_, **{f"with.{k}": v for k, v in (st.get("with") or {}).items()}}
            if helper.get("command"):
                exec_helper(_sub(helper["command"], inner), sess.log)
            else:
                _run_steps(helper.get("steps") or [], sess, kit, inner, exec_helper)
        elif "capture" in st:
            c = st["capture"]
            vars_[f"vars.{c['name']}"] = json_path(json.loads((sess.last or {}).get("text") or "null"), c["json"])


def _expect(ex: dict[str, Any], last: dict[str, Any] | None) -> tuple[bool, Any, Any]:
    """(pass, observed, expected) of one expectation on the last response."""
    if last is None:
        return False, None, "a response"
    if "status" in ex:
        return last["status"] == ex["status"], last["status"], ex["status"]
    if "textAbsent" in ex:
        found = [t for t in ex["textAbsent"] if t and t in last["text"]]
        return not found, {"present": found}, {"absent": ex["textAbsent"]}
    if "textPresent" in ex:
        missing = [t for t in ex["textPresent"] if t not in last["text"]]
        return not missing, {"missing": missing}, {"present": ex["textPresent"]}
    if "json" in ex:
        try:
            got = json_path(json.loads(last["text"]), ex["json"]["path"])
        except (ValueError, KeyError, IndexError, TypeError):
            return False, {"path": ex["json"]["path"], "found": False}, ex["json"].get("equals")
        return got == ex["json"].get("equals"), got, ex["json"].get("equals")
    if "header" in ex:
        got = last["headers"].get(ex["header"]["name"].lower())
        if "equals" in ex["header"]:
            return got == ex["header"]["equals"], got, ex["header"]["equals"]
        return got is not None and ex["header"].get("contains", "") in got, got, {"contains": ex["header"].get("contains")}
    return False, None, "an expectation"


def run_file(path: Path, kit: dict[str, Any], base_url: str, env: dict[str, str], log_path: Path,
             obs_path: Path | None, exec_helper: Any, rel: str | None = None) -> int:
    """Run one step file. 0 = every expectation passed, 1 = a product failure, 3 = infra (the server did not answer).
    Observations go to `obs_path` as evidence() lines."""
    doc = load_steps(path)
    problems = validate(doc, kit)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        if problems:
            log.write("STEP-ERROR: step file invalid (nothing ran):\n" + "\n".join(f"- {p}" for p in problems) + "\n")
            return 2
        ok = True
        source = rel or path.name
        for param in (doc.get("params") or [None]):
            sess = Session(base_url, log)
            vars_: dict[str, Any] = {"baseUrl": base_url, "case": doc["case"], "_env": env,
                                     **{f"env.{k}": v for k, v in env.items()}}
            if param is not None:
                vars_["P"] = param
                log.write(f"\n== param {param!r}\n")
            try:
                if doc.get("as"):
                    _run_steps([{"login": doc["as"]}], sess, kit, vars_, exec_helper)
                _run_steps(doc.get("setup") or [], sess, kit, vars_, exec_helper)
                _run_steps(doc.get("steps") or [], sess, kit, vars_, exec_helper)
                for i, ex in enumerate(doc["expect"]):
                    passed, observed, expected = _expect(_sub(ex, vars_), sess.last)
                    ok = ok and passed
                    log.write(f"expect[{i}] {full_check(doc['case'], ex['check'])}: "
                              f"{'ok' if passed else 'FAILED'} observed={json.dumps(observed)} expected={json.dumps(expected)}\n")
                    if obs_path is not None:
                        row = {"check": full_check(doc["case"], ex["check"]), "observed": observed, "expected": expected,
                               "pass": passed, "ref": f"{source}#expect[{i}]",
                               "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
                        if param is not None:
                            row["param"] = param
                        with obs_path.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(row) + "\n")
            except ConnectionError as exc:
                log.write(f"infra: {exc}\n")
                return 3
            except (RuntimeError, KeyError, IndexError, ValueError) as exc:
                log.write(f"STEP-ERROR: {exc}\n")  # a helper or capture failed: the step data, not the product
                return 4
            finally:
                try:
                    _run_steps(doc.get("teardown") or [], sess, kit, vars_, exec_helper)
                except ConnectionError:
                    pass
        return 0 if ok else 1


def shell_helper(cwd: Path, env: dict[str, str]):
    def run(cmd: str, log: Any) -> None:
        p = subprocess.run(cmd, shell=True, cwd=str(cwd), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        log.write(f"$ {cmd} -> exit {p.returncode}\n{(p.stdout + p.stderr)[-2000:]}\n")
        if p.returncode != 0:
            raise RuntimeError(f"helper command failed (exit {p.returncode}): {cmd}")
    return run
