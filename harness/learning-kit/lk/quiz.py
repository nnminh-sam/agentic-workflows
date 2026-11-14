"""Quiz items: the one schema every question type shares, validation, import, coverage,
client-safe presentation and deterministic grading.

Adding a question type = add it to TYPES, a branch in `validate_item`, `present`, and
`grade_auto` (or put it in LLM_TYPES), plus a renderer in web/app.js.
"""
from __future__ import annotations

import random
import re
import unicodedata

from . import sources, topics
from .store import LkError, now_iso, read_json, write_json

AUTO_TYPES = {"mcq", "multi", "order", "cloze"}
LLM_TYPES = {"predict", "explain"}
TYPES = AUTO_TYPES | LLM_TYPES
FREE_RECALL = LLM_TYPES

_BLANK = re.compile(r"\{\{(\d+)\}\}")


# ---------------------------------------------------------------- bank

def bank_path(slug: str):
    return topics.tdir(slug) / "quiz.json"


def load_bank(slug: str) -> list[dict]:
    return (read_json(bank_path(slug), {"items": []}) or {}).get("items", [])


def save_bank(slug: str, items: list[dict]) -> None:
    write_json(bank_path(slug), {"items": items})


def item_map(slug: str) -> dict[str, dict]:
    return {i["id"]: i for i in load_bank(slug)}


# ---------------------------------------------------------------- validation

def _strs(x, n_min=1) -> bool:
    return isinstance(x, list) and len(x) >= n_min and all(isinstance(s, str) and s.strip() for s in x)


def validate_item(it: dict, concept_ids: set[str] | None = None, source_ids: set[str] | None = None,
                  variant: bool = False) -> list[str]:
    e: list[str] = []
    tag = it.get("id") or it.get("origin") or (it.get("prompt", "")[:40] + "...")
    t = it.get("type")
    if t not in TYPES:
        return [f"[{tag}] type must be one of {sorted(TYPES)}"]
    if not isinstance(it.get("prompt"), str) or not it["prompt"].strip():
        e.append(f"[{tag}] prompt is required")
    if not isinstance(it.get("concept"), str):
        e.append(f"[{tag}] concept is required")
    elif concept_ids is not None and it["concept"] not in concept_ids:
        e.append(f"[{tag}] unknown concept '{it['concept']}'")
    if it.get("difficulty", 2) not in (1, 2, 3):
        e.append(f"[{tag}] difficulty is 1..3")
    sids = it.get("source_ids")
    if not _strs(sids):
        e.append(f"[{tag}] source_ids: cite at least one source id (grounding)")
    elif source_ids is not None:
        missing = [s for s in sids if s not in source_ids]
        if missing:
            e.append(f"[{tag}] source_ids not in bank: {missing}")
    if variant and not isinstance(it.get("origin"), str):
        e.append(f"[{tag}] a variant needs 'origin' = the uid it rewords (topic/qNNNN)")
    if not isinstance(it.get("explanation"), str) or len(it["explanation"].strip()) < 10:
        e.append(f"[{tag}] explanation is required: why the answer is right (and why the tempting one is wrong)")
    if "code" in it and not isinstance(it["code"], str):
        e.append(f"[{tag}] code must be a string")

    if t in ("mcq", "multi"):
        opts = it.get("options")
        lo, hi = (3, 6) if t == "mcq" else (3, 8)
        if not _strs(opts, lo) or len(opts) > hi:
            e.append(f"[{tag}] {t} needs {lo}-{hi} non-empty options")
        elif len({o.strip().lower() for o in opts}) != len(opts):
            e.append(f"[{tag}] options must be unique")
        else:
            a = it.get("answer")
            if t == "mcq":
                if not isinstance(a, int) or isinstance(a, bool) or not 0 <= a < len(opts):
                    e.append(f"[{tag}] mcq answer must be an option index 0..{len(opts) - 1}")
            else:
                if (not isinstance(a, list) or not a or len(set(a)) != len(a)
                        or not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(opts) for i in a)):
                    e.append(f"[{tag}] multi answer must be a non-empty list of unique option indexes")
    elif t == "order":
        steps = it.get("steps")
        if not _strs(steps, 3) or len({s.strip().lower() for s in steps}) != len(steps):
            e.append(f"[{tag}] order needs >=3 unique steps, listed in the correct order")
    elif t == "cloze":
        blanks = it.get("blanks")
        nums = sorted({int(m) for m in _BLANK.findall(it.get("prompt", ""))})
        if not isinstance(blanks, list) or not blanks or not all(_strs(b) for b in blanks):
            e.append(f"[{tag}] cloze blanks: list of accepted-answer lists, one per {{{{n}}}}")
        elif nums != list(range(1, len(blanks) + 1)):
            e.append(f"[{tag}] cloze prompt must contain {{{{1}}}}..{{{{{len(blanks)}}}}} exactly")
    elif t in LLM_TYPES:
        if not isinstance(it.get("reference_answer"), str) or len(it["reference_answer"].strip()) < 10:
            e.append(f"[{tag}] {t} needs a reference_answer")
        if not _strs(it.get("rubric"), 2):
            e.append(f"[{tag}] {t} needs a rubric of >=2 gradable points")
        if t == "predict" and not it.get("code") and "```" not in it.get("prompt", ""):
            e.append(f"[{tag}] predict needs code (field 'code' or a fenced block in the prompt)")
    return e


