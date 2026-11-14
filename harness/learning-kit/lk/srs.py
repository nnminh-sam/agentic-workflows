"""Spaced recall: per-item FSRS cards across all topics, enrollment, due queue, recording."""
from __future__ import annotations

import datetime as _dt
import random

from . import fsrs, paths, quiz, topics
from .store import append_jsonl, now_iso, read_json, read_jsonl, today, write_json


def load() -> dict:
    return read_json(paths.srs_file(), {"cards": {}}) or {"cards": {}}


def save(data: dict) -> None:
    write_json(paths.srs_file(), data)


def uid(slug: str, item_id: str) -> str:
    return f"{slug}/{item_id}"


def enroll(slug: str) -> list[str]:
    """Put every gate-attempted item of a mastered concept into SRS, seeded by its last attempt."""
    cfg = paths.config()["srs"]
    data = load()
    mastered = {c["id"] for c in topics.load_concepts(slug) if c.get("status") == "mastered"}
    last: dict[str, dict] = {}
    for a in topics.attempts(slug):
        if a.get("mode") == "gate":
            last[a["item_id"]] = a
    added = []
    on = today()
    for item_id, a in last.items():
        u = uid(slug, item_id)
        if u in data["cards"] or a["concept"] not in mastered:
            continue
        g = fsrs.grade_from(a["score"], a.get("confidence"))
        card = fsrs.review(None, g, on, cfg["desired_retention"], cfg["max_interval_days"])
        card.update(topic=slug, item_id=item_id, concept=a["concept"], enrolled=now_iso())
        data["cards"][u] = card
        added.append(u)
    save(data)
    return added


def _interleave(cards: list[dict]) -> list[dict]:
    """Round-robin across topics so consecutive questions rarely share a topic."""
    by_topic: dict[str, list[dict]] = {}
    for c in cards:
        by_topic.setdefault(c["topic"], []).append(c)
    out = []
    queues = list(by_topic.values())
    while queues:
        for q in list(queues):
            out.append(q.pop(0))
            if not q:
                queues.remove(q)
    return out


def due(limit: int | None = None, on: _dt.date | None = None, include_mistakes: int | None = None) -> list[dict]:
    cfg = paths.config()["srs"]
    on = on or today()
    limit = limit or cfg["daily_limit"]
    extras_n = cfg["mistake_extras"] if include_mistakes is None else include_mistakes
    cards = load()["cards"]
    rows = []
    for u, c in cards.items():
        if _dt.date.fromisoformat(c["due"]) <= on:
            el = (on - _dt.date.fromisoformat(c["last"])).days
            rows.append({**c, "uid": u, "r_now": round(fsrs.retrievability(el, c["s"]), 3), "extra": False})
    rows.sort(key=lambda r: (r["due"], r["r_now"]))
    rows = rows[:limit]
    if extras_n:
        chosen = {r["uid"] for r in rows}
        recent = [m for m in reversed(read_jsonl(paths.mistakes_jsonl())) if m.get("confident")]
        for m in recent:
            if len([r for r in rows if r["extra"]]) >= extras_n:
                break
            u = m.get("origin") or m["uid"]
            c = cards.get(u)
            if c and u not in chosen and (on - _dt.date.fromisoformat(c["last"])).days >= 3:
                chosen.add(u)
                rows.append({**c, "uid": u, "r_now": None, "extra": True})
    random.Random(on.toordinal()).shuffle(rows)
    return _interleave(rows)


def due_items(limit: int | None = None, include_mistakes: int | None = None) -> list[dict]:
    """Due cards joined with their full bank item (for rewording and serving)."""
    out = []
    banks: dict[str, dict] = {}
    for c in due(limit, include_mistakes=include_mistakes):
        if c["topic"] not in banks:
            banks[c["topic"]] = quiz.item_map(c["topic"]) if topics.exists(c["topic"]) else {}
        it = banks[c["topic"]].get(c["item_id"])
        if it:
            out.append({"uid": c["uid"], "topic": c["topic"], "card": c, "item": it})
    return out


def record(u: str, score: float, confidence: int | None, on: _dt.date | None = None) -> dict:
    cfg = paths.config()["srs"]
    on = on or today()
    data = load()
    card = data["cards"].get(u)
    if card is None:
        return {}
    g = fsrs.grade_from(score, confidence)
    new = fsrs.review(card, g, on, cfg["desired_retention"], cfg["max_interval_days"])
    data["cards"][u] = new
    save(data)
    append_jsonl(paths.srs_log(), {"uid": u, "at": now_iso(), "grade": g, "score": score,
                                   "confidence": confidence, "s": new["s"], "d": new["d"], "due": new["due"],
                                   "r_at_review": new["r_at_review"]})
    if g == fsrs.AGAIN and topics.exists(new["topic"]):
        try:
            topics.set_concept_status(new["topic"], new["concept"], "lapsed")
        except Exception:
            pass
    return {"uid": u, "grade": g, "due": new["due"], "s": new["s"]}


def stats(on: _dt.date | None = None) -> dict:
    on = on or today()
    cards = load()["cards"]
    week = on + _dt.timedelta(days=7)
    due_now = sum(1 for c in cards.values() if _dt.date.fromisoformat(c["due"]) <= on)
    due_week = sum(1 for c in cards.values() if _dt.date.fromisoformat(c["due"]) <= week)
    rs = [fsrs.retrievability((on - _dt.date.fromisoformat(c["last"])).days, c["s"]) for c in cards.values()]
    nxt = min((c["due"] for c in cards.values()), default=None)
    log = read_jsonl(paths.srs_log())
    recent = [x for x in log if x["at"][:10] >= (on - _dt.timedelta(days=30)).isoformat()]
    return {
        "cards": len(cards),
        "due_today": due_now,
        "due_7d": due_week,
        "next_due": nxt,
        "est_retention_now": round(sum(rs) / len(rs), 3) if rs else None,
        "reviews_30d": len(recent),
        "recall_rate_30d": round(sum(1 for x in recent if x["grade"] > 1) / len(recent), 3) if recent else None,
        "lapses_total": sum(c.get("lapses", 0) for c in cards.values()),
    }
