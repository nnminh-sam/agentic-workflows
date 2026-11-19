"""Bite-sized mode: lesson drafts and import, mode rules, the lesson as the gate (piece-by-piece checks),
reteach and extend rounds, the HTTP flow and the CLI. Run: python -m unittest discover -s tests -v"""
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_kit import Base, item  # noqa: E402

from lk import lesson, quiz, sessions, srs, topics  # noqa: E402
from lk.cli import main as lk_main  # noqa: E402
from lk.store import LkError, read_json, read_jsonl, write_json  # noqa: E402

BODY = ("Each transaction reads from a snapshot taken when it starts, so a reader sees the committed state as of "
        "that moment and never waits for a writer [S0001]. Writers add new row versions instead of overwriting.")
RV = {"criteria": {k: {"score": 2, "feedback": "ok"}
                   for k in ("correctness", "coverage", "own_words", "when_not", "connections")}}


def check(concept, typ="mcq", **kw):
    it = item(concept, typ, **kw)
    it.pop("concept")      # a check inherits the piece's concept and sources
    it.pop("source_ids")
    return it


def piece(concept, title=None, checks=None, **kw):
    p = {"concept": concept, "title": title or f"Why {concept} matters", "question": f"What problem does {concept} solve?",
         "body": BODY, "reference_answer": "Readers use a snapshot, so they never block writers.",
         "rubric": ["names the snapshot", "says readers don't block writers"], "source_ids": ["S0001"],
         "checks": [check(concept)] if checks is None else checks}
    p.update(kw)
    return p


def respond(it, right=True, text="correct, in my own words"):
    t = it["type"]
    if t == "mcq":
        return {"choice": it["answer"] if right else (it["answer"] + 1) % len(it["options"])}
    if t == "cloze":
        return {"blanks": [b[0] for b in it["blanks"]] if right else ["x"] * len(it["blanks"])}
    if t == "order":
        return {"order": it["steps"] if right else list(reversed(it["steps"]))}
    return {"text": text if right else "no idea"}


class BiteBase(Base):
    def make_bite(self, slug="mvcc", depth="use", pieces=None):
        topics.create(slug, "MVCC", depth)  # no mode given: config default (bite)
        d = topics.tdir(slug)
        (d / "prior.md").write_text("I think readers never block writers because of snapshots.", encoding="utf-8")
        topics.advance(slug, "research")
        topics.merge_concepts(slug, [{"id": "snapshots", "name": "Snapshots", "core": True, "source_ids": ["S0001"]},
                                     {"id": "vacuum", "name": "Vacuum", "core": False, "source_ids": ["S0001"]}])
        lesson.import_draft(slug, {"pieces": pieces or [
            piece("snapshots"), piece("vacuum", checks=[check("vacuum", "cloze"), check("vacuum")])]})
        topics.advance(slug, "lesson")
        return slug

    def answer_piece(self, s, pid, right=True, **kw):
        for e in s["items"]:
            if e.get("piece") == pid:
                sessions.record_answer(s, e["uid"], respond(e["item"], right, **kw), 3, 1000)


