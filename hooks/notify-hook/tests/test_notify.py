"""Tests for notify-hook. Run: python -m unittest discover -s tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import focus  # noqa: E402
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
        install.main(["--claude-dir", str(self.dir), "--no-click", *extra])
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


def write_transcript(path: Path, entries: list) -> str:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def assistant(*content, sidechain=False):
    return {"type": "assistant", "isSidechain": sidechain, "message": {"role": "assistant", "content": list(content)}}


def tool_result(tid):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid}]}}


class Transcript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def pending(self, *entries):
        return notify.read_pending(write_transcript(self.path, list(entries)))

    def test_pending_bash_with_title(self):
        p = self.pending(
            {"type": "ai-title", "aiTitle": "Old title"},
            assistant({"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "x"}}),
            tool_result("a"),
            {"type": "custom-title", "customTitle": "my-session"},
            assistant({"type": "text", "text": "Running tests"},
                      {"type": "tool_use", "id": "b", "name": "Bash",
                       "input": {"command": "npm test", "description": "Run tests"}}),
        )
        self.assertEqual(p.describe("permission_prompt"), "Approve Bash: Run tests: npm test")
        self.assertEqual((p.name, p.title), ("my-session", "my-session"))

    def test_question(self):
        p = self.pending(assistant({"type": "tool_use", "id": "q", "name": "AskUserQuestion",
                                    "input": {"questions": [{"question": "Which DB?"}, {"question": "x"}]}}))
        self.assertEqual(p.describe("elicitation_dialog"), "Answer: Which DB? (+1 more)")

    def test_idle_uses_last_text_and_skips_sidechain(self):
        p = self.pending(
            assistant({"type": "text", "text": "All done.\nShould I ship it?"}),
            assistant({"type": "tool_use", "id": "s", "name": "Bash", "input": {"command": "ls"}}, sidechain=True),
        )
        self.assertIsNone(p.tool)
        self.assertEqual(p.describe("idle_prompt"), "Waiting for you: All done. Should I ship it?")

    def test_resolved_tool_is_not_pending(self):
        p = self.pending(assistant({"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "ls"}}),
                         tool_result("a"))
        self.assertIsNone(p.tool)
        self.assertEqual(p.describe("permission_prompt"), "")

    def test_missing_or_garbage_transcript(self):
        self.assertIsNone(notify.read_pending(None))
        self.assertIsNone(notify.read_pending(str(self.path) + ".nope"))
        self.path.write_text("garbage\n{\n", encoding="utf-8")
        self.assertEqual(notify.read_pending(str(self.path)).describe("permission_prompt"), "")

    def test_message_combines_action_and_title(self):
        p = notify.Pending(tool=("Edit", {"file_path": r"D:\app\src\main.py"}), title="fix login")
        t, m = notify.title_message({"message": "needs Edit", "cwd": "D:/app", "notification_type": "permission_prompt"}, p)
        self.assertEqual((t, m), ("Claude Code - app", "Approve Edit: src/main.py\nfix login"))

    def test_mcp_and_unknown_tools(self):
        self.assertEqual(notify.describe_tool("mcp__slack__send_message", {"text": "hi"}),
                         ("Approve slack send_message", "hi"))
        self.assertEqual(notify.describe_tool("Foo", {"n": 1, "s": "z"}), ("Approve Foo", "z"))


class ClickTarget(unittest.TestCase):
    def test_in_zed(self):
        self.assertTrue(notify.in_zed({"ZED_TERM": "true"}))
        self.assertTrue(notify.in_zed({"TERM_PROGRAM": "zed"}))
        self.assertFalse(notify.in_zed({"TERM_PROGRAM": "vscode"}))

    def test_url_round_trip(self):
        url = notify.focus_url({"cwd": r"D:\w\my app", "session_id": "s1"}, notify.Pending(name="A & B?"))
        self.assertTrue(url.startswith("claude-notify-focus:?"))
        self.assertEqual(focus.parse_url(url), {"cwd": r"D:\w\my app", "session": "s1", "tab": "A & B?"})
        self.assertEqual(focus.parse_url(f'"{url}"')["tab"], "A & B?")

    def test_ai_title_is_shown_but_not_searched(self):
        p = notify.Pending(title="Some AI title")
        self.assertNotIn("tab=", notify.focus_url({"cwd": "x"}, p))
        self.assertEqual(notify.title_message({"message": "m"}, p)[1], "m\nSome AI title")

    def test_agent_name_is_the_tab_name(self):
        d = Path(tempfile.mkdtemp())
        t = write_transcript(d / "t.jsonl", [{"type": "ai-title", "aiTitle": "AI"},
                                             {"type": "agent-name", "agentName": "api-review"}])
        p = notify.read_pending(t)
        self.assertEqual((p.name, p.title), ("api-review", "api-review"))

    def test_windows_command_carries_launch(self):
        _, env = notify.build_command("Windows", "T", "M", which_only("powershell"), launch="claude-notify-focus:?x=1")
        self.assertEqual(env["CC_NOTIFY_LAUNCH"], "claude-notify-focus:?x=1")

    def test_pick_window_prefers_deepest_folder(self):
        wins = [(1, "other"), (2, "workspace"), (3, "my-app \u2014 src/main.rs")]
        self.assertEqual(focus.pick_window(wins, r"D:\workspace\my-app\sub"), 3)
        self.assertEqual(focus.pick_window(wins, r"D:\workspace\x"), 2)
        self.assertEqual(focus.pick_window(wins, r"E:\none"), 1)
        self.assertIsNone(focus.pick_window([], r"D:\x"))


class ZedKeymap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.km = Path(self.tmp.name) / "keymap.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_is_idempotent_and_remove_keeps_user_bindings(self):
        user = [{"context": "Workspace", "bindings": {"ctrl-alt-shift-1": "workspace::NewCenterTerminal"}}]
        self.km.write_text(json.dumps(user), encoding="utf-8")
        install.update_keymap(self.km, True, False)
        install.update_keymap(self.km, True, False)
        data = json.loads(self.km.read_text(encoding="utf-8"))
        self.assertEqual(data[0], user[0])
        self.assertEqual(data[1:], focus.ZED_BINDINGS)
        self.assertTrue(focus.keymap_ready(self.km))
        self.assertTrue(self.km.with_name("keymap.json.bak").exists())
        install.update_keymap(self.km, False, False)
        self.assertEqual(json.loads(self.km.read_text(encoding="utf-8")), user)
        self.assertFalse(focus.keymap_ready(self.km))

    def test_jsonc_is_left_alone(self):
        self.km.write_text("// comment\n[]", encoding="utf-8")
        install.update_keymap(self.km, True, False)
        self.assertEqual(self.km.read_text(encoding="utf-8"), "// comment\n[]")

    def test_protocol_registration_uses_registry(self):
        calls = []

        class Key:
            def __init__(self, path): self.path = path
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeReg:
            HKEY_CURRENT_USER, REG_SZ = "HKCU", 1
            def CreateKey(self, root, path): return Key(path)
            def SetValueEx(self, k, name, _r, _t, value): calls.append((k.path, name, value))

        install.register_protocol(Path("C:/h/focus.py"), False, FakeReg())
        self.assertIn((r"Software\Classes\claude-notify-focus", "URL Protocol", ""), calls)
        cmd = [c for c in calls if c[0].endswith(r"shell\open\command")][0][2]
        self.assertTrue(cmd.endswith('"%1"') and "focus.py" in cmd)


class TabNames(unittest.TestCase):
    def test_label_prefers_session_name(self):
        import tabname
        self.assertEqual(tabname.tab_label({"cwd": "D:/w/app", "session_id": "ab-cd12"}, "api-review"), "api-review")
        self.assertEqual(tabname.tab_label({"cwd": r"D:\w\app", "session_id": "ab-cd12"}, ""), "app #abcd")
        self.assertEqual(tabname.tab_label({}, ""), "claude")

    def test_should_try(self):
        import tabname
        self.assertTrue(tabname.should_try({"ZED_TERM": "true"}, "win32"))
        self.assertFalse(tabname.should_try({"ZED_TERM": "true", "NOTIFY_HOOK_TAB_NAMES": "0"}, "win32"))
        self.assertFalse(tabname.should_try({"ZED_TERM": "true"}, "linux"))
        self.assertFalse(tabname.should_try({}, "win32"))

    def test_saved_label_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            old = focus.TABS_DIR
            focus.TABS_DIR = Path(d)
            try:
                self.assertEqual(focus.read_tab_label("s/../1"), "")
                focus.tab_state("s/../1").write_text("app #s1\n", encoding="utf-8")
                self.assertEqual(focus.read_tab_label("s/../1"), "app #s1")
                self.assertEqual(focus.tab_state("s/../1").parent, Path(d))
            finally:
                focus.TABS_DIR = old

    def test_rename_bindings_are_scoped(self):
        ctx = {b["context"]: b["bindings"] for b in focus.ZED_BINDINGS}
        self.assertEqual(ctx["Terminal"], {focus.RENAME_KEY: "terminal::RenameTerminal"})
        self.assertIn(focus.RENAME_CONFIRM_KEY, ctx["Editor && mode == single_line"])
        self.assertNotIn("enter", json.dumps(focus.ZED_BINDINGS))

    @unittest.skipUnless(sys.platform == "win32", "click setup is Windows-only")
    def test_installer_adds_and_removes_prompt_hook(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            km = d / "keymap.json"
            orig_reg, orig_unreg = install.register_protocol, install.unregister_protocol
            install.register_protocol = lambda *a, **k: None
            install.unregister_protocol = lambda *a, **k: None
            try:
                install.main(["--claude-dir", str(d), "--zed-keymap", str(km)])
                s = json.loads((d / "settings.json").read_text(encoding="utf-8"))
                self.assertIn("tabname.py", s["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"])
                self.assertTrue((d / "hooks" / "notify-hook" / "tabname.py").exists())
                self.assertTrue(focus.keymap_ready(km))
                install.main(["--claude-dir", str(d), "--zed-keymap", str(km)])
                s = json.loads((d / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(len(s["hooks"]["UserPromptSubmit"]), 1)
                install.main(["--claude-dir", str(d), "--zed-keymap", str(km), "--uninstall"])
                s = json.loads((d / "settings.json").read_text(encoding="utf-8"))
                self.assertNotIn("hooks", s)
                self.assertEqual(json.loads(km.read_text(encoding="utf-8")), [])
            finally:
                install.register_protocol, install.unregister_protocol = orig_reg, orig_unreg


if __name__ == "__main__":
    unittest.main()
