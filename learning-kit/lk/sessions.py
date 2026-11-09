"""Quiz sessions: one file per sitting (gate, practice or review), shared by every question type.

Lifecycle: open -> (answers recorded) -> grading -> graded -> applied
                                      +-> grade_failed (retry with `lk grade <id>` or the UI)
"Applied" means the side effects ran exactly once: attempts log, mistakes log, then either the
gate (concepts + stage) or the scheduler (FSRS cards).
"""
from __future__ import annotations

import random
from pathlib import Path

from . import gate, grader, mistakes, paths, quiz, sources, srs, topics
from .store import LkError, append_jsonl, now_iso, read_json, short_token, today, write_json

MODES = ("gate", "practice", "review")


def spath(sid: str) -> Path:
    return paths.sessions_dir() / f"{sid}.json"


def load(sid: str) -> dict:
    s = read_json(spath(sid))
    if s is None:
        raise LkError(f"no session '{sid}'")
    return s


def save(s: dict) -> None:
    s["updated"] = now_iso()
    write_json(spath(s["id"]), s)


def _new(mode: str, topic: str | None, entries: list[dict], round_: int | None = None, title: str = "") -> dict:
    sid = f"{today().isoformat()}-{mode}-{topic or 'mixed'}-{short_token(4)}"
    s = {
        "id": sid, "mode": mode, "topic": topic, "round": round_, "title": title,
        "created": now_iso(), "updated": now_iso(), "status": "open",
        "seed": random.randrange(1 << 30),
        "items": entries, "answers": {}, "results": {}, "summary": None, "gate": None, "srs": [],
    }
    save(s)
    return s


def _entry(slug: str, item: dict, variant_of: str | None = None) -> dict:
    origin = variant_of or srs.uid(slug, item["id"])
    u = f"{origin}~v{short_token(4)}" if variant_of else origin
    return {"uid": u, "topic": slug, "origin": origin, "variant": bool(variant_of), "item": item}


def find_open(topic: str | None, mode: str) -> dict | None:
    d = paths.sessions_dir()
    if not d.exists():
        return None
    for p in sorted(d.glob(f"*-{mode}-{topic or 'mixed'}-*.json"), reverse=True):
        s = read_json(p)
        if s and s["status"] in ("open", "grade_failed", "grading"):
            return s
    return None


# ---------------------------------------------------------------- creation

def _ordered(items: list[dict], rng: random.Random) -> list[dict]:
    """Easy -> hard, shuffled within a difficulty, concepts interleaved."""
    out = []
    for lvl in (1, 2, 3):
        tier = [i for i in items if i.get("difficulty", 2) == lvl]
        rng.shuffle(tier)
        by_c: dict[str, list[dict]] = {}
        for i in tier:
            by_c.setdefault(i["concept"], []).append(i)
        queues = list(by_c.values())
        while queues:
            for q in list(queues):
                out.append(q.pop(0))
                if not q:
                    queues.remove(q)
    return out


def create_gate(slug: str, force: bool = False) -> dict:
    cfg = paths.config()
    st = topics.load_state(slug)
    if st["stage"] != "quiz" and not force:
        raise LkError(f"topic is at '{st['stage']}', not 'quiz' ({topics.NEXT_HINT[st['stage']]})")
    existing = find_open(slug, "gate")
    if existing:
        return existing
    if topics.rounds_today(st) >= cfg["gate"]["max_rounds_per_day"] and not force:
        raise LkError(f"{topics.rounds_today(st)} gate rounds already today. Sleep on it and come back "
                      "tomorrow: consolidation overnight is part of the method (--force to override).")
    cov = quiz.coverage(slug, cfg)
    if cov["issues"] and not force:
        raise LkError("not enough fresh items:\n  - " + "\n  - ".join(cov["issues"]))
    cap = cfg["depth"][st["depth"]]["items_per_concept"][1] + 1
    used = quiz.gate_attempted_ids(slug)
    targets = {c["id"]: c for c in topics.target_concepts(slug)}
    chosen = []
    for cid, c in targets.items():
        fresh = [i for i in quiz.load_bank(slug) if i["concept"] == cid and i["id"] not in used]
        fresh.sort(key=lambda i: (-i.get("round", 0), i["type"] not in quiz.FREE_RECALL))
        pick = fresh[:cap]
        if c.get("core") and not any(i["type"] in quiz.FREE_RECALL for i in pick):
            fr = next((i for i in fresh if i["type"] in quiz.FREE_RECALL), None)
            if fr:
                pick = pick[:-1] + [fr]
        chosen += pick
    if not chosen:
        raise LkError("no items to quiz")
    rng = random.Random()
    entries = [_entry(slug, i) for i in _ordered(chosen, rng)]
    return _new("gate", slug, entries, st["round"], f"{st['title']} — round {st['round']}")


def create_practice(slug: str, n: int = 10) -> dict:
    st = topics.load_state(slug)
    bank = quiz.load_bank(slug)
    if not bank:
        raise LkError("quiz bank is empty")
    rng = random.Random()
    pick = rng.sample(bank, min(n, len(bank)))
    return _new("practice", slug, [_entry(slug, i) for i in _ordered(pick, rng)], None, f"{st['title']} — practice")