class TestModes(BiteBase):
    def test_default_mode_and_legacy_topics(self):
        st = topics.create("a", "A")
        self.assertEqual(st["mode"], "bite")
        self.assertEqual(topics.status("a")["mode"], "bite")
        legacy = topics.load_state("a")
        legacy.pop("mode")
        topics.save_state("a", legacy)
        self.assertEqual(topics.status("a")["mode"], "doc", "state files from before bite mode stay doc")
        write_json(self.tmp / "config.json", {"learn": {"default_mode": "doc"}})
        self.assertEqual(topics.create("b", "B")["mode"], "doc")
        write_json(self.tmp / "config.json", {"learn": {"default_mode": "slides"}})
        with self.assertRaises(LkError):
            topics.create("c", "C")

    def test_each_mode_keeps_to_its_own_flow(self):
        slug = self.make_bite()
        st = topics.load_state(slug)
        self.assertEqual(st["stage"], "lesson")
        with self.assertRaises(LkError):
            sessions.create_gate(slug)          # a bite topic's gate is its lesson
        with self.assertRaises(LkError):
            topics.set_mode(slug, "doc")        # mid-round
        with self.assertRaises(LkError):
            topics.advance(slug, "note")        # the lesson decides, not a hand move
        topics.create("d", "D", mode="doc")
        (topics.tdir("d") / "prior.md").write_text("Some prior belief about this topic.", encoding="utf-8")
        topics.advance("d", "research")
        with self.assertRaises(LkError):
            topics.advance("d", "lesson")       # doc topics read, then quiz
        with self.assertRaises(LkError):
            sessions.create_lesson("d")
        with self.assertRaises(LkError):
            lesson.import_draft("d", {"pieces": [piece("x")]})
        topics.set_mode("d", "bite")            # allowed at research
        self.assertEqual(topics.status("d")["mode"], "bite")
        self.assertIn("lesson", topics.status("d")["next"])

    def test_lesson_stage_needs_a_piece_per_target_concept(self):
        topics.create("m", "M")
        (topics.tdir("m") / "prior.md").write_text("Some prior belief about this topic.", encoding="utf-8")
        topics.advance("m", "research")
        topics.merge_concepts("m", [{"id": "snapshots", "name": "S", "core": True}, {"id": "vacuum", "name": "V"}])
        with self.assertRaises(LkError):
            topics.advance("m", "lesson")       # no pieces at all
        lesson.import_draft("m", {"pieces": [piece("snapshots")]})
        with self.assertRaises(LkError) as cm:
            topics.advance("m", "lesson")       # vacuum still has none
        self.assertIn("vacuum", str(cm.exception))
        lesson.import_draft("m", {"pieces": [piece("vacuum")]})
        self.assertEqual(topics.advance("m", "lesson")["stage"], "lesson")


class TestDraft(BiteBase):
    def setUp(self):
        super().setUp()
        topics.create("t", "T")
        topics.merge_concepts("t", [{"id": "c", "name": "C"}, {"id": "d", "name": "D"}])

    def test_validation_errors(self):
        bad = [
            piece("c", body=BODY * 5),                                   # 1 too long for one paragraph
            piece("c", body="Too short [S0001]."),                       # 2 too short
            piece("c", body=BODY.replace("S0001", "S9999")),             # 3 cites a source not in the bank
            piece("c", rubric=["only one"]),                             # 4 rubric needs >= 2 points
            piece("c", checks=[check("c")] * 4),                         # 5 small quiz: 1-3 checks
            piece("c", checks=[]),                                       # 6 ... and at least one
            piece("zzz"),                                                # 7 unknown concept
            piece("c", checks=[dict(check("c"), concept="d")]),          # 8 a check tests its own piece
            piece("c", hint="x"),                                        # 9 unknown field (typo guard)
            piece("c", checks=[check("c", answer=9)]),                   # 10 the check itself is invalid
        ]
        _, errs, _ = lesson.validate_draft({"pieces": bad}, "t")
        tags = sorted({e.split("]")[0] for e in errs})
        self.assertEqual(len(tags), 10, errs)
        _, ok, warns = lesson.validate_draft([piece("c"), piece("d", checks=[check("d", "explain")],
                                                                question="Explain it.")], "t")
        self.assertEqual(ok, [])
        self.assertEqual(len(warns), 2, warns)  # no '?' in a question; every check is free recall

    def test_code_and_citations_dont_count_as_words(self):
        body = BODY + "\n\n```sql\n" + "SELECT * FROM t WHERE id = 1;\n" * 40 + "```"
        self.assertLess(lesson.prose_words(body), 60)
        _, errs, _ = lesson.validate_draft([piece("c", body=body)], "t")
        self.assertEqual(errs, [])

    def test_import_writes_pieces_bank_items_and_page(self):
        r = lesson.import_draft("t", {"pieces": [piece("c"), piece("d", checks=[check("d"), check("d", "cloze")])]},
                                reviewed_by="opus")
        self.assertEqual(r["pieces"], ["p001", "p002"])
        self.assertEqual(len(r["items"]), 5)
        ps = lesson.load("t")
        self.assertEqual([len(p["check_items"]) for p in ps], [1, 2])
        self.assertEqual(ps[0]["round"], 1)
        bank = quiz.item_map("t")
        recall = bank[ps[0]["recall_item"]]
        self.assertEqual((recall["type"], recall["role"], recall["piece"]), ("explain", "recall", "p001"))
        self.assertEqual(recall["explanation"], BODY, "the paragraph is the 'why' after grading")
        chk = bank[ps[1]["check_items"][1]]
        self.assertEqual((chk["concept"], chk["source_ids"], chk["role"]), ("d", ["S0001"], "check"))
        page = lesson.md_path("t").read_text(encoding="utf-8")
        self.assertIn("## C {#c}", page)
        self.assertIn("**Question:** What problem does c solve?", page)
        self.assertNotIn("Readers use a snapshot", page, "the page never shows model answers")
        # A second import continues the numbering.
        self.assertEqual(lesson.import_draft("t", [piece("c")])["pieces"], ["p003"])


