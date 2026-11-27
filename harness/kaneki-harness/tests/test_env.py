"""E3 / B3 / B4: the harness-owned evidence environment and the prepare baseline (harness/env.py)."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402

from harness import env as E  # noqa: E402
from harness.cli import Session  # noqa: E402

PY = Path(sys.executable).as_posix()

# A stand-in for `docker`: inspect / exec psql / exec pg_isready / restart, with max_connections kept in a file.
FAKE_DOCKER = r'''
import json, sys
from pathlib import Path
st = Path(__file__).with_name("pg.json")
data = json.loads(st.read_text()) if st.exists() else {"max": 20, "pending": None, "dbs": ["postgres"], "restarts": 0}
a = sys.argv[1:]
def save(): st.write_text(json.dumps(data))
if a[0] == "inspect": print("true"); sys.exit(0)
if a[0] == "restart":
    if data["pending"]: data["max"], data["pending"] = data["pending"], None
    data["restarts"] += 1; save(); sys.exit(0)
if a[0] == "exec" and a[2] == "pg_isready": sys.exit(0)
sql = a[-1].lower()
if sql == "show max_connections": print(data["max"])
elif sql.startswith("alter system set max_connections"): data["pending"] = int(sql.split("=")[1]); save()
elif "pg_stat_activity" in sql: print(f"{data['max']}|3")
elif sql.startswith("select 1 from pg_database"): print("1" if sql.split("'")[1] in data["dbs"] else "")
elif sql.startswith("create database"): data["dbs"].append(sql.split('"')[1]); save()
elif sql.startswith("drop database"): data["dbs"].remove(sql.split('"')[1]); save()
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EnvTests(Base):
    T = "ENV-1"

    def setUp(self) -> None:
        super().setUp()
        code, res = run("init", "--ticket", self.T, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        tools = self.tmp / "tools"
        tools.mkdir()
        (tools / "fake_docker.py").write_text(FAKE_DOCKER, encoding="utf-8")
        self.pg = tools / "pg.json"
        (self.tmp / "test.env").write_text("DATABASE_URL=postgres://u:secret@localhost:5432/base\nSECRET_TOKEN=s3cr3t\n",
                                           encoding="utf-8")
        self.port = free_port()
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof.setdefault("environment", {})["evidence"] = {
            "envFile": "test.env",
            "database": {"container": "pg", "docker": f'"{PY}" "{(tools / "fake_docker.py").as_posix()}"',
                         "maxConnections": 100, "name": "app_{ticket}", "urlVars": ["DATABASE_URL"]},
            "migrate": f'"{PY}" -c "import os; assert os.environ[\'DATABASE_URL\'].endswith(\'/app_env_1\')"',
            "server": {"command": f'"{PY}" -m http.server {{port}} --bind 127.0.0.1', "port": self.port,
                       "health": "/", "readyTimeoutSeconds": 30},
            "seed": f'"{PY}" -c "import sys; print(sys.argv[1])" {{baseUrl}}',
        }
        self.profile.write_text(json.dumps(prof), encoding="utf-8")
        self.addCleanup(lambda: run("env", "down", "--ticket", self.T, *self.p))

    def session(self) -> Session:
        return Session(argparse.Namespace(profile=str(self.profile), ticket=self.T, host=None))

    def test_check_up_reuse_exec_down(self) -> None:
        code, chk = run("env", "check", "--ticket", self.T, *self.p)
        self.assertEqual(code, 1, chk)                       # max_connections 20 < 100: fixable by env up
        self.assertEqual(chk["fixable"], ["connections"])
        code, res = run("env", "up", "--ticket", self.T, *self.p)
        self.assertEqual(code, 0, res)
        steps = {st["step"]: st for st in res["steps"]}
        self.assertEqual((steps["max-connections"]["before"], steps["max-connections"]["after"]), (20, 100))
        self.assertTrue(steps["database"]["created"])
        self.assertEqual(res["baseUrl"], f"http://localhost:{self.port}")
        self.assertNotIn("s3cr3t", json.dumps(res))
        self.assertNotIn("secret@", (self.tdir(self.T) / "env.json").read_text(encoding="utf-8"))
        pg = json.loads(self.pg.read_text())
        self.assertEqual((pg["max"], pg["restarts"], "app_env_1" in pg["dbs"]), (100, 1, True))

        code, again = run("env", "up", "--ticket", self.T, *self.p)
        self.assertEqual((code, again.get("reused")), (0, True))
        code, chk = run("env", "check", "--ticket", self.T, *self.p)
        self.assertEqual(code, 0, chk)                       # our own server on the port is fine

        rc = E.exec_in(self.session(), [PY, "-c", "import os,sys; sys.exit(0 if os.environ['SECRET_TOKEN']=='s3cr3t' "
                                                  "and os.environ['BASE_URL'].startswith('http://localhost') else 3)"])
        self.assertEqual(rc, 0)

        code, res = run("env", "down", "--ticket", self.T, "--drop-db", *self.p)
        self.assertEqual((code, res["stopped"], res["droppedDb"]), (0, True, "app_env_1"))
        code, st = run("env", "status", "--ticket", self.T, *self.p)
        self.assertEqual((code, st["alive"]), (1, False))

    def test_failed_step_is_classified(self) -> None:
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof["environment"]["evidence"]["migrate"] = f'"{PY}" -c "import sys; sys.exit(4)"'
        self.profile.write_text(json.dumps(prof), encoding="utf-8")
        code, res = run("env", "up", "--ticket", self.T, *self.p)
        self.assertEqual((code, res["failedStep"], res["class"]), (1, "migrate", "setup"))

    def test_no_block_is_g7(self) -> None:
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof["environment"].pop("evidence")
        self.profile.write_text(json.dumps(prof), encoding="utf-8")
        code, res = run("env", "check", "--ticket", self.T, *self.p)
        self.assertEqual(code, 2)
        self.assertIn("environment.evidence", res["error"])

    def test_baseline_records_failures_and_refuses_failed_install(self) -> None:
        s = self.session()
        rid = s.ctx["repositories"][0]["id"]
        stacks = {rid: {"commands": {"lint": f'"{PY}" -c "print(1)" {{paths}}',
                                     "unitTest": f'"{PY}" -c "import sys; sys.exit(1)" {{testPaths}}'}}}
        res = E.baseline(s, stacks)
        self.assertTrue(res["ok"])                            # a failing test is the baseline, not a refusal
        self.assertEqual(res["summary"][rid], {"lint": 0, "unitTest": 1})
        self.assertTrue(res["repos"][0]["head"])
        self.assertTrue((self.tdir(self.T) / "baseline.json").is_file())
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof["environment"]["baseline"] = {"install": {rid: f'"{PY}" -c "import sys; sys.exit(2)"'}}
        self.profile.write_text(json.dumps(prof), encoding="utf-8")
        res = E.baseline(self.session(), stacks)
        self.assertFalse(res["ok"])
        self.assertEqual(res["repos"][0]["blocked"], "install failed")


if __name__ == "__main__":
    unittest.main()
