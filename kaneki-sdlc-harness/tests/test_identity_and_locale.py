"""Project git identity (every git command goes through it) and configurable output languages."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Base, run  # noqa: E402

from harness.cli import main  # noqa: E402


def git_cmd(*argv: str) -> tuple[int, str]:
    """`sdlc git` prints git's own output (not JSON); errors still come back as one JSON line."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["git", *argv])
    return code, buf.getvalue()


def log_identity(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%an <%ae> / %cn <%ce>"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


class IdentityTests(Base):
    def setUp(self) -> None:
        super().setUp()
        run("init", "--ticket", "ID-1", "--kind", "bug", *self.p)

    def reach_coding(self) -> None:
        for node in ("prepare", "investigate"):
            self.complete("ID-1", node)
        self.complete("ID-1", "fix-plan", actor="user")

    def try_close_coding(self) -> tuple[int, dict]:
        self.write_artifacts("ID-1", "coding")
        code, pk = run("packet", "--ticket", "ID-1", "--node", "coding", "--unit", "worker", *self.p)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("ok")
        run("record", "--ticket", "ID-1", "--node", "coding", "--unit", "worker", "--result", pk["resultPath"], "--model", "sonnet", *self.p)
        return run("gate", "--ticket", "ID-1", "--node", "coding", "--status", "approved", *self.p)

    def test_wrapper_runs_allowed_git_and_logs_it(self) -> None:
        (self.repo / "new.py").write_text("x = 1\n")
        code, _ = git_cmd("--ticket", "ID-1", "--repo", "app", *self.p, "--", "add", "new.py")
        self.assertEqual(code, 0)
        code, outp = git_cmd("--ticket", "ID-1", "--repo", "app", *self.p, "--", "status", "--short")
        self.assertEqual(code, 0)
        events = [json.loads(line) for line in (self.tdir("ID-1") / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn(["add", "new.py"], [e["args"] for e in events if e["event"] == "git"])

    def test_nodes_cannot_commit_even_as_the_project_identity(self) -> None:
        """Option b: only kaneki-ship commits. A commit made during the flow fails the gate, whoever made it."""
        (self.repo / "main.py").write_text("print('v2')\n")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Kaneki Dev", "-c", "user.email=dev@kaneki.test",
                        "commit", "-qam", "sneaky"], check=True)
        self.reach_coding()
        code, res = self.try_close_coding()
        self.assertEqual(code, 2)
        self.assertIn("not by kaneki-ship", res["error"])

    def test_gate_names_commits_by_another_account(self) -> None:
        (self.repo / "main.py").write_text("print('rogue')\n")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Someone Else", "-c", "user.email=else@corp.test",
                        "commit", "-qam", "rogue"], check=True)
        self.reach_coding()
        code, res = self.try_close_coding()
        self.assertEqual(code, 2)
        self.assertIn("else@corp.test", res["error"])

    def test_wrapper_refuses_history_identity_and_destructive_commands(self) -> None:
        for argv, needle in ((["config", "user.email", "x@y.z"], "identity"),
                             (["-c", "user.name=X", "status"], "identity"),
                             (["commit", "-m", "m"], "only kaneki-ship"),
                             (["push", "origin", "HEAD"], "only kaneki-ship"),
                             (["rebase", "main"], "only kaneki-ship"),
                             (["reset", "--hard", "HEAD"], "delete work"),
                             (["clean", "-fd"], "delete work"),
                             (["config", "core.autocrlf", "true"], "read-only")):
            code, outp = git_cmd("--ticket", "ID-1", "--repo", "app", *self.p, "--", *argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(needle, json.loads(outp.strip().splitlines()[-1])["error"], argv)

    def test_profile_without_git_identity_is_refused(self) -> None:
        prof = json.loads(self.profile.read_text())
        del prof["git"]
        self.profile.write_text(json.dumps(prof))
        code, res = run("validate", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("git.user", res["error"])
        prof["git"] = {"user": {"name": "X", "email": "not-an-email"}}
        self.profile.write_text(json.dumps(prof))
        code, res = run("validate", *self.p)
        self.assertIn("not an email", res["error"])


class LocaleTests(Base):
    def drafts_profile(self, locale: dict | None) -> None:
        prof = json.loads(self.profile.read_text())
        prof.pop("locale", None)
        if locale is not None:
            prof["locale"] = locale
        prof["delivery"] = {"adapter": "github-pr", "drafts": ["commit-msg", "pr-body", "tracker-reply"]}
        self.profile.write_text(json.dumps(prof))

    def drafts_packet(self, ticket: str) -> str:
        code, pk = run("packet", "--ticket", ticket, "--node", "delivery-drafts", "--unit", "worker", *self.p)
        self.assertEqual(code, 0, pk)
        return Path(pk["path"]).read_text(encoding="utf-8")

    def test_everything_is_english_by_default(self) -> None:
        self.drafts_profile(None)
        run("init", "--ticket", "L-0", "--kind", "bug", *self.p)
        text = self.drafts_packet("L-0")
        for draft in ("commit-msg", "pr-body", "tracker-reply"):
            self.assertIn(f"`drafts/{draft}.md`: **en**", text)
        code, res = run("lang", "--ticket", "L-0", *self.p)
        self.assertEqual({k: v["language"] for k, v in res["languages"].items()},
                         {"chat": "en", "artifacts": "en", "delivery": "en", "review": "en", "tracker": "en"})
        self.assertEqual({v["source"] for v in res["languages"].values()}, {"default"})

    def test_keys_are_independent_no_fallback(self) -> None:
        self.drafts_profile({"chat": "vi", "artifacts": "vi"})   # delivery/tracker/review not set → English
        run("init", "--ticket", "L-1", "--kind", "bug", *self.p)
        text = self.drafts_packet("L-1")
        self.assertIn("`drafts/commit-msg.md`: **en**", text)
        self.assertIn("`drafts/pr-body.md`: **en**", text)
        self.assertIn("`drafts/tracker-reply.md`: **en**", text)   # artifacts=vi does not leak into tracker
        self.assertIn("result file and reply to the controller: **en**", text)
        self.assertIn("Kaneki Dev <dev@kaneki.test>", text)
        self.assertIn("git --ticket L-1 --repo <repo-id> --", text)
        code, pk = run("packet", "--ticket", "L-1", "--node", "investigate", "--unit", "worker", *self.p)
        self.assertIn("`investigation.md`: **vi**", Path(pk["path"]).read_text(encoding="utf-8"))

    def test_prompt_override_per_ticket(self) -> None:
        """The user asked in the prompt: 'write the PR in Japanese for this ticket'."""
        self.drafts_profile(None)
        run("init", "--ticket", "L-2", "--kind", "bug", "--lang", "delivery=ja", *self.p)
        text = self.drafts_packet("L-2")
        self.assertIn("`drafts/pr-body.md`: **ja**", text)
        self.assertIn("`drafts/tracker-reply.md`: **en**", text)
        code, res = run("lang", "--ticket", "L-2", *self.p)
        self.assertEqual(res["languages"]["delivery"]["source"], "ticket override (requested in the prompt)")
        code, res = run("lang", "--ticket", "L-2", "--set", "tracker=vi", "--clear", "delivery",
                        "--reason", "user: reply to the customer in Vietnamese", *self.p)
        self.assertEqual(res["languages"]["delivery"]["language"], "en")
        self.assertEqual(res["languages"]["tracker"]["language"], "vi")
        text = self.drafts_packet("L-2")
        self.assertIn("`drafts/pr-body.md`: **en**", text)
        self.assertIn("`drafts/tracker-reply.md`: **vi**", text)
        code, res = run("lang", "--ticket", "L-2", "--set", "delivery=Japanese", *self.p)
        self.assertEqual(code, 2)

    def test_bad_language_tag_is_refused(self) -> None:
        prof = json.loads(self.profile.read_text())
        prof["locale"] = {"artifacts": "Japanese"}
        self.profile.write_text(json.dumps(prof))
        code, res = run("validate", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("language tag", res["error"])


if __name__ == "__main__":
    unittest.main()
