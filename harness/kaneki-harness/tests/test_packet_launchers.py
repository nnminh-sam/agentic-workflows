"""M1 wrap-up: packets name the launcher that works on the unit's OS (PowerShell has no `sh`), and a unit that calls
the driver through a per-host launcher is recorded under that host, whatever `.sdlc/host.local.json` names."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import HOME, Base, run  # noqa: E402

from harness.cli import launcher_cmd  # noqa: E402
from harness.state import Ticket  # noqa: E402

IS_WINDOWS = os.name == "nt"
NO_HOST_ENV = {k: v for k, v in os.environ.items() if k not in ("SDLC_HOST", "SDLC_HOME")}
BUNDLED = HOME / "skills" / "kaneki-flow" / "scripts"


class LauncherCmdTests(Base):
    def install(self, *hosts: str) -> Path:
        for host in hosts:
            code, res = run("install", "--target", str(self.tmp), "--host", host)
            self.assertEqual(code, 0, res)
        return self.tmp / ".sdlc" / "bin"

    def test_windows_prefers_own_host_cmd_then_sdlc_cmd(self) -> None:
        bin_dir = self.install("claude", "codex")
        cmd = launcher_cmd(self.profile, "codex", windows=True)
        self.assertTrue(cmd.startswith('& "'), cmd)
        self.assertIn("\\.sdlc\\bin\\sdlc-codex.cmd\"", cmd)
        self.assertNotIn("sh ", cmd)
        self.assertNotIn("/", cmd)
        # a host with no launcher of its own, and the profile binding: the shared sdlc.cmd
        for host in ("gemini", "profile", None):
            cmd = launcher_cmd(self.profile, host, windows=True)
            self.assertIn("\\.sdlc\\bin\\sdlc.cmd\"", cmd, host)
        self.assertTrue((bin_dir / "sdlc-codex.cmd").is_file())

    def test_posix_prefers_own_host_shim_then_sdlc(self) -> None:
        self.install("claude", "codex")
        cmd = launcher_cmd(self.profile, "codex", windows=False)
        self.assertTrue(cmd.startswith('sh "'), cmd)
        self.assertIn("/.sdlc/bin/sdlc-codex\"", cmd)
        self.assertNotIn("\\", cmd)
        self.assertIn("/.sdlc/bin/sdlc\"", launcher_cmd(self.profile, "gemini", windows=False))

    def test_no_project_launcher_falls_back_to_bundled_driver(self) -> None:
        """User-scope install in ~/.claude: no `.sdlc/bin` in the project."""
        posix = launcher_cmd(self.profile, "claude", windows=False)
        self.assertEqual(posix, f'sh "{(BUNDLED / "sdlc").as_posix()}" --profile "{self.profile.as_posix()}"')
        win = launcher_cmd(self.profile, "claude", windows=True)
        self.assertTrue(win.startswith('python "'), win)
        self.assertIn("\\skills\\kaneki-flow\\scripts\\sdlc.py\"", win)

    def test_every_form_passes_the_git_guard(self) -> None:
        sys.path.insert(0, str(BUNDLED))
        import guard_git  # noqa: E402
        self.install("codex")
        for windows in (True, False):
            for host in ("codex", None):
                cmd = f"{launcher_cmd(self.profile, host, windows=windows)} git --ticket T-1 --repo app -- fetch"
                self.assertTrue(guard_git.WRAPPER_RE.search(cmd), cmd)
                self.assertEqual(guard_git.refusals(cmd), [], cmd)
        cmd = f"{launcher_cmd(Path('/nowhere/p.json'), None, windows=True)} git --ticket T-1 --repo app -- fetch"
        self.assertEqual(guard_git.refusals(cmd), [], cmd)

    def test_packet_git_section_names_the_units_launcher(self) -> None:
        self.install("claude", "codex")
        code, _ = run("init", "--ticket", "PL-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            code, pk = run("packet", "--ticket", "PL-1", "--node", "prepare", "--unit", "worker", "--host", "codex", *self.p)
        self.assertEqual(code, 0, pk)
        text = Path(pk["path"]).read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if "Run **every** git command as" in l)
        self.assertIn(launcher_cmd(self.profile, "codex"), line)
        self.assertIn("sdlc-codex.cmd" if IS_WINDOWS else "sdlc-codex\"", line)
        if IS_WINDOWS:
            self.assertNotIn("`sh ", line)


class DriverReachTests(Base):
    """B1: the forms packets hand out actually run. Feature-flags units failed 40x on a bare `sdlc` (not on PATH) and
    15x on `<launcher> --profile P <command>` (argparse took --profile only after the command)."""

    install = LauncherCmdTests.install

    def test_global_options_before_the_command(self) -> None:
        code, res = run("init", "--ticket", "GO-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        code, res = run(*self.p, "status", "--ticket", "GO-1", "--compact")
        self.assertEqual((code, res["ticket"]), (0, "GO-1"), res)
        code, res = run(f"--profile={self.profile}", "--host", "claude", "plan", "--ticket", "GO-1")
        self.assertEqual(code, 0, res)
        code, res = run(*self.p, "digest", str(self.profile))  # digest: positional path, profile accepted
        self.assertEqual(code, 0, res)
        self.assertEqual(len(res["sha256"]), 64)
        code, res = run(*self.p, "contract")  # takes no profile: the option is dropped, not an error
        self.assertEqual(code, 0, res)
        code, res = run(*self.p, "learn", "list")  # nested command
        self.assertEqual(code, 0, res)

    def test_digest_forms(self) -> None:
        code, a = run("digest", str(self.profile))
        code2, b = run("digest", "--path", str(self.profile), "--expect", a["sha256"])
        self.assertEqual((code, code2, a["sha256"]), (0, 0, b["sha256"]))
        code, res = run("digest", str(self.profile), "--expect", "0" * 64)
        self.assertEqual(code, 2)
        self.assertIn("mismatch", res["error"])
        code, res = run("digest")
        self.assertEqual(code, 2)
        self.assertIn("sdlc digest <path>", res["error"])

    def test_packet_driver_section_has_absolute_commands(self) -> None:
        self.install("claude")
        code, _ = run("init", "--ticket", "DR-1", "--kind", "bug", *self.p)
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            code, pk = run("packet", "--ticket", "DR-1", "--node", "prepare", "--unit", "worker", "--host", "claude", *self.p)
        self.assertEqual(code, 0, pk)
        text = Path(pk["path"]).read_text(encoding="utf-8")
        driver = text.split("## Driver", 1)[1].split("## Git", 1)[0]
        posix = (self.tmp / ".sdlc" / "bin" / "sdlc-claude").resolve().as_posix()
        self.assertIn(f'`sh "{posix}" --profile "{self.profile.resolve().as_posix()}"`', driver)
        if IS_WINDOWS:
            self.assertIn("- PowerShell: `& \"", driver)
            self.assertIn("sdlc-claude.cmd", driver)
        self.assertNotIn("sdlc digest --path", text)
        self.assertNotIn("{{", text)

    @unittest.skipUnless(__import__("shutil").which("sh"), "needs sh")
    def test_the_sh_form_runs_from_any_directory(self) -> None:
        self.install("claude")
        cmd = __import__("harness.cli", fromlist=["posix_launcher_cmd"]).posix_launcher_cmd(self.profile, "claude")
        proc = subprocess.run(["sh", "-c", f"{cmd} digest \"{self.profile.as_posix()}\""], capture_output=True,
                              text=True, env=NO_HOST_ENV, cwd=str(HOME), timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("sha256", proc.stdout)


class PerHostLauncherRecordTests(Base):
    """Two hosts installed; the workspace binding names claude; units run through `sdlc-codex` then `sdlc-claude`."""

    def setUp(self) -> None:
        super().setUp()
        for host in ("claude", "codex"):
            code, res = run("install", "--target", str(self.tmp), "--host", host)
            self.assertEqual(code, 0, res)
        binding = json.loads((self.tmp / ".sdlc" / "host.local.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["host"], "claude")
        code, res = run("init", "--ticket", "PH-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)

    def launch(self, host: str, *args: str) -> dict:
        bin_dir = self.tmp / ".sdlc" / "bin"
        argv = (["cmd", "/c", str(bin_dir / f"sdlc-{host}.cmd")] if IS_WINDOWS
                else ["sh", str(bin_dir / f"sdlc-{host}")]) + [*args, *self.p]
        proc = subprocess.run(argv, capture_output=True, text=True, env=NO_HOST_ENV)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def unit_through(self, host: str) -> dict:
        unit = ("--ticket", "PH-1", "--node", "prepare", "--unit", "worker")
        pk = self.launch(host, "packet", *unit)
        self.assertEqual(pk["execution"]["host"], host, pk)
        text = Path(pk["path"]).read_text(encoding="utf-8")
        self.assertIn(f"sdlc-{host}.cmd" if IS_WINDOWS else f"sdlc-{host}\"", text)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n", encoding="utf-8")
        return self.launch(host, "record", *unit, "--result", pk["resultPath"], "--model", pk["model"])

    def events(self, kind: str) -> list[dict]:
        events, problems = Ticket(self.tmp / "tickets", "PH-1").verify_log()
        self.assertEqual(problems, [])
        return [e for e in events if e.get("event") == kind]

    def test_record_keeps_the_launchers_host_and_logs_the_switch(self) -> None:
        rec = self.unit_through("codex")
        self.assertEqual(rec["recorded"]["host"], "codex", rec)
        self.assertEqual(self.events("host-switch"), [])

        rec = self.unit_through("claude")
        self.assertEqual(rec["recorded"]["host"], "claude", rec)
        sw = self.events("host-switch")
        self.assertEqual([(e["from"], e["to"]) for e in sw], [("codex", "claude")])

        state = Ticket(self.tmp / "tickets", "PH-1").load()
        self.assertEqual([u["host"] for u in state["nodes"]["prepare"]["units"]], ["codex", "claude"])


if __name__ == "__main__":
    unittest.main()
