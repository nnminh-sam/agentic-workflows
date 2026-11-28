"""E4 + B4: the evidence runner core (harness/evidence.py): smoke first, resume, failure classes, the breaker."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402

from harness import evidence as EV  # noqa: E402

PY = Path(sys.executable).as_posix()


class Fake:
    """run_case stand-in: each case id maps to a list of (exit, output) for its successive attempts."""

    def __init__(self, script: dict[str, list[tuple[int | None, str]]]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    def __call__(self, case: dict, log: Path) -> dict:
        self.calls.append(case["id"])
        code, text = (self.script.get(case["id"]) or [(0, "ok")]).pop(0) if self.script.get(case["id"]) else (0, "ok")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
        return {"exit": code, "seconds": 0.1}


def cases(*ids: str, surface: str = "web") -> list[dict]:
    return [{"id": i, "surface": surface, "command": f"run {i}"} for i in ids]


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="ev-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.slept: list[float] = []

    def go(self, cs: list[dict], fake: Fake, **kw) -> dict:
        return EV.execute(self.dir, cs, fake, sleep=self.slept.append, **kw)

    def test_classify(self) -> None:
        self.assertEqual(EV.classify(0, ""), "PASS")
        self.assertEqual(EV.classify(1, "error: sorry, too many clients already"), "infra")
        self.assertEqual(EV.classify(1, "NeonDbError: Control plane request failed"), "infra")
        self.assertEqual(EV.classify(1, "locator.click: Timeout 5000ms exceeded"), "step")
        self.assertEqual(EV.classify(None, "killed"), "step")
        self.assertEqual(EV.classify(1, "expected 404, got 200"), "product")
        self.assertEqual(EV.classify(1, "at line 502 of flags.ts expected false"), "product")

    def test_smoke_first_then_batch_and_resume(self) -> None:
        cs = cases("TC-1", "TC-2") + cases("TC-3", surface="api")
        fake = Fake({"TC-2": [(1, "expected 404, got 200")]})
        res = self.go(cs, fake)
        self.assertEqual(fake.calls, ["TC-1", "TC-3", "TC-2"])  # one smoke case per surface first
        self.assertEqual(res["summary"], {"PASS": 2, "FAIL": 1})
        self.assertTrue(res["ok"])  # every case finished; TC-2's FAIL is a result
        fake2 = Fake({})
        self.go(cs, fake2)
        self.assertEqual(fake2.calls, [])                        # finished cases are not rerun
        fake3 = Fake({})
        res = self.go(cs, fake3, rerun=["TC-2"])
        self.assertEqual((fake3.calls, res["summary"]), (["TC-2"], {"PASS": 3}))

    def test_product_failure_is_never_retried(self) -> None:
        fake = Fake({"TC-1": [(0, "ok")], "TC-2": [(1, "expected 404, got 200")]})
        self.go(cases("TC-1", "TC-2"), fake)
        self.assertEqual(fake.calls.count("TC-2"), 1)
        self.assertEqual(self.slept, [])

    def test_infra_retries_with_backoff_then_passes(self) -> None:
        fake = Fake({"TC-2": [(1, "too many clients"), (1, "too many clients"), (0, "ok")]})
        res = self.go(cases("TC-1", "TC-2"), fake)
        self.assertEqual(fake.calls.count("TC-2"), 3)
        self.assertEqual(self.slept, list(EV.BACKOFF_SECONDS))
        self.assertEqual(res["summary"], {"PASS": 2})
        run_ = EV.load_run(self.dir)
        self.assertEqual(run_["cases"]["TC-2"]["attempts"], 3)

    def test_breaker_opens_after_infra_in_a_row(self) -> None:
        infra = [(1, "ECONNREFUSED")] * 3
        fake = Fake({"TC-2": list(infra), "TC-3": list(infra)})
        res = self.go(cases("TC-1", "TC-2", "TC-3", "TC-4"), fake)
        self.assertIn("breaker", res["stopped"])
        self.assertEqual(res["escalations"][-1]["class"], "infra")
        self.assertNotIn("TC-4", fake.calls)
        self.assertEqual(EV.load_run(self.dir)["cases"]["TC-4"]["status"], "NOT-RUN")

    def test_smoke_failure_stops_before_the_batch(self) -> None:
        fake = Fake({"TC-1": [(1, "locator.click: Timeout 5000ms exceeded")]})
        res = self.go(cases("TC-1", "TC-2"), fake)
        self.assertEqual(fake.calls, ["TC-1"])
        self.assertIn("smoke case TC-1", res["stopped"])

    def test_step_error_fixed_once_then_escalated(self) -> None:
        cs = cases("TC-1", "TC-2")
        step = (1, "waiting for locator('#toggle')")
        self.go(cs, Fake({"TC-2": [step]}))
        self.assertEqual(EV.load_run(self.dir)["cases"]["TC-2"]["status"], "STEP-ERROR")
        fake = Fake({})
        self.go(cs, fake)
        self.assertEqual(fake.calls, [])                         # not retried until the agent asks
        res = self.go(cs, Fake({"TC-2": [step]}), rerun=["TC-2"])
        self.assertEqual(res["escalations"], [{"case": "TC-2", "class": "step",
                                               "why": "a second step error after the step fix"}])

    def test_time_cap_leaves_the_rest_not_run(self) -> None:
        fake = Fake({})
        res = self.go(cases("TC-1", "TC-2"), fake, deadline=time.time() - 1)
        self.assertEqual(fake.calls, [])
        self.assertEqual(res["stopped"], "time cap reached")
        self.assertEqual(res["summary"], {"NOT-RUN": 2})


class CliTests(Base):
    def test_evidence_run_through_the_cli(self) -> None:
        t = "EV-1"
        run("init", "--ticket", t, "--kind", "bug", *self.p)
        f = self.tmp / "cases.json"
        f.write_text(json.dumps({"cases": [
            {"id": "TC-1", "surface": "web", "command": f'"{PY}" -c "print(1)"'},
            {"id": "TC-2", "surface": "web", "command": f'"{PY}" -c "import sys; print(\'expected 404\'); sys.exit(1)"'},
        ]}), encoding="utf-8")
        code, res = run("evidence", "run", "--ticket", t, "--cases", str(f), *self.p)
        self.assertEqual(code, 0, res)  # a FAIL is a result for the judge, not a failed run
        self.assertEqual((res["ok"], res["summary"]), (True, {"PASS": 1, "FAIL": 1}))
        code, st = run("evidence", "status", "--ticket", t, *self.p)
        self.assertEqual((code, st["cases"]["TC-2"]["status"]), (0, "FAIL"))
        self.assertTrue(Path(st["cases"]["TC-2"]["log"]).is_file())


if __name__ == "__main__":
    unittest.main()