def validate_file(data, slug: str | None, variant: bool = False) -> tuple[list[dict], list[str]]:
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return [], ["file must be a list of items or {\"items\": [...]}"]
    cids = set(topics.concept_map(slug)) if slug and not variant else None
    sids = set(sources.by_id())
    errs: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            errs.append(f"not an object: {it!r}")
            continue
        errs += validate_item(it, cids, sids, variant)
    return items, errs


def import_items(slug: str, data, reviewed_by: str = "") -> list[str]:
    st = topics.load_state(slug)
    items, errs = validate_file(data, slug)
    if errs:
        raise LkError("quiz file invalid:\n  - " + "\n  - ".join(errs))
    bank = load_bank(slug)
    n = max((int(i["id"][1:]) for i in bank), default=0)
    ids = []
    for it in items:
        n += 1
        it = dict(it)
        it["id"] = f"q{n:04d}"
        it.setdefault("difficulty", 2)
        it["round"] = st["round"]
        it["added_in"] = st["version"]
        it["created"] = now_iso()
        if reviewed_by:
            it["reviewed"] = {"by": reviewed_by, "at": now_iso()}
        bank.append(it)
        ids.append(it["id"])
    save_bank(slug, bank)
    return ids


# ---------------------------------------------------------------- coverage

def gate_attempted_ids(slug: str) -> set[str]:
    return {a["item_id"] for a in topics.attempts(slug) if a.get("mode") == "gate"}


def coverage(slug: str, cfg: dict | None = None) -> dict:
    from .paths import config
    cfg = cfg or config()
    min_items = cfg["gate"]["min_items_per_concept"]
    st = topics.load_state(slug)
    if st["depth"] == "aware":
        min_items = 1
    used = gate_attempted_ids(slug)
    fresh = [i for i in load_bank(slug) if i["id"] not in used]
    rows, issues = [], []
    for c in topics.target_concepts(slug):
        mine = [i for i in fresh if i["concept"] == c["id"]]
        fr = [i for i in mine if i["type"] in FREE_RECALL]
        rows.append({"concept": c["id"], "core": c.get("core", False), "fresh_items": len(mine),
                     "fresh_free_recall": len(fr)})
        if len(mine) < min_items:
            issues.append(f"{c['id']}: {len(mine)} fresh item(s), need >= {min_items}")
        if c.get("core") and not fr:
            issues.append(f"{c['id']}: core concept needs >= 1 fresh explain/predict item")
    if not rows:
        issues.append("no target concepts (everything mastered?)")
    return {"concepts": rows, "issues": issues}


# ---------------------------------------------------------------- presentation

def present(item: dict, rng: random.Random) -> dict:
    """What the browser may see: never answers, rubrics or reference answers."""
    out = {k: item[k] for k in ("type", "prompt", "code", "code_lang", "difficulty") if k in item}
    t = item["type"]
    if t in ("mcq", "multi"):
        keys = list(range(len(item["options"])))
        rng.shuffle(keys)
        out["options"] = [{"key": k, "text": item["options"][k]} for k in keys]
    elif t == "order":
        steps = list(item["steps"])
        for _ in range(5):
            rng.shuffle(steps)
            if steps != item["steps"]:
                break
        out["steps"] = steps
    elif t == "cloze":
        out["blank_count"] = len(item["blanks"])
    return out


# ---------------------------------------------------------------- grading (deterministic)

def _norm(s: str, case_sensitive: bool = False) -> str:
    s = unicodedata.normalize("NFKC", s or "").strip()
    s = re.sub(r"\s+", " ", s).strip(" .,;:!?\"'`")
    return s if case_sensitive else s.lower()