class TestLessonGate(BiteBase):
    def test_pass_piece_by_piece_then_note_and_cards(self):
        slug = self.make_bite()
        s = sessions.create_lesson(slug)
        self.assertEqual(s["mode"], "lesson")
        self.assertEqual([e["item"]["role"] for e in s["items"]], ["recall", "check", "recall", "check", "check"])
        self.assertEqual(sessions.create_lesson(slug)["id"], s["id"], "an open lesson is resumed")
        with self.assertRaises(LkError):
            sessions.check_piece(s["id"], "p001")          # nothing answered yet
        self.answer_piece(s, "p001")
        r = sessions.check_piece(s["id"], "p001")
        self.assertEqual((r["pieces_done"], r["pieces"]), (1, 2))
        self.assertEqual([x["role"] for x in r["results"]], ["recall", "check"])
        self.assertTrue(all(x["result"]["correct"] for x in r["results"]))
        self.assertEqual(r["results"][0]["result"]["expected"], "Readers use a snapshot, so they never block writers.")
        s = sessions.load(s["id"])
        with self.assertRaises(LkError):
            sessions.record_answer(s, s["pieces"][0]["recall"], {"text": "changed my mind"}, 3, 10)
        self.assertEqual(sessions.check_piece(s["id"], "p001")["results"], r["results"], "idempotent")
        with self.assertRaises(LkError):
            sessions.check_piece(s["id"], "p009")
        self.answer_piece(s, "p002")
        done = sessions.finish(s["id"])
        self.assertTrue(done["gate"]["passed"])
        self.assertEqual(topics.load_state(slug)["stage"], "note")
        self.assertEqual({a["mode"] for a in topics.attempts(slug)}, {"lesson"})
        self.assertEqual(len(quiz.gate_attempted_ids(slug)), 5, "lesson items are spent like gate items")
        rv = self.tmp / "rv.json"
        write_json(rv, RV)
        self.assertEqual(lk_main(["note", "record", slug, str(rv)]), 0)
        self.assertEqual(topics.load_state(slug)["stage"], "scheduled")
        self.assertEqual(len(srs.load()["cards"]), 5)

    def test_fail_then_reteach_round_with_fresh_pieces(self):
        slug = self.make_bite()
        s = sessions.create_lesson(slug)
        self.answer_piece(s, "p001", right=True)
        self.answer_piece(s, "p002", right=False)
        g = sessions.finish(s["id"])["gate"]
        self.assertFalse(g["passed"])
        self.assertEqual(g["missed_concepts"], ["vacuum"])
        self.assertEqual(g["next_stage"], "reteach")
        st = topics.load_state(slug)
        self.assertEqual((st["stage"], st["round"]), ("reteach", 2))
        self.assertTrue(read_jsonl(self.tmp / "mistakes.jsonl"))
        with self.assertRaises(LkError):
            topics.advance(slug, "lesson")                  # no round-2 piece for vacuum yet
        lesson.import_draft(slug, [piece("vacuum", title="Vacuum, from another angle")])
        topics.advance(slug, "lesson")
        s2 = sessions.create_lesson(slug)
        self.assertEqual([p["title"] for p in s2["pieces"]], ["Vacuum, from another angle"])
        self.assertTrue(all(e["item"]["round"] == 2 for e in s2["items"]))
        self.answer_piece(s2, s2["pieces"][0]["id"])
        self.assertTrue(sessions.finish(s2["id"])["gate"]["passed"])
        self.assertEqual(topics.load_state(slug)["stage"], "note")

    def test_core_idea_needs_a_good_own_words_answer(self):
        slug = self.make_bite()
        s = sessions.create_lesson(slug)
        for e in s["items"]:   # every check right; the own-words answers only half there
            r = {"text": "partial"} if e["item"]["role"] == "recall" else respond(e["item"])
            sessions.record_answer(s, e["uid"], r, 2, 10)
        g = sessions.finish(s["id"])["gate"]
        self.assertFalse(g["passed"])
        self.assertEqual(g["core_without_free_recall"], ["snapshots"])

    def test_peek_is_recorded(self):
        slug = self.make_bite()
        s = sessions.create_lesson(slug)
        p = s["pieces"][0]
        sessions.record_answer(s, p["recall"], {"text": "correct", "peeked": True}, 2, 10)
        for u in p["checks"]:
            sessions.record_answer(s, u, respond(next(e for e in s["items"] if e["uid"] == u)["item"]), 3, 10)
        rows = sessions.check_piece(s["id"], p["id"])["results"]
        self.assertEqual([x["peeked"] for x in rows], [True, False])

    def test_grader_failure_keeps_checks_and_finish_grades_the_rest(self):
        slug = self.make_bite()
        s = sessions.create_lesson(slug)
        self.answer_piece(s, "p001")
        good = read_json(self.tmp / "config.json")
        write_json(self.tmp / "config.json", {"grader": {"command": ["definitely-not-a-binary-xyz"]}})
        with self.assertRaises(LkError) as cm:
            sessions.check_piece(s["id"], "p001")
        self.assertIn("carry on", str(cm.exception))
        s = sessions.load(s["id"])
        self.assertEqual(set(s["results"]), set(s["pieces"][0]["checks"]), "the mcq check was graded by script")
        self.assertEqual(s["status"], "open")
        write_json(self.tmp / "config.json", good)
        self.answer_piece(s, "p002")
        self.assertTrue(sessions.finish(s["id"])["gate"]["passed"])

    def test_extend_brings_pieces_for_new_concepts_only(self):
        slug = self.make_bite(depth="aware")
        s = sessions.create_lesson(slug)
        for p in s["pieces"]:
            self.answer_piece(s, p["id"])
        sessions.finish(s["id"])
        self.assertEqual(topics.load_state(slug)["stage"], "scheduled")  # aware skips the note
        topics.extend(slug)
        topics.merge_concepts(slug, [{"id": "xmin", "name": "xmin/xmax", "core": True}])
        lesson.import_draft(slug, [piece("xmin")])
        topics.advance(slug, "lesson")
        s2 = sessions.create_lesson(slug)
        self.assertEqual({p["concept"] for p in s2["pieces"]}, {"xmin"})

    def test_rounds_cap_applies_to_lessons(self):
        import datetime as dt
        slug = self.make_bite()
        st = topics.load_state(slug)
        st["rounds_log"] = [{"date": dt.date.today().isoformat()}] * 3
        topics.save_state(slug, st)
        with self.assertRaises(LkError):
            sessions.create_lesson(slug)


