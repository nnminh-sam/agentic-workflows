"""Tests for Phase 3 multi-host `sdlc install` and external runner `sdlc dispatch`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME / "tests"))

from harness import dispatch as D  # noqa: E402
from harness import hosts as H  # noqa: E402
from harness.errors import HarnessError  # noqa: E402
from test_lifecycle import Base, run  # noqa: E402


class MultiHostInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = Path(tempfile.mkdtemp(prefix="sdlc-multi-install-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.target, ignore_errors=True)

    def test_default_install_is_antigravity_with_working_shim(self) -> None:
        code, res = run("install", "--target", str(self.target))
        self.assertEqual(code, 0, res)
        self.assertEqual(res["host"], "antigravity")

        skill_md = self.target / ".agents" / "skills" / "kaneki-flow" / "SKILL.md"
        self.assertTrue(skill_md.is_file())
        skill_text = skill_md.read_text(encoding="utf-8")
        self.assertIn("Host Tool Binding (Antigravity)", skill_text)
        self.assertNotIn("$CLAUDE_SKILL_DIR", skill_text)

        self.assertTrue((self.target / ".agents" / "skills" / "kaneki-flow" / "references" / "roles" / "deep.md").is_file())
        self.assertTrue((self.target / ".sdlc" / "host.local.json").is_file())
        binding = json.loads((self.target / ".sdlc" / "host.local.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["host"], "antigravity")

        shim = self.target / ".sdlc" / "bin" / "sdlc"
        self.assertTrue(shim.is_file())
        raw = shim.read_bytes()
        self.assertNotIn(b"\r\n", raw)

        # Execute the shim cleanly with SDLC_HOME unset
        proc = subprocess.run(
            ["sh", str(shim), "digest", "--path", str(HOME / "graph" / "lifecycle.json")],
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k != "SDLC_HOME"},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("sha256", proc.stdout)

    def test_every_host_installs_tracks_edits_and_supports_dry_run(self) -> None:
        expected_skill = {
            "antigravity": ".agents/skills/kaneki-flow/SKILL.md",
            "claude": ".claude/skills/kaneki-flow/SKILL.md",
            "codex": ".agents/skills/kaneki-flow/SKILL.md",
            "gemini": ".gemini/skills/kaneki-flow/SKILL.md",
            "cursor": ".cursor/rules/kaneki-flow.mdc",
            "opencode": ".opencode/skills/kaneki-flow/SKILL.md",
            "generic": ".sdlc/playbooks/flow.md",
        }
        for host in H.KNOWN_HOSTS:
            d = self.target / host
            d.mkdir()
            code, dry = run("install", "--target", str(d), "--host", host, "--dry-run")
            self.assertEqual(code, 0, dry)
            self.assertFalse((d / ".sdlc").exists(), f"dry-run created .sdlc for {host}")

            code, res = run("install", "--target", str(d), "--host", host)
            self.assertEqual(code, 0, res)
            sf = d / expected_skill[host]
            self.assertTrue(sf.is_file(), f"missing skill/rule for {host}: {sf}")
            text = sf.read_text(encoding="utf-8")
            if host != "claude":
                self.assertNotIn("$CLAUDE_SKILL_DIR", text, host)
            if host == "codex":
                self.assertTrue((d / "AGENTS.md").is_file())
            if host == "gemini":
                self.assertTrue((d / "GEMINI.md").is_file())
                self.assertTrue((d / ".gemini" / "scripts" / "guard-git").is_file())

            # Re-install preserves edited file until --force
            sf.write_text(text + "\n<!-- custom -->\n", encoding="utf-8")
            code, res2 = run("install", "--target", str(d), "--host", host)
            self.assertEqual(code, 0, res2)
            self.assertTrue(res2["keptYourEdits"], host)
            self.assertIn("<!-- custom -->", sf.read_text(encoding="utf-8"))

            code, res3 = run("install", "--target", str(d), "--host", host, "--force")
            self.assertEqual(code, 0, res3)
            self.assertNotIn("<!-- custom -->", sf.read_text(encoding="utf-8"))

    def test_with_my_git_rejected_outside_claude(self) -> None:
        code, res = run("install", "--target", str(self.target), "--host", "antigravity", "--with-my-git")
        self.assertEqual(code, 2, res)
        self.assertIn("--with-my-git", res["error"])


class DispatchTests(Base):
    def test_build_argv_runners_effort_and_braces(self) -> None:
        codex_high = {"host": "codex", "runner": "codex", "model": "gpt-5.2-codex", "effort": "high"}
        argv = D.build_argv(codex_high, "Prompt {braces}", "/tmp/p.md", "/tmp/r.md", tier="deep")
        self.assertEqual(argv, ["codex", "exec", "-m", "gpt-5.2-codex", "-c", "model_reasoning_effort=high", "Prompt {braces}"])

        no_effort = {"host": "codex", "runner": "codex", "model": "gpt-5.2-codex", "effort": None}
        self.assertEqual(
            D.build_argv(no_effort, "Run", "/tmp/p.md", "/tmp/r.md", tier="routine"),
            ["codex", "exec", "-m", "gpt-5.2-codex", "Run"],
        )

        cur = {"host": "cursor", "runner": "claude", "model": "inherit", "effort": None}
        self.assertEqual(
            D.build_argv(cur, "Run", "/tmp/p.md", "/tmp/r.md", tier="routine"),
            ["claude", "-p", "Run", "--output-format", "json"],
        )

        with self.assertRaises(HarnessError):
            D.build_argv({"host": "antigravity", "model": "flash"}, "Run", "/tmp/p.md", "/tmp/r.md", tier="routine")

        self.assertEqual(D.extract_verdict("status: ok\nVerdict: PASS\n"), ("PASS", None))
        self.assertEqual(
            D.extract_verdict("## Summary\n- **Verdict**: NEEDS CHANGES\n- failureClass: artifact\n"),
            ("NEEDS CHANGES", "artifact"),
        )

    def test_dispatch_dry_run_and_subprocess_end_to_end(self) -> None:
        code, _ = run("init", "--ticket", "DS-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)

        code, dry = run("dispatch", "--ticket", "DS-1", "--node", "prepare", "--unit", "worker",
                        "--host", "codex", "--dry-run", *self.p)
        self.assertEqual(code, 0, dry)
        self.assertTrue(dry["dryRun"])
        self.assertEqual(dry["argv"][:4], ["codex", "exec", "-m", "gpt-5-codex-mini"])
        self.assertEqual(dry["argv"][4:6], ["-c", "model_reasoning_effort=low"])

        runner = self.tmp / "fake_runner.py"
        runner.write_text(
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "res = pathlib.Path(args[0])\n"
            "verdict = args[1]\n"
            "mode = args[2]\n"
            "if mode == 'fail_exit':\n"
            "    sys.exit(3)\n"
            "if mode == 'ok':\n"
            "    res.parent.mkdir(parents=True, exist_ok=True)\n"
            "    res.write_text(f'status: ok\\nVerdict: {verdict}\\n' if verdict != 'NONE' else 'status: ok\\n')\n"
            "    print(json.dumps({'usage': {'input_tokens': 120, 'output_tokens': 80}}))\n",
            encoding="utf-8",
        )

        cmd_ok = f"{sys.executable} {runner} {{result}} NONE ok"
        code, res = run("dispatch", "--ticket", "DS-1", "--node", "prepare", "--unit", "worker",
                        "--host", "antigravity", "--command", cmd_ok, *self.p)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["ok"])
        self.assertEqual(res["tokens"], 200)
        self.assertEqual(res["recorded"]["model"], "flash_lite")
        self.assertEqual(res["recorded"]["tokens"], 200)
        self.assertTrue(Path(res["log"]).is_file())
        self.assertIn("200", (self.tdir("DS-1") / "token-usage.md").read_text(encoding="utf-8"))

        # Reviewer FAIL extraction + recording
        self.complete("DS-1", "prepare")
        cmd_fail_exit = f"{sys.executable} {runner} {{result}} NONE fail_exit"
        code, err1 = run("dispatch", "--ticket", "DS-1", "--node", "investigate", "--unit", "worker",
                         "--host", "antigravity", "--command", cmd_fail_exit, *self.p)
        self.assertEqual(code, 2, err1)
        self.assertIn("failed with exit code 3", err1["error"])

        # Missing result file fails closed (G7)
        cmd_no_file = f"{sys.executable} {runner} {{result}} NONE no_write"
        code, err2 = run("dispatch", "--ticket", "DS-1", "--node", "investigate", "--unit", "worker",
                         "--host", "antigravity", "--command", cmd_no_file, *self.p)
        self.assertEqual(code, 2, err2)
        self.assertIn("G7:", err2["error"])


if __name__ == "__main__":
    unittest.main()
