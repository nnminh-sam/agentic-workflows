"""Bite-sized lessons: a topic taught as small pieces, each one question -> one paragraph -> an own-words
answer -> a small check. Topics in "bite" mode use them, and in that mode the lesson is the gate.

    topics/<slug>/lesson.json  {"pieces": [{id p001, concept, title, question, body, source_ids,
                                            recall_item, check_items[], round, added_in, created, reviewed?}]}
    topics/<slug>/lesson.md    every piece as one readable page (derived on import; for looking back)

A piece's question and its checks are stored as ordinary items in quiz.json (with `piece` and
`role` = "recall" | "check"), so grading, the mistakes log, practice, recall and checkpoints treat them like
any other item. The own-words answer is graded as an `explain` item: its reference answer and rubric live on
the recall item only, never in the session view.

A lesson round = the pieces imported in the topic's current round, for its target concepts. The first round
comes from research, a reteach round brings fresh pieces for the missed concepts only, and extending the
topic brings pieces for the new concepts.

Draft format (what the lesson author writes; `lk lesson validate | import`):
    {"pieces": [{"concept", "title", "question", "body", "reference_answer", "rubric": [...],
                 "source_ids": [...], "difficulty"?, "explanation"?,
                 "checks": [<quiz items; concept and source_ids default to the piece's>]}]}
Authoring rules: docs/authoring/lesson-authoring.md.
"""
from __future__ import annotations

import re

from . import paths, quiz, sources, topics
from .store import LkError, now_iso, read_json, write_json, write_text

PIECE_FIELDS = {"concept", "title", "question", "body", "reference_answer", "rubric", "source_ids",
                "difficulty", "explanation", "checks"}
MIN_BODY_WORDS = 15
_CITE = re.compile(r"\[(S\d{4,})\]")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


# ---------------------------------------------------------------- files

def lesson_path(slug: str):
    return topics.tdir(slug) / "lesson.json"


def md_path(slug: str):
    return topics.tdir(slug) / "lesson.md"


def load(slug: str) -> list[dict]:
    return (read_json(lesson_path(slug), {"pieces": []}) or {}).get("pieces", [])


def save(slug: str, pieces: list[dict]) -> None:
    write_json(lesson_path(slug), {"pieces": pieces})


def piece_map(slug: str) -> dict[str, dict]:
    return {p["id"]: p for p in load(slug)}


# ---------------------------------------------------------------- validation

def prose_words(body: str) -> int:
    """Words a reader has to read: fenced code and citation ids don't count."""
    words, fence = 0, False
    for ln in (body or "").splitlines():
        if _FENCE.match(ln):
            fence = not fence
            continue
        if not fence:
            words += len(re.findall(r"[\w'’-]+", _CITE.sub("", ln)))
    return words


def _recall_item(p: dict) -> dict:
    """The piece's opening question as a free-recall item: answered in the learner's own words."""
    it = {"type": "explain", "concept": p.get("concept"), "prompt": p.get("question", ""),
          "reference_answer": p.get("reference_answer"), "rubric": p.get("rubric"),
          "source_ids": p.get("source_ids"), "difficulty": p.get("difficulty", 2),
          # Shown after grading (and in the mistakes log): the paragraph is the best "why".
          "explanation": p.get("explanation") or p.get("body", "")}
    return it


def _check_item(p: dict, c: dict) -> dict:
    it = dict(c)
    it.setdefault("concept", p.get("concept"))
    it.setdefault("source_ids", p.get("source_ids"))
    return it


