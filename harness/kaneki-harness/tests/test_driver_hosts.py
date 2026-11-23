"""M1 track B driver fixes: portable packet paths, Windows-safe `--command` splitting, the Antigravity dispatch
refusal (plan D6), and the agent (host) made visible: the `host-switch` decision event and host per node and unit
in STATUS.md, `sdlc tickets`, `sdlc ticket-detail` and `sdlc status`."""

from __future__ import annotations

import json
import os
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME / "tests"))

from harness import dispatch as D  # noqa: E402
from harness.errors import HarnessError  # noqa: E402
from harness.state import Ticket  # noqa: E402
from test_lifecycle import Base, run  # noqa: E402

NO_HOST_ENV = {k: v for k, v in os.environ.items() if k != "SDLC_HOST"}


class SplitCommandTests(unittest.TestCase):
    def test_windows_keeps_backslashes_and_strips_quotes(self) -> None:
        self.assertEqual(D.split_command(r"C:\Py\python.exe D:\w\run.py {result} NONE ok", windows=True),
                         [r"C:\Py\python.exe", r"D:\w\run.py", "{result}", "NONE", "ok"])
        self.assertEqual(D.split_command(r'"C:\Program Files\py.exe" ' + "'a b' --x=\"c d\"", windows=True),
                         [r"C:\Program Files\py.exe", "a b", "--x=c d"])
        self.assertEqual(D.split_command('  a   ""  b ', windows=True), ["a", "", "b"])
        with self.assertRaises(HarnessError):
            D.split_command('a "b', windows=True)

    def test_posix_behaviour_unchanged(self) -> None:
        for cmd in (r'a "b c" d\ e', "x 'y z' --k=\"v w\"", r"p\\q"):
            self.assertEqual(D.split_command(cmd, windows=False), shlex.split(cmd))

    def test_json_argv_on_every_os(self) -> None:
        argv = [r"C:\Program Files\py.exe", "run.py", "{result}"]
        for windows in (True, False):
            self.assertEqual(D.split_command(json.dumps(argv), windows=windows), argv)
        with self.assertRaises(HarnessError):
            D.split_command("[1, 2]")

    def test_build_argv_command_override_keeps_windows_path(self) -> None:
        exe = {"host": "codex", "runner": "codex", "model": "m", "effort": None}
        cmd = json.dumps([r"C:\Py\python.exe", r"D:\w\run.py", "{result}"])
        self.assertEqual(D.build_argv(exe, "P", "p.md", r"D:\t\r.md", tier="light", command_override=cmd),
                         [r"C:\Py\python.exe", r"D:\w\run.py", r"D:\t\r.md"])


