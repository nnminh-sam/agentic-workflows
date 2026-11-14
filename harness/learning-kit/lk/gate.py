"""The mastery gate: the 80% rule, measured on the topic's *current* target concepts.

A gate session passes when
  1. the overall mean score over its items is >= pass_score (0.8), and
  2. every core target concept has at least one free-recall (explain/predict) answer
     scoring >= free_recall_pass (0.7).
A concept passes individually when its own mean is >= pass_score and (if core) rule 2 holds.
On a failed round, concepts that passed individually are still marked mastered, so the next
round re-tests only what is still missing. Growing the topic adds new concepts, which become
the next targets; mastered ones stay in spaced review.
"""
from __future__ import annotations

from . import topics
from .paths import config
from .quiz import FREE_RECALL
from .store import today


def evaluate(session: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    pass_score = cfg["gate"]["pass_score"]
    fr_pass = cfg["gate"]["free_recall_pass"]
    slug = session["topic"]
    cmap = topics.concept_map(slug)
    per: dict[str, dict] = {}
    scores = []
    for e in session["items"]:
        it = e["item"]
        r = session["results"].get(e["uid"], {})
        sc = float(r.get("score", 0.0))
        scores.append(sc)
        row = per.setdefault(it["concept"], {"scores": [], "fr_ok": False,
                                             "core": bool(cmap.get(it["concept"], {}).get("core"))})
        row["scores"].append(sc)
        if it["type"] in FREE_RECALL and sc >= fr_pass:
            row["fr_ok"] = True
    overall = round(sum(scores) / len(scores), 3) if scores else 0.0
    concepts, missed, blocking = {}, [], []
    for cid, row in per.items():
        mean = round(sum(row["scores"]) / len(row["scores"]), 3)
        ok = mean >= pass_score and (row["fr_ok"] or not row["core"])
        concepts[cid] = {"mean": mean, "n": len(row["scores"]), "core": row["core"],
                         "free_recall_ok": row["fr_ok"], "passed": ok}
        if not ok:
            missed.append(cid)
        if row["core"] and not row["fr_ok"]:
            blocking.append(cid)
    passed = overall >= pass_score and not blocking
    # Passing overall can still hide a weak concept. It is mastered with the rest, but its low-scoring
    # items enter SRS with grade Again (due tomorrow), and the UI and note review name it.
    return {"passed": passed, "overall": overall, "pass_score": pass_score, "concepts": concepts,
            "missed_concepts": sorted(missed), "core_without_free_recall": sorted(blocking),
            "weak_concepts": sorted(missed) if passed else []}


def next_stage_after_pass(st: dict) -> str:
    if st.get("relearn") or st["depth"] == "aware":
        return "scheduled"
    return "lab" if st["depth"] == "deep" else "note"


def apply(session: dict, result: dict) -> dict:
    """Write the gate outcome into concepts.json and state.json; move the stage."""
    slug = session["topic"]
    cs = topics.load_concepts(slug)
    t = today().isoformat()
    for c in cs:
        r = result["concepts"].get(c["id"])
        if r is None:
            continue
        if result["passed"] or r["passed"]:
            c["status"], c["mastered_at"] = "mastered", t
        else:
            c["status"] = "learning"
    topics.save_concepts(slug, cs)

    st = topics.load_state(slug)
    st["rounds_log"].append({"round": session.get("round", st["round"]), "date": t, "session": session["id"],
                             "score": result["overall"], "passed": result["passed"],
                             "missed": result["missed_concepts"]})
    topics.save_state(slug, st)
    to = next_stage_after_pass(st) if result["passed"] else "reteach"
    topics.advance(slug, to, note=f"gate {'pass' if result['passed'] else 'fail'} {result['overall']:.0%}",
                   _from_gate=True)
    return {"next_stage": to}
