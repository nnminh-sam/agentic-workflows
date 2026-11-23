"""sdlc learn (M4·2 / M4·3): the workspace learning store, apply / reject / revert (people only), review at a terminal,
project lessons in packets, the retro, and the STATUS field."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import board, learn, schema, zed  # noqa: E402
from harness import rules as R  # noqa: E402
from harness.cli import Session, main  # noqa: E402
from harness.profile import load_profile  # noqa: E402
from harness.state import sha256_text  # noqa: E402
from test_lifecycle import Base, run  # noqa: E402


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def proposal(pid: str, ptype: str = "lesson", nodes: list[str] | None = None, stacks: list[str] | None = None,
             paths: list[str] | None = None, title: str = "Name the edge cases before the change",
             body: str = "List the edge cases the check will try before writing the change.") -> dict:
    return {"id": pid, "type": ptype, "title": title, "body": body, "evidence": [{"ticket": "?", "ref": "decisions.jsonl#1"}],
            "scope": {"nodes": nodes or ["check"], "stacks": stacks or [], "paths": paths or []},
            "metric": "check loop-backs per ticket", "baseline": 1, "status": "proposed"}


class LearnBase(Base):
    def init(self, t: str) -> None:
        code, res = run("init", "--ticket", t, "--kind", "bug", "--lane", "lite", *self.p)
        self.assertEqual(code, 0, res)

    def with_proposals(self, t: str, props: list[dict]) -> None:
        self.init(t)
        for p in props:
            p["evidence"][0]["ticket"] = t
        doc = {"schemaVersion": 1, "ticket": t, "proposals": props}
        (self.tdir(t) / "proposals.json").write_text(json.dumps(doc), encoding="utf-8")

    def learn(self, *argv: str) -> tuple[int, dict]:
        return run("learn", *argv, *self.p)

    def store(self) -> learn.Store:
        return learn.Store(self.tmp)

    def lessons(self) -> list[dict]:
        return json.loads(self.store().lessons_path.read_text(encoding="utf-8"))["lessons"]

    def log(self) -> list[dict]:
        return [json.loads(l) for l in self.store().log_path.read_text(encoding="utf-8").splitlines() if l]

    def apply(self, t: str, pid: str, *extra: str) -> dict:
        code, res = self.learn("apply", "--ticket", t, "--id", pid, "--actor", "user", *extra)
        self.assertEqual(code, 0, res)
        return res

    def set_learning(self, **kw: object) -> None:
        prof = json.loads(self.profile.read_text())
        prof["learning"] = {**prof.get("learning", {}), **kw}
        self.profile.write_text(json.dumps(prof))

    def to_check(self, t: str) -> None:
        self.complete(t, "scope", actor="user")
        self.complete(t, "change")

    def packet(self, t: str, facts: dict | None = None) -> tuple[dict, str]:
        argv = ["packet", "--ticket", t, "--node", "check", "--unit", "quick-review", *self.p]
        if facts is not None:
            fp = self.tmp / "facts.json"
            fp.write_text(json.dumps(facts), encoding="utf-8")
            argv += ["--facts", str(fp)]
        code, pk = run(*argv)
        self.assertEqual(code, 0, pk)
        return pk, Path(pk["path"]).read_text(encoding="utf-8")


class ListApplyTests(LearnBase):
    def test_list_joins_proposals_and_the_log(self) -> None:
        self.with_proposals("L-1", [proposal("a"), proposal("b")])
        self.with_proposals("L-2", [proposal("c")])
        code, res = self.learn("list")
        self.assertEqual(code, 0, res)
        self.assertEqual([(r["ticket"], r["id"]) for r in res["proposals"]], [("L-1", "a"), ("L-1", "b"), ("L-2", "c")])
        self.assertEqual(res["pending"], 3)
        self.apply("L-1", "a")
        code, res = self.learn("reject", "--ticket", "L-1", "--id", "b", "--actor", "user", "--reason", "one-off")
        self.assertEqual(code, 0, res)
        _, res = self.learn("list")
        self.assertEqual([r["id"] for r in res["proposals"]], ["c"])
        _, res = self.learn("list", "--status", "all")
        self.assertEqual({r["id"]: r["status"] for r in res["proposals"]}, {"a": "applied", "b": "rejected", "c": "proposed"})
        self.assertEqual(res["lessons"][0]["id"], "L-1-a")
        self.assertEqual(res["activeLessons"], 1)
        _, res = self.learn("list", "--ticket", "L-1", "--status", "rejected")
        self.assertEqual([(r["id"], r["decision"]["reason"]) for r in res["proposals"]], [("b", "one-off")])
        # proposals.json stays the gate-checked artifact: nothing in it changed
        doc = json.loads((self.tdir("L-1") / "proposals.json").read_text(encoding="utf-8"))
        self.assertEqual({p["status"] for p in doc["proposals"]}, {"proposed"})
        self.assertIsNone(res["integrity"])

    def test_apply_lesson(self) -> None:
        self.with_proposals("A-1", [proposal("edge")])
        res = self.apply("A-1", "edge", "--note", "good catch")
        self.assertEqual((res["lesson"], res["type"], res["applied"]), ("A-1-edge", "lesson", True))
        [lesson] = self.lessons()
        self.assertEqual(schema.errors(lesson, "lesson.schema.json"), [])
        self.assertEqual((lesson["approvedBy"], lesson["status"], lesson["hits"]), ("user", "active", 0))
        self.assertEqual(lesson["appliesTo"], lesson["scope"])
        text = (self.tdir("A-1") / "proposals.json").read_text(encoding="utf-8")
        self.assertEqual(lesson["proposal"], {"ticket": "A-1", "id": "edge", "digest": sha256_text(text)})
        events = [e["event"] for e in self.log()]
        self.assertEqual(events, ["store-write", "learn-apply"])
        self.assertEqual(self.log()[-1]["note"], "good catch")
        code, again = self.learn("apply", "--ticket", "A-1", "--id", "edge", "--actor", "user")
        self.assertEqual(code, 2)
        self.assertIn("already decided", again["error"])

    def test_only_the_user_applies_rejects_or_reverts(self) -> None:
        self.with_proposals("U-1", [proposal("x")])
        for argv in (("apply", "--ticket", "U-1", "--id", "x", "--actor", "controller"),
                     ("reject", "--ticket", "U-1", "--id", "x", "--actor", "agent", "--reason", "r"),
                     ("revert", "--id", "U-1-x", "--actor", "controller", "--reason", "r")):
            code, res = self.learn(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("D10", res["error"])
        self.assertFalse(self.store().log_path.exists())

    def test_apply_revalidates_the_proposal(self) -> None:
        bad = proposal("cmd", body="Run `git reset --hard` before the change.")
        self.with_proposals("V-1", [bad, proposal("fine")])
        code, res = self.learn("apply", "--ticket", "V-1", "--id", "cmd", "--actor", "user")
        self.assertEqual(code, 2)
        self.assertIn("no longer passes the reflect gate", res["error"])
        self.assertIn("command", res["error"])
        self.apply("V-1", "fine")  # the other proposal's problem does not block this one

    def test_apply_rule_and_the_loader_picks_it_up(self) -> None:
        self.with_proposals("R-1", [proposal("null-input", "rule", stacks=["python"],
                                             title="Null input is checked",
                                             body="Every changed entry point handles a missing value explicitly.")])
        prof = load_profile(self.profile)
        ctx = {"repositories": [{"stack": "python"}], "families": [], "kind": "bug", "surfaces": ["cli"]}
        before = {r["id"] for r in R.load_rulepacks(HOME, self.tmp, prof, ctx)}
        res = self.apply("R-1", "null-input")
        rid = learn.rule_id("R-1-null-input")
        self.assertEqual(rid, "LRN-R-1-NULL-INPUT")
        self.assertEqual((res["rule"]["id"], res["rule"]["phase"], res["rule"]["severity"]), (rid, "code", "medium"))
        pack = json.loads(self.store().rulepack_path.read_text(encoding="utf-8"))
        self.assertEqual(schema.errors(pack, "rulepack.schema.json"), [])
        rules = R.load_rulepacks(HOME, self.tmp, prof, ctx)
        self.assertEqual({r["id"] for r in rules} - before, {rid})
        learned = next(r for r in rules if r["id"] == rid)
        self.assertEqual((learned["pack"], learned["appliesTo"]), (learn.LEARNED_PACK_ID, {"stacks": ["python"]}))
        self.assertNotIn("rulepacks", json.loads(self.profile.read_text()).get("learning", {}))  # no profile edit
        self.assertEqual(self.lessons()[0]["type"], "rule")
        # the check node judges it (phase code) and its packet records it
        self.to_check("R-1")
        code, plan = run("plan", "--ticket", "R-1", "--node", "check", *self.p)
        self.assertIn(rid, plan["ruleIds"])
        pk, _ = self.packet("R-1")
        self.assertEqual(pk["learnedRules"], [rid])
        self.assertEqual(self.lessons()[0]["hits"], 1)  # a learned rule's lesson counts the packets that judged it

    def test_rule_without_a_phase_needs_one(self) -> None:
        self.with_proposals("R-2", [proposal("scope-rule", "rule", nodes=["scope"])])
        code, res = self.learn("apply", "--ticket", "R-2", "--id", "scope-rule", "--actor", "user")
        self.assertEqual(code, 2)
        self.assertIn("--phase", res["error"])
        res = self.apply("R-2", "scope-rule", "--phase", "design", "--severity", "high")
        self.assertEqual((res["rule"]["phase"], res["rule"]["severity"]), ("design", "high"))

    def test_profile_proposal_is_only_a_suggestion(self) -> None:
        self.with_proposals("P-1", [proposal("deeper-check", "profile", title="Run the check on the deep tier",
                                             body="Move the check node to the deep tier for this project.")])
        before = self.profile.read_bytes()
        res = self.apply("P-1", "deeper-check")
        self.assertEqual(self.profile.read_bytes(), before)
        sugg = json.loads(Path(res["suggestion"]).read_text(encoding="utf-8"))
        self.assertEqual(Path(res["suggestion"]).parent, self.store().suggestions)
        self.assertEqual((sugg["lesson"], sugg["profile"]), ("P-1-deeper-check", str(self.profile)))
        self.assertIn("never edits the profile", res["howToApply"])
        self.assertEqual(self.lessons()[0]["type"], "profile")

    def test_harness_proposal_is_refused_and_forwarded(self) -> None:
        self.with_proposals("H-1", [proposal("new-gate", "harness", title="Add a gate before the change",
                                             body="The harness could stop before the change when the scope is thin.")])
        res = self.apply("H-1", "new-gate")
        self.assertFalse(res["applied"])
        self.assertIn("never applied", res["refused"])
        text = self.store().feedback.read_text(encoding="utf-8")
        self.assertIn("H-1/new-gate", text)
        self.assertFalse(self.store().lessons_path.exists())  # never a lesson
        _, lst = self.learn("list", "--status", "all")
        self.assertEqual(lst["proposals"][0]["status"], "rejected")
        self.assertEqual(lst["proposals"][0]["decision"]["forwarded"], learn.FEEDBACK_FILE)

    def test_reject_needs_a_reason(self) -> None:
        self.with_proposals("J-1", [proposal("x")])
        code, res = self.learn("reject", "--ticket", "J-1", "--id", "x", "--actor", "user", "--reason", "  ")
        self.assertEqual(code, 2)
        self.assertIn("--reason", res["error"])
        code, res = self.learn("reject", "--ticket", "J-1", "--id", "x", "--actor", "user", "--reason", "noise")
        self.assertEqual((code, res["rejected"]), (0, "J-1/x"))
        self.assertEqual(self.log()[-1]["event"], "learn-reject")


class RevertAndIntegrityTests(LearnBase):
    def test_revert_retires_a_lesson_and_removes_a_learned_rule(self) -> None:
        self.with_proposals("RV-1", [proposal("les"), proposal("rul", "rule")])
        self.apply("RV-1", "les")
        self.apply("RV-1", "rul")
        code, res = self.learn("revert", "--id", "RV-1-rul", "--actor", "user", "--reason", "no effect")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["removedRule"], learn.rule_id("RV-1-rul"))
        pack = json.loads(self.store().rulepack_path.read_text(encoding="utf-8"))
        self.assertEqual(pack["rules"], [])
        code, res = self.learn("revert", "--id", "RV-1-les", "--actor", "user", "--reason", "outdated")
        self.assertEqual(code, 0, res)
        by_id = {l["id"]: l for l in self.lessons()}
        self.assertEqual({l["status"] for l in by_id.values()}, {"retired"})
        self.assertEqual(by_id["RV-1-les"]["retiredReason"], "outdated")
        self.assertEqual([e["event"] for e in self.log()].count("learn-revert"), 2)
        code, res = self.learn("revert", "--id", "RV-1-les", "--actor", "user", "--reason", "again")
        self.assertEqual(code, 2)
        self.assertIn("already retired", res["error"])

    def test_log_chain_detects_tampering(self) -> None:
        self.with_proposals("TM-1", [proposal("a"), proposal("b")])
        self.apply("TM-1", "a")
        lines = self.store().log_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["reason"] = "edited"
        lines[0] = json.dumps(first)
        self.store().log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertTrue(any("hash chain" in p for p in self.store().problems()))
        code, res = self.learn("apply", "--ticket", "TM-1", "--id", "b", "--actor", "user")
        self.assertEqual(code, 2)
        self.assertIn("integrity", res["error"])
        _, lst = self.learn("list")
        self.assertTrue(lst["integrity"])

    def test_hand_edited_store_is_detected_and_can_be_accepted(self) -> None:
        self.with_proposals("TM-2", [proposal("a", "rule")])
        self.apply("TM-2", "a")
        path = self.store().rulepack_path
        pack = json.loads(path.read_text(encoding="utf-8"))
        pack["rules"][0]["severity"] = "critical"
        path.write_text(json.dumps(pack), encoding="utf-8")
        _, ver = self.learn("verify")
        self.assertFalse(ver["ok"])
        self.assertIn("changed outside", ver["problems"][0])
        prof = load_profile(self.profile)
        ctx = {"repositories": [{"stack": "python"}], "families": [], "kind": "bug", "surfaces": []}
        with self.assertRaises(Exception):  # a learned rule changed by hand never reaches a gate silently
            R.load_rulepacks(HOME, self.tmp, prof, ctx)
        code, res = self.learn("verify", "--accept-current", "--reason", "raise severity by hand")
        self.assertEqual(code, 0, res)
        _, ver = self.learn("verify")
        self.assertTrue(ver["ok"])
        self.assertEqual(R.load_rulepacks(HOME, self.tmp, prof, ctx)[-1]["severity"], "critical")


class ReviewTests(LearnBase):
    def review(self, answers: list[str], stdin_tty: bool = True, stdout_cls: type = TTY) -> tuple[int, str, dict]:
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
            code = main(["learn", "review", *self.p])
        text = buf.getvalue()
        return code, text, json.loads(text.strip().splitlines()[-1])

    def test_refuses_without_a_terminal(self) -> None:
        self.with_proposals("RW-1", [proposal("a")])
        code, _, res = self.review(["a", "", "y"], stdin_tty=False)
        self.assertEqual(code, 2)
        self.assertIn("terminal", res["error"])
        code, _, res = self.review(["a", "", "y"], stdout_cls=io.StringIO)
        self.assertEqual(code, 2)
        self.assertFalse(self.store().log_path.exists())

    def test_scripted_review_applies_rejects_and_skips(self) -> None:
        self.with_proposals("RW-2", [proposal("a"), proposal("b"), proposal("c"), proposal("d")])
        code, text, res = self.review(["a", "looks right", "y",   # apply a
                                       "r", "", "noise", "y",     # reject b (reason asked again)
                                       "s",                       # skip c
                                       "a", "", "n"])             # d: declined at the confirmation
        self.assertEqual(code, 0, text)
        self.assertEqual((res["applied"], res["rejected"], res["skipped"]), (1, 1, 2))
        self.assertIn("RW-2/a · lesson", text)
        self.assertIn("metric:", text)
        self.assertIn("evidence: RW-2 decisions.jsonl#1", text)
        self.assertIn("--actor user", text)
        _, lst = self.learn("list", "--status", "all")
        self.assertEqual({r["id"]: r["status"] for r in lst["proposals"]},
                         {"a": "applied", "b": "rejected", "c": "proposed", "d": "proposed"})
        self.assertEqual(self.log()[-1]["reason"], "noise")

    def test_zed_task(self) -> None:
        labels = {t["label"]: t for t in zed.tasks(HOME)}
        task = labels["kaneki: review learning"]
        self.assertEqual((task["args"][1:], task["use_new_terminal"]), (["learn", "review"], True))


class PacketTests(LearnBase):
    def seed(self, t: str, props: list[dict]) -> None:
        self.with_proposals(t, props)
        for p in props:
            self.apply(t, p["id"])

    def test_packet_has_matching_lessons_only_as_data_and_records_them(self) -> None:
        t = "PK-1"
        self.seed(t, [proposal("match", title="Check the empty list"),
                      proposal("other-node", nodes=["scope"]),
                      proposal("other-stack", stacks=["php-laravel"]),
                      proposal("other-path", paths=["lib/"], body="Keep the helpers under lib/ pure."),
                      proposal("same-stack", stacks=["python"], title="Python lesson")])
        code, res = self.learn("reject", "--ticket", t, "--id", "match", "--actor", "user", "--reason", "x")
        self.assertEqual(code, 2)  # already applied
        self.to_check(t)
        pk, text = self.packet(t, {"authorizedPaths": ["main.py"]})
        section = text.split("### Project lessons", 1)[1].split("### Findings to fix", 1)[0]
        self.assertEqual(pk["lessons"], ["PK-1-same-stack", "PK-1-match"])  # most specific first
        self.assertIn("`PK-1-match` (lesson from ticket `PK-1`", section)
        self.assertIn("**data**", section)
        self.assertIn('lesson: "List the edge cases', section)
        for absent in ("other-node", "other-stack", "other-path"):
            self.assertNotIn(absent, section)
        self.assertEqual(pk["lessonsDigest"], sha256_text(section.strip()))
        # recorded on the unit and in the ticket log
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n")
        code, rec = run("record", "--ticket", t, "--node", "check", "--unit", "quick-review", "--result", pk["resultPath"],
                        "--model", "sonnet", "--verdict", "PASS", *self.p)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["recorded"]["lessons"], pk["lessons"])
        self.assertEqual(rec["recorded"]["lessonsDigest"], pk["lessonsDigest"])
        events = [json.loads(l) for l in (self.tdir(t) / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e for e in events if e["event"] == "record"][-1]["lessons"], pk["lessons"])

    def test_unknown_paths_keep_path_lessons_and_hits_count_once_per_dispatch(self) -> None:
        t = "PK-2"
        self.seed(t, [proposal("pathy", paths=["lib/"], body="Keep the helpers under lib/ pure.")])
        self.to_check(t)
        pk, _ = self.packet(t)
        self.assertEqual(pk["lessons"], ["PK-2-pathy"])
        self.packet(t)  # the same dispatch rendered again
        [lesson] = self.lessons()
        self.assertEqual(lesson["hits"], 1)
        self.assertTrue(lesson["lastUsedAt"])
        self.assertTrue(self.store().problems() == [])  # hit counters are outside the store digest

    def test_cap_and_off(self) -> None:
        t = "PK-3"
        self.seed(t, [proposal(f"l{i}", title=f"Lesson {i}") for i in range(5)])
        self.set_learning(lessonsPerPacket=2)
        self.to_check(t)
        pk, text = self.packet(t)
        self.assertEqual(len(pk["lessons"]), 2)
        self.assertIn("3 more matching lesson(s) left out", text)
        self.set_learning(lessonsPerPacket=0)
        code, pk = run("packet", "--ticket", t, "--node", "check", "--unit", "quick-review", *self.p)
        self.assertNotIn("lessons", pk)

    def test_no_store_no_lessons(self) -> None:
        t = "PK-4"
        self.init(t)
        self.to_check(t)
        pk, text = self.packet(t)
        self.assertNotIn("lessons", pk)
        self.assertIn("- none (no active lesson matches", text)
        self.assertFalse((self.tmp / ".sdlc" / "learning").exists())

    def test_tampered_store_drops_lessons_with_a_warning(self) -> None:
        t = "PK-5"
        self.seed(t, [proposal("x")])
        doc = json.loads(self.store().lessons_path.read_text(encoding="utf-8"))
        doc["lessons"][0]["body"] = "Something the user never approved."
        self.store().lessons_path.write_text(json.dumps(doc), encoding="utf-8")
        self.to_check(t)
        pk, text = self.packet(t)
        self.assertNotIn("lessons", pk)
        self.assertIn("integrity", pk["lessonsWarning"])
        self.assertNotIn("never approved", text)


class RetroAndStatusTests(LearnBase):
    def loop_ticket(self, t: str) -> None:
        """A lite ticket whose check fails once (a FAIL record and a loop-back), then the change is redone."""
        self.init(t)
        self.to_check(t)
        pk, _ = self.packet(t)
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("verdict: FAIL\n")
        code, rec = run("record", "--ticket", t, "--node", "check", "--unit", "quick-review", "--result",
                        pk["resultPath"], "--model", "sonnet", "--verdict", "FAIL", *self.p)
        self.assertEqual(code, 0, rec)
        self.write_artifacts(t, "check", verdict="NEEDS CHANGES")
        code, res = run("gate", "--ticket", t, "--node", "check", "--status", "needs-changes", "--note", "edge", *self.p)
        self.assertEqual(code, 0, res)
        self.complete(t, "change")

    def shift(self, t: str, start: str) -> None:
        """Pretend the ticket started at `start` (createdAt is what the retro reads)."""
        path = self.tdir(t) / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["createdAt"] = start
        path.write_text(json.dumps(state), encoding="utf-8")

    def test_retro_numbers_on_a_multi_ticket_log(self) -> None:
        self.loop_ticket("RT-0")
        self.shift("RT-0", "2026-01-01T00:00:00+00:00")  # started long before the approval
        self.with_proposals("RT-SRC", [proposal("used"), proposal("idle", nodes=["delivery-drafts"])])
        self.apply("RT-SRC", "used")
        self.apply("RT-SRC", "idle")
        self.set_learning(retireAfterTickets=2)
        # three tickets after approval: each dispatches check with the lesson, one of them still fails once
        self.loop_ticket("RT-1")
        self.complete("RT-1", "check")
        for t in ("RT-2", "RT-3"):
            self.init(t)
            self.to_check(t)
            self.complete(t, "check")
        code, rep = self.learn("retro")
        self.assertEqual(code, 0, rep)
        by = {r["id"]: r for r in rep["lessons"]}
        used = by["RT-SRC-used"]
        self.assertEqual(used["dispatches"], 4)  # RT-1 ran check twice (loop-back), RT-2 and RT-3 once
        self.assertEqual(used["ticketsUsing"], ["RT-1", "RT-2", "RT-3"])
        self.assertEqual(used["hits"], 4)
        b, a = used["before"], used["after"]
        self.assertEqual((b["tickets"], b["loopBacks"], b["failingRecords"], b["perTicket"]), (1, 1, 1, 2.0))
        self.assertEqual((a["tickets"], a["loopBacks"], a["failingRecords"], a["perTicket"]), (3, 1, 1, 0.667))
        self.assertEqual((used["verdict"], used["reasons"]), ("keep", []))
        idle = by["RT-SRC-idle"]
        self.assertEqual(idle["dispatches"], 0)
        self.assertGreaterEqual(idle["ticketsSinceApproval"], 3)
        self.assertEqual(idle["verdict"], "consider retiring")
        self.assertIn("no dispatch used it", idle["reasons"][0])
        self.assertEqual(rep["considerRetiring"], ["RT-SRC-idle"])
        self.assertEqual({l["status"] for l in self.lessons()}, {"active"})  # the retro never retires
        _, later = self.learn("retro", "--since", "2099-01-01")
        self.assertEqual((later["tickets"], later["lessons"][0]["dispatches"]), (0, 0))
        code, _ = self.learn("retro", "--since", "soon")
        self.assertEqual(code, 2)

    def test_retro_flags_no_improvement(self) -> None:
        self.with_proposals("NI-SRC", [proposal("weak")])
        self.apply("NI-SRC", "weak")
        for t in ("NI-1", "NI-2", "NI-3"):
            self.loop_ticket(t)
        self.loop_ticket("NI-0")
        self.shift("NI-0", "2026-01-01T00:00:00+00:00")
        _, rep = self.learn("retro")
        [row] = rep["lessons"]
        self.assertEqual(row["verdict"], "consider retiring")
        self.assertIn("no improvement", row["reasons"][0])

    def test_status_field_and_line(self) -> None:
        t = "ST-1"
        self.with_proposals(t, [proposal("a"), proposal("b")])
        s = Session(argparse.Namespace(profile=str(self.profile), ticket=t))
        self.assertEqual(board.summarize(s)["pendingProposals"], 2)
        self.apply(t, "a")  # refreshes STATUS.md
        status = (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("1 proposal(s)", status)
        self.assertIn("sdlc learn review", status)
        code, res = run("tickets", *self.p)
        self.assertEqual(res["tickets"][0]["pendingProposals"], 1)

    def test_profile_learning_keys(self) -> None:
        for kw, ok in (({"lessonsPerPacket": 3, "retireAfterTickets": 5}, True), ({"lessonsPerPacket": 0}, True),
                       ({"lessonsPerPacket": -1}, False), ({"retireAfterTickets": 0}, False),
                       ({"lessonsPerPacket": "8"}, False), ({"unknownKey": 1}, False)):
            prof = json.loads(self.profile.read_text())
            prof["learning"] = kw
            self.profile.write_text(json.dumps(prof))
            code, res = run("validate", *self.p)
            self.assertEqual(code == 0, ok, (kw, res))
        self.assertEqual(learn.settings({}), {"lessonsPerPacket": 8, "retireAfterTickets": 10})


if __name__ == "__main__":
    unittest.main()
