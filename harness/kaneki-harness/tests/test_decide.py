"""`sdlc decide`: a person closes a user gate at a terminal (Zed plan M2 step 6). It refuses without a TTY, offers only
the choices gate-request allows for `--actor user`, and closes through the same `sdlc gate` path."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import HOME, Base, run  # noqa: E402

from harness import decide, zed  # noqa: E402
from harness.cli import main  # noqa: E402


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class DecideTests(Base):
    # --- helpers ---------------------------------------------------------------------------------------
    def init(self, ticket: str) -> None:
        code, res = run("init", "--ticket", ticket, "--kind", "bug", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)

    def decide(self, *argv: str, answers: list[str] = (), stdin_tty: bool = True, stdout_cls: type = TTY
               ) -> tuple[int, str, dict]:
        """Run `sdlc decide` with scripted answers. Returns (rc, everything printed, the last JSON line)."""
        buf = stdout_cls()
        stdin = mock.Mock()
        stdin.isatty.return_value = stdin_tty
        feed = iter(answers)

        def fake_input(prompt: str = "") -> str:
            buf.write(prompt)
            try:
                answer = next(feed)
            except StopIteration:
                raise EOFError
            buf.write(answer + "\n")
            return answer

        with mock.patch("sys.stdin", stdin), mock.patch("builtins.input", fake_input), contextlib.redirect_stdout(buf):
            code = main(["decide", *argv, *self.p])
        text = buf.getvalue()
        last = json.loads(text.strip().splitlines()[-1])
        return code, text, last

    def events(self, ticket: str) -> list[dict]:
        log = self.tdir(ticket) / "decisions.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def at_drafts_done(self, t: str) -> None:
        self.init(t)
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        self.complete(t, "check")
        self.complete(t, "delivery-drafts")

    # --- safety ----------------------------------------------------------------------------------------
    def test_refuses_without_a_terminal(self) -> None:
        t = "D-TTY"
        self.init(t)
        self.write_artifacts(t, "scope")
        before = (self.tdir(t) / "decisions.jsonl").read_text(encoding="utf-8")
        for stdin_tty, out_cls in ((False, TTY), (True, Pipe), (False, Pipe)):
            code, _, res = self.decide("--ticket", t, answers=["1", "", "y"], stdin_tty=stdin_tty, stdout_cls=out_cls)
            self.assertEqual(code, 2)
            self.assertEqual(res["error"], decide.NOT_A_TERMINAL)
        self.assertEqual((self.tdir(t) / "decisions.jsonl").read_text(encoding="utf-8"), before)
        from harness.cli import build_parser  # no flag can bypass the check: decide takes only these options
        sub = next(a for a in build_parser()._actions if a.dest == "cmd").choices["decide"]
        self.assertEqual({o for a in sub._actions for o in a.option_strings},
                         {"-h", "--help", "--profile", "--host", "--ticket", "--node", "--path"})

    # --- picking ---------------------------------------------------------------------------------------
    def test_lists_waiting_tickets_and_nothing_waiting(self) -> None:
        code, text, res = self.decide()
        self.assertEqual(code, 0)
        self.assertIn("No ticket is waiting on you.", text)
        self.assertEqual(res["decided"], False)
        self.init("D-AGENT")  # waits on the agent (scope.md not written): not listed
        self.init("D-USER")
        self.write_artifacts("D-USER", "scope")
        code, text, res = self.decide(answers=[""])
        self.assertIn("1) D-USER", text)
        self.assertNotIn("D-AGENT", text)
        self.assertEqual(res["reason"], "nothing chosen")
        code, text, res = self.decide(answers=["1", "1", "", "y"])
        self.assertEqual(code, 0, text)
        self.assertEqual((res["node"], res["verdict"]), ("scope", "approved"))

    def test_ticket_from_path_or_picker(self) -> None:
        t = "D-PATH"
        self.init(t)
        self.write_artifacts(t, "scope")
        code, text, res = self.decide("--path", str(self.tdir(t) / "scope.md"), answers=[""])
        self.assertIn(f"Ticket {t} (from", text)
        self.assertIn("Node:     scope", text)
        code, text, res = self.decide("--path", ".", answers=[""])  # Zed's default when no file is open
        self.assertIn("Tickets waiting on you:", text)

    # --- the three paths ------------------------------------------------------------------------------
    def test_approve_shows_the_gate_and_closes_as_user(self) -> None:
        t = "D-OK"
        self.at_drafts_done(t)
        code, text, res = self.decide("--ticket", t, answers=["1", "looks good", "y"])
        self.assertEqual(code, 0, text)
        for expected in ("Node:     deliverable-ready", "Question: Proceed, re-run or stop?", "Ready:    yes",
                         "1) Proceed (approve)", "Stop", "Reopen delivery-drafts (rejects this round)",
                         "goes stale: delivery-drafts, deliverable-ready", "This will run:",
                         "gate --ticket D-OK --node deliverable-ready --status approved --actor user --note"):
            self.assertIn(expected, text)
        self.assertEqual((res["node"], res["verdict"]), ("deliverable-ready", "approved"))
        gates = [e for e in self.events(t) if e["event"] == "gate"]
        self.assertEqual((gates[-2]["actor"], gates[-2]["node"], gates[-2]["verdict"], gates[-2]["note"]),
                         ("user", "deliverable-ready", "approved", "looks good"))
        # nothing went wrong: the driver closed reflect right after (learning.reflect on-signal)
        self.assertEqual((gates[-1]["actor"], gates[-1]["node"], gates[-1].get("auto")), ("controller", "reflect", True))
        self.assertIn("deliverable-ready", (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8"))

    def test_reopen_rejects_then_invalidates(self) -> None:
        t = "D-REOPEN"
        self.at_drafts_done(t)
        _, text, _ = self.decide("--ticket", t, answers=[""])
        menu = [line.strip() for line in text.splitlines() if line.strip()[:2].rstrip(")").isdigit()]
        pick = next(line.split(")")[0] for line in menu if "Reopen delivery-drafts" in line)
        code, text, res = self.decide("--ticket", t, answers=[pick, "", "commit message too long", "y"])
        self.assertEqual(code, 0, text)
        self.assertIn("This choice needs a note", text)
        self.assertEqual(res["stale"], ["delivery-drafts", "deliverable-ready", "reflect"])
        gate = [e for e in self.events(t) if e["event"] == "gate"][-1]
        self.assertEqual((gate["actor"], gate["verdict"]), ("user", "rejected"))
        inv = [e for e in self.events(t) if e["event"] == "invalidate"][-1]
        self.assertEqual((inv["node"], inv["reason"]), ("delivery-drafts", "commit message too long"))

    def test_stop_and_cancel(self) -> None:
        t = "D-STOP"
        self.init(t)
        self.write_artifacts(t, "scope")
        code, text, res = self.decide("--ticket", t, answers=["3", "", "n"])  # declined at y/N: nothing runs
        self.assertEqual(res, {"decided": False, "ticket": t, "node": "scope", "reason": "cancelled"})
        self.assertFalse([e for e in self.events(t) if e["event"] == "gate"])
        self.assertIn("3) Stop", text)
        code, text, res = self.decide("--ticket", t, answers=["3", "", "y"])
        self.assertEqual(code, 0, text)
        self.assertEqual(res["verdict"], "stopped")
        gate = [e for e in self.events(t) if e["event"] == "gate"][-1]
        self.assertEqual((gate["actor"], gate["verdict"]), ("user", "stopped"))

    def test_rerun_offered_and_refused_choices_shown_not_offered(self) -> None:
        t = "D-RERUN"
        self.init(t)
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")
        self.write_artifacts(t, "check", verdict="NEEDS CHANGES")
        code, text, res = self.decide("--ticket", t, answers=[""])
        choices = text.split("Choices:")[1].split("Not available:")[0]
        self.assertNotIn("Proceed", choices)
        self.assertIn("Not available:\n  - Proceed: G7: check has no ok record", text)
        self.assertIn("1) Re-run change (needs changes)", choices)
        code, text, res = self.decide("--ticket", t, answers=["1", "fix the edge case", "y"])
        self.assertEqual(code, 0, text)
        self.assertEqual((res["verdict"], res["reopened"]), ("needs-changes", "change"))
        gate = [e for e in self.events(t) if e["event"] == "gate"][-1]
        self.assertEqual(gate["actor"], "user")

    def test_options_never_include_disallowed(self) -> None:
        req = {"choices": [
            {"id": "approve", "allowed": False, "whyNot": "nope", "argv": ["gate"], "reopenTargets": []},
            {"id": "reject", "allowed": True, "argv": ["gate", "r"]},
            {"id": "stop", "allowed": False, "whyNot": "locked", "argv": ["gate", "s"]},
            {"id": "reopen", "allowed": False, "whyNot": "locked", "argv": ["gate", "r"],
             "reopenTargets": [{"node": "scope", "staleNodes": ["scope"]}]}]}
        offered, refused = decide.options(req)
        self.assertEqual([o.label for o in offered], ["Reject"])
        self.assertEqual(refused, ["Proceed: nope", "Stop: locked", "Reopen scope (rejects this round): locked"])

    # --- pointers --------------------------------------------------------------------------------------
    def test_next_step_points_to_decide_only_when_waiting_on_user(self) -> None:
        t = "D-HINT"
        self.init(t)
        code, res = run("status-of", "--path", str(self.tdir(t)), *self.p)
        self.assertNotIn("sdlc decide", res["next"])
        self.write_artifacts(t, "scope")
        code, res = run("status-of", "--path", str(self.tdir(t)), *self.p)
        self.assertEqual(res["waitingOn"], "user")
        self.assertIn("or run `sdlc decide`", res["next"])
        card = HOME / "skills" / "kaneki-flow" / "references" / "nodes" / "deliverable-ready.md"
        self.assertIn("sdlc decide", card.read_text(encoding="utf-8"))

    def test_zed_setup_adds_decide_task_and_keeps_user_tasks(self) -> None:
        tasks = self.tmp / ".zed" / "tasks.json"
        tasks.parent.mkdir()
        tasks.write_text(json.dumps([{"label": "mine", "command": "echo"}]), encoding="utf-8")
        res = zed.setup(HOME, self.tmp)
        self.assertEqual(res["keptYours"], 1)
        written = json.loads(tasks.read_text(encoding="utf-8"))
        by_label = {t["label"]: t for t in written}
        self.assertIn("mine", by_label)
        for label in ("board (web)", "tickets", "ticket status (from open file)", "validate profile"):
            self.assertIn("kaneki: " + label, by_label)
        task = by_label["kaneki: decide gate"]
        self.assertEqual(task["args"][1:], ["decide", "--path", "${ZED_FILE:.}"])
        self.assertEqual((task["reveal"], task["use_new_terminal"]), ("always", True))


if __name__ == "__main__":
    unittest.main()
