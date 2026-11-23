"""`sdlc install`: required --host, several hosts per workspace, Windows/POSIX launchers, per-host identity,
the shared `.agents/` skill, host-bound driver commands and the one-copy kaneki-flow controller text."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import HOME, Base, run  # noqa: E402

from harness import hosts as H  # noqa: E402
from harness import install as I  # noqa: E402
from harness.cli import main  # noqa: E402

PROJECT_HOSTS = [h for h in H.KNOWN_HOSTS]
CODEX_TITLE = "Host Tool Binding (OpenAI Codex CLI)"
ANTIGRAVITY_TITLE = "Host Tool Binding (Antigravity)"
IS_WINDOWS = os.name == "nt"


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("SDLC_HOME", "SDLC_HOST")}
    env.update(extra)
    return env


def last_json(text: str) -> dict:
    return json.loads(text.strip().splitlines()[-1])


class TempTarget(unittest.TestCase):
    def setUp(self) -> None:
        self.target = Path(tempfile.mkdtemp(prefix="sdlc-launch-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.target, ignore_errors=True)


class RequiredHostTests(TempTarget):
    def test_install_without_host_is_refused(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["install", "--target", str(self.target)])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--host", err.getvalue())
        self.assertEqual(list(self.target.iterdir()), [])

    def test_install_function_has_no_default_host(self) -> None:
        with self.assertRaises(TypeError):
            I.install(HOME, self.target)  # type: ignore[call-arg]


class LauncherFileTests(TempTarget):
    def test_every_host_writes_driver_and_own_launchers(self) -> None:
        for host in PROJECT_HOSTS:
            d = self.target / host
            d.mkdir()
            code, res = run("install", "--target", str(d), "--host", host)
            self.assertEqual(code, 0, res)
            bin_dir = d / ".sdlc" / "bin"
            for name in ("sdlc", "sdlc.cmd", f"sdlc-{host}", f"sdlc-{host}.cmd"):
                self.assertTrue((bin_dir / name).is_file(), f"{host}: {name}")
            # POSIX scripts keep LF; .cmd files use CRLF (cmd's label/goto parsing needs it).
            for name in ("sdlc", f"sdlc-{host}"):
                self.assertNotIn(b"\r\n", (bin_dir / name).read_bytes(), f"{host}: {name}")
            for name in ("sdlc.cmd", f"sdlc-{host}.cmd"):
                raw = (bin_dir / name).read_bytes()
                self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), f"{host}: {name}")
            self.assertIn(f"SDLC_HOST={host}\n", (bin_dir / f"sdlc-{host}").read_text(encoding="utf-8"))
            self.assertIn(f'set "SDLC_HOST={host}"', (bin_dir / f"sdlc-{host}.cmd").read_text(encoding="utf-8"))
            self.assertIn("sdlc.py", (bin_dir / "sdlc.cmd").read_text(encoding="utf-8"))
            self.assertEqual(res["launchers"]["windows"], str(bin_dir / f"sdlc-{host}.cmd"))
            self.assertEqual(res["launchers"]["posix"], str(bin_dir / f"sdlc-{host}"))
            # Only this host's launcher pair: one launcher per installed host.
            others = [h for h in PROJECT_HOSTS if h != host and (bin_dir / f"sdlc-{h}").exists()]
            self.assertEqual(others, [], host)

    def test_several_hosts_in_one_workspace_each_get_a_launcher(self) -> None:
        for host in ("claude", "codex", "antigravity"):
            code, res = run("install", "--target", str(self.target), "--host", host)
            self.assertEqual(code, 0, res)
        bin_dir = self.target / ".sdlc" / "bin"
        for host in ("claude", "codex", "antigravity"):
            self.assertTrue((bin_dir / f"sdlc-{host}.cmd").is_file(), host)
            self.assertTrue((bin_dir / f"sdlc-{host}").is_file(), host)
        self.assertTrue((self.target / ".claude" / "skills" / "kaneki-flow" / "SKILL.md").is_file())
        self.assertTrue((self.target / ".agents" / "skills" / "kaneki-flow" / "SKILL.md").is_file())
        # The workspace binding is written once (first host) and kept; launchers carry the identity.
        binding = json.loads((self.target / ".sdlc" / "host.local.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["host"], "claude")

    def test_user_edited_launcher_is_kept_until_force(self) -> None:
        run("install", "--target", str(self.target), "--host", "codex")
        f = self.target / ".sdlc" / "bin" / "sdlc-codex.cmd"
        f.write_bytes(f.read_bytes() + b"rem mine\r\n")
        code, res = run("install", "--target", str(self.target), "--host", "codex")
        self.assertIn("project:.sdlc/bin/sdlc-codex.cmd", res["keptYourEdits"])
        code, res = run("install", "--target", str(self.target), "--host", "codex", "--force")
        self.assertNotIn(b"rem mine", f.read_bytes())


class LauncherRunTests(TempTarget):
    def setUp(self) -> None:
        super().setUp()
        code, res = run("install", "--target", str(self.target), "--host", "codex")
        self.assertEqual(code, 0, res)
        self.lifecycle = str(HOME / "graph" / "lifecycle.json")

    @unittest.skipUnless(IS_WINDOWS, "Windows launcher")
    def test_sdlc_cmd_runs_from_cmd_and_powershell(self) -> None:
        bin_dir = self.target / ".sdlc" / "bin"
        for launcher in ("sdlc.cmd", "sdlc-codex.cmd"):
            proc = subprocess.run(["cmd", "/c", str(bin_dir / launcher), "digest", "--path", self.lifecycle],
                                  capture_output=True, text=True, env=clean_env())
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("sha256", proc.stdout)
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            # The exact form the bindings give the agents: a relative path from the workspace root.
            proc = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command",
                                   f".sdlc\\bin\\sdlc-codex.cmd digest --path '{self.lifecycle}'"],
                                  capture_output=True, text=True, cwd=str(self.target), env=clean_env())
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("sha256", proc.stdout)

    @unittest.skipUnless(IS_WINDOWS, "Windows launcher")
    def test_sdlc_cmd_passes_the_exit_code(self) -> None:
        proc = subprocess.run(["cmd", "/c", str(self.target / ".sdlc" / "bin" / "sdlc-codex.cmd"), "digest",
                               "--path", str(self.target / "missing.json")],
                              capture_output=True, text=True, env=clean_env())
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("error", last_json(proc.stdout))

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_posix_host_launcher_runs(self) -> None:
        proc = subprocess.run(["sh", str(self.target / ".sdlc" / "bin" / "sdlc-codex"), "digest", "--path",
                               self.lifecycle], capture_output=True, text=True, env=clean_env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("sha256", proc.stdout)


class LauncherIdentityTests(Base):
    """Each launcher sets SDLC_HOST, which beats `.sdlc/host.local.json` (written by the first install)."""

    def launch(self, host: str | None, *args: str) -> dict:
        bin_dir = self.tmp / ".sdlc" / "bin"
        if IS_WINDOWS:
            name = f"sdlc-{host}.cmd" if host else "sdlc.cmd"
            argv = ["cmd", "/c", str(bin_dir / name), *args]
        else:
            argv = ["sh", str(bin_dir / (f"sdlc-{host}" if host else "sdlc")), *args]
        proc = subprocess.run(argv, capture_output=True, text=True, env=clean_env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return last_json(proc.stdout)

    def test_each_launcher_reports_its_own_host(self) -> None:
        for host in ("codex", "antigravity", "claude"):
            code, res = run("install", "--target", str(self.tmp), "--host", host)
            self.assertEqual(code, 0, res)
        binding = json.loads((self.tmp / ".sdlc" / "host.local.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["host"], "codex")

        for host in ("antigravity", "claude", "codex"):
            res = self.launch(host, "host", *self.p)
            self.assertEqual(res["activeHost"], host, res)
            self.assertEqual(res["source"], "env", res)
        res = self.launch(None, "host", *self.p)
        self.assertEqual((res["activeHost"], res["source"]), ("codex", "local"), res)


class SharedAgentsSkillTests(TempTarget):
    SKILL = Path(".agents") / "skills" / "kaneki-flow" / "SKILL.md"

    def install(self, host: str, *extra: str) -> dict:
        code, res = run("install", "--target", str(self.target), "--host", host, *extra)
        self.assertEqual(code, 0, res)
        return res

    def skill_text(self) -> str:
        return (self.target / self.SKILL).read_text(encoding="utf-8")

    def assert_both_sections(self) -> None:
        text = self.skill_text()
        self.assertIn(CODEX_TITLE, text)
        self.assertIn(ANTIGRAVITY_TITLE, text)
        self.assertIn("## Host bindings", text)
        self.assertEqual(text.count("# Kaneki Flow (controller)"), 1)
        for skill in ("kaneki-onboarding", "kaneki-ship"):
            other = (self.target / ".agents" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(CODEX_TITLE, other, skill)
            self.assertIn(ANTIGRAVITY_TITLE, other, skill)
        manifest = json.loads((self.target / ".agents" / I.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["hosts"], ["antigravity", "codex"])
        self.assertTrue((self.target / "AGENTS.md").is_file())

    def test_codex_then_antigravity_keeps_both(self) -> None:
        self.install("codex")
        self.assertIn(CODEX_TITLE, self.skill_text())
        self.assertNotIn("## Host bindings", self.skill_text())  # one host: no shared intro
        res = self.install("antigravity")
        self.assertEqual(res["hosts"], ["antigravity", "codex"])
        self.assertIn("skills/kaneki-flow/SKILL.md", res["written"])
        self.assert_both_sections()

    def test_antigravity_then_codex_keeps_both(self) -> None:
        self.install("antigravity")
        self.assertNotIn(CODEX_TITLE, self.skill_text())
        self.install("codex")
        self.assert_both_sections()

    def test_reinstall_keeps_both_and_codex_pointer_stays_owned(self) -> None:
        self.install("codex")
        self.install("antigravity")
        res = self.install("codex")
        self.assertEqual(res["keptYourEdits"], [], res)
        self.assert_both_sections()
        res = self.install("antigravity")
        self.assertEqual(res["keptYourEdits"], [], res)
        self.assert_both_sections()

    def test_user_edit_of_shared_skill_is_kept_unless_force(self) -> None:
        self.install("codex")
        f = self.target / self.SKILL
        f.write_text(self.skill_text() + "\n<!-- mine -->\n", encoding="utf-8")
        res = self.install("antigravity")
        self.assertIn("skills/kaneki-flow/SKILL.md", res["keptYourEdits"])
        self.assertIn("<!-- mine -->", self.skill_text())
        self.install("antigravity", "--force")
        self.assertNotIn("<!-- mine -->", self.skill_text())
        self.assert_both_sections()

    def test_sections_name_own_launcher_and_unit_rules(self) -> None:
        self.install("codex")
        self.install("antigravity")
        text = self.skill_text()
        codex = text[text.index(CODEX_TITLE):]
        codex = codex[: codex.index("\n## ", 1)]
        anti = text[text.index(ANTIGRAVITY_TITLE):]
        anti = anti[: anti.index("\n## ", 1)]
        self.assertIn(".sdlc\\bin\\sdlc-codex.cmd", codex)
        self.assertIn("sh .sdlc/bin/sdlc-codex", codex)
        self.assertIn("$SDLC dispatch --ticket", codex)
        self.assertIn(".sdlc\\bin\\sdlc-antigravity.cmd", anti)
        self.assertIn("sh .sdlc/bin/sdlc-antigravity", anti)
        self.assertIn("invoke_subagent", anti)
        self.assertIn("Never run `$SDLC dispatch`", anti)
        self.assertIn("`sdlc dispatch` refuses host `antigravity`", anti)
        self.assertNotIn("$SDLC dispatch --ticket", anti)
        self.assertNotIn("sdlc-codex", anti)
        self.assertNotIn("sdlc-antigravity", codex)
        pointer = (self.target / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("sdlc-codex.cmd", pointer)

    def test_other_shared_folder_hosts_unaffected(self) -> None:
        # gemini/opencode/cursor/generic keep their own folders and single binding.
        self.install("gemini")
        self.install("codex")
        gem = (self.target / ".gemini" / "skills" / "kaneki-flow" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Host Tool Binding (Gemini CLI)", gem)
        self.assertNotIn(CODEX_TITLE, gem)
        self.assertNotIn("Host Tool Binding (Gemini CLI)", self.skill_text())


class DriverCommandTests(unittest.TestCase):
    def test_playbooks_take_the_driver_from_the_binding(self) -> None:
        for pb in ("flow.md", "onboarding.md", "ship.md"):
            text = (HOME / "playbooks" / pb).read_text(encoding="utf-8")
            self.assertNotIn('SDLC="sh .sdlc/bin/sdlc"', text, pb)
            self.assertIn("host binding", text, pb)
            self.assertIn(".sdlc\\bin\\sdlc-<host>.cmd", text, pb)
            self.assertIn("sh .sdlc/bin/sdlc-<host>", text, pb)

    def test_main_host_preambles_name_their_launchers(self) -> None:
        for host in ("claude", "codex", "antigravity"):
            text = H.load_preamble(HOME, host)
            win, posix = I.launcher_names(host)
            self.assertIn(win, text, host)
            self.assertIn(posix, text, host)
            self.assertNotIn("sh .sdlc/bin/sdlc ", text, host)
            self.assertNotIn("sh .sdlc/bin/sdlc dispatch", text, host)


def _frontmatter(text: str) -> str:
    raw, _, _ = I._split_frontmatter(text)
    return raw


class OneControllerCopyTests(TempTarget):
    SRC = HOME / "skills" / "kaneki-flow" / "SKILL.md"

    def test_source_skill_is_frontmatter_and_pointer_only(self) -> None:
        text = self.SRC.read_text(encoding="utf-8")
        raw, meta, body = I._split_frontmatter(text)
        for key in ("name", "description", "when_to_use", "argument-hint"):
            self.assertIn(key, meta, key)
        self.assertIn("hooks:", raw)
        self.assertIn("playbooks/flow.md", body)
        self.assertNotIn("## Lanes", body)
        self.assertLess(len(body.splitlines()), 15)

    def test_claude_install_renders_frontmatter_binding_and_playbook(self) -> None:
        code, res = run("install", "--target", str(self.target), "--host", "claude")
        self.assertEqual(code, 0, res)
        out = (self.target / ".claude" / "skills" / "kaneki-flow" / "SKILL.md").read_text(encoding="utf-8")
        src = self.SRC.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(out), _frontmatter(src))
        self.assertIn("Host Tool Binding (Claude Code)", out)
        self.assertIn((HOME / "playbooks" / "flow.md").read_text(encoding="utf-8").strip(), out)
        self.assertNotIn("Do not add controller instructions here", out)  # the pointer body is not shipped

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_installed_guard_hook_command_still_blocks(self) -> None:
        run("install", "--target", str(self.target), "--host", "claude")
        out = (self.target / ".claude" / "skills" / "kaneki-flow" / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"^\s*command: '(.*)'\s*$", _frontmatter(out), re.M)
        self.assertIsNotNone(m)
        hook_cmd = m.group(1).replace("''", "'")
        fake_home = self.target / "home"
        fake_home.mkdir()

        def hook(command: str, project: Path, native_path: bool = False) -> int:
            env = clean_env(CLAUDE_PROJECT_DIR=str(project) if native_path else project.as_posix(),
                            HOME=fake_home.as_posix())
            proc = subprocess.run(["sh", "-c", hook_cmd], input=json.dumps({"tool_input": {"command": command}}),
                                  capture_output=True, text=True, env=env, timeout=60)
            return proc.returncode

        self.assertEqual(hook("git reset --hard", self.target), 2)
        self.assertEqual(hook("git commit -m x", self.target), 2)
        self.assertEqual(hook("git status", self.target), 0)
        self.assertEqual(hook('.sdlc\\bin\\sdlc-claude.cmd --profile "p.json" git --ticket T-1 --repo app -- fetch',
                              self.target), 0)
        # No guard installed anywhere: commands that run git fail closed.
        bare = Path(tempfile.mkdtemp(prefix="sdlc-noguard-"))
        self.addCleanup(shutil.rmtree, bare, True)
        self.assertEqual(hook("git status", bare), 2)
        self.assertEqual(hook("npm test", bare), 0)
        # The project dir in the platform's own form (C:\... on Windows) neither hangs nor misses the guard.
        self.assertEqual(hook("git status", bare, native_path=True), 2)
        self.assertEqual(hook("git reset --hard", self.target, native_path=True), 2)
        self.assertEqual(hook("git status", self.target, native_path=True), 0)

    def test_guard_knows_every_launcher_form(self) -> None:
        sys.path.insert(0, str(HOME / "skills" / "kaneki-flow" / "scripts"))
        try:
            import guard_git
        finally:
            sys.path.pop(0)
        for launcher in ("sh .sdlc/bin/sdlc", "sh .sdlc/bin/sdlc-codex", ".sdlc\\bin\\sdlc.cmd",
                         ".sdlc\\bin\\sdlc-antigravity.cmd", 'python "x/sdlc.py"'):
            cmd = f'{launcher} --profile "p.json" git --ticket T-1 --repo app -- fetch'
            self.assertTrue(guard_git.WRAPPER_RE.search(cmd), launcher)
            self.assertEqual(guard_git.refusals(cmd), [], launcher)
        self.assertTrue(guard_git.refusals("sh .sdlc/bin/sdlc-codex status; git push origin HEAD"))


if __name__ == "__main__":
    unittest.main()
