"""kaneki-evidence: record what a test observed for one check of the ticket's test cases (TC-n.aN).

    from kaneki_evidence import evidence
    evidence("TC-61.a1", observed=res.status_code, expected=404)
    assert res.status_code == 404

While `sdlc evidence run` runs a case it sets KANEKI_EVIDENCE_FILE; each call appends one JSON line there (check id,
observed and expected values, whether they match, the calling test line). Outside a run the variable is unset and
evidence() does nothing. Copied into the repository by `sdlc evidence helper`; no dependencies.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

CHECK_ID = re.compile(r"^TC-\d+\.a\d+$")
_MISSING = object()


def evidence(check: str, observed: Any = None, expected: Any = _MISSING, passed: bool | None = None,
             note: str | None = None, param: Any = None) -> bool:
    if not CHECK_ID.match(str(check)):
        raise ValueError(f'evidence(): "{check}" is not a check id like TC-12.a1')
    ok = passed if passed is not None else (observed == expected if expected is not _MISSING else None)
    path = os.environ.get("KANEKI_EVIDENCE_FILE")
    if path:
        frame = inspect.stack()[1]
        ref = os.path.relpath(frame.filename).replace("\\", "/") + f":{frame.lineno}"
        row: dict[str, Any] = {"check": check, "observed": observed, "pass": ok, "ref": ref,
                               "at": datetime.now(timezone.utc).isoformat()}
        if expected is not _MISSING:
            row["expected"] = expected
        if note:
            row["note"] = note
        if param is not None:
            row["param"] = param
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    return ok is True
