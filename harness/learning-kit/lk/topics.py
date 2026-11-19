"""Topics: the per-topic state machine and the concept set the 80% gate is measured on."""
from __future__ import annotations

from pathlib import Path

from . import paths
from .store import LkError, now_iso, read_json, read_jsonl, today, write_json

DEPTHS = ("aware", "use", "deep")

# How a topic is taught. "doc": read one research doc, then a gate quiz over the whole topic.
# "bite": a lesson of small pieces (question -> one paragraph -> own-words answer -> small check); the
# pieces are the gate (lk/lesson.py). State files without "mode" predate bite mode and are "doc".
MODES = ("bite", "doc")

STAGES = ("prior", "research", "read", "quiz_gen", "quiz", "lesson", "reteach", "lab", "note", "scheduled")

# Legal moves per mode. Transitions out of "quiz" / "lesson" are made by the gate
# (sessions.finish -> gate.apply), so `lk topic advance` refuses them without --force.
TRANSITIONS: dict[str, set[str]] = {
    "prior": {"research"},
    "research": {"read"},
    "read": {"quiz_gen"},
    "quiz_gen": {"quiz"},
    "quiz": {"quiz_gen", "reteach", "lab", "note", "scheduled"},
    "reteach": {"quiz_gen"},
    "lab": {"note"},
    "note": {"scheduled", "reteach"},
    "scheduled": {"research", "reteach"},
}
BITE_TRANSITIONS: dict[str, set[str]] = {
    "prior": {"research"},
    "research": {"lesson"},
    "lesson": {"reteach", "lab", "note", "scheduled"},
    "reteach": {"lesson"},
    "lab": {"note"},
    "note": {"scheduled", "reteach"},
    "scheduled": {"research", "reteach"},
}
GATE_ONLY = {(g, to) for g in ("quiz", "lesson") for to in ("reteach", "lab", "note", "scheduled")}
# Stages that belong to one mode's flow: switching mode there would strand the topic.
MODE_STAGES = {"doc": {"read", "quiz_gen", "quiz"}, "bite": {"lesson"}}

CONCEPT_STATUSES = ("new", "learning", "mastered", "lapsed")
TARGET_STATUSES = ("new", "learning", "lapsed")

NEXT_HINT = {
    "prior": "Capture what you already know (prior.md), then advance to research.",
    "research": "Collect sources into the bank, aggregate research.md + concepts, then advance to read.",
    "read": "Read research.md (ask questions; they are logged to qa.md), then advance to quiz_gen.",
    "quiz_gen": "Author quiz items for the target concepts, review them, import, then advance to quiz.",
    "quiz": "Take the gate quiz in the browser: `lk serve --topic <slug>`.",
    "reteach": "Read the targeted reteach for missed concepts, then advance to quiz_gen.",
    "lab": "Run the experiment in lab/ and record lab/RESULTS.md, then advance to note.",
    "note": "Write or dictate note.md in your own words, get it reviewed (/note).",
    "scheduled": "In spaced review (/recall). Use --extend to grow the topic.",
}
BITE_HINT = {
    "research": "Collect sources into the bank, author the bite-sized lesson (concepts + pieces), review it, "
                "`lk lesson import`, then advance to lesson.",
    "lesson": "Take the lesson in the browser, one piece at a time: `lk serve --topic <slug>`.",
    "reteach": "Author fresh pieces for the missed concepts (`lk lesson import`), then advance to lesson.",
}


def mode_of(st: dict) -> str:
    return st.get("mode") or "doc"


def transitions_for(st: dict) -> dict[str, set[str]]:
    return BITE_TRANSITIONS if mode_of(st) == "bite" else TRANSITIONS


def hint_for(st: dict) -> str:
    stage = st["stage"]
    return (BITE_HINT.get(stage) if mode_of(st) == "bite" else None) or NEXT_HINT[stage]


def default_mode() -> str:
    m = (paths.config().get("learn") or {}).get("default_mode", "bite")
    if m not in MODES:
        raise LkError(f"config learn.default_mode must be one of {MODES}, not {m!r}")
    return m


# ---------------------------------------------------------------- files

def tdir(slug: str) -> Path:
    return paths.topic_dir(slug)


def state_path(slug: str) -> Path:
    return tdir(slug) / "state.json"


def concepts_path(slug: str) -> Path:
    return tdir(slug) / "concepts.json"


def exists(slug: str) -> bool:
    return state_path(slug).exists()


def load_state(slug: str) -> dict:
    st = read_json(state_path(slug))
    if st is None:
        raise LkError(f"topic '{slug}' does not exist (lk topic new {slug} ...)")
    return st


def save_state(slug: str, st: dict) -> None:
    st["updated"] = now_iso()
    write_json(state_path(slug), st)