class PacketPathTests(Base):
    def test_packet_role_card_is_portable_posix(self) -> None:
        code, _ = run("init", "--ticket", "PP-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        self.complete("PP-1", "prepare")
        code, pk = run("packet", "--ticket", "PP-1", "--node", "investigate", "--unit", "worker", *self.p)
        self.assertEqual(code, 0, pk)
        self.assertEqual(pk["roleCard"], "playbooks/roles/deep.md")
        self.assertNotIn("\\", pk["roleCard"])


class AntigravityDispatchRefusalTests(Base):
    def setUp(self) -> None:
        super().setUp()
        code, res = run("init", "--ticket", "AG-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        self.cmd = json.dumps([sys.executable, "-c", "print('never runs')"])

    def assert_refused(self, *extra: str) -> None:
        packets = self.tdir("AG-1") / "packets"
        before = sorted(packets.iterdir()) if packets.is_dir() else []
        code, err = run("dispatch", "--ticket", "AG-1", "--node", "prepare", "--unit", "worker", *extra, *self.p)
        self.assertEqual(code, 2, err)
        self.assertIn("refuses host 'antigravity'", err["error"])
        self.assertIn("own subagents", err["error"])
        self.assertEqual(sorted(packets.iterdir()) if packets.is_dir() else [], before, "a refused dispatch wrote a packet")

    def test_refused_whatever_runner_or_command(self) -> None:
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            self.assert_refused("--host", "antigravity", "--command", self.cmd)
            self.assert_refused("--host", "antigravity", "--runner", "codex")
            self.assert_refused("--host", "antigravity", "--dry-run")
            self.assert_refused("--host", "antigravity", "--runner", "claude", "--command", self.cmd, "--dry-run")

    def test_refused_with_binding_command_template_and_env(self) -> None:
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            code, res = run("host", "--set", "antigravity", *self.p)
            self.assertEqual(code, 0, res)
            path = Path(res["bindingPath"])
            binding = json.loads(path.read_text(encoding="utf-8"))
            binding["commandTemplate"] = [sys.executable, "-c", "print('never runs')"]
            binding["runner"] = "codex"
            path.write_text(json.dumps(binding), encoding="utf-8")
            self.assert_refused()
        with mock.patch.dict(os.environ, {**NO_HOST_ENV, "SDLC_HOST": "antigravity"}, clear=True):
            self.assert_refused("--command", self.cmd)

    def test_other_hosts_still_dispatch(self) -> None:
        code, dry = run("dispatch", "--ticket", "AG-1", "--node", "prepare", "--unit", "worker",
                        "--host", "codex", "--dry-run", *self.p)
        self.assertEqual(code, 0, dry)
        self.assertEqual(dry["argv"][0], "codex")
        with self.assertRaises(HarnessError):
            D.check_dispatchable("antigravity")
        for host in ("codex", "claude", "profile", None):
            D.check_dispatchable(host)


class HostSwitchAndVisibilityTests(Base):
    MODELS = {"claude": "haiku", "codex": "gpt-5-codex-mini"}

    def record(self, ticket: str, host: str) -> dict:
        code, pk = run("packet", "--ticket", ticket, "--node", "prepare", "--unit", "worker", "--host", host, *self.p)
        self.assertEqual(code, 0, pk)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n", encoding="utf-8")
        code, rec = run("record", "--ticket", ticket, "--node", "prepare", "--unit", "worker", "--result",
                        pk["resultPath"], "--model", self.MODELS[host], "--tokens", "10", "--host", host, *self.p)
        self.assertEqual(code, 0, rec)
        return rec

    def events(self, ticket: str, kind: str) -> list[dict]:
        events, problems = Ticket(self.tmp / "tickets", ticket).verify_log()
        self.assertEqual(problems, [])
        return [e for e in events if e.get("event") == kind]

    def test_host_switch_event_chain_and_views(self) -> None:
        t = "HS-1"
        code, _ = run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            self.record(t, "claude")
            self.assertEqual(self.events(t, "host-switch"), [], "the first record is not a switch")
            self.record(t, "claude")
            self.assertEqual(self.events(t, "host-switch"), [], "same host is not a switch")
            self.record(t, "codex")

            switches = self.events(t, "host-switch")
            self.assertEqual(len(switches), 1)
            sw = switches[0]
            self.assertEqual((sw["node"], sw["unit"], sw["from"], sw["to"]), ("prepare", "worker", "claude", "codex"))
            self.assertEqual((sw["previousNode"], sw["previousUnit"]), ("prepare", "worker"))

            # the chain still verifies: a full Session load runs the integrity check
            code, st = run("status", "--ticket", t, *self.p)
            self.assertEqual(code, 0, st)
            self.assertEqual((st["host"], st["hosts"]), ("codex", ["claude", "codex"]))
            prepare = next(n for n in st["nodes"] if n["node"] == "prepare")
            self.assertEqual(prepare["hosts"], ["claude", "codex"])

            code, det = run("ticket-detail", "--ticket", t, *self.p)
            self.assertEqual(code, 0, det)
            self.assertEqual((det["host"], det["hosts"]), ("codex", ["claude", "codex"]))
            node = next(n for n in det["nodes"] if n["node"] == "prepare")
            self.assertEqual(node["hosts"], ["claude", "codex"])
            self.assertEqual([u["host"] for u in node["unitRecords"]], ["claude", "claude", "codex"])
            self.assertTrue(all("host" in u for u in node["plannedUnits"]))
            self.assertTrue(any(e["event"] == "host-switch" for e in det["events"]))

            code, lst = run("tickets", *self.p)
            self.assertEqual(code, 0, lst)
            item = next(i for i in lst["tickets"] if i["ticket"] == t)
            self.assertEqual((item["host"], item["hosts"]), ("codex", ["claude", "codex"]))

            md = (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8")
            self.assertIn("| Agent |", md)
            self.assertIn("**Agent:** codex (also claude)", md)
            row = next(line for line in md.splitlines() if line.startswith("| 1 | prepare"))
            self.assertIn("| claude, codex |", row)
            self.assertIn("**host-switch** node=prepare unit=worker from=claude to=codex", md)

    def test_profile_host_counts_as_a_host(self) -> None:
        t = "HS-2"
        code, _ = run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            code, pk = run("packet", "--ticket", t, "--node", "prepare", "--unit", "worker", *self.p)
            self.assertEqual(code, 0, pk)
            Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
            Path(pk["resultPath"]).write_text("status: ok\n", encoding="utf-8")
            code, rec = run("record", "--ticket", t, "--node", "prepare", "--unit", "worker", "--result",
                            pk["resultPath"], "--model", pk["model"], *self.p)
            self.assertEqual(code, 0, rec)
            self.assertNotIn("host", rec["recorded"])
            code, st = run("status", "--ticket", t, *self.p)
            self.assertEqual((st["host"], st["hosts"]), ("profile", ["profile"]))
            self.record(t, "claude")
            sw = self.events(t, "host-switch")
            self.assertEqual([(e["from"], e["to"]) for e in sw], [("profile", "claude")])

    def test_ticket_without_records_has_no_host(self) -> None:
        code, _ = run("init", "--ticket", "HS-3", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        code, st = run("status", "--ticket", "HS-3", *self.p)
        self.assertEqual((st["host"], st["hosts"]), (None, []))
        md = (self.tdir("HS-3") / "STATUS.md").read_text(encoding="utf-8")
        self.assertNotIn("**Agent:**", md)


if __name__ == "__main__":
    unittest.main()