def create_review(limit: int | None = None, variants: dict | list | None = None,
                  include_mistakes: int | None = None) -> dict:
    due = srs.due_items(limit, include_mistakes)
    if not due:
        nxt = srs.stats()["next_due"]
        raise LkError(f"nothing due. Next card due {nxt}." if nxt else "SRS is empty: finish a topic first.")
    vmap: dict[str, dict] = {}
    if variants:
        items, errs = quiz.validate_file(variants, None, variant=True)
        if errs:
            raise LkError("variants invalid:\n  - " + "\n  - ".join(errs))
        orig = {d["uid"]: d for d in due}
        for v in items:
            o = orig.get(v["origin"])
            if o is None:
                continue  # stale variant for a card no longer due
            if v["concept"] != o["item"]["concept"]:
                raise LkError(f"variant for {v['origin']} changed concept ({v['concept']} != {o['item']['concept']})")
            vmap[v["origin"]] = v
    entries = []
    for d in due:
        v = vmap.get(d["uid"])
        entries.append(_entry(d["topic"], v, variant_of=d["uid"]) if v else _entry(d["topic"], d["item"]))
    return _new("review", None, entries, None, f"Recall — {today().isoformat()}")


# ---------------------------------------------------------------- answering

def record_answer(s: dict, u: str, response, confidence: int | None, time_ms: int | None) -> None:
    if s["status"] not in ("open", "grade_failed"):
        raise LkError("session already graded")
    if u not in {e["uid"] for e in s["items"]}:
        raise LkError(f"unknown item {u}")
    if confidence is not None and confidence not in (1, 2, 3):
        raise LkError("confidence is 1..3")
    s["answers"][u] = {"response": response, "confidence": confidence, "time_ms": time_ms, "at": now_iso()}
    save(s)


# ---------------------------------------------------------------- grading + side effects

def _grade(s: dict, grade_fn) -> None:
    pending = []
    for e in s["items"]:
        u, it = e["uid"], e["item"]
        if u in s["results"]:
            continue
        a = s["answers"].get(u, {})
        resp = a.get("response")
        base = {"expected": quiz.expected_text(it), "explanation": it.get("explanation", "")}
        if it["type"] in quiz.AUTO_TYPES:
            r = quiz.grade_auto(it, resp)
            if quiz.needs_llm(it, resp, r):
                pending.append({"uid": u, "item": it, "response": resp})
            else:
                s["results"][u] = r
        elif quiz.is_empty(it, resp):
            s["results"][u] = {**base, "score": 0.0, "correct": False, "feedback": "No answer.", "graded_by": "script"}
        else:
            pending.append({"uid": u, "item": it, "response": resp})
    if pending:
        graded = grade_fn(pending)
        for p in pending:
            g = graded[p["uid"]]
            s["results"][p["uid"]] = {"expected": quiz.expected_text(p["item"]),
                                      "explanation": p["item"].get("explanation", ""), **g}


def summarize(s: dict) -> dict:
    rows = []
    for e in s["items"]:
        r = s["results"].get(e["uid"], {})
        a = s["answers"].get(e["uid"], {})
        rows.append((e, r, a))
    scores = [float(r.get("score", 0)) for _, r, _ in rows]
    by_c: dict[str, list[float]] = {}
    by_t: dict[str, list[float]] = {}
    cw = dk = 0
    for e, r, a in rows:
        by_c.setdefault(f"{e['topic']}:{e['item']['concept']}", []).append(float(r.get("score", 0)))
        by_t.setdefault(e["item"]["type"], []).append(float(r.get("score", 0)))
        if (a.get("confidence") or 0) >= 3 and float(r.get("score", 0)) < 0.5:
            cw += 1
        if not a or (a.get("response") or {}).get("dont_know"):
            dk += 1
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else 0.0  # noqa: E731
    return {
        "n": len(rows), "overall": mean(scores), "correct": sum(1 for _, r, _ in rows if r.get("correct")),
        "by_concept": {k: {"mean": mean(v), "n": len(v)} for k, v in by_c.items()},
        "by_type": {k: {"mean": mean(v), "n": len(v)} for k, v in by_t.items()},
        "confident_wrong": cw, "dont_know": dk,
    }


def _apply(s: dict) -> None:
    for e in s["items"]:
        u, it = e["uid"], e["item"]
        r = s["results"][u]
        a = s["answers"].get(u, {})
        conf = a.get("confidence")
        if (a.get("response") or {}).get("dont_know"):
            conf = 1
        if topics.exists(e["topic"]):
            append_jsonl(topics.tdir(e["topic"]) / "attempts.jsonl", {
                "session": s["id"], "mode": s["mode"], "uid": u, "origin": e["origin"],
                "item_id": it.get("id") or e["origin"].split("/")[-1].split("~")[0],
                "concept": it["concept"], "type": it["type"], "score": r["score"], "correct": r["correct"],
                "confidence": conf, "time_ms": a.get("time_ms"), "at": now_iso(),
            })
        if float(r["score"]) < 0.5:
            mistakes.record({
                "uid": u, "origin": e["origin"], "topic": e["topic"], "concept": it["concept"],
                "prompt": it["prompt"], "answer": quiz.response_text(it, a.get("response")),
                "expected": r.get("expected", ""), "feedback": " ".join(x for x in (r.get("feedback", ""),
                                                                                      r.get("explanation", "")) if x),
                "confidence": conf, "session": s["id"],
            })
        if s["mode"] == "review":
            upd = srs.record(e["origin"], float(r["score"]), conf)
            if upd:
                s["srs"].append(upd)
    if s["mode"] == "gate":
        res = gate.evaluate(s)
        res.update(gate.apply(s, res))
        s["gate"] = res


