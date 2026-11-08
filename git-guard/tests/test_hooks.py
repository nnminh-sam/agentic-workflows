import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import SCRIPTS, CWD, FakeRunner, contracts
import pre_tool_use
import render_rules
import session_start
import check_message

ENV = {"GH_CONFIG_DIR": os.path.join(CWD, "no-gh")}


def decide(command, tool="Bash", runner=None):
    return pre_tool_use.decide({"tool_name": tool, "tool_input": {"command": command}, "cwd": CWD},
                               contracts=contracts(), run=runner or FakeRunner(), process_env=ENV)


class PreToolUse(unittest.TestCase):
    def test_clean_command_produces_no_output(self):
        self.assertIsNone(decide("ls -la"))
        self.assertIsNone(decide("git commit -m 'feat: add x'"))

    def test_non_shell_tools_ignored(self):
        out = pre_tool_use.decide({"tool_name": "Read", "tool_input": {"file_path": "x"}}, contracts=contracts())
        self.assertIsNone(out)

    def test_deny_shape(self):
        out = decide("git commit -m 'bad'")
        hso = out["hookSpecificOutput"]
        self.assertEqual((hso["hookEventName"], hso["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn("[CM003]", hso["permissionDecisionReason"])
        self.assertIn("rules.html", hso["permissionDecisionReason"])

    def test_ask_shape(self):
        out = decide("gh pr merge 1", tool="PowerShell")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_warn_shape(self):
        out = decide("git commit -m 'feat: Add x'")
        self.assertIn("CM009", out["systemMessage"])
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])

    def test_script_end_to_end(self):
        """Run the real script: unrelated commands pass silently, bad input asks instead of crashing."""
        script = os.path.join(SCRIPTS, "pre_tool_use.py")
        p = subprocess.run([sys.executable, script], input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "echo hello"}, "cwd": CWD}),
            capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))
        p = subprocess.run([sys.executable, script], input="{broken", capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_script_denies_bad_commit_message(self):
        script = os.path.join(SCRIPTS, "pre_tool_use.py")
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, script], input=json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'did stuff'"}, "cwd": tmp}),
                capture_output=True, text=True, timeout=60)
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("CM003", out["permissionDecisionReason"])

    def test_contract_error_fails_safe(self):
        with mock.patch.object(pre_tool_use.contracts_mod, "load", side_effect=pre_tool_use.contracts_mod.ContractError("boom")), \
                mock.patch("sys.stdin") as stdin, mock.patch("builtins.print") as pr:
            stdin.buffer.read.return_value = json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": CWD}).encode()
            pre_tool_use.main()
        out = json.loads(pr.call_args[0][0])
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("boom", out["hookSpecificOutput"]["permissionDecisionReason"])


class SessionStart(unittest.TestCase):
    def test_summary_flags_mismatches(self):
        notes = session_start.summary(CWD, contracts(), run=FakeRunner(name="someone-else", email="a@b.c", gh_login="someone-else"), env=ENV)
        self.assertEqual(len(notes), 3)
        self.assertTrue(any("ID005" in n for n in notes))

    def test_summary_clean(self):
        self.assertEqual(session_start.summary(CWD, contracts(), run=FakeRunner(), env=ENV), [])


class Render(unittest.TestCase):
    def test_html_embeds_contracts(self):
        html = render_rules.build_html()
        self.assertNotIn(render_rules.PLACEHOLDER, html)
        blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL).group(1)
        data = json.loads(blob)
        self.assertEqual(data["order"], ["identity", "commit-message", "github-cli"])
        self.assertEqual(data["problems"], [])
        self.assertEqual(len(data["contracts"]["commit-message"]["rules"]), 15)

    def test_script_tags_in_data_are_escaped(self):
        raw = json.loads(json.dumps(render_rules.load_raw()))
        raw["identity"]["description"] = "</script><script>alert(1)</script>"
        html = render_rules.build_html(raw)
        self.assertEqual(html.count("</script>"), 2)  # only the two real closing tags

    def test_problems_are_shown(self):
        raw = render_rules.load_raw()
        raw["identity"]["rules"][0]["severity"] = "nope"
        data_problems = json.loads(re.search(r'application/json">(.*?)</script>', render_rules.build_html(raw), re.DOTALL)
                                   .group(1).replace("<\\/", "</"))["problems"]
        self.assertTrue(data_problems)

    def test_stale_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "rules.html")
            self.assertTrue(render_rules.is_stale(out))
            render_rules.render(out)
            self.assertFalse(render_rules.is_stale(out))
            os.utime(out, (0, 0))
            self.assertTrue(render_rules.is_stale(out))


class CheckMessageCli(unittest.TestCase):
    def test_exit_codes(self):
        with mock.patch("builtins.print"):
            self.assertEqual(check_message.main(["feat: add x"]), 0)
            self.assertEqual(check_message.main(["feat: Add x"]), 0)  # warning only
            self.assertEqual(check_message.main(["bad message"]), 1)
            self.assertEqual(check_message.main(["--title", "feat: x y\nnot checked"]), 0)

    def test_commit_msg_file_drops_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "COMMIT_EDITMSG")
            f.write_text("fix: handle null\n# Please enter the commit message\n", encoding="utf-8")
            with mock.patch("builtins.print"):
                self.assertEqual(check_message.main(["--file", str(f)]), 0)


if __name__ == "__main__":
    unittest.main()