class TestLessonHttp(BiteBase):
    def test_http_lesson_flow(self):
        from lk.server import AppServer
        slug = self.make_bite()
        srv = AppServer(("127.0.0.1", 0))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        t = call(f"/api/topics/{slug}")
        self.assertEqual((t["mode"], t["can_lesson"], t["can_gate"]), ("bite", True, False))
        self.assertEqual((t["lesson"]["pieces"], t["lesson"]["current"], t["lesson"]["coverage_issues"]), (2, 2, []))
        self.assertIn("lesson.md", [d["file"] for d in t["docs"]])
        sid = call("/api/sessions", {"kind": "lesson", "topic": slug})["id"]
        view = call(f"/api/sessions/{sid}")
        self.assertEqual(view["mode"], "lesson")
        self.assertEqual([p["id"] for p in view["pieces"]], ["p001", "p002"])
        self.assertEqual(view["pieces"][0]["body"], BODY)
        self.assertEqual(view["pieces"][0]["sources"][0]["id"], "S0001")
        secrets = {"answer", "rubric", "reference_answer", "blanks", "explanation"}
        for v in view["items"]:
            self.assertFalse(secrets & set(v), v)
        self.assertNotIn("Readers use a snapshot", json.dumps(view), "no model answer before the piece is checked")
        full = sessions.load(sid)
        by_uid = {e["uid"]: e["item"] for e in full["items"]}
        for p in view["pieces"]:
            for u in [p["recall"], *p["checks"]]:
                call(f"/api/sessions/{sid}/answer", {"uid": u, "response": respond(by_uid[u]), "confidence": 3})
            r = call(f"/api/sessions/{sid}/pieces/{p['id']}/check", {})
            self.assertTrue(all(x["result"]["correct"] for x in r["results"]))
        self.assertTrue(all(p["checked"] for p in call(f"/api/sessions/{sid}")["pieces"]))
        try:
            call(f"/api/sessions/{sid}/pieces/..%2Fx/check", {})
            self.fail("bad piece id accepted")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
        call(f"/api/sessions/{sid}/finish", {})
        for _ in range(100):
            r = call(f"/api/sessions/{sid}/result")
            if r["status"] == "applied":
                break
            threading.Event().wait(0.1)
        self.assertTrue(r["gate"]["passed"])
        self.assertEqual(r["items"][0]["role"], "recall")
        with urllib.request.urlopen(f"{base}/read/{slug}?file=lesson.md") as page:
            self.assertIn(b"the lesson", page.read())