def validate_draft(data, slug: str | None = None) -> tuple[list[dict], list[str], list[str]]:
    """Returns (pieces, errors, warnings). Errors block import; warnings are style calls for the author."""
    cfg = paths.config()["lesson"]
    max_words, max_checks = int(cfg["max_body_words"]), int(cfg["max_checks_per_piece"])
    pieces = data.get("pieces") if isinstance(data, dict) else data
    if not isinstance(pieces, list) or not pieces:
        return [], ['file must be {"pieces": [...]} (or a bare list of pieces) with at least one piece'], []
    cids = set(topics.concept_map(slug)) if slug else None
    sids = set(sources.by_id())
    errs: list[str] = []
    warns: list[str] = []
    for n, p in enumerate(pieces, 1):
        if not isinstance(p, dict):
            errs.append(f"[piece {n}] not an object")
            continue
        tag = f"piece {n}" + (f" '{p['title'][:40]}'" if isinstance(p.get("title"), str) else "")
        unknown = sorted(set(p) - PIECE_FIELDS)
        if unknown:
            errs.append(f"[{tag}] unknown field(s) {unknown}; allowed: {sorted(PIECE_FIELDS)}")
        item_cids = cids  # report an unknown concept once, on the piece, not again on each of its items
        if not isinstance(p.get("concept"), str) or not p["concept"].strip():
            errs.append(f"[{tag}] concept is required (a concept id from concepts.json)")
            item_cids = None
        elif cids is not None and p["concept"] not in cids:
            errs.append(f"[{tag}] unknown concept '{p['concept']}' (`lk concepts merge` first)")
            item_cids = None
        if not isinstance(p.get("title"), str) or not p["title"].strip():
            errs.append(f"[{tag}] title is required")
        elif len(p["title"]) > 90:
            errs.append(f"[{tag}] title is over 90 characters: name the idea, don't explain it")
        q = p.get("question")
        if not isinstance(q, str) or not q.strip():
            errs.append(f"[{tag}] question is required: the question this piece answers, asked before the paragraph")
        elif "?" not in q:
            warns.append(f"[{tag}] the question has no '?': is it really a question?")
        body = p.get("body")
        if not isinstance(body, str) or not body.strip():
            errs.append(f"[{tag}] body is required: one paragraph that answers the question")
        else:
            w = prose_words(body)
            if w > max_words:
                errs.append(f"[{tag}] body is {w} words; a piece is one paragraph of <= {max_words}. "
                            "Split it into two pieces")
            elif w < MIN_BODY_WORDS:
                errs.append(f"[{tag}] body is {w} words: too short to teach anything")
            if re.search(r"\n\s*\n\s*\S", body.strip()) and not re.search(r"```|~~~", body):
                warns.append(f"[{tag}] body has several paragraphs: keep it to one, or split the piece")
            cited = _CITE.findall(body)
            missing = sorted({c for c in cited if c not in sids})
            if missing:
                errs.append(f"[{tag}] body cites sources not in the bank: {missing}")
            if not cited:
                warns.append(f"[{tag}] body cites no source: add [S####] after the claims")
        for e in quiz.validate_item(_recall_item(p), item_cids, sids):
            errs.append(f"[{tag}] question/answer: " + e.split("] ", 1)[-1])
        checks = p.get("checks")
        if not isinstance(checks, list) or not 1 <= len(checks) <= max_checks:
            errs.append(f"[{tag}] checks: 1-{max_checks} quiz items (the small quiz after the piece)")
            continue
        for k, c in enumerate(checks, 1):
            if not isinstance(c, dict):
                errs.append(f"[{tag} check {k}] not an object")
                continue
            it = _check_item(p, c)
            if isinstance(p.get("concept"), str) and it.get("concept") != p["concept"]:
                errs.append(f"[{tag} check {k}] a check tests its own piece: concept must be '{p['concept']}'")
            for e in quiz.validate_item(it, item_cids, sids):
                errs.append(f"[{tag} check {k}] " + e.split("] ", 1)[-1])
        if all(isinstance(c, dict) and c.get("type") in quiz.FREE_RECALL for c in checks):
            warns.append(f"[{tag}] every check is free recall: the own-words answer already is; "
                         "prefer mcq / multi / order / cloze checks (instant feedback)")
    return pieces, errs, warns


# ---------------------------------------------------------------- import

