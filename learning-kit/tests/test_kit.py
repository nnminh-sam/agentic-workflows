"""End-to-end-ish tests for the deterministic core. Run: python -m unittest discover -s tests -v"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lk import fsrs, gate, quiz, sessions, sources, srs, topics  # noqa: E402
from lk.cli import main as lk_main  # noqa: E402
from lk.store import LkError, write_json  # noqa: E402

LONG = "Multi-version concurrency control keeps several versions of a row. " * 10


def item(concept, typ="mcq", **kw):
    base = {"type": typ, "concept": concept, "prompt": f"Q about {concept} ({typ})", "source_ids": ["S0001"],
            "difficulty": 2, "explanation": "because of the snapshot rule"}
    if typ == "mcq":
        base.update(options=["a", "b", "c", "d"], answer=1)
    elif typ == "multi":
        base.update(options=["a", "b", "c", "d"], answer=[0, 2])
    elif typ == "order":
        base.update(steps=["one", "two", "three", "four"])
    elif typ == "cloze":
        base.update(prompt="MVCC stands for {{1}} concurrency {{2}}.", blanks=[["multi-version", "multiversion"], ["control"]])
    else:
        base.update(reference_answer="A full reference answer.", rubric=["point a", "point b"])
        if typ == "predict":
            base["code"] = "print(1)"
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lk-test-"))
        os.environ["LK_HOME"] = str(self.tmp)
        os.environ.pop("LK_TODAY", None)
        write_json(self.tmp / "config.json", {"grader": {"command": [sys.executable, str(ROOT / "tests" / "fake_grader.py")]}})
        sources.add("https://example.com/mvcc?utm_source=x", "MVCC doc", LONG, ["mvcc"], "docs", 3)

    def tearDown(self):
        os.environ.pop("LK_HOME", None)
        os.environ.pop("LK_TODAY", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_topic(self, slug="mvcc", depth="use"):
        topics.create(slug, "MVCC", depth)
        d = topics.tdir(slug)
        (d / "prior.md").write_text("I think readers never block writers because of snapshots.", encoding="utf-8")
        topics.advance(slug, "research")
        (d / "research.md").write_text("# MVCC\n\n" + LONG, encoding="utf-8")
        topics.merge_concepts(slug, [{"id": "snapshots", "name": "Snapshots", "core": True, "source_ids": ["S0001"]},
                                     {"id": "vacuum", "name": "Vacuum", "core": False, "source_ids": ["S0001"]}])
        topics.advance(slug, "read")
        topics.advance(slug, "quiz_gen")
        quiz.import_items(slug, [item("snapshots"), item("snapshots", "explain"), item("vacuum"), item("vacuum", "cloze")])
        topics.advance(slug, "quiz")
        return slug


def answer_all(s, right=True, text="correct answer", conf=3):
    for e in s["items"]:
        it = e["item"]
        t = it["type"]
        if t == "mcq":
            r = {"choice": it["answer"] if right else (it["answer"] + 1) % len(it["options"])}
        elif t == "multi":
            r = {"choices": it["answer"] if right else [1]}
        elif t == "order":
            r = {"order": it["steps"] if right else list(reversed(it["steps"]))}
        elif t == "cloze":
            r = {"blanks": [b[0] for b in it["blanks"]] if right else ["x"] * len(it["blanks"])}
        else:
            r = {"text": text if right else "no idea"}
        sessions.record_answer(s, e["uid"], r, conf, 1000)


class TestSources(Base):
    def test_dedupe_and_search(self):
        again = sources.add("https://EXAMPLE.com/mvcc/", "dup", LONG, ["postgres"], "docs", 3)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], "S0001")
        self.assertEqual(sources.by_id()["S0001"]["topics"], ["mvcc", "postgres"])
        hits = sources.search("versions row")
        self.assertEqual(hits[0]["id"], "S0001")

    def test_html_to_text(self):
        title, text = sources.html_to_text("<html><head><title>T</title><script>x()</script></head><body>"
                                           "<h2>Head</h2><p>Para <code>c</code></p><pre>a\n b</pre></body></html>")
        self.assertEqual(title, "T")
        self.assertIn("## Head", text)
        self.assertIn("`c`", text)
        self.assertNotIn("x()", text)

    def test_rejects_short(self):
        with self.assertRaises(LkError):
            sources.add("https://e.com/x", "t", "short", ["a"])


class TestQuiz(Base):
    def test_validation_errors(self):
        topics.create("t", "T")
        topics.merge_concepts("t", [{"id": "c", "name": "C"}])
        bad = [item("c", answer=9), item("c", "explain", rubric=["one"]), item("zzz"),
               item("c", "cloze", blanks=[["x"]]), item("c", source_ids=["S9999"])]
        _, errs = quiz.validate_file(bad, "t")
        self.assertEqual(len(errs), 5, errs)
        _, ok = quiz.validate_file([item("c", t) for t in ("mcq", "multi", "order", "cloze", "predict", "explain")], "t")
        self.assertEqual(ok, [])

    def test_auto_grading(self):
        self.assertEqual(quiz.grade_auto(item("c"), {"choice": 1})["score"], 1.0)
        self.assertEqual(quiz.grade_auto(item("c", "multi"), {"choices": [0]})["score"], 0.5)
        self.assertEqual(quiz.grade_auto(item("c", "multi"), {"choices": [0, 1, 2]})["score"], 0.5)
        o = quiz.grade_auto(item("c", "order"), {"order": ["two", "one", "three", "four"]})
        self.assertAlmostEqual(o["score"], 1 - 1 / 6, places=3)
        c = quiz.grade_auto(item("c", "cloze"), {"blanks": [" Multiversion ", "control."]})
        self.assertTrue(c["correct"])
        self.assertEqual(quiz.grade_auto(item("c"), {"dont_know": True})["score"], 0.0)

    def test_present_hides_answers(self):
        import random
        for t in ("mcq", "multi", "order", "cloze", "explain", "predict"):
            p = quiz.present(item("c", t), random.Random(1))
            for secret in ("answer", "rubric", "reference_answer", "blanks", "explanation"):
                self.assertNotIn(secret, p)
        self.assertNotEqual(quiz.present(item("c", "order"), random.Random(1))["steps"], ["one", "two", "three", "four"])


class TestLifecycle(Base):
    def test_guards(self):
        topics.create("g", "G")
        with self.assertRaises(LkError):
            topics.advance("g", "research")  # no prior.md
        with self.assertRaises(LkError):
            topics.advance("g", "quiz")  # illegal jump

    def test_gate_pass_flows_to_note_then_scheduled(self):
        slug = self.make_topic()
        s = sessions.create_gate(slug)
        self.assertEqual(len(s["items"]), 4)
        answer_all(s, right=True)
        s = sessions.finish(s["id"])
        self.assertTrue(s["gate"]["passed"])
        self.assertEqual(topics.load_state(slug)["stage"], "note")
        self.assertTrue(all(c["status"] == "mastered" for c in topics.load_concepts(slug)))
        # note review pass -> scheduled + enrolled
        rv = self.tmp / "rv.json"
        write_json(rv, {"criteria": {k: {"score": 2, "feedback": "ok"} for k in
                                     ("correctness", "coverage", "own_words", "when_not", "connections")}})
        self.assertEqual(lk_main(["note", "record", slug, str(rv)]), 0)
        self.assertEqual(topics.load_state(slug)["stage"], "scheduled")
        self.assertEqual(len(srs.load()["cards"]), 4)

    def test_gate_fail_partial_mastery_and_reteach(self):
        slug = self.make_topic()
        s = sessions.create_gate(slug)
        # snapshots right, vacuum wrong -> overall 50%
        for e in s["items"]:
            it = e["item"]
            right = it["concept"] == "snapshots"
            if it["type"] == "mcq":
                r = {"choice": it["answer"] if right else (it["answer"] + 1) % 4}
            elif it["type"] == "cloze":
                r = {"blanks": ["x", "y"]}
            else:
                r = {"text": "correct"}
            sessions.record_answer(s, e["uid"], r, 3, 10)
        s = sessions.finish(s["id"])
        g = s["gate"]
        self.assertFalse(g["passed"])
        self.assertEqual(g["missed_concepts"], ["vacuum"])
        st = topics.load_state(slug)
        self.assertEqual((st["stage"], st["round"]), ("reteach", 2))
        cm = topics.concept_map(slug)
        self.assertEqual((cm["snapshots"]["status"], cm["vacuum"]["status"]), ("mastered", "learning"))
        self.assertEqual([c["id"] for c in topics.target_concepts(slug)], ["vacuum"])
        self.assertTrue((self.tmp / "mistakes.md").exists())
        # reteach requires the reteach file
        with self.assertRaises(LkError):
            topics.advance(slug, "quiz_gen")
        (topics.tdir(slug) / "reteach" / "round-2.md").write_text("Vacuum explained again, properly." * 3, encoding="utf-8")
        topics.advance(slug, "quiz_gen")
        with self.assertRaises(LkError):
            topics.advance(slug, "quiz")  # old vacuum items are used -> coverage fails
        quiz.import_items(slug, [item("vacuum"), item("vacuum", "explain")])
        topics.advance(slug, "quiz")
        s2 = sessions.create_gate(slug)
        self.assertEqual({e["item"]["concept"] for e in s2["items"]}, {"vacuum"})
        self.assertTrue(all(e["item"]["round"] == 2 for e in s2["items"]))

    def test_core_needs_free_recall(self):
        slug = self.make_topic()
        s = sessions.create_gate(slug)
        for e in s["items"]:
            it = e["item"]
            if it["type"] == "explain":
                r = {"text": "partial"}  # 0.5 < 0.7
            elif it["type"] == "mcq":
                r = {"choice": it["answer"]}
            else:
                r = {"blanks": [b[0] for b in it["blanks"]]}
            sessions.record_answer(s, e["uid"], r, 2, 10)
        s = sessions.finish(s["id"])
        self.assertAlmostEqual(s["gate"]["overall"], 0.875)
        self.assertFalse(s["gate"]["passed"])
        self.assertEqual(s["gate"]["core_without_free_recall"], ["snapshots"])

    def test_pass_with_weak_concept(self):
        slug = self.make_topic()
        s = sessions.create_gate(slug)
        for e in s["items"]:
            it = e["item"]
            if it["type"] == "cloze":
                r = {"blanks": ["multi-version", "nope"]}  # vacuum: mcq 1.0 + cloze 0.5 -> 0.75 < 0.8
            elif it["type"] == "mcq":
                r = {"choice": it["answer"]}
            else:
                r = {"text": "correct"}
            sessions.record_answer(s, e["uid"], r, 3, 10)
        g = sessions.finish(s["id"])["gate"]
        self.assertTrue(g["passed"])  # 0.875 overall
        self.assertEqual(g["weak_concepts"], ["vacuum"])

    def test_rounds_cap(self):
        slug = self.make_topic()
        st = topics.load_state(slug)
        st["rounds_log"] = [{"date": dt.date.today().isoformat()}] * 3
        topics.save_state(slug, st)
        with self.assertRaises(LkError):
            sessions.create_gate(slug)

    def test_grader_failure_is_recoverable(self):
        slug = self.make_topic()
        write_json(self.tmp / "config.json", {"grader": {"command": ["definitely-not-a-binary-xyz"]}})
        s = sessions.create_gate(slug)
        answer_all(s)
        with self.assertRaises(LkError):
            sessions.finish(s["id"])
        s = sessions.load(s["id"])
        self.assertEqual(s["status"], "grade_failed")
        pend = sessions.pending_llm(s["id"])
        self.assertEqual(len(pend), 1)
        s = sessions.apply_manual_grades(s["id"], {"grades": [{"id": pend[0]["id"], "points": [1, 1], "feedback": "ok"}]})
        self.assertEqual(s["status"], "applied")
        self.assertTrue(s["gate"]["passed"])

    def test_extend_targets_only_new(self):
        slug = self.make_topic(depth="aware")
        s = sessions.create_gate(slug)
        answer_all(s)
        sessions.finish(s["id"])
        self.assertEqual(topics.load_state(slug)["stage"], "scheduled")  # aware skips the note
        topics.extend(slug)
        topics.merge_concepts(slug, [{"id": "xmin", "name": "xmin/xmax", "core": True}])
        self.assertEqual([c["id"] for c in topics.target_concepts(slug)], ["xmin"])
        self.assertEqual(topics.concept_map(slug)["xmin"]["added_in"], 2)


class TestSrs(Base):
    def test_fsrs_monotonic(self):
        d0 = dt.date(2026, 1, 1)
        c = fsrs.review(None, 3, d0)
        self.assertEqual(c["due"], (d0 + dt.timedelta(days=fsrs.next_interval(fsrs.W[2]))).isoformat())
        later = fsrs.review(c, 3, dt.date.fromisoformat(c["due"]))
        self.assertGreater(later["s"], c["s"])
        lapse = fsrs.review(later, 1, dt.date.fromisoformat(later["due"]))
        self.assertLess(lapse["s"], later["s"])
        self.assertEqual(lapse["lapses"], 1)
        self.assertAlmostEqual(fsrs.retrievability(10, 10), 0.9, places=6)
        self.assertEqual([fsrs.grade_from(x, c) for x, c in ((0.2, 3), (0.6, 3), (0.9, 1), (0.9, 2), (1, 3))], [1, 2, 2, 3, 4])

    def test_review_cycle_with_variants_and_lapse(self):
        slug = self.make_topic(depth="aware")
        s = sessions.create_gate(slug)
        answer_all(s)
        sessions.finish(s["id"])
        cards = srs.load()["cards"]
        self.assertEqual(len(cards), 4)
        os.environ["LK_TODAY"] = "2099-01-01"
        due = srs.due_items()
        self.assertEqual(len(due), 4)
        target = due[0]
        v = dict(target["item"], prompt="Reworded: " + target["item"]["prompt"], origin=target["uid"])
        v.pop("id")
        r = sessions.create_review(variants={"items": [v]})
        variant_entries = [e for e in r["items"] if e["variant"]]
        self.assertEqual(len(variant_entries), 1)
        self.assertTrue(variant_entries[0]["uid"].startswith(target["uid"] + "~v"))
        answer_all(r, right=False, conf=3)
        r = sessions.finish(r["id"])
        self.assertEqual(len(r["srs"]), 4)
        self.assertTrue(all(x["grade"] == 1 for x in r["srs"]))
        self.assertTrue(all(c["status"] == "lapsed" for c in topics.load_concepts(slug)))
        self.assertEqual(srs.load()["cards"][target["uid"]]["lapses"], 1)
        self.assertEqual(topics.status(slug)["concepts"]["lapsed"], 2)
        topics.advance(slug, "reteach")
        self.assertTrue(topics.load_state(slug)["relearn"])


class TestServer(Base):
    def test_http_flow(self):
        from lk.server import QuizServer
        slug = self.make_topic()
        sid = sessions.create_gate(slug)["id"]
        srv = QuizServer(("127.0.0.1", 0), sid)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        try:
            view = call("/api/session")
            self.assertEqual(len(view["items"]), 4)
            for v in view["items"]:
                self.assertFalse({"answer", "rubric", "reference_answer", "blanks", "explanation"} & set(v))
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn(b"app.js", r.read())
            full = sessions.load(sid)
            for e, v in zip(full["items"], view["items"]):
                it = e["item"]
                resp = {"mcq": lambda: {"choice": it["answer"]},
                        "cloze": lambda: {"blanks": [b[0] for b in it["blanks"]]},
                        "explain": lambda: {"text": "correct"}}[it["type"]]()
                call("/api/answer", {"uid": v["uid"], "response": resp, "confidence": 3, "time_ms": 5})
            call("/api/finish", {})
            for _ in range(100):
                r = call("/api/result")
                if r["status"] == "applied":
                    break
                threading.Event().wait(0.1)
            self.assertEqual(r["status"], "applied")
            self.assertTrue(r["gate"]["passed"])
            self.assertEqual(len(r["items"]), 4)
        finally:
            srv.shutdown()
            srv.server_close()


class TestPortability(Base):
    def test_slugs_safe_everywhere(self):
        from lk.store import slugify
        self.assertEqual(slugify("CON"), "con-topic")          # Windows device name
        self.assertEqual(slugify("com1"), "com1-topic")
        self.assertEqual(slugify("Học máy cơ bản"), "hoc-may-co-ban")  # non-ASCII folded
        self.assertEqual(slugify("  Rust: Ownership & Borrowing!  "), "rust-ownership-borrowing")
        self.assertEqual(slugify("x" * 80), "x" * 60)

    def test_cli_slug_case_insensitive(self):
        self.assertEqual(lk_main(["topic", "new", "Postgres MVCC", "--depth", "aware"]), 0)
        self.assertEqual(lk_main(["topic", "status", "POSTGRES-MVCC"]), 0)

    def test_grader_without_json_schema_parses_text(self):
        from lk import grader
        cfg = json.loads(json.dumps(__import__("lk.paths", fromlist=["config"]).config()))
        cfg["grader"]["json_schema"] = False
        it = item("c", "explain")
        out = grader.grade([{"uid": "t/q1", "item": it, "response": {"text": "correct"}}], cfg)
        self.assertEqual(out["t/q1"]["score"], 1.0)

    def test_extract_json_variants(self):
        from lk.grader import GraderError, extract_json
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('Sure:\n```json\n{"grades": []}\n```\nDone.'), {"grades": []})
        with self.assertRaises(GraderError):
            extract_json("no json here")

    def test_schema_arg_skipped_for_windows_shims(self):
        from unittest import mock
        from lk import grader
        with mock.patch.object(grader, "_resolve", lambda c: ["C:/npm/claude.cmd"] + c[1:]), \
                mock.patch.object(grader.os, "name", "nt"):
            self.assertNotIn("--json-schema", grader.command_for(["claude", "-p"]))
            self.assertIn("--json-schema", grader.command_for(["claude", "-p"], use_schema=True))
        with mock.patch.object(grader, "_resolve", lambda c: ["/usr/local/bin/claude"] + c[1:]):
            self.assertIn("--json-schema", grader.command_for(["claude", "-p"]))
        # nothing multi-line or quote-heavy in argv except the compact schema
        for a in grader.command_for(["claude", "-p"]):
            self.assertNotIn("\n", a)

    def test_command_quoting(self):
        from lk import install
        self.assertEqual(install.quote("C:/Python312/python.exe"), "C:/Python312/python.exe")
        q = install.quote("/Users/me/My Projects/lk.py")
        self.assertIn(q[0], "'\"")
        self.assertIn("My Projects", q)

    def test_atomic_write_and_lf(self):
        from lk.store import write_text
        p = self.tmp / "sub" / "f.md"
        write_text(p, "a\nb\n")
        self.assertEqual(p.read_bytes(), b"a\nb\n")  # LF on every OS
        write_text(p, "c\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "c\n")
        self.assertEqual([x.name for x in p.parent.iterdir()], ["f.md"])  # no temp leftovers

    def test_home_expands_user(self):
        os.environ["LK_HOME"] = "~/lk-somewhere"
        try:
            from lk import paths
            self.assertEqual(paths.home(), Path.home() / "lk-somewhere")
        finally:
            os.environ["LK_HOME"] = str(self.tmp)

    def test_open_browser_headless_linux(self):
        from unittest import mock
        from lk import server
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with mock.patch.object(server.sys, "platform", "linux"), mock.patch.object(server, "is_wsl", lambda: False), \
                mock.patch.dict(os.environ, env, clear=True), mock.patch.object(server.webbrowser, "open") as wb:
            self.assertFalse(server.open_browser("http://127.0.0.1:1/"))
            wb.assert_not_called()  # never falls back to a terminal browser


class TestInstall(Base):
    def test_render_skills(self):
        dest = self.tmp / "skills"
        self.assertEqual(lk_main(["install", "--skills-dir", str(dest)]), 0)
        for name in ("learn", "quiz", "note", "recall"):
            text = (dest / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("{{LK}}", text)
            self.assertNotIn("{{KIT}}", text)
            self.assertIn("lk.py", text)
        self.assertEqual(lk_main(["install", "--skills-dir", str(dest)]), 0)  # idempotent


if __name__ == "__main__":
    unittest.main()
