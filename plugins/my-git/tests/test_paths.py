"""Plugin vs vendored layout, per-project contract overrides, and install.py."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from helpers import SCRIPTS  # noqa: F401  (puts scripts/ on sys.path)
from gitguard import paths

PKG = Path(SCRIPTS).parent
sys.path.insert(0, str(PKG))
import install  # noqa: E402


class Layout(unittest.TestCase):
    def test_vendored_detection(self):
        self.assertTrue(paths.is_vendored(Path("/p/.claude/hooks/git-guard")))
        self.assertFalse(paths.is_vendored(Path("/home/u/.claude/plugins/cache/git-guard-local/git-guard/1.1.0")))

    def test_plugin_local_dir_follows_project_env(self):
        root = Path("/cache/git-guard")
        env = {"CLAUDE_PROJECT_DIR": "/work/app"}
        self.assertEqual(paths.local_dir(env, root), Path("/work/app").resolve() / ".claude" / "git-guard")
        env["GIT_GUARD_PROJECT_DIR"] = "/work/other"
        self.assertEqual(paths.local_dir(env, root), Path("/work/other").resolve() / ".claude" / "git-guard")

    def test_vendored_local_dir_is_the_hook_dir(self):
        root = Path("/p/.claude/hooks/git-guard")
        self.assertEqual(paths.local_dir({"CLAUDE_PROJECT_DIR": "/elsewhere"}, root), root)
        self.assertEqual(paths.project_dir({}, root), Path("/p"))

    def test_contract_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            self.assertEqual(paths.contract_path("identity.json", local), paths.DEFAULT_CONTRACTS_DIR / "identity.json")
            (local / "contracts").mkdir()
            (local / "contracts" / "identity.json").write_text("{}", encoding="utf-8")
            self.assertEqual(paths.contract_path("identity.json", local), local / "contracts" / "identity.json")
            self.assertEqual(paths.contract_path("github-cli.json", local), paths.DEFAULT_CONTRACTS_DIR / "github-cli.json")

    def test_ensure_local_dir_writes_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = paths.ensure_local_dir(Path(tmp) / ".claude" / "git-guard")
            self.assertIn("profile.local.json", (d / ".gitignore").read_text(encoding="utf-8"))


def run_install(*argv):
    with redirect_stdout(io.StringIO()):
        install.main(list(argv))


class Install(unittest.TestCase):
    def settings(self, project):
        return json.loads((Path(project) / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def test_plugin_mode_round_trip_keeps_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Path(tmp, ".claude", "settings.json")
            s.parent.mkdir()
            s.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
            run_install(tmp)
            data = self.settings(tmp)
            self.assertTrue(data["enabledPlugins"][install.PLUGIN_ID])
            self.assertEqual(data["extraKnownMarketplaces"][install.MARKETPLACE]["source"],
                             {"source": "directory", "path": PKG.as_posix()})
            run_install(tmp, "--uninstall")
            self.assertEqual(self.settings(tmp), {"permissions": {"allow": ["Bash(ls)"]}})

    def test_vendor_mode_is_idempotent_and_keeps_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_install(tmp, "--mode", "vendor")
            hook = Path(tmp, ".claude", "hooks", "git-guard")
            self.assertTrue((hook / "scripts" / "pre_tool_use.py").exists())
            self.assertTrue(Path(tmp, ".claude", "skills", "git-guard", "SKILL.md").exists())
            custom = hook / "contracts" / "identity.json"
            custom.write_text(custom.read_text(encoding="utf-8").replace('"version"', '"_custom": 1, "version"', 1),
                              encoding="utf-8")
            run_install(tmp, "--mode", "vendor")  # upgrade
            self.assertIn('"_custom"', custom.read_text(encoding="utf-8"))
            hooks = self.settings(tmp)["hooks"]
            self.assertEqual(len(hooks["PreToolUse"]), 1)
            self.assertEqual(len(hooks["SessionStart"]), 1)
            run_install(tmp, "--mode", "vendor", "--uninstall")
            self.assertFalse(hook.exists())
            self.assertNotIn("hooks", self.settings(tmp))

    def test_plugin_install_replaces_vendored_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_install(tmp, "--mode", "vendor")
            run_install(tmp)
            data = self.settings(tmp)
            self.assertNotIn("hooks", data)
            self.assertTrue(data["enabledPlugins"][install.PLUGIN_ID])


if __name__ == "__main__":
    unittest.main()