def import_draft(slug: str, data, reviewed_by: str = "") -> dict:
    st = topics.load_state(slug)
    if topics.mode_of(st) != "bite":
        raise LkError(f"topic '{slug}' is in {topics.mode_of(st)} mode: `lk topic mode {slug} bite` first")
    pieces, errs, warns = validate_draft(data, slug)
    if errs:
        raise LkError("lesson file invalid:\n  - " + "\n  - ".join(errs))
    existing = load(slug)
    n = max((int(p["id"][1:]) for p in existing), default=0)
    items, owners = [], []
    for p in pieces:
        n += 1
        pid = f"p{n:03d}"
        items.append({**_recall_item(p), "piece": pid, "role": "recall"})
        owners.append(pid)
        for c in p["checks"]:
            items.append({**_check_item(p, c), "piece": pid, "role": "check"})
            owners.append(pid)
    ids = quiz.import_items(slug, items, reviewed_by)
    by_piece: dict[str, list[str]] = {}
    for pid, qid in zip(owners, ids):
        by_piece.setdefault(pid, []).append(qid)
    added = []
    for p, (pid, qids) in zip(pieces, by_piece.items()):
        rec = {"id": pid, "concept": p["concept"], "title": p["title"].strip(), "question": p["question"].strip(),
               "body": p["body"].strip(), "source_ids": p["source_ids"], "recall_item": qids[0],
               "check_items": qids[1:], "round": st["round"], "added_in": st["version"], "created": now_iso()}
        if reviewed_by:
            rec["reviewed"] = {"by": reviewed_by, "at": now_iso()}
        existing.append(rec)
        added.append(pid)
    save(slug, existing)
    write_text(md_path(slug), render_md(slug))
    return {"pieces": added, "items": ids, "warnings": warns, "coverage": coverage(slug)}


# ---------------------------------------------------------------- the current round

def current_pieces(slug: str) -> list[dict]:
    """Pieces of the topic's current round, for target concepts, not yet answered in a lesson or gate."""
    st = topics.load_state(slug)
    targets = {c["id"] for c in topics.target_concepts(slug)}
    spent = quiz.gate_attempted_ids(slug)
    return [p for p in load(slug)
            if p["round"] == st["round"] and p["concept"] in targets and p["recall_item"] not in spent]


def coverage(slug: str) -> dict:
    st = topics.load_state(slug)
    cur = current_pieces(slug)
    rows, issues = [], []
    for c in topics.target_concepts(slug):
        mine = [p for p in cur if p["concept"] == c["id"]]
        rows.append({"concept": c["id"], "core": c.get("core", False), "pieces": len(mine),
                     "checks": sum(len(p["check_items"]) for p in mine)})
        if not mine:
            issues.append(f"{c['id']}: no piece for round {st['round']} (author one and `lk lesson import`)")
    if not rows:
        issues.append("no target concepts (everything mastered?)")
    return {"round": st["round"], "concepts": rows, "issues": issues}


def summary(slug: str) -> dict:
    """For the library: counts only (the pieces themselves are teaching, not answers, but the
    question/answer pairs live in the bank and are never summarised here)."""
    all_p = load(slug)
    cov = coverage(slug) if topics.exists(slug) else {"concepts": [], "issues": []}
    return {"pieces": len(all_p), "current": sum(r["pieces"] for r in cov["concepts"]),
            "round": cov.get("round"), "by_concept": {r["concept"]: r["pieces"] for r in cov["concepts"]},
            "coverage_issues": cov["issues"], "has_page": md_path(slug).exists()}


# ---------------------------------------------------------------- lesson.md (derived)

def render_md(slug: str) -> str:
    st = topics.load_state(slug)
    cmap = topics.concept_map(slug)
    pieces = load(slug)
    order: list[str] = []
    for p in pieces:
        if p["concept"] not in order:
            order.append(p["concept"])
    out = [f"# {st['title']}: the lesson", "",
           "> [!note] Every piece in one page, for looking back. The method is to take them one at a time in the "
           "app: question first, then the paragraph, then your own words. Generated by `lk lesson import`; "
           "don't edit this file.", ""]
    for cid in order:
        out += [f"## {cmap.get(cid, {}).get('name', cid)} {{#{cid}}}", ""]
        for p in sorted((x for x in pieces if x["concept"] == cid), key=lambda x: (x["round"], x["id"])):
            out += [f"### {p['title']}", ""]
            if p["round"] > 1:
                out += [f"_Round {p['round']}_", ""]
            out += [f"**Question:** {p['question']}", "", p["body"], ""]
    return "\n".join(out).rstrip() + "\n"
