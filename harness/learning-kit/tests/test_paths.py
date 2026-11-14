"""Learning paths: plan validation, versioned import, derived status, next-step selection,
placement and milestone checkpoints. Run: python -m unittest discover -s tests -v"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_kit import Base, answer_all, item  # noqa: E402

from lk import learnpath, sessions, srs, topics  # noqa: E402
from lk.cli import main as lk_main  # noqa: E402
from lk.store import LkError, read_jsonl, write_json  # noqa: E402

RATIONALE = "# Plan rationale\n\n" + "Why these nodes, in this order, from these curricula [S0001]. " * 6


def node(nid, topic, prereqs=(), detail="full", **kw):
    n = {"id": nid, "topic": topic, "title": topic.replace("-", " ").title(), "why": f"needed for {topic}",
         "prereqs": list(prereqs), "detail": detail, "depth": "aware"}
    if detail == "full":
        n.update(focus=["what it is", "trade-offs"], outcomes=[f"explain {topic}"])
    n.update(kw)
    return n


def draft(**kw):
    d = {
        "goal": "Design backend systems and defend the trade-offs in an interview",
        "learner": {"level": "mid backend", "hours_per_week": 6},
        "nodes": [node("n01", "alpha"), node("n02", "beta", ["n01"]), node("n03", "gamma", ["n01"], detail="stub"),
                  node("n04", "delta", ["n02", "n03"], detail="stub", kind="capstone")],
        "milestones": [{"id": "m1", "title": "Basics", "nodes": ["n01", "n02"]}],
    }
    d.update(kw)
    return d


class PathBase(Base):
    def new_path(self, slug="sd", plan=None):
        learnpath.create(slug, "System design")
        d = learnpath.pdir(slug)
        (d / "intake.md").write_text("Goal: senior interviews. Level: mid. 6 h/week.", encoding="utf-8")
        (d / "plan.md").write_text(RATIONALE, encoding="utf-8")
        learnpath.import_plan(slug, plan or draft())
        return slug

    def finish_topic(self, slug, make=True):
        """Run a topic through an aware gate pass, straight to scheduled."""
        if make:
            self.make_topic(slug, depth="aware")
        s = sessions.create_gate(slug)
        answer_all(s)
        sessions.finish(s["id"])
        self.assertEqual(topics.load_state(slug)["stage"], "scheduled")


class TestPathValidation(PathBase):
    def errs(self, plan):
        return learnpath.validate_plan(plan)[0]

    def test_valid_draft(self):
        errs, warns = learnpath.validate_plan(draft())
        self.assertEqual(errs, [])

    def test_prereqs_must_point_backwards(self):
        d = draft()
        d["nodes"][0]["prereqs"] = ["n02"]  # forward edge (would allow a cycle)
        self.assertTrue(any("later node" in e for e in self.errs(d)))
        d = draft()
        d["nodes"][1]["prereqs"] = ["zz"]
        self.assertTrue(any("unknown node" in e for e in self.errs(d)))
        d = draft()
        d["nodes"][1]["prereqs"] = ["n02"]
        self.assertTrue(any("itself" in e for e in self.errs(d)))

    def test_node_rules(self):
        d = draft()
        d["nodes"][1]["topic"] = "alpha"
        self.assertTrue(any("already used" in e for e in self.errs(d)))
        d = draft()
        d["nodes"][1]["topic"] = "Not A Slug"
        self.assertTrue(any("valid slug" in e for e in self.errs(d)))
        d = draft()
        d["nodes"][3]["prereqs"] = ["n02"]
        self.assertTrue(any("capstone" in e for e in self.errs(d)))
        d = draft()
        del d["nodes"][0]["focus"]
        self.assertTrue(any("focus" in e for e in self.errs(d)))
        d = draft()
        d["nodes"][0]["colour"] = "red"
        self.assertTrue(any("unknown fields" in e for e in self.errs(d)))
        d = draft()
        for n in d["nodes"]:
            n["detail"] = "stub"
        self.assertTrue(any("detail=full" in e for e in self.errs(d)))
        d = draft()
        d["nodes"][0]["source_ids"] = ["S9999"]
        self.assertTrue(any("not in the bank" in e for e in self.errs(d)))

    def test_milestones_and_size(self):
        d = draft(milestones=[{"id": "m1", "title": "A", "nodes": ["n01", "n02"]},
                              {"id": "m2", "title": "B", "nodes": ["n02", "n03"]}])
        self.assertTrue(any("already in milestone" in e for e in self.errs(d)))
        big = draft(nodes=[node(f"n{i:02d}", f"t{i}") for i in range(1, 17)], milestones=[])
        self.assertTrue(any("max 15" in e for e in self.errs(big)))

    def test_cli_validate_exit_code(self):
        f = self.tmp / "bad.json"
        write_json(f, {"goal": "short"})
        with self.assertRaises(SystemExit):
            lk_main(["path", "validate", str(f)])


class TestPathImport(PathBase):
    def test_guards_and_versions(self):
        learnpath.create("sd", "System design")
        with self.assertRaises(LkError):
            learnpath.import_plan("sd", draft())  # no intake.md
        d = learnpath.pdir("sd")
        (d / "intake.md").write_text("Goal: senior interviews. Level: mid.", encoding="utf-8")
        with self.assertRaises(LkError):
            learnpath.import_plan("sd", draft())  # no plan.md
        (d / "plan.md").write_text(RATIONALE, encoding="utf-8")
        r = learnpath.import_plan("sd", draft())
        self.assertEqual(r["version"], 1)
        p = learnpath.load("sd")
        self.assertEqual(p["nodes"][2]["kind"], "core")  # defaults filled
        with self.assertRaises(LkError):
            learnpath.import_plan("sd", draft())  # re-plan needs a reason
        d2 = draft()
        d2["nodes"][2] = node("n03", "gamma", ["n01"])  # expand the stub
        d2["nodes"].append(node("n05", "epsilon", ["n04"], detail="stub"))
        r = learnpath.import_plan("sd", d2, reason="expanded n03 after alpha")
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["diff"]["added"], ["n05"])
        self.assertIn("n03", r["diff"]["changed"])
        self.assertEqual(learnpath.load("sd")["history"][-1]["reason"], "expanded n03 after alpha")

    def test_record_fields_survive_and_started_nodes_are_protected(self):
        slug = self.new_path()
        learnpath.set_override(slug, "n03", "skipped", "knows it")
        topics.create("beta", "Beta", "aware")  # n02 started
        d2 = draft()
        d2["nodes"] = [n for n in d2["nodes"] if n["id"] != "n02"]
        d2["nodes"][-1]["prereqs"] = ["n01", "n03"]
        d2["milestones"] = []
        d2["overrides"] = {"n01": {"status": "tested_out"}}  # record field in a draft: ignored
        with self.assertRaises(LkError):
            learnpath.import_plan(slug, d2, reason="drop beta")
        r = learnpath.import_plan(slug, d2, reason="drop beta", force=True)
        self.assertEqual(r["ignored_fields"], ["overrides"])
        p = learnpath.load(slug)
        self.assertEqual(set(p["overrides"]), {"n03"})
        self.assertTrue(topics.exists("beta"))  # topic data is never deleted


class TestPathNext(PathBase):
    def act(self, slug="sd"):
        return learnpath.next_action(slug)

    def test_walk_the_path(self):
        learnpath.create("sd", "System design")
        self.assertEqual(self.act()["action"], "plan")
        d = learnpath.pdir("sd")
        (d / "intake.md").write_text("Goal: senior interviews. Level: mid.", encoding="utf-8")
        (d / "plan.md").write_text(RATIONALE, encoding="utf-8")
        learnpath.import_plan("sd", draft())
        self.assertEqual(self.act()["action"], "placement")
        learnpath.placement_skip("sd", "learner wants to start")
        a = self.act()
        self.assertEqual((a["action"], a["node"]["id"]), ("learn", "n01"))
        self.assertEqual(a["command"], "/learn alpha --new --depth aware")

        self.make_topic("alpha", depth="aware")
        a = self.act()
        self.assertEqual((a["action"], a["node"]["topic"], a["node"]["stage"]), ("resume", "alpha", "quiz"))
        self.finish_topic("alpha", make=False)
        self.assertEqual(self.act()["action"], "review")
        learnpath.mark_reviewed("sd", "n01", "plan still fits")
        a = self.act()
        self.assertEqual((a["action"], a["node"]["id"]), ("learn", "n02"))  # n02 ready, n03 is a stub later

        self.finish_topic("beta")
        learnpath.mark_reviewed("sd", "n02")
        a = self.act()
        self.assertEqual((a["action"], a["milestone"]["id"]), ("checkpoint", "m1"))
        p = learnpath.load("sd")
        p["checkpoints"]["m1"] = {"overall": 1.0, "passed": True}
        learnpath.save(p)
        a = self.act()
        self.assertEqual((a["action"], a["node"]["id"]), ("expand", "n03"))
        learnpath.set_override("sd", "n03", "skipped", "covered at work")
        a = self.act()
        self.assertEqual((a["action"], a["node"]["id"]), ("expand", "n04"))  # capstone now ready
        learnpath.set_override("sd", "n04", "skipped")
        self.assertEqual(self.act()["action"], "complete")
        v = learnpath.view("sd")
        self.assertEqual(v["progress"], 1.0)
        self.assertEqual(v["counts"], {"done": 2, "skipped": 2})

    def test_relearn_comes_before_new_material(self):
        slug = self.new_path()
        learnpath.placement_skip(slug)
        self.finish_topic("alpha")
        learnpath.mark_reviewed(slug, "n01")
        topics.set_concept_status("alpha", "snapshots", "lapsed")
        a = self.act()
        self.assertEqual((a["action"], a["command"]), ("relearn", "/learn alpha"))

    def test_for_topic_gives_learn_its_context(self):
        slug = self.new_path()
        rows = learnpath.for_topic("beta")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["path"], r["node"]["id"]), (slug, "n02"))
        self.assertEqual([x["topic"] for x in r["prereqs"]], ["alpha"])
        self.assertEqual([x["topic"] for x in r["unlocks"]], ["delta"])
        self.assertEqual(learnpath.for_topic("nope"), [])


class TestPlacement(PathBase):
    def test_placement_flow_and_test_out_guard(self):
        slug = self.new_path()
        bad = [item("zz"), item("n01")]
        with self.assertRaises(LkError):
            learnpath.placement_import(slug, bad)  # zz is not a node
        with self.assertRaises(LkError):
            learnpath.placement_import(slug, [item("n01")] * 4)  # > 3 per node
        learnpath.placement_import(slug, [item("n01"), item("n01", "explain"), item("n02"), item("n02", "cloze")])
        s = sessions.create_placement(slug)
        self.assertEqual(sessions.create_placement(slug)["id"], s["id"])  # resumes the open one
        for e in s["items"]:
            it = e["item"]
            if it["concept"] == "n01":
                r = {"choice": it["answer"]} if it["type"] == "mcq" else {"text": "correct"}
                sessions.record_answer(s, e["uid"], r, 3, 10)
            else:
                sessions.record_answer(s, e["uid"], {"dont_know": True}, None, 10)
        s = sessions.finish(s["id"])
        rec = s["placement"]["nodes"]
        self.assertEqual(rec["n01"]["recommend"], "test_out")
        self.assertEqual(rec["n02"]["recommend"], "learn")
        self.assertEqual(read_jsonl(self.tmp / "mistakes.jsonl"), [])  # not knowing yet isn't a mistake
        self.assertFalse(topics.exists("alpha"))  # placement never creates topics
        learnpath.set_override(slug, "n01", "tested_out", "placement 100%")
        with self.assertRaises(LkError):
            learnpath.set_override(slug, "n02", "tested_out")  # no evidence
        learnpath.set_override(slug, "n02", "tested_out", force=True)
        learnpath.set_override(slug, "n02", None)
        a = learnpath.next_action(slug)
        self.assertEqual((a["action"], a["node"]["id"]), ("learn", "n02"))  # n01 tested out satisfies the prereq
        view = sessions.results_view(sessions.load(s["id"]))
        self.assertIn(view["items"][0]["concept_name"], {"Alpha", "Beta"})  # node titles, not ids


class TestCheckpoint(PathBase):
    def test_checkpoint_updates_cards_and_records(self):
        slug = self.new_path()
        learnpath.placement_skip(slug)
        self.finish_topic("alpha")
        self.finish_topic("beta")
        for nid in ("n01", "n02"):
            learnpath.mark_reviewed(slug, nid)
        with self.assertRaises(LkError):
            sessions.create_checkpoint(slug, "m9")
        s = sessions.create_checkpoint(slug, "m1")
        self.assertEqual({e["topic"] for e in s["items"]}, {"alpha", "beta"})
        answer_all(s, right=False)
        s = sessions.finish(s["id"])
        c = s["checkpoint"]
        self.assertFalse(c["passed"])
        self.assertEqual(c["weak_topics"], ["alpha", "beta"])
        self.assertTrue(s["srs"])  # existing cards were reviewed
        self.assertEqual(learnpath.load(slug)["checkpoints"]["m1"]["session"], s["id"])
        self.assertTrue(any(c["status"] == "lapsed" for c in topics.load_concepts("alpha")))
        a = learnpath.next_action(slug)
        self.assertEqual(a["action"], "relearn")
        cards = srs.load()["cards"]
        self.assertTrue(any(v["lapses"] for v in cards.values()))


class TestPathCli(PathBase):
    def test_cli_round_trip(self):
        self.assertEqual(lk_main(["path", "new", "System Design", "--title", "System design"]), 0)
        d = learnpath.pdir("system-design")
        (d / "intake.md").write_text("Goal: senior interviews. Level: mid.", encoding="utf-8")
        (d / "plan.md").write_text(RATIONALE, encoding="utf-8")
        f = self.tmp / "draft.json"
        write_json(f, draft())
        self.assertEqual(lk_main(["path", "validate", str(f)]), 0)
        self.assertEqual(lk_main(["path", "import", "system-design", str(f)]), 0)
        self.assertEqual(lk_main(["path", "show", "System-Design"]), 0)
        self.assertEqual(lk_main(["path", "next", "system-design"]), 0)
        self.assertEqual(lk_main(["path", "override", "system-design", "n03", "tested_out"]), 2)  # LkError
        self.assertEqual(lk_main(["path", "placement-skip", "system-design"]), 0)
        self.assertEqual(lk_main(["path", "list"]), 0)
        self.assertEqual(lk_main(["path", "for-topic", "alpha"]), 0)

    def test_install_renders_path_skill(self):
        dest = self.tmp / "skills"
        self.assertEqual(lk_main(["install", "--skills-dir", str(dest)]), 0)
        text = (dest / "path" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("{{LK}}", text)
        self.assertIn("lk.py", text)
        self.assertTrue((self.tmp / "paths").is_dir())


if __name__ == "__main__":
    import unittest
    unittest.main()