def expected_text(item: dict) -> str:
    t = item["type"]
    if t == "mcq":
        return item["options"][item["answer"]]
    if t == "multi":
        return " | ".join(item["options"][i] for i in sorted(item["answer"]))
    if t == "order":
        return " -> ".join(item["steps"])
    if t == "cloze":
        return " | ".join(f"{{{{{n + 1}}}}} {b[0]}" for n, b in enumerate(item["blanks"]))
    return item.get("reference_answer", "")


def response_text(item: dict, response) -> str:
    """Human-readable rendering of a learner response (for results and the mistakes log)."""
    if not response or response.get("dont_know"):
        return "(I don't know)"
    t = item["type"]
    if t == "mcq":
        k = response.get("choice")
        return item["options"][k] if isinstance(k, int) and 0 <= k < len(item["options"]) else "(no choice)"
    if t == "multi":
        ks = [k for k in response.get("choices", []) if isinstance(k, int) and 0 <= k < len(item["options"])]
        return " | ".join(item["options"][k] for k in sorted(ks)) or "(none selected)"
    if t == "order":
        return " -> ".join(response.get("order", []))
    if t == "cloze":
        return " | ".join(f"{{{{{n + 1}}}}} {v}" for n, v in enumerate(response.get("blanks", [])))
    return response.get("text", "")


def is_empty(item: dict, response) -> bool:
    if not response or response.get("dont_know"):
        return True
    t = item["type"]
    if t == "mcq":
        return response.get("choice") is None
    if t == "multi":
        return not response.get("choices")
    if t == "order":
        return not response.get("order")
    if t == "cloze":
        return not any((v or "").strip() for v in response.get("blanks", []))
    return not (response.get("text") or "").strip()


def grade_auto(item: dict, response) -> dict:
    t = item["type"]
    res = {"expected": expected_text(item), "explanation": item.get("explanation", ""), "graded_by": "script"}
    if is_empty(item, response):
        return {**res, "score": 0.0, "correct": False, "feedback": "No answer."}
    if t == "mcq":
        ok = response.get("choice") == item["answer"]
        fb = "Correct." if ok else (item.get("misconceptions", {}).get(str(response.get("choice")), "") or "Not this one.")
        return {**res, "score": 1.0 if ok else 0.0, "correct": ok, "feedback": fb}
    if t == "multi":
        sel = set(response.get("choices", []))
        ans = set(item["answer"])
        tp, fp = len(sel & ans), len(sel - ans)
        score = max(0.0, (tp - fp) / len(ans))
        ok = sel == ans
        fb = "Correct." if ok else f"{tp}/{len(ans)} right picks, {fp} wrong pick(s)."
        return {**res, "score": round(score, 3), "correct": ok, "feedback": fb}
    if t == "order":
        got = response.get("order", [])
        if sorted(got) != sorted(item["steps"]):
            return {**res, "score": 0.0, "correct": False, "feedback": "Steps did not match the item."}
        pos = {s: i for i, s in enumerate(item["steps"])}
        seq = [pos[s] for s in got]
        inv = sum(1 for i in range(len(seq)) for j in range(i + 1, len(seq)) if seq[i] > seq[j])
        mx = len(seq) * (len(seq) - 1) / 2
        score = 1 - inv / mx
        return {**res, "score": round(score, 3), "correct": inv == 0,
                "feedback": "Correct order." if inv == 0 else f"{inv} pair(s) out of order."}
    if t == "cloze":
        cs = bool(item.get("case_sensitive"))
        vals = list(response.get("blanks", [])) + [""] * len(item["blanks"])
        hits = [_norm(vals[n], cs) in {_norm(a, cs) for a in acc} for n, acc in enumerate(item["blanks"])]
        score = sum(hits) / len(hits)
        wrong = [str(n + 1) for n, h in enumerate(hits) if not h]
        return {**res, "score": round(score, 3), "correct": all(hits),
                "feedback": "All blanks correct." if all(hits) else f"Blank(s) {', '.join(wrong)} not accepted."}
    raise LkError(f"{t} is not auto-gradable")


def needs_llm(item: dict, response, auto_result: dict | None = None) -> bool:
    if is_empty(item, response):
        return False
    if item["type"] in LLM_TYPES:
        return True
    # cloze can opt into LLM fallback for wording variants the accepted list did not anticipate
    return bool(item["type"] == "cloze" and item.get("llm_fallback") and auto_result and not auto_result["correct"])