class TestLessonCli(BiteBase):
    def run_lk(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = lk_main(list(argv))
        return code, buf.getvalue()

    def test_cli_round_trip(self):
        code, out = self.run_lk("topic", "new", "Postgres MVCC", "--mode", "doc")
        self.assertEqual((code, json.loads(out)["mode"]), (0, "doc"))
        self.assertEqual(self.run_lk("topic", "mode", "postgres-mvcc", "bite")[0], 0)
        d = topics.tdir("postgres-mvcc")
        (d / "prior.md").write_text("Readers never block writers, I think.", encoding="utf-8")
        topics.advance("postgres-mvcc", "research")
        topics.merge_concepts("postgres-mvcc", [{"id": "snapshots", "name": "Snapshots", "core": True}])
        draft = self.tmp / "lesson.json"
        write_json(draft, {"pieces": [piece("snapshots", body=BODY * 5)]})
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):  # validate exits 1 on errors, like quiz validate
            lk_main(["lesson", "validate", str(draft), "--topic", "postgres-mvcc"])
        self.assertFalse(json.loads(buf.getvalue())["ok"])
        write_json(draft, {"pieces": [piece("snapshots")]})
        code, out = self.run_lk("lesson", "import", "Postgres-MVCC", str(draft), "--reviewed-by", "opus")
        self.assertEqual((code, json.loads(out)["pieces"]), (0, ["p001"]))
        code, out = self.run_lk("lesson", "coverage", "postgres-mvcc")
        self.assertEqual(json.loads(out)["issues"], [])
        self.assertEqual(self.run_lk("topic", "advance", "postgres-mvcc", "lesson")[0], 0)
        code, out = self.run_lk("session", "lesson", "postgres-mvcc")
        self.assertEqual((code, json.loads(out)["items"]), (0, 2))
        code, out = self.run_lk("topic", "list")
        self.assertIn("bite", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
