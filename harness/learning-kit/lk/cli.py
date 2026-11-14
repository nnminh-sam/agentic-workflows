"""`lk` command line. Deterministic plumbing only: state, storage, scheduling, serving.
All judgement (research, writing questions, reviewing) happens in the skills."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, gate, install, learnpath, paths, quiz, reader, service, sessions, sources, srs, topics
from .lock import locked
from .store import LkError, dumps, now_iso, read_json, slugify, write_json, write_text


def _load_json_file(p: str):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LkError(f"no such file: {p}")
    except json.JSONDecodeError as e:
        raise LkError(f"{p} is not valid JSON: {e}")


def out(data, as_json: bool = True):
    print(dumps(data) if as_json or not isinstance(data, str) else data)


# ---------------------------------------------------------------- handlers

def cmd_install(a):
    out(install.install(a.home, a.skills_dir))


def cmd_config(a):
    if a.action == "path":
        out({"home": str(paths.home()), "config": str(paths.home() / "config.json"), "kit": str(paths.KIT_DIR)})
    else:
        out(paths.config())


def cmd_topic(a):
    if a.action == "new":
        slug = slugify(a.slug)
        out(topics.create(slug, a.title or a.slug, a.depth, a.fresh))
    elif a.action == "list":
        rows = topics.list_topics()
        if a.json:
            return out(rows)
        if not rows:
            return print("no topics yet")
        for r in rows:
            c = r["concepts"]
            print(f"{r['slug']:<28} {r['stage']:<10} {r['depth']:<6} v{r['version']} r{r['round']}  "
                  f"mastered {c['mastered']}/{sum(c.values())}  lapsed {c['lapsed']}")
    elif a.action == "status":
        out(topics.status(a.slug))
    elif a.action == "advance":
        st = topics.advance(a.slug, a.stage, force=a.force, note=a.note or "")
        out(topics.status(a.slug) | {"moved_to": st["stage"]})
    elif a.action == "extend":
        topics.extend(a.slug)
        out(topics.status(a.slug))


def _print_path(v: dict) -> None:
    print(f"{v['title']}  (v{v['version']}, {v['progress']:.0%} done)")
    print(f"goal: {v['goal']}")
    ms = {x: m["id"] for m in v["milestones"] for x in m["nodes"]}
    for r in v["nodes"]:
        stage = f" @{r['stage']}" if r["status"] == "in_progress" else ""
        lapsed = f"  lapsed {r['lapsed']}" if r["lapsed"] else ""
        pre = ",".join(r.get("prereqs", [])) or "-"
        print(f"  {r['id']:<6} {r['status'] + stage:<22} {r.get('kind', 'core'):<9} {r.get('depth', 'use'):<6} "
              f"{r.get('detail', 'stub'):<5} {ms.get(r['id'], ''):<4} {r['topic']:<32} after {pre}{lapsed}")


def cmd_path(a):
    if a.action == "new":
        slug = slugify(a.slug)
        p = learnpath.create(slug, a.title or a.slug)
        out({"path": slug, "dir": str(learnpath.pdir(slug)), "version": p["version"],
             "next": "write intake.md, then plan.md + a draft, then `lk path import`"})
    elif a.action == "list":
        rows = learnpath.list_paths()
        if a.json:
            return out(rows)
        if not rows:
            return print("no paths yet")
        for r in rows:
            print(f"{r['id']:<28} v{r['version']}  {r['nodes']:>2} nodes  {r['progress']:.0%} done  {r['title']}")
    elif a.action == "show":
        v = learnpath.view(a.slug)
        if a.json:
            return out(v)
        _print_path(v)
    elif a.action == "raw":
        out(learnpath.load(a.slug))
    elif a.action == "validate":
        data = _load_json_file(a.file)
        errs, warns = learnpath.validate_plan(data)
        out({"ok": not errs, "errors": errs, "warnings": warns})
        if errs:
            sys.exit(1)
    elif a.action == "import":
        out(learnpath.import_plan(a.slug, _load_json_file(a.file), a.reason or "", a.force))
    elif a.action == "next":
        out(learnpath.next_action(a.slug))
    elif a.action == "reviewed":
        out(learnpath.mark_reviewed(a.slug, a.node, a.note or ""))
    elif a.action == "override":
        status = None if a.status == "clear" else a.status
        out(learnpath.set_override(a.slug, a.node, status, a.note or "", a.force))
    elif a.action == "placement-import":
        out(learnpath.placement_import(a.slug, _load_json_file(a.file)))
    elif a.action == "placement-skip":
        out(learnpath.placement_skip(a.slug, a.reason or ""))
    elif a.action == "for-topic":
        out(learnpath.for_topic(a.slug))


def cmd_concepts(a):
    if a.action == "merge":
        data = _load_json_file(a.file)
        items = data.get("concepts", data) if isinstance(data, dict) else data
        out(topics.merge_concepts(a.slug, items))
    elif a.action == "list":
        cs = topics.target_concepts(a.slug) if a.targets else topics.load_concepts(a.slug)
        if a.json:
            return out(cs)
        for c in cs:
            print(f"{c['id']:<32} {c.get('status', 'new'):<9} {'core' if c.get('core') else '    '}  {c['name']}")
    elif a.action == "set":
        topics.set_concept_status(a.slug, a.concept, a.status)
        out({"ok": True})


def cmd_source(a):
    tps = [t for t in (a.topic or "").split(",") if t] if hasattr(a, "topic") else []
    if a.action == "fetch":
        page_title, text = sources.fetch_text(a.url)  # network: outside the data-home lock
        with locked():
            out(sources.add(a.url, a.title or page_title or a.url, text, tps, a.kind, a.quality, a.notes or "",
                            a.refresh))
    elif a.action == "add":
        text = Path(a.file).read_text(encoding="utf-8")
        out(sources.add(a.url, a.title, text, tps, a.kind, a.quality, a.notes or "", a.refresh))
    elif a.action == "list":
        rows = sources.list_sources(a.topic)
        if a.json:
            return out(rows)
        for s in rows:
            print(f"{s['id']}  q{s['quality']} {s['kind']:<6} {s['title'][:70]:<70} {','.join(s['topics'])}")
    elif a.action == "search":
        out(sources.search(a.query, a.limit))
    elif a.action == "path":
        p = sources.snapshot_path(a.id)
        if not p.exists():
            raise LkError(f"no snapshot for {a.id}")
        print(p)


def cmd_quiz(a):
    if a.action == "validate":
        _, errs = quiz.validate_file(_load_json_file(a.file), a.topic, a.variant)
        out({"ok": not errs, "errors": errs})
        if errs:
            sys.exit(1)
    elif a.action == "import":
        ids = quiz.import_items(a.slug, _load_json_file(a.file), a.reviewed_by or "")
        out({"imported": ids, "coverage": quiz.coverage(a.slug)})
    elif a.action == "coverage":
        out(quiz.coverage(a.slug))
    elif a.action == "list":
        items = quiz.load_bank(a.slug)
        if a.json:
            return out(items)
        used = quiz.gate_attempted_ids(a.slug)
        for i in items:
            print(f"{i['id']} r{i.get('round')} {i['type']:<8} {i['concept']:<28} {'used ' if i['id'] in used else 'fresh'} "
                  f"{i['prompt'][:60]!r}")


def cmd_session(a):
    if a.action == "gate":
        s = sessions.create_gate(a.slug, a.force)
    elif a.action == "practice":
        s = sessions.create_practice(a.slug, a.n)
    elif a.action == "review":
        s = sessions.create_review(a.limit, _load_json_file(a.variants) if a.variants else None,
                                   0 if a.no_mistakes else None)
    elif a.action == "placement":
        s = sessions.create_placement(a.slug)
    elif a.action == "checkpoint":
        s = sessions.create_checkpoint(a.slug, a.milestone)
    elif a.action == "show":
        s = sessions.load(a.id)
        return out(sessions.results_view(s) if a.full else service.session_brief(s))
    else:
        raise LkError(a.action)
    out({"session": s["id"], "items": len(s["items"]), "status": s["status"]})


def cmd_serve(a):
    with locked():
        sid = _serve_session(a)
    from .server import serve
    s = serve(sid, open_browser_flag=not a.no_open, port=a.port)
    print("SESSION_RESULT=" + dumps(service.session_brief(s)))


def _serve_session(a) -> str:
    if a.session:
        sid = a.session
    elif a.topic:
        sid = (sessions.create_practice(a.topic, a.n) if a.practice else sessions.create_gate(a.topic, a.force))["id"]
    elif a.review:
        sid = sessions.create_review(a.limit, _load_json_file(a.variants) if a.variants else None)["id"]
    elif a.placement:
        sid = sessions.create_placement(a.placement)["id"]
    elif a.checkpoint:
        if not a.milestone:
            raise LkError("--checkpoint needs --milestone <id>")
        sid = sessions.create_checkpoint(a.checkpoint, a.milestone)["id"]
    else:
        raise LkError("give --session, --topic, --review, --placement or --checkpoint")
    return sid


def cmd_web(a):
    from .server import web
    web(open_browser_flag=not a.no_open, port=a.port)


def cmd_mcp(a):
    from .mcp_server import run
    run()


def cmd_grade(a):
    if a.pending:
        return out(sessions.pending_llm(a.id))
    if a.apply:
        s = sessions.apply_manual_grades(a.id, _load_json_file(a.apply))
    else:
        s = sessions.finish(a.id)
    out(service.session_brief(s))


def cmd_gate(a):
    st = topics.load_state(a.slug)
    if not st["rounds_log"]:
        return out({"rounds": [], "note": "no gate rounds yet"})
    last = st["rounds_log"][-1]
    s = sessions.load(last["session"])
    out({"rounds": st["rounds_log"], "last": s.get("gate"), "stage": st["stage"]})


def cmd_srs(a):
    if a.action == "due":
        rows = srs.due_items(a.limit, 0 if a.no_mistakes else None)
        if a.json:
            return out(rows)
        for r in rows:
            c = r["card"]
            print(f"{r['uid']:<36} due {c['due']}  S={c['s']:.1f}d  {r['item']['type']:<8} {r['item']['prompt'][:50]!r}")
        if not rows:
            print(f"nothing due; next {srs.stats()['next_due']}")
    elif a.action == "enroll":
        out({"enrolled": srs.enroll(a.slug)})
    elif a.action == "stats":
        out(srs.stats())


NOTE_CRITERIA = ("correctness", "coverage", "own_words", "when_not", "connections")


def cmd_note(a):
    rv = _load_json_file(a.file)
    crit = rv.get("criteria", {})
    missing = [c for c in NOTE_CRITERIA if c not in crit]
    if missing:
        raise LkError(f"review missing criteria: {missing}")
    scores = {c: int(crit[c]["score"]) for c in NOTE_CRITERIA}
    if any(v not in (0, 1, 2) for v in scores.values()):
        raise LkError("criterion scores are 0..2")
    total = sum(scores.values())
    # Same 80% bar as the quiz: >= 8/10, correctness must be full, no criterion at zero.
    passed = total >= 8 and scores["correctness"] == 2 and min(scores.values()) > 0
    d = topics.tdir(a.slug)
    prev = read_json(d / "note-review.json", {}) or {}
    hist = prev.get("history", []) + ([{k: prev[k] for k in ("at", "total", "passed")}] if prev.get("at") else [])
    rec = {"at": now_iso(), "criteria": crit, "total": total, "passed": passed,
           "missing_concepts": rv.get("missing_concepts", []), "summary": rv.get("summary", ""), "history": hist}
    write_json(d / "note-review.json", rec)
    lines = [f"# Note review — {rec['at'][:10]} — {total}/10 — {'PASS' if passed else 'REVISE'}", ""]
    if rec["summary"]:
        lines += [rec["summary"], ""]
    for c in NOTE_CRITERIA:
        lines.append(f"- **{c}** {scores[c]}/2 — {crit[c].get('feedback', '')}")
    if rec["missing_concepts"]:
        lines += ["", "Missing or misunderstood concepts: " + ", ".join(rec["missing_concepts"])]
    write_text(d / "note-review.md", "\n".join(lines) + "\n")
    moved = None
    if passed and topics.load_state(a.slug)["stage"] == "note":
        moved = topics.advance(a.slug, "scheduled", note=f"note {total}/10")["stage"]
    out({"total": total, "passed": passed, "scores": scores, "moved_to": moved})


def cmd_read(a):
    if a.check:
        r = reader.check(a.slug, a.file)
        out(r)
        if not r["ok"]:
            raise LkError("fix the visual blocks above (docs/authoring/visuals.md)")
        return
    r = reader.build(a.slug, a.file, a.out)
    if not a.no_open:
        from .server import open_file
        r["opened"] = open_file(r["html"])
    out(r)


def cmd_stats(a):
    rows = topics.list_topics()
    by_stage: dict[str, int] = {}
    for r in rows:
        by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
    from .store import read_jsonl
    ms = read_jsonl(paths.mistakes_jsonl())
    out({"topics": len(rows), "by_stage": by_stage, "paths": len(learnpath.list_paths()),
         "srs": srs.stats(), "mistakes": len(ms),
         "confident_mistakes": sum(1 for m in ms if m.get("confident")), "home": str(paths.home())})


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lk", description="learning-kit: research -> quiz -> note -> spaced recall")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("install", help="init data home, render skills into ~/.claude/skills")
    s.add_argument("--home")
    s.add_argument("--skills-dir")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("config")
    s.add_argument("action", choices=["show", "path"], nargs="?", default="show")
    s.set_defaults(fn=cmd_config)

    t = sub.add_parser("topic").add_subparsers(dest="action", required=True)
    x = t.add_parser("new"); x.add_argument("slug"); x.add_argument("--title"); x.add_argument("--fresh", action="store_true")
    x.add_argument("--depth", choices=topics.DEPTHS, default="use")
    x = t.add_parser("list"); x.add_argument("--json", action="store_true")
    x = t.add_parser("status"); x.add_argument("slug")
    x = t.add_parser("advance"); x.add_argument("slug"); x.add_argument("stage", choices=topics.STAGES)
    x.add_argument("--force", action="store_true"); x.add_argument("--note")
    x = t.add_parser("extend"); x.add_argument("slug")
    sub.choices["topic"].set_defaults(fn=cmd_topic)

    pa = sub.add_parser("path", help="learning paths: a DAG of topics for a broad goal")
    ps = pa.add_subparsers(dest="action", required=True)
    x = ps.add_parser("new"); x.add_argument("slug"); x.add_argument("--title")
    x = ps.add_parser("list"); x.add_argument("--json", action="store_true")
    x = ps.add_parser("show"); x.add_argument("slug"); x.add_argument("--json", action="store_true")
    x = ps.add_parser("raw", help="print path.json (to copy into a re-plan draft)"); x.add_argument("slug")
    x = ps.add_parser("validate", help="check a draft plan"); x.add_argument("file")
    x = ps.add_parser("import", help="store a validated draft as the next version"); x.add_argument("slug")
    x.add_argument("file"); x.add_argument("--reason"); x.add_argument("--force", action="store_true")
    x = ps.add_parser("next", help="the one next step, as JSON"); x.add_argument("slug")
    x = ps.add_parser("reviewed", help="the plan was reviewed after a node finished"); x.add_argument("slug")
    x.add_argument("node"); x.add_argument("--note")
    x = ps.add_parser("override", help="learner decision: tested_out / skipped / clear"); x.add_argument("slug")
    x.add_argument("node"); x.add_argument("status", choices=[*learnpath.OVERRIDES, "clear"])
    x.add_argument("--note"); x.add_argument("--force", action="store_true")
    x = ps.add_parser("placement-import"); x.add_argument("slug"); x.add_argument("file")
    x = ps.add_parser("placement-skip"); x.add_argument("slug"); x.add_argument("--reason")
    x = ps.add_parser("for-topic", help="path nodes that point at a topic (context for /learn)")
    x.add_argument("slug", help="topic slug")
    pa.set_defaults(fn=cmd_path)

    c = sub.add_parser("concepts")
    cs = c.add_subparsers(dest="action", required=True)
    x = cs.add_parser("merge"); x.add_argument("slug"); x.add_argument("file")
    x = cs.add_parser("list"); x.add_argument("slug"); x.add_argument("--targets", action="store_true")
    x.add_argument("--json", action="store_true")
    x = cs.add_parser("set"); x.add_argument("slug"); x.add_argument("concept")
    x.add_argument("--status", required=True, choices=topics.CONCEPT_STATUSES)
    c.set_defaults(fn=cmd_concepts)

    so = sub.add_parser("source")
    ss = so.add_subparsers(dest="action", required=True)
    for name in ("fetch", "add"):
        x = ss.add_parser(name)
        if name == "fetch":
            x.add_argument("url")
            x.add_argument("--title")
        else:
            x.add_argument("--url", required=True)
            x.add_argument("--title", required=True)
            x.add_argument("--file", required=True, help="markdown/text snapshot (e.g. saved WebFetch output)")
        x.add_argument("--topic", required=True, help="topic slug(s), comma-separated")
        x.add_argument("--kind", choices=sources.KINDS, default="other")
        x.add_argument("--quality", type=int, choices=[1, 2, 3], default=2)
        x.add_argument("--notes")
        x.add_argument("--refresh", action="store_true")
    x = ss.add_parser("list"); x.add_argument("--topic"); x.add_argument("--json", action="store_true")
    x = ss.add_parser("search"); x.add_argument("query"); x.add_argument("--limit", type=int, default=10)
    x = ss.add_parser("path"); x.add_argument("id")
    so.set_defaults(fn=cmd_source)

    q = sub.add_parser("quiz")
    qs = q.add_subparsers(dest="action", required=True)
    x = qs.add_parser("validate"); x.add_argument("file"); x.add_argument("--topic")
    x.add_argument("--variant", action="store_true")
    x = qs.add_parser("import"); x.add_argument("slug"); x.add_argument("file"); x.add_argument("--reviewed-by")
    x = qs.add_parser("coverage"); x.add_argument("slug")
    x = qs.add_parser("list"); x.add_argument("slug"); x.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_quiz)

    se = sub.add_parser("session")
    sss = se.add_subparsers(dest="action", required=True)
    x = sss.add_parser("gate"); x.add_argument("slug"); x.add_argument("--force", action="store_true")
    x = sss.add_parser("practice"); x.add_argument("slug"); x.add_argument("--n", type=int, default=10)
    x = sss.add_parser("review"); x.add_argument("--limit", type=int); x.add_argument("--variants")
    x.add_argument("--no-mistakes", action="store_true")
    x = sss.add_parser("placement"); x.add_argument("slug", help="path slug")
    x = sss.add_parser("checkpoint"); x.add_argument("slug", help="path slug"); x.add_argument("milestone")
    x = sss.add_parser("show"); x.add_argument("id"); x.add_argument("--full", action="store_true")
    se.set_defaults(fn=cmd_session)

    sv = sub.add_parser("serve", help="open the quiz app for a session")
    sv.add_argument("--session")
    sv.add_argument("--topic")
    sv.add_argument("--practice", action="store_true")
    sv.add_argument("--n", type=int, default=10)
    sv.add_argument("--force", action="store_true")
    sv.add_argument("--review", action="store_true")
    sv.add_argument("--variants")
    sv.add_argument("--limit", type=int)
    sv.add_argument("--placement", metavar="PATH", help="placement quiz for a learning path")
    sv.add_argument("--checkpoint", metavar="PATH", help="milestone checkpoint for a learning path")
    sv.add_argument("--milestone")
    sv.add_argument("--port", type=int)
    sv.add_argument("--no-open", action="store_true")
    sv.set_defaults(fn=cmd_serve)

    g = sub.add_parser("grade", help="retry LLM grading, or apply manual grades")
    g.add_argument("id")
    g.add_argument("--apply", help="JSON {grades:[{id, points, feedback, missed, wrong_claims}]}")
    g.add_argument("--pending", action="store_true", help="print what still needs LLM grading")
    g.set_defaults(fn=cmd_grade)

    x = sub.add_parser("gate"); x.add_argument("slug"); x.set_defaults(fn=cmd_gate)

    r = sub.add_parser("srs")
    rs = r.add_subparsers(dest="action", required=True)
    x = rs.add_parser("due"); x.add_argument("--limit", type=int); x.add_argument("--json", action="store_true")
    x.add_argument("--no-mistakes", action="store_true")
    x = rs.add_parser("enroll"); x.add_argument("slug")
    rs.add_parser("stats")
    r.set_defaults(fn=cmd_srs)

    n = sub.add_parser("note")
    ns = n.add_subparsers(dest="action", required=True)
    x = ns.add_parser("record", help="store a note review and advance if it passes"); x.add_argument("slug")
    x.add_argument("file")
    n.set_defaults(fn=cmd_note)

    x = sub.add_parser("read", help="render research.md (or another topic doc) as an HTML page and open it")
    x.add_argument("slug"); x.add_argument("--file", help="a Markdown file inside the topic folder (default research.md)")
    x.add_argument("--out", help="where to write the page (default: next to the .md)")
    x.add_argument("--check", action="store_true", help="validate only: visual blocks, citations, long paragraphs")
    x.add_argument("--no-open", action="store_true"); x.set_defaults(fn=cmd_read)

    x = sub.add_parser("stats"); x.set_defaults(fn=cmd_stats)

    x = sub.add_parser("web", help="the library app: browse paths, topics, docs, sessions; take quizzes")
    x.add_argument("--port", type=int); x.add_argument("--no-open", action="store_true"); x.set_defaults(fn=cmd_web)

    x = sub.add_parser("mcp", help="MCP server on stdio for Claude Code (needs: pip install -e .[mcp])")
    x.set_defaults(fn=cmd_mcp)
    return p


# Commands that manage the data-home lock themselves: long-running servers, LLM grading, network fetches.
_SELF_LOCKING = {"serve", "web", "mcp", "grade", "install"}


def _unlocked(args) -> bool:
    return args.cmd in _SELF_LOCKING or (args.cmd == "source" and args.action == "fetch")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    # Slugs are lowercase on disk; normalizing here makes "MVCC" and "mvcc" the same topic on
    # case-sensitive filesystems (Linux) exactly as they already are on Windows and macOS.
    for attr in ("slug", "topic", "placement", "checkpoint", "node", "milestone"):
        v = getattr(args, attr, None)
        if isinstance(v, str):
            setattr(args, attr, v.strip().lower())
    try:
        if _unlocked(args):
            args.fn(args)
        else:
            with locked():
                args.fn(args)
    except LkError as e:
        print(f"lk: {e}", file=sys.stderr)
        return 2
    return 0
