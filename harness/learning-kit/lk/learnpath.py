"""Learning paths: an ordered DAG of topics for a goal too broad for one topic.

A path is a *plan* (authored by the planner agent, imported through `lk path import`) plus a
*record* (written only by lk: overrides, placement, checkpoints, reviews, history). Node progress
is never stored here: it is derived from each topic's state.json, so the path can't drift from
what actually happened. Topics are shared, not owned: one topic can sit in several paths.

Plan fields (draft file):  title, goal, learner, nodes[], milestones[]
Record fields (lk only):   schema_version, id, version, created, updated, overrides, placement,
                           checkpoints, reviewed, history
"""
from __future__ import annotations

import re
from pathlib import Path

from . import paths, quiz, sources, topics
from .store import LkError, now_iso, read_json, slugify, today, write_json

SCHEMA_VERSION = 1
KINDS = ("core", "elective", "capstone")
DETAILS = ("stub", "full")
OVERRIDES = ("tested_out", "skipped")
SATISFIED = ("done", "tested_out", "skipped")
PLAN_FIELDS = ("title", "goal", "learner", "nodes", "milestones")
RECORD_FIELDS = ("schema_version", "id", "version", "created", "updated", "overrides", "placement",
                 "checkpoints", "reviewed", "history")
NODE_FIELDS = ("id", "topic", "title", "why", "kind", "depth", "prereqs", "detail", "focus", "outcomes",
               "est_hours", "source_ids")

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


# ---------------------------------------------------------------- files

def root() -> Path:
    return paths.learning_paths_dir()


def pdir(slug: str) -> Path:
    return root() / slug


def path_file(slug: str) -> Path:
    return pdir(slug) / "path.json"


def placement_file(slug: str) -> Path:
    return pdir(slug) / "placement.json"


def exists(slug: str) -> bool:
    return path_file(slug).exists()


def load(slug: str) -> dict:
    p = read_json(path_file(slug))
    if p is None:
        raise LkError(f"path '{slug}' does not exist (lk path new {slug} --title ...)")
    return p


def save(p: dict) -> None:
    p["updated"] = now_iso()
    write_json(path_file(p["id"]), p)


def _cfg() -> dict:
    return paths.config()["path"]


def create(slug: str, title: str) -> dict:
    if exists(slug):
        raise LkError(f"path '{slug}' already exists; resume it with `lk path next {slug}`")
    pdir(slug).mkdir(parents=True, exist_ok=True)
    p = {
        "schema_version": SCHEMA_VERSION, "id": slug, "title": title, "goal": "", "learner": {},
        "version": 0, "nodes": [], "milestones": [],
        "overrides": {}, "placement": None, "checkpoints": {}, "reviewed": [],
        "created": now_iso(), "updated": now_iso(),
        "history": [{"at": now_iso(), "version": 0, "change": "created", "reason": ""}],
    }
    write_json(path_file(slug), p)
    return p


# ---------------------------------------------------------------- validation

def _strs(x, n_min=1) -> bool:
    return isinstance(x, list) and len(x) >= n_min and all(isinstance(s, str) and s.strip() for s in x)