def finish(sid: str, grade_fn=None) -> dict:
    s = load(sid)
    if s["status"] == "applied":
        return s
    s["status"] = "grading"
    s.pop("error", None)
    save(s)
    try:
        _grade(s, grade_fn or grader.grade)
    except LkError as e:
        s["status"] = "grade_failed"
        s["error"] = str(e)
        save(s)  # keep the script-graded results; only the LLM part is retried
        raise
    s["summary"] = summarize(s)
    s["status"] = "graded"
    save(s)
    _apply(s)
    s["status"] = "applied"
    s["finished"] = now_iso()
    save(s)
    return s


def apply_manual_grades(sid: str, grades: dict) -> dict:
    """Fallback when the CLI grader is unavailable: the agent grades in the terminal and writes
    {"grades": [{"id": uid, "points": [...], "feedback": "...", "missed": [], "wrong_claims": []}]}."""
    by = {g["id"]: g for g in grades.get("grades", [])}
    cfg = paths.config()

    def fn(entries):
        out = {}
        for e in entries:
            g = by.get(e["uid"])
            if g is None:
                raise LkError(f"manual grades missing {e['uid']}")
            n = len(e["item"].get("rubric") or e["item"].get("blanks") or [1])
            pts = [float(p) for p in g.get("points", [])][:n] + [0.0] * max(0, n - len(g.get("points", [])))
            sc = round(sum(pts) / n, 3)
            out[e["uid"]] = {"score": sc, "correct": sc >= cfg["gate"]["free_recall_pass"], "points": pts,
                             "feedback": g.get("feedback", ""), "missed": g.get("missed", []),
                             "wrong_claims": g.get("wrong_claims", []), "graded_by": "manual"}
        return out

    return finish(sid, fn)


def pending_llm(sid: str) -> list[dict]:
    """What still needs LLM grading (for manual grading by the agent)."""
    s = load(sid)
    out = []
    for e in s["items"]:
        if e["uid"] in s["results"]:
            continue
        it = e["item"]
        resp = s["answers"].get(e["uid"], {}).get("response")
        llm_free = it["type"] in quiz.LLM_TYPES and not quiz.is_empty(it, resp)
        llm_cloze = it["type"] == "cloze" and quiz.needs_llm(it, resp, quiz.grade_auto(it, resp))
        if llm_free or llm_cloze:
            out.append({"id": e["uid"], **grader._payload([{"uid": e["uid"], "item": it, "response": resp}])[0]})
    return out


# ---------------------------------------------------------------- views for the browser

def client_view(s: dict) -> dict:
    cfg = paths.config()
    items = []
    for n, e in enumerate(s["items"]):
        rng = random.Random(s["seed"] + n)
        items.append({"uid": e["uid"], "topic": e["topic"], **quiz.present(e["item"], rng)})
    return {"id": s["id"], "mode": s["mode"], "topic": s["topic"], "title": s["title"], "round": s["round"],
            "status": s["status"], "speech_lang": cfg["speech_lang"], "items": items,
            "answers": {u: {"response": a["response"], "confidence": a["confidence"]} for u, a in s["answers"].items()}}


def results_view(s: dict) -> dict:
    srcs = sources.by_id()
    cnames: dict[str, dict] = {}
    rows = []
    for n, e in enumerate(s["items"]):
        it, r = e["item"], s["results"].get(e["uid"])
        a = s["answers"].get(e["uid"], {})
        if e["topic"] not in cnames:
            cnames[e["topic"]] = topics.concept_map(e["topic"]) if topics.exists(e["topic"]) else {}
        rows.append({
            "n": n + 1, "uid": e["uid"], "topic": e["topic"], "type": it["type"], "prompt": it["prompt"],
            "code": it.get("code"), "concept": it["concept"],
            "concept_name": cnames[e["topic"]].get(it["concept"], {}).get("name", it["concept"]),
            "your_answer": quiz.response_text(it, a.get("response")), "confidence": a.get("confidence"),
            "result": r,
            "confident_wrong": bool(r and (a.get("confidence") or 0) >= 3 and float(r.get("score", 0)) < 0.5),
            "sources": [{"id": x, "title": srcs.get(x, {}).get("title", x), "url": srcs.get(x, {}).get("url")}
                        for x in it.get("source_ids", [])],
        })
    return {"id": s["id"], "mode": s["mode"], "status": s["status"], "error": s.get("error"),
            "summary": s.get("summary"), "gate": s.get("gate"), "srs": s.get("srs"), "items": rows}
