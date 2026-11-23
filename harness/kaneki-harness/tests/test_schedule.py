"""`sdlc schedule` (harness/schedule.py): Windows Task Scheduler tasks, crontab lines, the wrapper, the pass.

Never creates a real scheduled task or crontab entry: SDLC_SCHTASKS / SDLC_CRONTAB point at tests/fakes/fake_sched.py,
which records every argv and keeps the "installed" state in a temp folder."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import schedule as S  # noqa: E402
from harness.errors import HarnessError  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_sched.py"
IS_WINDOWS = os.name == "nt"

SAMPLE_LIST = r"""
Folder: \
HostName:                             MYPC
TaskName:                             \kaneki tick ws-abc123
Next Run Time:                        11/23/2026 10:30:00 AM
Status:                               Ready
Logon Mode:                           Interactive only
Last Run Time:                        11/23/2026 10:15:00 AM
Last Result:                          267011
Task To Run:                          "C:\ws\.sdlc\bin\sdlc-schedule.cmd" tick
Repeat: Every:                        0 Hour(s), 15 Minute(s)
Repeat: Stop If Still Running:        Disabled
"""


class ScheduleBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.state = self.tmp / "fake-sched"
        self.env = mock.patch.dict(os.environ, {
            "SDLC_SCHTASKS": json.dumps([sys.executable, str(FAKE), "schtasks"]),
            "SDLC_CRONTAB": json.dumps([sys.executable, str(FAKE), "crontab"]),
            "FAKE_SCHED_DIR": str(self.state),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ws = self.tmp
        self.bin = self.ws / ".sdlc" / "bin"

    def launchers(self) -> None:
        code, res = run("install", "--target", str(self.ws), "--host", "codex")
        self.assertEqual(code, 0, res)

    def calls(self) -> list[list[str]]:
        path = self.state / "calls.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def tasks(self) -> dict:
        path = self.state / "tasks.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def opt_in(self, ticket: str) -> None:
        code, res = run("init", "--ticket", ticket, "--kind", "feature", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)
        code, res = run("autonomy", "--ticket", ticket, "--set", "on", *self.p)
        self.assertEqual(code, 0, res)


class NamesTests(ScheduleBase):
    def test_task_names_are_per_workspace_and_carry_no_path(self) -> None:
        a, b = self.tmp / "x" / "shop", self.tmp / "y" / "shop"
        self.assertNotEqual(S.task_name(a, "tick"), S.task_name(b, "tick"))
        name = S.task_name(a, "tick")
        self.assertRegex(name, r"^kaneki tick shop-[0-9a-f]{6}$")
        self.assertEqual(S.task_name(a, "recover"), name.replace("tick", "recover"))
        weird = S.workspace_id(self.tmp / "my ws & co%")
        self.assertRegex(weird, r"^[A-Za-z0-9._-]+$")

    def test_parse_list_handles_keys_with_colons(self) -> None:
        info = S.parse_list(SAMPLE_LIST)
        self.assertEqual(info["Folder"], "\\")
        self.assertEqual(info["Next Run Time"], "11/23/2026 10:30:00 AM")
        self.assertEqual(info["Last Result"], "267011")
        self.assertEqual(info["Repeat: Every"], "0 Hour(s), 15 Minute(s)")
        self.assertEqual(info["Repeat: Stop If Still Running"], "Disabled")
        self.assertEqual(info["Task To Run"], '"C:\\ws\\.sdlc\\bin\\sdlc-schedule.cmd" tick')
        view = S._task_view("tick", "kaneki tick ws-abc123",
                            subprocess.CompletedProcess([], 0, SAMPLE_LIST, ""))
        self.assertEqual(view["lastResultText"], "has not run yet")
        self.assertEqual(view["status"], "Ready")


class WindowsTests(ScheduleBase):
    def test_install_creates_both_tasks_and_the_wrapper(self) -> None:
        self.launchers()
        code, res = run("schedule", "--install", "--every", "7", "--platform", "windows", *self.p)
        self.assertEqual(code, 0, res)
        tick, recover = S.task_name(self.ws, "tick"), S.task_name(self.ws, "recover")
        creates = [c for c in self.calls() if c[1] == "/Create"]
        self.assertEqual([c[c.index("/TN") + 1] for c in creates], [tick, recover])
        for c in creates:
            self.assertEqual(c[-1], "/F")
        tasks = self.tasks()
        self.assertEqual(set(tasks), {tick, recover})
        self.assertIn("<Interval>PT7M</Interval>", tasks[tick])  # every-N, written as UTF-16 (the fake decodes it)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", tasks[tick])
        self.assertIn("<LogonTrigger>", tasks[recover])
        self.assertIn("<Arguments>recover</Arguments>", tasks[recover])
        self.assertIn(str(self.bin / "sdlc-schedule.cmd"), tasks[tick])
        wrapper = (self.bin / "sdlc-schedule.cmd").read_bytes()
        self.assertEqual(wrapper.count(b"\n"), wrapper.count(b"\r\n"))
        text = wrapper.decode("utf-8")
        self.assertIn('call "%~dp0sdlc.cmd" schedule --pass %SDLC_PASS% --profile "%SDLC_WS%\\sdlc.profile.json"', text)
        self.assertIn("schedule.log", text)
        self.assertIn("hint", res)  # nothing opted in yet: the reminder
        self.assertIn("schedule install (windows)", (self.ws / ".sdlc" / "run" / "schedule.log").read_text(encoding="utf-8"))

    def test_dry_run_prints_commands_and_changes_nothing(self) -> None:
        self.launchers()
        code, res = run("schedule", "--install", "--dry-run", "--platform", "windows", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.bin / "sdlc-schedule.cmd").exists())
        self.assertFalse((self.ws / ".sdlc" / "run").exists())
        self.assertEqual(len(res["commands"]), 2)
        self.assertIn(f'/TN "{S.task_name(self.ws, "tick")}" /XML', res["commands"][0])
        self.assertIn("<Interval>PT15M</Interval>", res["taskXml"]["tick"])

    def test_install_needs_the_project_launcher(self) -> None:
        code, res = run("schedule", "--install", "--platform", "windows", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("sdlc install", res["error"])
        self.assertEqual(self.calls(), [])

    def test_a_failed_create_is_an_error(self) -> None:
        self.launchers()
        with mock.patch.dict(os.environ, {"FAKE_SCHED_FAIL": S.task_name(self.ws, "recover")}):
            code, res = run("schedule", "--install", "--platform", "windows", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("Access is denied", res["error"])
        self.assertIn(S.task_name(self.ws, "tick"), res["error"])  # says which one was created

    def test_status_reads_the_list_output(self) -> None:
        self.launchers()
        code, res = run("schedule", "--status", "--platform", "windows", *self.p)
        self.assertEqual(code, 0, res)
        self.assertFalse(res["installed"])
        run("schedule", "--install", "--platform", "windows", *self.p)
        code, res = run("schedule", "--status", "--platform", "windows", *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["installed"])
        tick = res["tasks"][0]
        self.assertEqual((tick["nextRun"], tick["status"], tick["lastResultText"]),
                         ("11/23/2026 10:30:00 AM", "Ready", "ok"))
        self.assertTrue(any("schedule install" in ln for ln in res["log"]))
        self.assertIn("LIST", self.calls()[-1])

    def test_uninstall_removes_only_ours(self) -> None:
        self.launchers()
        run("schedule", "--install", "--platform", "windows", *self.p)
        tasks = self.tasks()
        others = {"kaneki tick other-123456": "x", "My Backup": "y"}
        (self.state / "tasks.json").write_text(json.dumps({**tasks, **others}), encoding="utf-8")
        code, res = run("schedule", "--uninstall", "--platform", "windows", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.tasks(), others)
        deleted = [c[c.index("/TN") + 1] for c in self.calls() if c[1] == "/Delete"]
        self.assertEqual(sorted(deleted), sorted([S.task_name(self.ws, "tick"), S.task_name(self.ws, "recover")]))
        self.assertFalse((self.bin / "sdlc-schedule.cmd").exists())
        self.assertTrue((self.bin / "sdlc.cmd").exists())
        code, res = run("schedule", "--uninstall", "--platform", "windows", *self.p)  # again: nothing to do
        self.assertEqual(code, 0, res)
        self.assertEqual(len(res["absent"]), 2)


class PosixTests(ScheduleBase):
    def test_install_prints_lines_and_writes_crontab_only_with_write(self) -> None:
        self.launchers()
        code, res = run("schedule", "--install", "--platform", "posix", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.calls(), [])
        tick_line, reboot_line = res["crontabLines"]
        self.assertTrue(tick_line.startswith("*/15 * * * * /bin/sh "))
        self.assertTrue(tick_line.endswith(f" tick # {S.task_name(self.ws, 'tick')}"))
        self.assertTrue(reboot_line.startswith("@reboot /bin/sh "))
        self.assertTrue(reboot_line.endswith(f" recover # {S.task_name(self.ws, 'recover')}"))
        wrapper = (self.bin / "sdlc-schedule").read_bytes()
        self.assertNotIn(b"\r\n", wrapper)
        self.assertIn(b'sh "$here/sdlc" schedule --pass "$pass" --profile "$ws/sdlc.profile.json"', wrapper)

        (self.state).mkdir(parents=True, exist_ok=True)
        (self.state / "crontab.txt").write_text("MAILTO=me\n0 3 * * * backup.sh\n", encoding="utf-8")
        for _ in range(2):  # re-install replaces, never duplicates
            code, res = run("schedule", "--install", "--write", "--every", "120", "--platform", "posix", *self.p)
            self.assertEqual(code, 0, res)
        tab = (self.state / "crontab.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(tab[:2], ["MAILTO=me", "0 3 * * * backup.sh"])
        self.assertEqual(len(tab), 4)
        self.assertTrue(tab[2].startswith("0 */2 * * * "))

        code, res = run("schedule", "--status", "--platform", "posix", *self.p)
        self.assertTrue(res["installed"], res)

    def test_every_must_fit_cron(self) -> None:
        self.assertEqual(S.cron_schedule(5), "*/5 * * * *")
        self.assertEqual(S.cron_schedule(1440), "0 0 * * *")
        with self.assertRaises(HarnessError):
            S.cron_schedule(90)
        code, res = run("schedule", "--install", "--every", "0", "--platform", "posix", *self.p)
        self.assertEqual(code, 2)

    def test_empty_crontab_and_uninstall_keeps_other_lines(self) -> None:
        self.launchers()
        code, res = run("schedule", "--install", "--write", "--platform", "posix", *self.p)  # no crontab yet
        self.assertEqual(code, 0, res)
        tab = self.state / "crontab.txt"
        other_ws = "*/15 * * * * /bin/sh '/other/.sdlc/bin/sdlc-schedule' tick # kaneki tick other-123456"
        tab.write_text("0 3 * * * backup.sh\n" + tab.read_text(encoding="utf-8") + other_ws + "\n", encoding="utf-8")
        code, res = run("schedule", "--uninstall", "--platform", "posix", *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(len(res["removed"]), 2)
        self.assertEqual(tab.read_text(encoding="utf-8").splitlines(), ["0 3 * * * backup.sh", other_ws])
        self.assertFalse((self.bin / "sdlc-schedule").exists())


class PassTests(ScheduleBase):
    def factory_must_not_run(self, ppath: Path):
        raise AssertionError("the runner was loaded although nothing is opted in")

    def log(self) -> str:
        return (self.ws / ".sdlc" / "run" / "schedule.log").read_text(encoding="utf-8")

    def test_idle_pass_does_not_load_the_runner(self) -> None:
        rc, res = S.run_pass(self.profile, "tick", runner_factory=self.factory_must_not_run)
        self.assertEqual((rc, res["result"]), (0, "idle"))
        self.assertIn("tick idle: no ticket opted in", self.log())
        self.assertFalse((self.ws / ".sdlc" / "run" / "schedule.lock").exists())

    def test_a_running_pass_is_not_overlapped(self) -> None:
        lock = self.ws / ".sdlc" / "run" / "schedule.lock"
        lock.parent.mkdir(parents=True)
        import time
        lock.write_text(json.dumps({"pid": os.getpid(), "epoch": time.time()}), encoding="utf-8")
        rc, res = S.run_pass(self.profile, "tick", runner_factory=self.factory_must_not_run)
        self.assertEqual((rc, res["result"]), (0, "skipped"))
        self.assertTrue(lock.exists())  # still the other pass's lock
        lock.write_text(json.dumps({"pid": os.getpid(), "epoch": time.time() - 3 * 3600}), encoding="utf-8")
        rc, res = S.run_pass(self.profile, "tick", runner_factory=self.factory_must_not_run)
        self.assertEqual(res["result"], "idle")  # a stale lock is taken over

    def test_opted_in_pass_runs_the_runner_and_logs_one_line(self) -> None:
        self.opt_in("T-1")
        self.assertEqual(S.pending_work(self.profile)["optedIn"], ["T-1"])
        fake = mock.Mock()
        fake.tick.return_value = {"started": [{"ticket": "T-1"}], "skipped": [], "recover": []}
        rc, res = S.run_pass(self.profile, "tick", runner_factory=lambda p: fake)
        self.assertEqual((rc, res["result"]), (0, "ran"))
        self.assertIn("tick ok: started 1 (T-1), skipped 0", self.log())
        fake.recover.return_value = {"recovered": [{"ticket": "T-1", "action": "released"}]}
        S.run_pass(self.profile, "recover", runner_factory=lambda p: fake)
        self.assertIn("recover ok: recovered 1 (T-1:released)", self.log())

    def test_a_failing_pass_logs_and_returns_3(self) -> None:
        self.opt_in("T-2")

        def boom(ppath):
            raise HarnessError("the runner runs only claude, codex")
        rc, res = S.run_pass(self.profile, "tick", runner_factory=boom)
        self.assertEqual(rc, 3)
        self.assertIn("tick error: the runner runs only claude, codex", self.log())
        self.assertFalse((self.ws / ".sdlc" / "run" / "schedule.lock").exists())

    def test_cli_pass(self) -> None:
        code, res = run("schedule", "--pass", "recover", *self.p)
        self.assertEqual((code, res["result"]), (0, "idle"))


class WrapperTests(ScheduleBase):
    """The generated wrapper finds Python through the project launcher and runs one real (idle) pass."""

    @unittest.skipUnless(IS_WINDOWS, "Windows wrapper")
    def test_cmd_wrapper_runs_a_pass(self) -> None:
        self.launchers()
        run("schedule", "--install", "--platform", "windows", *self.p)
        env = {k: v for k, v in os.environ.items() if k != "SDLC_HOME"}
        proc = subprocess.run(["cmd", "/c", str(self.bin / "sdlc-schedule.cmd"), "tick"], capture_output=True,
                              text=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("tick idle", (self.ws / ".sdlc" / "run" / "schedule.log").read_text(encoding="utf-8"))
        last = json.loads((self.ws / ".sdlc" / "run" / "schedule.last").read_text(encoding="utf-8"))
        self.assertEqual(last["result"], "idle")
        proc = subprocess.run(["cmd", "/c", str(self.bin / "sdlc-schedule.cmd"), "bogus"], capture_output=True,
                              text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 64)

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_sh_wrapper_runs_a_pass(self) -> None:
        self.launchers()
        code, res = run("schedule", "--install", "--platform", "posix", *self.p)
        self.assertEqual(code, 0, res)
        env = {k: v for k, v in os.environ.items() if k != "SDLC_HOME"}
        proc = subprocess.run(["sh", str(self.bin / "sdlc-schedule"), "recover"], capture_output=True, text=True,
                              env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("recover idle", (self.ws / ".sdlc" / "run" / "schedule.log").read_text(encoding="utf-8"))
        proc = subprocess.run(["sh", str(self.bin / "sdlc-schedule"), "bogus"], capture_output=True, text=True,
                              env=env, timeout=60)
        self.assertEqual(proc.returncode, 64)


if __name__ == "__main__":
    unittest.main()