def validate_plan(plan: dict, cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """Check a plan (draft or stored). Returns (errors, warnings). Node order must be a learning
    order: every prereq points to an earlier node, which also rules out cycles."""
    cfg = cfg or _cfg()
    errs: list[str] = []
    warns: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"], []
    if not isinstance(plan.get("goal"), str) or len(plan["goal"].strip()) < 10:
        errs.append("goal: one sentence on what the learner wants to be able to do (>= 10 chars)")
    if "learner" in plan and not isinstance(plan["learner"], dict):
        errs.append("learner must be an object (level, hours_per_week, deadline, ...)")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return errs + ["nodes: a non-empty list"], warns
    if len(nodes) > cfg["max_nodes"]:
        errs.append(f"{len(nodes)} nodes > max {cfg['max_nodes']}: split the goal into milestones or a second path")
    known_sources = set(sources.by_id())
    seen: dict[str, int] = {}
    topics_seen: dict[str, str] = {}
    for n, node in enumerate(nodes):
        if not isinstance(node, dict):
            errs.append(f"nodes[{n}] must be an object")
            continue
        nid = node.get("id")
        tag = nid if isinstance(nid, str) else f"nodes[{n}]"
        if not isinstance(nid, str) or not _ID_RE.match(nid):
            errs.append(f"[{tag}] id must match {_ID_RE.pattern} (e.g. n01); keep ids stable across versions")
        elif nid in seen:
            errs.append(f"[{tag}] duplicate node id")
        unknown = sorted(set(node) - set(NODE_FIELDS))
        if unknown:
            errs.append(f"[{tag}] unknown fields {unknown}")
        t = node.get("topic")
        if not isinstance(t, str) or not t:
            errs.append(f"[{tag}] topic slug is required")
        else:
            try:
                ok = slugify(t) == t
            except LkError:
                ok = False
            if not ok:
                errs.append(f"[{tag}] topic '{t}' is not a valid slug (lowercase ascii kebab-case)")
            elif t in topics_seen:
                errs.append(f"[{tag}] topic '{t}' already used by {topics_seen[t]}")
            else:
                topics_seen[t] = tag
        for k in ("title", "why"):
            if not isinstance(node.get(k), str) or not node[k].strip():
                errs.append(f"[{tag}] {k} is required")
        if node.get("kind", "core") not in KINDS:
            errs.append(f"[{tag}] kind must be one of {KINDS}")
        if node.get("depth", "use") not in topics.DEPTHS:
            errs.append(f"[{tag}] depth must be one of {topics.DEPTHS}")
        detail = node.get("detail", "stub")
        if detail not in DETAILS:
            errs.append(f"[{tag}] detail must be one of {DETAILS}")
        if detail == "full":
            if not _strs(node.get("focus")) or len(node["focus"]) > 5:
                errs.append(f"[{tag}] a full node needs focus: 1-5 facets for the research stage")
            if not _strs(node.get("outcomes")):
                errs.append(f"[{tag}] a full node needs outcomes: what the learner can do after it")
        pre = node.get("prereqs", [])
        if not isinstance(pre, list) or not all(isinstance(x, str) for x in pre):
            errs.append(f"[{tag}] prereqs must be a list of node ids")
            pre = []
        for x in pre:
            if x == nid:
                errs.append(f"[{tag}] depends on itself")
            elif x not in seen:
                where = "a later node (reorder: prereqs come first)" if any(
                    isinstance(o, dict) and o.get("id") == x for o in nodes) else "an unknown node"
                errs.append(f"[{tag}] prereq '{x}' is {where}")
        if len(set(pre)) != len(pre):
            errs.append(f"[{tag}] duplicate prereqs")
        if node.get("kind") == "capstone" and len(pre) < 2:
            errs.append(f"[{tag}] a capstone integrates earlier nodes: it needs >= 2 prereqs")
        if "est_hours" in node and (not isinstance(node["est_hours"], (int, float)) or isinstance(node["est_hours"], bool)
                                    or node["est_hours"] <= 0):
            errs.append(f"[{tag}] est_hours must be a positive number")
        sids = node.get("source_ids", [])
        if not isinstance(sids, list):
            errs.append(f"[{tag}] source_ids must be a list")
        else:
            missing = [s for s in sids if s not in known_sources]
            if missing:
                errs.append(f"[{tag}] source_ids not in the bank: {missing}")
        if isinstance(nid, str):
            seen[nid] = n
    if not any(isinstance(nd, dict) and nd.get("detail") == "full" for nd in nodes):
        errs.append("at least the first node must be detail=full (rolling wave: detail the next few)")
    full = sum(1 for nd in nodes if isinstance(nd, dict) and nd.get("detail") == "full")
    if full > cfg["full_window"] + 2:
        warns.append(f"{full} full nodes: detail only the next ~{cfg['full_window']}; later ones change as you learn")

    ms = plan.get("milestones", [])
    if not isinstance(ms, list):
        errs.append("milestones must be a list")
        ms = []
    in_ms: dict[str, str] = {}
    mids = set()
    for m in ms:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not _ID_RE.match(m["id"]):
            errs.append(f"milestone needs an id matching {_ID_RE.pattern}: {m}")
            continue
        if m["id"] in mids:
            errs.append(f"[{m['id']}] duplicate milestone id")
        mids.add(m["id"])
        if not isinstance(m.get("title"), str) or not m["title"].strip():
            errs.append(f"[{m['id']}] milestone title is required")
        mn = m.get("nodes")
        if not _strs(mn, 2):
            errs.append(f"[{m['id']}] a milestone groups >= 2 nodes")
            continue
        for x in mn:
            if x not in seen:
                errs.append(f"[{m['id']}] unknown node '{x}'")
            elif x in in_ms:
                errs.append(f"[{m['id']}] node '{x}' is already in milestone {in_ms[x]}")
            else:
                in_ms[x] = m["id"]
    if len(nodes) > 6 and not ms:
        warns.append("more than 6 nodes and no milestones: group every 3-4 nodes so checkpoints mix them")
    return errs, warns


# ---------------------------------------------------------------- import (new version)

def _started(topic: str) -> bool:
    return topics.exists(topic)


def _diff(old: list[dict], new: list[dict]) -> dict:
    o = {n["id"]: n for n in old}
    nw = {n["id"]: n for n in new}
    changed = {}
    for nid in o.keys() & nw.keys():
        fields = sorted(k for k in set(o[nid]) | set(nw[nid]) if o[nid].get(k) != nw[nid].get(k))
        if fields:
            changed[nid] = fields
    return {"added": [x for x in nw if x not in o], "removed": [x for x in o if x not in nw], "changed": changed}


def import_plan(slug: str, draft: dict, reason: str = "", force: bool = False) -> dict:
    """Replace the plan fields with a validated draft as a new version. Record fields are kept."""
    p = load(slug)
    d = pdir(slug)
    if not (d / "intake.md").exists() or len((d / "intake.md").read_text(encoding="utf-8").strip()) < 20:
        raise LkError("intake.md is missing or empty: record the learner's goal, level and time first")
    if not (d / "plan.md").exists() or len((d / "plan.md").read_text(encoding="utf-8").strip()) < 200:
        raise LkError("plan.md is missing or too short: the planner's rationale must be saved with the path")
    if not isinstance(draft, dict):
        raise LkError("draft must be a JSON object with goal, nodes, ...")
    ignored = sorted(k for k in draft if k in RECORD_FIELDS)
    unknown = sorted(k for k in draft if k not in PLAN_FIELDS and k not in RECORD_FIELDS)
    if unknown:
        raise LkError(f"unknown top-level fields {unknown}; a draft has {PLAN_FIELDS}")
    plan = {k: draft[k] for k in PLAN_FIELDS if k in draft}
    plan.setdefault("title", p["title"])
    plan.setdefault("learner", p.get("learner") or {})
    plan.setdefault("milestones", [])
    errs, warns = validate_plan(plan)
    if errs:
        raise LkError("plan invalid:\n  - " + "\n  - ".join(errs))
    if p["version"] >= 1 and not reason.strip():
        raise LkError("re-planning needs --reason (it goes into the path history)")
    diff = _diff(p["nodes"], plan["nodes"])
    old = {n["id"]: n for n in p["nodes"]}
    new = {n["id"]: n for n in plan["nodes"]}
    if not force:
        lost = [x for x in diff["removed"] if _started(old[x]["topic"])]
        moved = [x for x in diff["changed"] if "topic" in diff["changed"][x] and _started(old[x]["topic"])]
        if lost or moved:
            raise LkError(f"nodes with a started topic would be removed {lost} or re-pointed {moved}; "
                          "the topic data is kept either way, but confirm with --force")
    for n in plan["nodes"]:
        n.setdefault("kind", "core")
        n.setdefault("depth", "use")
        n.setdefault("prereqs", [])
        n.setdefault("detail", "stub")
    p.update(plan)
    p["version"] += 1
    p["overrides"] = {k: v for k, v in p.get("overrides", {}).items() if k in new}
    p["reviewed"] = [x for x in p.get("reviewed", []) if x in new]
    p["history"].append({"at": now_iso(), "version": p["version"], "change": "plan imported" if p["version"] == 1
                         else "re-planned", "reason": reason, **diff})
    save(p)
    return {"version": p["version"], "diff": diff, "warnings": warns, "ignored_fields": ignored}


# ---------------------------------------------------------------- derived status

def node_status(p: dict, node: dict) -> dict:
    ov = p.get("overrides", {}).get(node["id"])
    t = node["topic"]
    info = {"stage": None, "lapsed": 0, "topic_exists": topics.exists(t)}
    if info["topic_exists"]:
        st = topics.status(t)
        info["stage"] = st["stage"]
        info["lapsed"] = st["concepts"]["lapsed"]
        info["topic_depth"] = st["depth"]
    if ov:
        status = ov["status"]
    elif not info["topic_exists"]:
        status = "not_started"
    elif info["stage"] == "scheduled":
        status = "done"
    else:
        status = "in_progress"
    return {"status": status, **info}


def view(slug: str) -> dict:
    p = load(slug)
    rows = []
    stat: dict[str, str] = {}
    for node in p["nodes"]:
        s = node_status(p, node)
        stat[node["id"]] = s["status"]
        ready = all(stat.get(x) in SATISFIED for x in node.get("prereqs", []))
        rows.append({**node, **s, "ready": ready, "reviewed": node["id"] in p.get("reviewed", [])})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    sat = sum(1 for r in rows if r["status"] in SATISFIED)
    return {"id": p["id"], "title": p["title"], "goal": p.get("goal", ""), "version": p["version"],
            "nodes": rows, "milestones": p.get("milestones", []), "counts": counts,
            "progress": round(sat / len(rows), 3) if rows else 0.0,
            "placement": p.get("placement"), "checkpoints": p.get("checkpoints", {}), "dir": str(pdir(slug))}


def _milestone_due(p: dict, rows: dict[str, dict]) -> dict | None:
    for m in p.get("milestones", []):
        if m["id"] in p.get("checkpoints", {}):
            continue
        ms_rows = [rows[x] for x in m["nodes"] if x in rows]
        if ms_rows and all(r["status"] in SATISFIED for r in ms_rows):
            learned = [r for r in ms_rows if r["status"] == "done"]
            if len(learned) >= 2:
                return m
    return None


def next_action(slug: str) -> dict:
    """The one next step, decided the same way every time, so any session can pick the path up.

    Order: plan -> placement -> review a just-finished node -> relearn lapsed -> milestone checkpoint
           -> resume an in-progress node -> expand a stub / learn the next ready node -> complete.
    """
    p = load(slug)
    v = view(slug)
    rows = {r["id"]: r for r in v["nodes"]}

    def act(action, msg, node=None, **kw):
        out = {"path": slug, "action": action, "message": msg, "progress": v["progress"]}
        if node:
            out["node"] = {k: node.get(k) for k in NODE_FIELDS if k in node}
            out["node"].update(status=node["status"], stage=node.get("stage"))
        out.update(kw)
        return out

    if not p["nodes"]:
        return act("plan", "No plan yet: run the intake, then the planner, then `lk path import`.")
    if p.get("placement") is None:
        return act("placement", "Offer the placement quiz (author items, `lk path placement-import`), "
                                "or skip it with `lk path placement-skip`.")
    for r in v["nodes"]:
        if r["status"] == "done" and not r["reviewed"]:
            return act("review", f"{r['id']} ({r['topic']}) is done: review the rest of the plan against what "
                                 f"was learned, then `lk path reviewed {slug} {r['id']}` or re-import with --reason.",
                       r)
    for r in v["nodes"]:
        if r["status"] == "done" and r["lapsed"]:
            return act("relearn", f"{r['lapsed']} concept(s) lapsed in {r['topic']}: relearn before new material.",
                       r, command=f"/learn {r['topic']}")
    m = _milestone_due(p, rows)
    if m:
        return act("checkpoint", f"Milestone '{m['title']}' finished: run its mixed checkpoint quiz.",
                   milestone=m, command=f"lk serve --checkpoint {slug} --milestone {m['id']}")
    prog = [r for r in v["nodes"] if r["status"] == "in_progress"]
    if prog:
        r = prog[0]
        return act("resume", f"Continue {r['topic']} (stage {r['stage']}).", r, command=f"/learn {r['topic']}",
                   also_in_progress=[x["topic"] for x in prog[1:]])
    for r in v["nodes"]:
        if r["status"] == "not_started" and r["ready"]:
            if r.get("detail") != "full":
                return act("expand", f"{r['id']} is a stub: detail it (focus, outcomes, depth) and re-import.", r)
            flags = f" --depth {r.get('depth', 'use')}"
            return act("learn", f"Start {r['id']}: {r['title']}.", r, command=f"/learn {r['topic']} --new{flags}")
    if all(r["status"] in SATISFIED for r in v["nodes"]):
        return act("complete", "Every node is done. Keep up /recall; extend the path or start a new one.")
    return act("blocked", "Nothing is ready: an earlier node must finish first.")


# ---------------------------------------------------------------- record-only operations

def mark_reviewed(slug: str, nid: str, note: str = "") -> dict:
    p = load(slug)
    if nid not in {n["id"] for n in p["nodes"]}:
        raise LkError(f"no node '{nid}' in path '{slug}'")
    if nid not in p["reviewed"]:
        p["reviewed"].append(nid)
        p["history"].append({"at": now_iso(), "version": p["version"], "change": f"reviewed after {nid}",
                             "reason": note})
        save(p)
    return {"reviewed": p["reviewed"]}


def set_override(slug: str, nid: str, status: str | None, note: str = "", force: bool = False) -> dict:
    p = load(slug)
    nodes = {n["id"]: n for n in p["nodes"]}
    if nid not in nodes:
        raise LkError(f"no node '{nid}' in path '{slug}'")
    if status is None:
        p["overrides"].pop(nid, None)
    else:
        if status not in OVERRIDES:
            raise LkError(f"status must be one of {OVERRIDES}")
        if status == "tested_out" and not force:
            rec = ((p.get("placement") or {}).get("nodes") or {}).get(nid)
            if not rec or rec.get("mean", 0) < paths.config()["gate"]["pass_score"]:
                raise LkError(f"{nid}: no placement evidence at the {paths.config()['gate']['pass_score']:.0%} bar "
                              "(run the placement quiz, or --force if the learner insists)")
        p["overrides"][nid] = {"status": status, "at": now_iso(), "note": note}
    p["history"].append({"at": now_iso(), "version": p["version"], "change": f"{nid} -> {status or 'cleared'}",
                         "reason": note})
    save(p)
    return {"node": nid, "override": p["overrides"].get(nid)}


def placement_skip(slug: str, reason: str = "") -> dict:
    p = load(slug)
    p["placement"] = {"skipped": True, "at": now_iso(), "reason": reason, "nodes": {}}
    p["history"].append({"at": now_iso(), "version": p["version"], "change": "placement skipped", "reason": reason})
    save(p)
    return p["placement"]


def placement_import(slug: str, data) -> dict:
    """Validate and store the placement bank: items whose `concept` is a node id."""
    p = load(slug)
    cfg = _cfg()
    node_ids = {n["id"] for n in p["nodes"]}
    if not node_ids:
        raise LkError("import a plan before placement items")
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise LkError("file must be a list of items or {\"items\": [...]}")
    sids = set(sources.by_id())
    errs: list[str] = []
    per: dict[str, int] = {}
    for it in items:
        errs += quiz.validate_item(it, node_ids, sids)
        if isinstance(it, dict) and isinstance(it.get("concept"), str):
            per[it["concept"]] = per.get(it["concept"], 0) + 1
    if len(items) > cfg["placement_max_items"]:
        errs.append(f"{len(items)} items > placement_max_items {cfg['placement_max_items']}: keep it short")
    over = [k for k, n in per.items() if n > cfg["placement_items_per_node"]]
    if over:
        errs.append(f"too many items for {over}: max {cfg['placement_items_per_node']} per node")
    if errs:
        raise LkError("placement items invalid:\n  - " + "\n  - ".join(errs))
    clean = []
    for n, it in enumerate(items, 1):
        it = {k: v for k, v in it.items() if k not in ("id", "round", "added_in", "created", "reviewed")}
        clean.append({**it, "id": f"p{n:03d}", "created": now_iso()})
    write_json(placement_file(slug), {"items": clean})
    return {"items": len(clean), "nodes": sorted(per)}


def load_placement(slug: str) -> list[dict]:
    return (read_json(placement_file(slug), {"items": []}) or {}).get("items", [])


def evaluate_placement(slug: str, session: dict) -> dict:
    """Per-node mean score and confidence -> a recommendation the learner decides on."""
    cfg = paths.config()
    pc = cfg["path"]
    nodes = {n["id"]: n for n in load(slug)["nodes"]}
    per: dict[str, dict] = {}
    for e in session["items"]:
        nid = e["item"]["concept"]
        r = session["results"].get(e["uid"], {})
        a = session["answers"].get(e["uid"], {})
        conf = 1 if (a.get("response") or {}).get("dont_know") else (a.get("confidence") or 1)
        row = per.setdefault(nid, {"scores": [], "conf": []})
        row["scores"].append(float(r.get("score", 0.0)))
        row["conf"].append(conf)
    out = {}
    for nid, row in per.items():
        mean = round(sum(row["scores"]) / len(row["scores"]), 3)
        conf = round(sum(row["conf"]) / len(row["conf"]), 2)
        if mean >= cfg["gate"]["pass_score"] and conf >= pc["test_out_confidence"]:
            rec = "test_out"
        elif mean >= pc["fast_track_score"]:
            rec = "fast_track"
        else:
            rec = "learn"
        out[nid] = {"mean": mean, "confidence": conf, "n": len(row["scores"]), "recommend": rec,
                    "topic": nodes.get(nid, {}).get("topic")}
    return out


def record_placement(slug: str, session: dict) -> dict:
    p = load(slug)
    res = evaluate_placement(slug, session)
    p["placement"] = {"session": session["id"], "date": today().isoformat(), "skipped": False, "nodes": res}
    recs = {k: v["recommend"] for k, v in res.items()}
    p["history"].append({"at": now_iso(), "version": p["version"], "change": "placement recorded",
                         "reason": ", ".join(f"{k}={v}" for k, v in sorted(recs.items()))})
    save(p)
    return p["placement"]


def milestone(slug: str, mid: str) -> dict:
    p = load(slug)
    for m in p.get("milestones", []):
        if m["id"] == mid:
            return m
    raise LkError(f"no milestone '{mid}' in path '{slug}'")


def checkpoint_items(slug: str, mid: str) -> list[tuple[str, dict]]:
    """A mixed sample from each learned node of the milestone, free recall first."""
    import random
    p = load(slug)
    m = milestone(slug, mid)
    per = _cfg()["checkpoint_items_per_node"]
    nodes = {n["id"]: n for n in p["nodes"]}
    rng = random.Random()
    out = []
    for nid in m["nodes"]:
        node = nodes.get(nid)
        if not node or node_status(p, node)["status"] != "done":
            continue
        bank = quiz.load_bank(node["topic"])
        rng.shuffle(bank)
        bank.sort(key=lambda i: i["type"] not in quiz.FREE_RECALL)
        out += [(node["topic"], i) for i in bank[:per]]
    if not out:
        raise LkError(f"milestone '{mid}' has no learned node with quiz items yet")
    return out


def record_checkpoint(slug: str, mid: str, session: dict) -> dict:
    p = load(slug)
    by_topic: dict[str, list[float]] = {}
    for e in session["items"]:
        by_topic.setdefault(e["topic"], []).append(float(session["results"].get(e["uid"], {}).get("score", 0.0)))
    overall = (session.get("summary") or {}).get("overall", 0.0)
    bar = paths.config()["gate"]["pass_score"]
    rec = {"session": session["id"], "date": today().isoformat(), "overall": overall, "passed": overall >= bar,
           "by_topic": {k: round(sum(v) / len(v), 3) for k, v in by_topic.items()},
           "weak_topics": sorted(k for k, v in by_topic.items() if sum(v) / len(v) < bar)}
    p["checkpoints"][mid] = rec
    p["history"].append({"at": now_iso(), "version": p["version"], "change": f"checkpoint {mid}",
                         "reason": f"{overall:.0%}"})
    save(p)
    return rec


# ---------------------------------------------------------------- lookups

def list_paths() -> list[dict]:
    r = root()
    if not r.exists():
        return []
    out = []
    for d in sorted(r.iterdir()):
        if (d / "path.json").exists():
            v = view(d.name)
            out.append({"id": v["id"], "title": v["title"], "version": v["version"], "nodes": len(v["nodes"]),
                        "progress": v["progress"], "counts": v["counts"]})
    return out


def for_topic(topic: str) -> list[dict]:
    """Every path node that points at this topic, with the context /learn needs."""
    out = []
    for row in list_paths():
        p = load(row["id"])
        nodes = {n["id"]: n for n in p["nodes"]}
        for n in p["nodes"]:
            if n["topic"] == topic:
                pre = [{"id": x, "topic": nodes[x]["topic"], "title": nodes[x]["title"]}
                       for x in n.get("prereqs", []) if x in nodes]
                after = [{"id": o["id"], "topic": o["topic"], "title": o["title"]}
                         for o in p["nodes"] if n["id"] in o.get("prereqs", [])]
                out.append({"path": p["id"], "path_title": p["title"], "goal": p.get("goal", ""),
                            "learner": p.get("learner", {}), "node": n, "prereqs": pre, "unlocks": after})
    return out
