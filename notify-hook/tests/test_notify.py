"""Tests for notify-hook. Run: python -m unittest discover -s tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install  # noqa: E402
import notify  # noqa: E402


def which_only(*names):
    return lambda n: f"/bin/{n}" if n in names else None


class ParsePayload(unittest.TestCase):
    def test_message_and_project(self):
        t, m = notify.parse_payload(json.dumps({"message": "need Bash", "cwd": "D:\\work\\my-app\\"}))
        self.assertEqual((t, m), ("Claude Code - my-app", "need Bash"))

    def test_bom_is_ignored(self):
        self.assertEqual(notify.parse_payload('﻿{"message":"hi"}')[1], "hi")

    def test_bad_input_falls_back(self):
        for raw in ("", "not json", "[1,2]"):
            self.assertEqual(notify.parse_payload(raw), (notify.APP_TITLE, notify.DEFAULT_MESSAGE))


class BuildCommand(unittest.TestCase):
    def test_windows_passes_text_by_env(self):
        argv, env = notify.build_command("Windows", "T", "M & <x>", which_only("powershell"))
        self.assertIn("-EncodedCommand", argv)
        self.assertEqual(env, {"CC_NOTIFY_TITLE": "T", "CC_NOTIFY_MESSAGE": "M & <x>"})

    def test_macos_passes_text_as_argv(self):
        argv, env = notify.build_command("Darwin", "T", 'say "hi"', which_only("osascript"))
        self.assertEqual(argv[-2:], ["T", 'say "hi"'])
        self.assertEqual(env, {})

    def test_linux_notify_send(self):
        argv, _ = notify.build_command("Linux", "T", "M", which_only("notify-send"))
        self.assertEqual(argv[0], "/bin/notify-send")

    def test_wsl_prefers_windows_toast(self):
        argv, env = notify.build_command("Linux", "T", "M", which_only("powershell.exe", "notify-send"), wsl=True)
        self.assertEqual(argv[0], "/bin/powershell.exe")
        self.assertIn("CC_NOTIFY_TITLE/u", env["WSLENV"])

    def test_no_backend(self):
        self.assertIsNone(notify.build_command("Linux", "T", "M", which_only()))
        self.assertIsNone(notify.build_command("SunOS", "T", "M", which_only("notify-send")))


class Installer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.settings = self.dir / "settings.json"
        self.settings.write_text(json.dumps({
            "model": "opus",
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "other"}]}],
                "Notification": [
                    {"hooks": [{"type": "command", "command": "keep-me"}]},
                    {"hooks": [{"type": "command", "command": "powershell -File C:/u/.claude/hooks/notify-toast.ps1"}]},
                ],
            },
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_installer(self, *extra):
        install.main(["--claude-dir", str(self.dir), *extra])
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_install_is_idempotent_and_preserves_others(self):
        self.run_installer()
        s = self.run_installer("--matcher", "permission_prompt")
        notif = s["hooks"]["Notification"]
        self.assertEqual(len(notif), 2)
        self.assertEqual(notif[0]["hooks"][0]["command"], "keep-me")
        self.assertEqual(notif[1]["matcher"], "permission_prompt")
        self.assertIn("notify-hook/notify.py", notif[1]["hooks"][0]["command"])
        self.assertEqual(s["model"], "opus")
        self.assertIn("Stop", s["hooks"])
        self.assertTrue((self.dir / "hooks" / "notify-hook" / "notify.py").exists())

    def test_uninstall(self):
        self.run_installer()
        s = self.run_installer("--uninstall")
        self.assertEqual([e["hooks"][0]["command"] for e in s["hooks"]["Notification"]], ["keep-me"])
        self.assertFalse((self.dir / "hooks" / "notify-hook").exists())


if __name__ == "__main__":
    unittest.main()