def load_concepts(slug: str) -> list[dict]:
    return (read_json(concepts_path(slug), {"concepts": []}) or {}).get("concepts", [])


def save_concepts(slug: str, concepts: list[dict]) -> None:
    write_json(concepts_path(slug), {"concepts": concepts})


def concept_map(slug: str) -> dict[str, dict]:
    return {c["id"]: c for c in load_concepts(slug)}


def target_concepts(slug: str) -> list[dict]:
    return [c for c in load_concepts(slug) if c.get("status", "new") in TARGET_STATUSES]


# ---------------------------------------------------------------- lifecycle

def create(slug: str, title: str, depth: str = "use", fresh: bool = False, mode: str | None = None) -> dict:
    if depth not in DEPTHS:
        raise LkError(f"depth must be one of {DEPTHS}")
    mode = mode or default_mode()
    if mode not in MODES:
        raise LkError(f"mode must be one of {MODES}")
    if exists(slug):
        raise LkError(f"topic '{slug}' already exists; resume it, or use extend to grow it")
    d = tdir(slug)
    for sub in ("", "lab", "reteach"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    st = {
        "slug": slug,
        "title": title,
        "depth": depth,
        "mode": mode,
        "fresh": fresh,
        "stage": "prior",
        "version": 1,
        "round": 1,
        "relearn": False,
        "created": now_iso(),
        "updated": now_iso(),
        "history": [{"stage": "prior", "at": now_iso(), "note": "created"}],
        "rounds_log": [],
    }
    write_json(state_path(slug), st)
    save_concepts(slug, [])
    write_json(d / "quiz.json", {"items": []})
    return st


def _file_ok(p: Path, min_chars: int = 20) -> bool:
    return p.exists() and len(p.read_text(encoding="utf-8").strip()) >= min_chars


def _guard(slug: str, st: dict, to: str) -> None:
    d = tdir(slug)
    if to == "research" and st["stage"] == "prior" and not _file_ok(d / "prior.md"):
        raise LkError("prior.md is missing or empty: capture what you know first")
    if to == "read":
        if not _file_ok(d / "research.md", 200):
            raise LkError("research.md is missing or too short")
        if not target_concepts(slug):
            raise LkError("no target concepts for this version: `lk concepts merge` first")
        from .reader import check  # local import: reader imports topics
        bad = check(slug)["errors"]
        if bad:
            raise LkError("research.md has broken visual blocks (`lk read <slug> --check`):\n  - "
                          + "\n  - ".join(bad))
    if to == "quiz":
        from .quiz import coverage  # local import: quiz imports topics
        cov = coverage(slug)
        if cov["issues"]:
            raise LkError("quiz coverage is not ready:\n  - " + "\n  - ".join(cov["issues"]))
    if to == "lesson":
        if not target_concepts(slug):
            raise LkError("no target concepts for this version: `lk concepts merge` first")
        from .lesson import coverage as lesson_coverage  # local import: lesson imports topics
        cov = lesson_coverage(slug)
        if cov["issues"]:
            raise LkError("the lesson is not ready (`lk lesson coverage <slug>`):\n  - " + "\n  - ".join(cov["issues"]))
    if to == "quiz_gen" and st["stage"] == "reteach":
        if not _file_ok(d / "reteach" / f"round-{st['round']}.md"):
            raise LkError(f"reteach/round-{st['round']}.md is missing")
    if to == "note" and st["stage"] == "lab" and not _file_ok(d / "lab" / "RESULTS.md"):
        raise LkError("lab/RESULTS.md is missing: record prediction vs actual first")
    if to == "scheduled" and st["stage"] == "note":
        rv = read_json(d / "note-review.json", {}) or {}
        if not rv.get("passed"):
            raise LkError("note has not passed review yet")
    if to == "reteach" and st["stage"] == "scheduled":
        if not [c for c in load_concepts(slug) if c.get("status") in ("lapsed", "learning")]:
            raise LkError("nothing to relearn: no lapsed concepts")


def advance(slug: str, to: str, force: bool = False, note: str = "", _from_gate: bool = False) -> dict:
    st = load_state(slug)
    cur = st["stage"]
    if to not in STAGES:
        raise LkError(f"unknown stage '{to}'")
    allowed = transitions_for(st).get(cur, set())
    if to not in allowed and not force:
        raise LkError(f"cannot go {cur} -> {to} in {mode_of(st)} mode; allowed: {sorted(allowed)}")
    if (cur, to) in GATE_ONLY and not (_from_gate or force):
        raise LkError(f"{cur} -> {to} is decided by the quiz gate, not by hand (use --force to override)")
    if not force:
        _guard(slug, st, to)
    if to == "reteach":
        st["round"] += 1
        if cur == "scheduled":
            st["relearn"] = True
    if to == "scheduled":
        st["relearn"] = False
    st["stage"] = to
    st["history"].append({"stage": to, "at": now_iso(), "note": note or ("forced" if force else "")})
    save_state(slug, st)
    if to == "scheduled":
        from .srs import enroll
        enroll(slug)
    return st


def extend(slug: str) -> dict:
    """Grow a topic: new version, back to research. Old mastered concepts stay in SRS."""
    st = load_state(slug)
    if st["stage"] != "scheduled":
        raise LkError(f"extend only from 'scheduled' (topic is at '{st['stage']}')")
    st["version"] += 1
    st["round"] += 1
    st["stage"] = "research"
    st["history"].append({"stage": "research", "at": now_iso(), "note": f"extend -> v{st['version']}"})
    save_state(slug, st)
    return st


def set_mode(slug: str, mode: str) -> dict:
    """Switch how the topic is taught from its next round on. Refused mid-flow (read/quiz_gen/quiz for doc,
    lesson for bite): finish or reteach first. Concepts, sources, the quiz bank and SRS cards are shared."""
    if mode not in MODES:
        raise LkError(f"mode must be one of {MODES}")
    st = load_state(slug)
    cur = mode_of(st)
    if cur == mode:
        return st
    if st["stage"] in MODE_STAGES[cur]:
        raise LkError(f"topic is at '{st['stage']}', part of the {cur} flow: finish that round first "
                      "(switching is allowed at prior, research, reteach, lab, note and scheduled)")
    st["mode"] = mode
    st["history"].append({"stage": st["stage"], "at": now_iso(), "note": f"mode {cur} -> {mode}"})
    save_state(slug, st)
    return st


def rounds_today(st: dict) -> int:
    t = today().isoformat()
    return sum(1 for r in st.get("rounds_log", []) if r.get("date") == t)


# ---------------------------------------------------------------- concepts

def merge_concepts(slug: str, incoming: list[dict]) -> dict:
    """Add new concepts (status new, added_in=current version); update text of existing ones.

    Status of an existing concept is never changed here: mastery is earned through the gate.
    """
    st = load_state(slug)
    cur = load_concepts(slug)
    by_id = {c["id"]: c for c in cur}
    added, updated = [], []
    for raw in incoming:
        cid = raw.get("id")
        if not cid or not isinstance(cid, str):
            raise LkError(f"concept without id: {raw}")
        if not raw.get("name"):
            raise LkError(f"concept '{cid}' needs a name")
        if cid in by_id:
            c = by_id[cid]
            for k in ("name", "summary", "core", "source_ids", "related"):
                if k in raw:
                    c[k] = raw[k]
            updated.append(cid)
        else:
            c = {
                "id": cid,
                "name": raw["name"],
                "summary": raw.get("summary", ""),
                "core": bool(raw.get("core", False)),
                "source_ids": raw.get("source_ids", []),
                "related": raw.get("related", []),
                "added_in": st["version"],
                "status": "new",
                "mastered_at": None,
            }
            cur.append(c)
            by_id[cid] = c
            added.append(cid)
    save_concepts(slug, cur)
    return {"added": added, "updated": updated}


def set_concept_status(slug: str, cid: str, status: str) -> None:
    if status not in CONCEPT_STATUSES:
        raise LkError(f"status must be one of {CONCEPT_STATUSES}")
    cs = load_concepts(slug)
    for c in cs:
        if c["id"] == cid:
            c["status"] = status
            c["mastered_at"] = today().isoformat() if status == "mastered" else c.get("mastered_at")
            save_concepts(slug, cs)
            return
    raise LkError(f"concept '{cid}' not in topic '{slug}'")


# ---------------------------------------------------------------- reporting

def list_topics() -> list[dict]:
    root = paths.topics_dir()
    if not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if (p / "state.json").exists():
            out.append(status(p.name))
    return out


def status(slug: str) -> dict:
    st = load_state(slug)
    cs = load_concepts(slug)
    counts = {s: 0 for s in CONCEPT_STATUSES}
    for c in cs:
        counts[c.get("status", "new")] = counts.get(c.get("status", "new"), 0) + 1
    hint = hint_for(st).replace("<slug>", slug)
    if st["stage"] == "scheduled" and counts["lapsed"]:
        hint = f"{counts['lapsed']} concept(s) lapsed in recall: relearn with /learn {slug}."
    last = st["rounds_log"][-1] if st["rounds_log"] else None
    return {
        "slug": slug,
        "title": st["title"],
        "depth": st["depth"],
        "mode": mode_of(st),
        "stage": st["stage"],
        "version": st["version"],
        "round": st["round"],
        "relearn": st.get("relearn", False),
        "rounds_today": rounds_today(st),
        "concepts": counts,
        "last_gate": last,
        "next": hint,
        "dir": str(tdir(slug)),
    }


def attempts(slug: str) -> list[dict]:
    return read_jsonl(tdir(slug) / "attempts.jsonl")
