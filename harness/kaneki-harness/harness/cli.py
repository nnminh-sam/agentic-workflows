"""`sdlc` driver. Every command prints one JSON object and exits 0, or prints {"error": ...} and exits 2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

from . import checks as C
from . import ship as SHIP
from . import gitid
from . import graph as G
from . import onboard as OB
from . import rules as R
from . import usage as U
from .errors import HarnessError
from .profile import (LOCALE_KEYS, _abs, artifact_languages, build_context, context_window, language, language_source,
                      load_profile, parse_lang_settings, resolve_adapters, tier_model, worktree_path, worktree_root,
                      worktree_root_errors)
from .state import (Ticket, check_receipts, issue_receipts, missing_prerequisites, new_node_record,
                    next_pending, now, repo_subject, settled, sha256_file, sha256_text)

HOME = Path(__file__).resolve().parent.parent


# --- helpers -----------------------------------------------------------------------------------------

def out(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=None))


def profile_path(args: argparse.Namespace) -> Path:
    raw = args.profile or os.environ.get("SDLC_PROFILE") or "sdlc.profile.json"
    return Path(raw).resolve()


def ticket_root(profile: dict[str, Any], pdir: Path) -> Path:
    root = Path(profile["paths"]["ticketRoot"])
    return root if root.is_absolute() else (pdir / root).resolve()


def portable(path: Path, base: Path) -> str:
    """POSIX path relative to `base`, so state written on Windows resolves on macOS/Linux and back.
    Falls back to an absolute POSIX path when no relative path exists (another Windows drive)."""
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path.as_posix()


class Session:
    """Everything one command needs about one ticket."""

    def __init__(self, args: argparse.Namespace):
        # The profile that located the ticket is the profile in force on this machine. Absolute paths stored in
        # state are only a cache: repository paths come from the profile, worktrees from `worktreeRel`.
        self.ppath = profile_path(args)
        self.pdir = self.ppath.parent
        self.profile = load_profile(self.ppath)
        self.ticket = Ticket(ticket_root(self.profile, self.pdir), args.ticket)
        # harness folders inside a repo never count as shipped content (they change on every command)
        self.excludes = [str(ticket_root(self.profile, self.pdir)), str(self.pdir / ".sdlc")]
        self.state = self.ticket.load()
        self.ctx = self.state["context"]
        by_id = {r["id"]: r for r in self.profile["repositories"]}
        for repo in self.ctx["repositories"]:
            if repo["id"] in by_id:
                repo["path"] = _abs(by_id[repo["id"]]["path"], self.profile)
            if repo.get("worktreeRel"):
                wt = Path(repo["worktreeRel"])
                repo["worktree"] = str(wt if wt.is_absolute() else (self.pdir / wt).resolve())
        self.graph = G.load_graph(HOME, self.profile, self.pdir)
        self.order = G.trace(self.graph, self.ctx)
        for node in self.order:  # nodes that enter the trace later (review round) get a record
            self.state["nodes"].setdefault(node, new_node_record())

    def spec(self, node: str) -> dict[str, Any]:
        if node not in self.order:
            raise HarnessError(f"{node} is not in this ticket's trace: {self.order}")
        spec = self.graph["nodes"][node]
        if G.is_inline(self.graph, self.ctx, node):  # this lane: the controller does it, no subagent, no reviewer
            spec = {**{k: v for k, v in spec.items() if k != "reviewer"}, "units": [], "pattern": "controller-inline"}
        return spec

    def artifacts(self, node: str) -> list[str]:
        names = []
        for name in self.spec(node).get("artifacts", []):
            if name == "@delivery.drafts":
                names += [f"drafts/{d}.md" for d in self.profile["delivery"].get("drafts", [])]
            else:
                names.append(name)
        return names

    def model(self, tier: str) -> str:
        return tier_model(self.profile, tier)

    def units(self, node: str) -> list[dict[str, Any]]:
        """Planned units of a node, the reviewer included, as graph specs."""
        spec = self.spec(node)
        units = [dict(u) for u in spec.get("units", [])]
        if spec.get("reviewer"):
            units.append({"id": "reviewer", "role": "reviewer", "tier": spec["reviewer"]["tier"]})
        return units

    def unit(self, node: str, unit_id: str) -> dict[str, Any]:
        found = next((u for u in self.units(node) if u["id"] == unit_id), None)
        if found is None:
            raise HarnessError(f"{node} has no unit {unit_id}; planned: {[u['id'] for u in self.units(node)]}")
        return found

    def failed_rounds(self, review_node: str) -> int:
        """Generations of a review node in which any unit recorded FAIL / NEEDS CHANGES."""
        rec = self.state["nodes"].get(review_node) or {"units": []}
        return len({u["generation"] for u in rec["units"] if u.get("verdict") in ("FAIL", "NEEDS CHANGES")})

    def tier(self, node: str, unit_id: str) -> tuple[str, str | None]:
        """Effective tier of a unit and, when it moved up, why. A unit with `escalate` runs on the stronger tier once
        its review node has failed it `afterFails` rounds (light coding that keeps failing review moves to routine)."""
        u = self.unit(node, unit_id)
        esc = u.get("escalate")
        if not esc:
            return u["tier"], None
        review = esc.get("reviewNode") or next((n for n in self.order if self.graph["nodes"][n].get("loopBackTo") == node), None)
        after = esc.get("afterFails") or self.profile.get("gates", {}).get("escalateAfter", 2)
        fails = self.failed_rounds(review) if review else 0
        if fails >= after:
            return esc["tier"], f"{review} failed {fails} round(s) (escalates after {after})"
        return u["tier"], None

    def stage_problem(self, node: str, unit_id: str) -> str | None:
        """A staged unit (`after`) runs only once its predecessor recorded PASS at the current generation."""
        before = self.unit(node, unit_id).get("after")
        if not before:
            return None
        rec = self.state["nodes"][node]
        prev = [u for u in rec["units"] if u["unit"] == before and u["generation"] == rec["generation"]]
        if not prev:
            return f"{node}/{unit_id} is staged after {before}, which has no record at generation {rec['generation']}"
        if prev[-1].get("verdict") is None and prev[-1]["status"] == "ok":
            return f"{node}/{unit_id} is staged after {before}, which was recorded without --verdict PASS|FAIL; re-record it"
        if prev[-1].get("verdict") not in ("PASS", "APPROVED") or prev[-1]["status"] != "ok":
            return (f"{node}/{unit_id} is staged after {before}, whose last verdict is {prev[-1].get('verdict')}: "
                    f"close {node} with needs-changes instead (the {unit_id} tier is not spent on a failed round)")
        return None

    def save(self) -> None:
        self.ticket.save(self.state)


# --- commands ----------------------------------------------------------------------------------------

def validate_profile_file(path: Path) -> dict[str, Any]:
    profile = load_profile(path)
    wt_errors = worktree_root_errors(profile, path.parent)
    if wt_errors:
        raise HarnessError("; ".join(wt_errors))
    graph = G.load_graph(HOME, profile, path.parent)
    ctx = build_context(profile, "VALIDATE", "feature", None, [f["id"] for f in profile.get("families", [])], None)
    adapters = resolve_adapters(HOME, path.parent, profile, ctx)
    rules = R.load_rulepacks(HOME, path.parent, profile, ctx)
    warnings = []
    for key in ("testcase", "evidence"):
        tpl = profile.get(key, {}).get("template")
        if tpl and not ((path.parent / tpl).is_file() or (HOME / tpl).is_file()):
            warnings.append(f"{key}.template not found: {tpl}")
    for repo_id, stack in adapters["stacks"].items():
        if stack.get("draft"):
            warnings.append(f"{repo_id}: stack adapter {stack['_path']} is a DRAFT; review its commands and worktree rule")
    for repo in ctx["repositories"]:
        if not Path(repo["path"]).is_dir():
            warnings.append(f"{repo['id']}: path does not exist: {repo['path']}")
    return {"ok": True, "profile": str(path), "graph": graph["graphId"], "nodes": len(graph["order"]),
            "adapters": {"tracker": adapters["tracker"]["id"], "delivery": adapters["delivery"]["id"],
                         "stacks": {k: v["id"] for k, v in adapters["stacks"].items()},
                         "surfaces": {k: v["id"] for k, v in adapters["surfaces"].items()}},
            "worktreeRoot": str(worktree_root(profile, path.parent)), "rules": len(rules), "warnings": warnings}


def cmd_validate(args: argparse.Namespace) -> None:
    out(validate_profile_file(profile_path(args)))


# --- onboarding (kaneki-onboarding skill) -------------------------------------------------------------

def cmd_onboard_scan(args: argparse.Namespace) -> None:
    cand = OB.scan(Path(args.root), args.repo or [])
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(cand, indent=2, ensure_ascii=False), encoding="utf-8")
    out({"candidates": str(dest), "repos": {k: {"stack": v["stack"], "commands": len(v["commands"])} for k, v in cand["repos"].items()},
         "lowConfidence": sorted(k for k, e in cand["evidence"].items() if e["confidence"] == "low"),
         "questions": [q["id"] for q in cand["questions"]], "gaps": cand["gaps"]})


def cmd_onboard_verify(args: argparse.Namespace) -> None:
    path = Path(args.candidates)
    cand = json.loads(path.read_text(encoding="utf-8"))
    cand["verification"] = OB.verify(cand, args.allow_tests, args.allow_build, args.timeout)
    path.write_text(json.dumps(cand, indent=2, ensure_ascii=False), encoding="utf-8")
    ran = [v for v in cand["verification"] if v.get("ran")]
    out({"ran": len(ran), "ok": sum(1 for v in ran if v.get("ok")),
         "failed": [{"repo": v["repo"], "cmd": v["cmd"], "exit": v.get("exit"), "tail": v.get("tail")} for v in ran if not v.get("ok")],
         "skipped": [{"repo": v["repo"], "cmd": v["cmd"], "reason": v["reason"]} for v in cand["verification"] if not v.get("ran")]})


def cmd_onboard_write(args: argparse.Namespace) -> None:
    cand = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    answers = json.loads(Path(args.answers).read_text(encoding="utf-8")) if args.answers else {}
    target = Path(args.profile or Path(cand["root"]) / "sdlc.profile.json").resolve()
    if target.exists() and not (args.refresh or args.dry_run):
        raise HarnessError(f"{target} exists; use --refresh (keeps every value you set) or --dry-run to preview")
    res = OB.write(cand, answers, target, HOME, dry_run=args.dry_run)
    profile = res.pop("_profile")
    try:
        if args.dry_run:
            from .profile import validate_profile
            validate_profile(profile)
            res["validation"] = {"ok": True, "note": "structure only (dry run: adapters not written)"}
        else:
            res["validation"] = validate_profile_file(target)
    except HarnessError as exc:
        res["validation"] = {"ok": False, "error": str(exc)}
    out(res)


def cmd_init(args: argparse.Namespace) -> None:
    path = profile_path(args)
    profile = load_profile(path)
    ticket = Ticket(ticket_root(profile, path.parent), args.ticket)
    if ticket.exists() and not args.force:
        raise HarnessError(f"ticket already initialised at {ticket.dir}; use status (never re-init to recover)")
    ctx = build_context(profile, args.ticket, args.kind, args.repo, args.family, args.surface)
    ctx["lane"] = args.lane
    if args.lane != G.DEFAULT_LANE and ctx["families"]:
        raise HarnessError(f"lane {args.lane} has no extension slots; families {ctx['families']} need --lane full")
    override = parse_lang_settings(args.lang)
    if override:
        ctx["locale"] = override
    graph = G.load_graph(HOME, profile, path.parent)
    resolve_adapters(HOME, path.parent, profile, ctx)  # fail-closed now, not at node 6 (G7)
    G.check_lane(graph, args.lane)
    wt_errors = worktree_root_errors(profile, path.parent) if G.graph_uses_worktree(graph, ctx) else []
    if wt_errors:
        raise HarnessError("G7: " + "; ".join(wt_errors))
    order = G.trace(graph, ctx)
    warnings = []
    for repo in ctx["repositories"]:
        ident = gitid.identity(profile, repo["id"])
        head = gitid.run(repo["path"], ["rev-parse", "HEAD"], ident)
        if head.returncode != 0:
            raise HarnessError(f"G7: {repo['id']}: not a git checkout with a commit: {repo['path']}")
        repo["startHead"] = head.stdout.strip()
        if not G.graph_uses_worktree(graph, ctx):  # in-place lanes: the checkout itself is the ticket's workspace
            repo["worktree"] = repo["path"]
            repo["worktreeRel"] = portable(Path(repo["path"]), path.parent)
            dirty = gitid.run(repo["path"], ["status", "--porcelain"], ident).stdout.strip()
            if dirty:
                warnings.append(f"{repo['id']}: the checkout has uncommitted changes; they will show up in the ship "
                                "file list (stash them first, or drop them from the list when you approve)")
            branch = gitid.run(repo["path"], ["rev-parse", "--abbrev-ref", "HEAD"], ident).stdout.strip()
            if branch == repo["baseBranch"]:
                warnings.append(f"{repo['id']}: on the base branch {branch}; the change node creates "
                                f"{profile['paths'].get('branchPrefix', 'ticket/')}{args.ticket} before editing")
    state = {"schemaVersion": 1, "graphId": graph["graphId"], "profilePath": portable(path, ticket.dir), "createdAt": now(),
             "context": ctx, "nodes": {n: new_node_record() for n in order}, "receipts": {}}
    ticket.save(state)
    ticket.log({"event": "init", "kind": args.kind, "lane": args.lane, "families": ctx["families"], "surfaces": ctx["surfaces"],
                **({"locale": override} if override else {})})
    out({"ok": True, "ticketDir": str(ticket.dir), "lane": args.lane, "trace": order, "nextPending": order[0],
         **({"warnings": warnings} if warnings else {})})


def cmd_lane(args: argparse.Namespace) -> None:
    """Show the lane, or move a ticket to another lane (normally lite -> full when the change turns out bigger).
    Nodes already closed keep their records; nodes of the new lane that never ran become pending."""
    s = Session(args)
    if not args.set:
        out({"ticket": s.ctx["ticket"], "lane": G.lane_of(s.ctx), "lanes": {k: v["description"] for k, v in s.graph["lanes"].items()}})
        return
    if not args.reason:
        raise HarnessError("--reason is required: say why the lane changes")
    G.check_lane(s.graph, args.set)
    old = G.lane_of(s.ctx)
    if old == args.set:
        raise HarnessError(f"ticket is already on lane {old}")
    if args.set != G.DEFAULT_LANE and s.ctx["families"]:
        raise HarnessError(f"lane {args.set} has no extension slots; this ticket uses families {s.ctx['families']}")
    s.ctx["lane"] = args.set
    s.order = G.trace(s.graph, s.ctx)
    for node in s.order:
        s.state["nodes"].setdefault(node, new_node_record())
    s.save()
    s.ticket.log({"event": "lane", "lane": args.set, "from": old, "reason": args.reason})
    out({"ok": True, "lane": args.set, "from": old, "trace": s.order, "nextPending": next_pending(s.state, s.order),
         "note": "repositories stay bound to their current checkout; prepare keeps it (sdlc worktree --path <repo>)"
                 if old != G.DEFAULT_LANE else None})


def cmd_status(args: argparse.Namespace) -> None:
    if args.accept_current_state:
        if not args.reason:
            raise HarnessError("--accept-current-state needs --reason '<why state.json was changed outside the driver>'")
        prof = load_profile(profile_path(args))
        t = Ticket(ticket_root(prof, profile_path(args).parent), args.ticket)
        t.accept_current(args.reason)
    s = Session(args)
    rows = []
    tokens_total = 0
    for node in s.order:
        rec = s.state["nodes"][node]
        toks = sum(u.get("tokens") or 0 for u in rec["units"] if u["generation"] == rec["generation"])
        tokens_total += toks
        rows.append({"node": node, "layer": s.spec(node)["layer"], "status": rec["status"],
                     "generation": rec["generation"], "verdict": rec["verdict"], "tokens": toks,
                     "models": sorted({u["model"] for u in rec["units"] if u["generation"] == rec["generation"]})})
    nxt = next_pending(s.state, s.order)
    res = {"ticket": s.ctx["ticket"], "kind": s.ctx["kind"], "lane": G.lane_of(s.ctx), "reviewRound": s.ctx["reviewRound"],
           "settled": sum(1 for n in s.order if settled(s.state, n)), "traceLength": len(s.order),
           "nextPending": nxt, "tokensTotal": tokens_total}
    if nxt:
        res["nextMissing"] = missing_prerequisites(s.graph, s.order, s.state, nxt)
    if not args.compact:
        res["nodes"] = rows
    out(res)


AGENT_TYPES = {"light": "kaneki-light", "routine": "kaneki-worker", "deep": "kaneki-deep"}


def agent_type(tier: str) -> str:
    return AGENT_TYPES[tier]


def cmd_plan(args: argparse.Namespace) -> None:
    s = Session(args)
    node = args.node or next_pending(s.state, s.order)
    if node is None:
        out({"done": True})
        return
    spec = s.spec(node)
    adapters = resolve_adapters(HOME, s.pdir, s.profile, s.ctx)
    rules = R.select(R.load_rulepacks(HOME, s.pdir, s.profile, s.ctx), s.ctx, spec.get("rulePhases", []))
    units = []
    for u in s.units(node):
        tier, why = s.tier(node, u["id"])
        row = {k: v for k, v in u.items() if k != "escalate"}
        row.update({"tier": tier, "model": s.model(tier), "agentType": agent_type(tier)})
        if why:
            row["escalated"] = {"from": u["tier"], "why": why}
        if u.get("after"):
            row["ready"] = s.stage_problem(node, u["id"]) is None
        units.append(row)
    rec = s.state["nodes"][node]
    plan = {
        "node": node, "lane": G.lane_of(s.ctx), "executor": "controller" if not units else "subagents",
        "layer": spec["layer"], "purpose": spec["purpose"], "generation": rec["generation"],
        "status": rec["status"], "missing": missing_prerequisites(s.graph, s.order, s.state, node),
        "units": units, "pattern": spec.get("pattern", "worker-only" if not spec.get("reviewer") else "worker+reviewer"),
        "boundary": spec["boundary"] if not (spec["boundary"] == "user" and node in ("fix-plan", "feature-plan")
                                              and s.profile.get("gates", {}).get("autoSkipPlanApproval")) else "controller",
        "gates": spec.get("gates", []), "artifacts": s.artifacts(node), "card": spec["card"],
        "inputs": [{k: v for k, v in i.items() if k != "sha256"} for i in node_inputs(s, node)],
        "checks": spec.get("checks", {}),
        "ruleCount": len(rules), "ruleIds": [r["id"] for r in rules] if not args.card else None,
        "adapters": _adapters_for(node, spec, adapters),
        "loopCap": s.profile.get("gates", {}).get("loopCap", 2), "attempts": rec["attempts"],
        "closeWith": f"sdlc gate --ticket {s.ctx['ticket']} --node {node} --status approved",
    }
    if args.card:
        plan = {k: v for k, v in plan.items() if v is not None}
    out(plan)


def _adapters_for(node: str, spec: dict[str, Any], adapters: dict[str, Any]) -> dict[str, Any]:
    """Only the adapter sections a node needs, as instruction file paths (the unit reads them)."""
    picked: dict[str, Any] = {}
    if node in ("prepare", "scope"):
        picked["tracker"] = adapters["tracker"]["_path"]
        picked["stacks"] = {k: v["_path"] for k, v in adapters["stacks"].items()}
    if spec.get("mutability") == "source" or node in ("code-review", "final-review", "investigate", "check"):
        picked["stacks"] = {k: v["_path"] for k, v in adapters["stacks"].items()}
    if node in ("execute-and-evidence", "testcase", "testcase-review") or spec.get("family"):
        picked["surfaces"] = {k: v.get("_path", v.get("notApplicable")) for k, v in adapters["surfaces"].items()}
    if spec["layer"] == "after-pr" or node in ("delivery-drafts", "deliverable-ready"):
        picked["delivery"] = adapters["delivery"]["_path"]
        picked["tracker"] = adapters["tracker"]["_path"]
    return picked


def cmd_packet(args: argparse.Namespace) -> None:
    s = Session(args)
    spec = s.spec(args.node)
    rec = s.state["nodes"][args.node]
    unit = s.unit(args.node, args.unit)
    staged = s.stage_problem(args.node, args.unit)
    if staged:
        raise HarnessError(staged)
    tier, escalated = s.tier(args.node, args.unit)
    facts = json.loads(Path(args.facts).read_text(encoding="utf-8")) if args.facts else {}
    from . import schema
    schema.validate(facts, "facts.schema.json", f"facts {args.facts}")
    rules = R.select(R.load_rulepacks(HOME, s.pdir, s.profile, s.ctx), s.ctx, spec.get("rulePhases", []))
    pdir = s.ticket.dir / "packets"
    pdir.mkdir(parents=True, exist_ok=True)
    round_no = 1 + sum(1 for u in rec["units"] if u["unit"] == args.unit and u["generation"] == rec["generation"])
    dispatch = f"{args.node}.g{rec['generation']}.{args.unit}.r{round_no}"
    rules_path = pdir / f"{dispatch}.rules.json"
    rules_path.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    result_path = s.ticket.dir / "results" / f"{dispatch}.md"
    adapters = _adapters_for(args.node, spec, resolve_adapters(HOME, s.pdir, s.profile, s.ctx))
    inputs = packet_inputs(s, args.node, pdir / f"{dispatch}.inputs")
    window = context_window(s.profile, tier)
    logs_dir = s.ticket.dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    template = (HOME / "skills" / "kaneki-flow" / "scripts" / "packet-template.md").read_text(encoding="utf-8")
    values = {
        "ticket": s.ctx["ticket"], "node": args.node, "generation": str(rec["generation"]), "unit": args.unit,
        "role": unit["role"], "round": str(round_no), "dispatchId": dispatch, "purpose": spec["purpose"],
        "card": str(HOME / "skills" / "kaneki-flow" / spec["card"]),
        "artifacts": "\n".join(f"- `{s.ticket.dir / a}`" for a in s.artifacts(args.node)) or "- none (findings only)",
        "rulesPath": str(rules_path), "ruleCount": str(len(rules)), "resultPath": str(result_path),
        "adapters": json.dumps(adapters, indent=2, ensure_ascii=False),
        "inputs": NL.join(f"- `{i['file']}` ({i['token']}, from `{i['from']}`, sha256 `{i['sha256'][:12]}`)"
                          + (f" — live round only; full history in `{i['original']}`" if i.get("original") else "")
                          for i in inputs) or "- none (first node, or only the ticket and the repositories)",
        "contextWindow": f"{window:,}", "logsDir": str(logs_dir),
        "checks": _checks_block(s.spec(args.node)),
        "repositories": _repositories_block(s),
        "weakestClaim": facts.get("weakestClaim", "(controller: name it)"),
        "observed": _bullets(facts.get("observed")), "doubts": _bullets(facts.get("doubts")),
        "settled": _bullets(facts.get("settled")), "authorizedPaths": _bullets(facts.get("authorizedPaths")),
        "findingsToFix": _bullets(facts.get("findingsToFix")),
        "languages": _languages_block(s, args.node),
        "git": _git_block(s),
    }
    text = template
    for key, val in values.items():
        text = text.replace("{{" + key + "}}", val)
    path = pdir / f"{dispatch}.md"
    path.write_text(text, encoding="utf-8")
    # what the harness hands the unit, in tokens (~4 chars each): the packet, its inputs, the rules and the card
    handed = [path, rules_path, Path(values["card"])] + [Path(i["file"]) for i in inputs]
    estimate = sum(f.stat().st_size for f in handed if f.is_file()) // 4
    res = {"path": str(path), "packetDigest": sha256_file(path), "agentType": agent_type(tier), "tier": tier,
           "model": s.model(tier), "resultPath": str(result_path), "dispatchId": dispatch,
           "handedTokens": estimate, "contextWindow": window}
    if escalated:
        res["escalated"] = {"from": unit["tier"], "why": escalated}
    if estimate > window // 4:
        res["contextWarning"] = (f"the packet and its inputs are ~{estimate:,} tokens, over a quarter of the {window:,}-token "
                                 f"window assumed for {tier}; trim the inputs or split the unit before dispatching")
    out(res)


def _repositories_block(s: Session) -> str:
    """Each repository, and for one not yet bound to a worktree, the exact worktree the stack adapter's rule
    must create: the driver resolves the placeholders, so the unit never picks the location."""
    prefix = s.profile["paths"].get("branchPrefix", "ticket/")
    lines = []
    for r in s.ctx["repositories"]:
        lines.append(f"- `{r['id']}` ({r['stack']}): `{r.get('worktree') or r['path']}`")
        if not r.get("worktree"):
            wt = worktree_path(s.profile, s.pdir, r["id"], s.ctx["ticket"])
            lines.append(f"  - ticket worktree (not bound yet): `{wt.as_posix()}` on branch `{prefix}{s.ctx['ticket']}` "
                         f"from `{r.get('baseBranch', 'main')}`; in the stack adapter's `worktree` rule "
                         f"`{{worktreeRoot}}` = `{wt.parent.as_posix()}`. Create it nowhere else.")
    return "\n".join(lines)


def _checks_block(spec: dict[str, Any]) -> str:
    lines = []
    for name, c in (spec.get("checks") or {}).items():
        parts = [f"sections {', '.join('`' + x + '`' for x in c.get('sections', []))}" if c.get("sections") else "",
                 f"parsers {', '.join(c.get('parsers', []))}" if c.get("parsers") else ""]
        lines.append(f"- `{name}`: " + "; ".join(p for p in parts if p))
    return NL.join(lines) or "- no structural checks for this node (existence and citations only)"


def launcher_cmd(profile_file: Path) -> str:
    """How a unit calls the driver: the OS-agnostic sh launcher, with the profile pinned."""
    launcher = (HOME / "skills" / "kaneki-flow" / "scripts" / "sdlc").as_posix()
    return f'sh "{launcher}" --profile "{profile_file.as_posix()}"'


def _languages_block(s: Session, node: str) -> str:
    override = s.ctx.get("locale")
    langs = artifact_languages(s.profile, node, s.artifacts(node), override)
    lines = [f"- `{a}`: **{lang}**" for a, lang in langs.items()] or [f"- findings and notes: **{language(s.profile, 'artifacts', override)}**"]
    lines.append(f"- result file and reply to the controller: **en** (the controller translates for the user)")
    lines.append("- code, identifiers and code comments: follow the repository's existing language (English when unsure)")
    return "\n".join(lines)


def _git_block(s: Session) -> str:
    ident = gitid.identity(s.profile)
    cmd = launcher_cmd(s.ppath)
    per_repo = [f"- `{r['id']}`: {gitid.identity(s.profile, r['id'])['name']} <{gitid.identity(s.profile, r['id'])['email']}>"
                for r in s.ctx["repositories"] if gitid.identity(s.profile, r["id"]) != ident]
    lines = [f"- Project identity: **{ident['name']} <{ident['email']}>**" + (" (custom SSH command configured)" if ident.get("sshCommand") else ""),
             *per_repo,
             f"- Run **every** git command as: `{cmd} git --ticket {s.ctx['ticket']} --repo <repo-id> -- <git args>`",
             "  (it runs in the ticket worktree, applies the identity and SSH command, and logs the call).",
             "- Never run plain `git` for anything that writes or talks to the remote, never set `user.*` or `core.sshCommand`, "
             "never pass `--author`. Nodes never commit, push, merge or rebase, and never delete work (reset --hard, clean -f): "
             "only kaneki-ship commits and pushes, after the user approves."]
    return "\n".join(lines)


def _bullets(items: Any) -> str:
    if not items:
        return "- none"
    if isinstance(items, str):
        return items
    return "\n".join(f"- {i}" for i in items)


def cmd_record(args: argparse.Namespace) -> None:
    s = Session(args)
    spec = s.spec(args.node)
    rec = s.state["nodes"][args.node]
    s.unit(args.node, args.unit)
    result = Path(args.result)
    if not result.is_file():
        raise HarnessError(f"G7: result file missing: {result} (the controller never fills a missing unit)")
    staged = s.stage_problem(args.node, args.unit)
    if staged:
        raise HarnessError(staged)
    tier, _ = s.tier(args.node, args.unit)
    if args.model != s.model(tier):
        raise HarnessError(f"{args.node}/{args.unit} is planned on {s.model(tier)} ({tier}); a {args.model} record is refused")
    entry = {"unit": args.unit, "generation": rec["generation"], "model": args.model, "tier": tier, "status": args.status,
             "result": str(result), "resultDigest": sha256_file(result), "tokens": args.tokens,
             "verdict": args.verdict, "failureClass": args.failure_class, "at": now()}
    rec["units"].append(entry)
    rec["status"] = "in_progress" if rec["status"] in ("pending", "stale") else rec["status"]
    s.save()
    s.ticket.log({"event": "record", "node": args.node, **entry})
    U.write_ticket_file(s.ticket.dir, s.ctx["ticket"], s.order, s.state["nodes"])
    out({"ok": True, "recorded": entry, "nodeTokens": U.node_usage(rec)["total"]})


def _readiness_walk(s: "Session", node: str, status: str, reopen: str | None, notes: dict[str, Any] | None = None,
                    memo: dict[str, Any] | None = None) -> Iterator[tuple[str, bool]]:
    """Every reason the gate would refuse `status` on `node`, lazily and in the order `sdlc gate` checks them, as
    `(message, controller_only)`. Never writes. `controller_only` marks the checks a `--actor user` call skips (G4, G5,
    G0). A check that would repeat or choke on an earlier finding (a unit with no record, a missing file) is skipped when
    that finding was already yielded; `cmd_gate` stops at the first yield, so it never reaches such a guard.
    `notes` receives non-blocking findings (`citationWarnings`); `memo` caches the git-backed checks across walks."""
    memo = {} if memo is None else memo
    spec = s.spec(node)
    rec = s.state["nodes"][node]
    if rec["status"] == "done":
        yield f"{node} already closed at generation {rec['generation']} (gate calls are not re-asked)", False
        return
    missing = missing_prerequisites(s.graph, s.order, s.state, node)
    if missing:
        yield f"G7: {node} is not ready; missing {missing}", False
        return
    current = [u for u in rec["units"] if u["generation"] == rec["generation"]]

    if status == "needs-changes":
        if not spec.get("loopBackTo"):
            yield f"{node} has no loop-back; use rejected + invalidate", False
            return
        attempts = rec["attempts"] + 1
        cap = s.profile.get("gates", {}).get("loopCap", 2)
        if attempts >= cap:
            yield (f"G4: {node} NEEDS CHANGES after {attempts} rounds (cap {cap}); ask the user, then repeat with "
                   "--actor user"), True
        problems = artifact_problems(s, node, "needs-changes", None)
        if problems:
            yield f"G7: {node}: " + "; ".join(problems), False
    elif status == "approved":
        planned = [u["id"] for u in s.units(node)]
        done_units = {u["unit"] for u in current if u["status"] == "ok"}
        lacking = [u for u in planned if u not in done_units]
        if lacking:
            yield f"G7: {node} has no ok record for units {lacking} at generation {rec['generation']}", False
        elif spec.get("reviewer"):
            last_rev = [u for u in current if u["unit"] == "reviewer"][-1]
            if last_rev.get("verdict") == "FAIL":
                yield f"{node}: last reviewer verdict is FAIL ({last_rev.get('failureClass')}); fix and re-review", False
        absent = [a for a in s.artifacts(node) if not (s.ticket.dir / a).is_file()]
        if absent:
            yield f"G7: {node} artifacts missing: {absent}", False
        not_rewritten = [a for a in s.artifacts(node) if a.endswith(".md") and a not in absent
                         and not live_text(s.ticket.dir / a).strip()]  # an absent file is already reported above
        if not_rewritten:
            yield (f"G7: {node} artifacts not rewritten since a rerun superseded them: {not_rewritten} "
                   f"(replace the file, or write the new round below its last `{SUPERSEDED_HEADER}` line)"), False
        if not (absent or not_rewritten):  # the content checks need files that exist and carry live text
            problems = artifact_problems(s, node, "approved", reopen)
            if problems:
                yield f"G7: {node}: " + "; ".join(problems), False
            broken = {a: C.citations(live_text(s.ticket.dir / a), citation_roots(s)) for a in s.artifacts(node) if a.endswith(".md")}
            broken = {a: b for a, b in broken.items() if b}
            if broken and spec.get("subjectNode"):
                yield f"G7: {node}: citations that do not resolve (receipt node): {broken}", False
            elif broken and notes is not None:
                notes["citationWarnings"] = broken
            wrong_lang = language_problems(s, node)
            if wrong_lang:
                yield (f"G5: {node}: output language does not match the configured one: {wrong_lang}. "
                       "Rewrite it, or if the user confirms the text is right, repeat with --actor user"), True
        if _effective_boundary(s, node) == "user":
            yield f"G0: {node} closes on the user's approval; ask Proceed / Re-run / Stop, then --actor user", True
        if spec.get("reopens") and not reopen:
            yield f"{node}: pass --reopen <earliest invalidated node> or --reopen none", False
        if spec.get("subjectNode") or spec.get("checksReceipts"):
            if "commit" not in memo:
                memo["commit"] = _commit_rule_problem(s)
            if memo["commit"]:
                yield memo["commit"], False
        if spec.get("checksReceipts"):
            if "receipts" not in memo:
                found = check_receipts(s.state, s.profile["delivery"].get("minHoursLeft", 24), s.excludes)
                memo["receipts"] = ("G7: receipts not shippable: " + "; ".join(found)) if found else ""
            if memo["receipts"]:
                yield memo["receipts"], False


def _effective_boundary(s: "Session", node: str) -> str:
    """The node's boundary after the plan-approval auto-skip."""
    boundary = s.spec(node)["boundary"]
    if boundary == "user" and node in ("fix-plan", "feature-plan") and s.profile.get("gates", {}).get("autoSkipPlanApproval"):
        return "controller"
    return boundary


def _commit_rule_problem(s: "Session") -> str:
    """The G7 commit-rule message (nodes never commit; only kaneki-ship does), or "" when the rule holds. Runs git."""
    commit_problems = {}
    for r in s.ctx["repositories"]:
        path, ident = r.get("worktree") or r["path"], gitid.identity(s.profile, r["id"])
        shipped = set((s.state.get("shipped") or {}).get(r["id"], {}).get("commits", []))
        bad = gitid.foreign_commits(path, r.get("startHead"), ident)
        unshipped = [c for c in gitid.commits_since(path, r.get("startHead"), ident) if c["sha"] not in shipped]
        if unshipped:
            bad.append("made during the flow, not by kaneki-ship: " + ", ".join(c["sha"][:10] for c in unshipped))
        if bad:
            commit_problems[r["id"]] = bad
    if not commit_problems:
        return ""
    ident = gitid.identity(s.profile)
    return (f"G7: commit rule broken {commit_problems}. Nodes never commit; only kaneki-ship does, as "
            f"{ident['name']} <{ident['email']}>. Undo with `sdlc git --ticket {s.ctx['ticket']} --repo <id> "
            "-- reset --soft <startHead>` (keeps the changes), then close the node again.")


def gate_readiness(s: "Session", node: str, status: str, actor: str, reopen: str | None,
                   notes: dict[str, Any] | None = None, memo: dict[str, Any] | None = None) -> Iterator[str]:
    """What `sdlc gate --status <status> --actor <actor> [--reopen <reopen>]` would refuse, one message at a time and
    lazily: the first message is the error `cmd_gate` raises. A `--actor user` call does not see the controller-only
    checks (G4, G5, G0)."""
    for message, controller_only in _readiness_walk(s, node, status, reopen, notes, memo):
        if controller_only and actor == "user":
            continue
        yield message


def readiness_views(s: "Session", node: str, status: str, reopen: str | None,
                    memo: dict[str, Any] | None = None) -> dict[str, Any]:
    """One walk, both actors: `{"user": [...], "controller": [...], "notes": {...}}`. Git runs once per walk. A
    HarnessError of a git-backed check ends the walk as its last problem (the gate raises the same text)."""
    user: list[str] = []
    controller: list[str] = []
    notes: dict[str, Any] = {}
    try:
        for message, controller_only in _readiness_walk(s, node, status, reopen, notes, memo):
            controller.append(message)
            if not controller_only:
                user.append(message)
    except HarnessError as exc:
        user.append(str(exc))
        controller.append(str(exc))
    return {"user": user, "controller": controller, "notes": notes}


def cmd_gate(args: argparse.Namespace) -> None:
    s = Session(args)
    node = args.node
    spec = s.spec(node)
    rec = s.state["nodes"][node]
    notes: dict[str, Any] = {}
    problem = next(gate_readiness(s, node, args.status, args.actor, args.reopen, notes), None)
    if problem:
        raise HarnessError(problem)
    result: dict[str, Any] = {"node": node}

    if args.status == "needs-changes":
        back = spec["loopBackTo"]
        attempts = rec["attempts"] + 1
        stale = _invalidate(s, back, f"{node} NEEDS CHANGES: {args.note or ''}".strip())
        rec["attempts"] = attempts  # _invalidate resets dependents' counters; the looping node keeps its own
        result.update({"verdict": "needs-changes", "attempts": attempts, "reopened": back, "stale": stale})
    elif args.status == "approved":
        if "citationWarnings" in notes:
            result["citationWarnings"] = notes["citationWarnings"]
        if spec.get("subjectNode"):
            rec["subject"] = {r["id"]: repo_subject(r.get("worktree") or r["path"], s.excludes) for r in s.ctx["repositories"]}
            result["subject"] = rec["subject"]
        if spec.get("issuesReceipts"):
            ttl = s.profile["delivery"].get("receiptTtlHours", 72)
            result["receipts"] = issue_receipts(s.state, ttl, s.excludes)
        usage = U.node_usage(rec)
        result["tokenUsage"] = {"total": usage["total"], "agents": usage["agents"],
                                "writtenTo": [a for a in s.artifacts(node) if U.writes_block(node, a)
                                              and U.write_node_block(s.ticket.dir / a, node, usage)]}
        rec["artifactDigests"] = {a: sha256_file(s.ticket.dir / a) for a in s.artifacts(node)}
        rec["status"] = "done"
        rec["verdict"] = "approved"
        rec["closedAt"] = now()
        result["verdict"] = "approved"
        if spec.get("reopens") and args.reopen != "none":
            result["reopened"] = args.reopen
            result["stale"] = _invalidate(s, args.reopen, f"review round {s.ctx['reviewRound']}: {args.note or ''}".strip())
    else:  # rejected / stopped
        rec["verdict"] = args.status
        result["verdict"] = args.status

    s.ticket.log({"event": "gate", "gate": args.gate, "actor": args.actor, "node": node,
                  "generation": rec["generation"], "verdict": result["verdict"], "note": args.note})
    s.save()
    result["tokenUsageFile"] = str(U.write_ticket_file(s.ticket.dir, s.ctx["ticket"], s.order, s.state["nodes"]))
    result["nextPending"] = next_pending(s.state, s.order)
    out(result)


def node_rule_ids(s: "Session", node: str) -> list[str]:
    spec = s.spec(node)
    return [r["id"] for r in R.select(R.load_rulepacks(HOME, s.pdir, s.profile, s.ctx), s.ctx, spec.get("rulePhases", []))]


def artifact_problems(s: "Session", node: str, status: str, reopen: str | None) -> list[str]:
    spec = s.spec(node)
    out = []
    ctx = {"ruleIds": node_rule_ids(s, node), "status": status, "reopen": reopen}
    for name, check in (spec.get("checks") or {}).items():
        path = s.ticket.dir / name
        if not path.is_file():
            out.append(f"{name} is missing")
            continue
        out += [f"{name}: {p}" for p in C.check_artifact(live_text(path), check, ctx)]
    return out


def citation_roots(s: "Session") -> list[Path]:
    return [Path(r.get("worktree") or r["path"]) for r in s.ctx["repositories"]] + [s.ticket.dir]


def language_problems(s: "Session", node: str) -> dict[str, str]:
    """Only outputs people read outside the ticket folder are checked: drafts and PR replies."""
    langs = artifact_languages(s.profile, node, s.artifacts(node), s.ctx.get("locale"))
    out = {}
    for name, lang in langs.items():
        if name.startswith("drafts/") or node == "respond-review":
            bad = C.language_mismatch(live_text(s.ticket.dir / name), lang)
            if bad:
                out[name] = bad
    return out


def node_inputs(s: "Session", node: str) -> list[dict[str, str]]:
    """The files a node reads: for every token it requires, the artifacts of the settled node that produced it."""
    out, seen = [], set()
    for tok in s.spec(node).get("requires", []):
        for producer in s.order:
            if producer == node or tok not in s.graph["nodes"][producer].get("produces", []) or not settled(s.state, producer):
                continue
            for name in s.artifacts(producer):
                path = s.ticket.dir / name
                if name in seen or not path.is_file():
                    continue
                seen.add(name)
                out.append({"token": tok, "from": producer, "file": str(path), "sha256": sha256_file(path)})
    return out


SUPERSEDED_HEADER = "## SUPERSEDED:"
NL = chr(10)


def packet_inputs(s: "Session", node: str, copy_dir: Path) -> list[dict[str, str]]:
    """The node's inputs as the unit should read them. An input that carries superseded rounds or a token-usage
    block is handed over as a live-only copy (the rounds a rerun invalidated are history nobody downstream needs,
    and on a looped review they are most of the file); the original stays the record."""
    out = []
    for i in node_inputs(s, node):
        src = Path(i["file"])
        if src.suffix != ".md":
            out.append(i)
            continue
        full = src.read_text(encoding="utf-8", errors="replace")
        live = U.strip_live_block(live_text(src)).strip() + NL
        if live.strip() == full.strip():
            out.append(i)
            continue
        copy_dir.mkdir(parents=True, exist_ok=True)
        dest = copy_dir / src.name
        dest.write_text(live, encoding="utf-8")
        out.append({**i, "file": str(dest), "sha256": sha256_file(dest), "original": str(src)})
    return out


def live_text(path: Path) -> str:
    """wms-flow rule: only the text after the last `## SUPERSEDED:` line counts; the rounds above it are history
    a rerun invalidated. A file with no such line is all live."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return text.rsplit(SUPERSEDED_HEADER, 1)[-1].split(NL, 1)[-1] if SUPERSEDED_HEADER in text else text


def mark_superseded(s: "Session", node: str, from_node: str, generation: int) -> list[str]:
    """Port of wms-flow `mark_superseded`: an artifact a rerun invalidated says so in its own text, so the stale
    content can never pass the gate again. One header per invalidation; replaying the same one adds nothing."""
    marked = []
    reason = f"rerun from {from_node} (generation {generation}) invalidated this artifact"
    for name in s.artifacts(node):
        path = s.ticket.dir / name
        if not name.endswith(".md") or path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        headers = [l for l in text.splitlines() if l.startswith(SUPERSEDED_HEADER)]
        if headers and headers[-1].endswith(reason):
            continue
        sep = "" if text.endswith(NL) else NL
        path.write_text(text + sep + NL + f"{SUPERSEDED_HEADER} {now()[:10]} — {reason}" + NL, encoding="utf-8")
        marked.append(name)
    return marked


def _never_ran(rec: dict[str, Any]) -> bool:
    """A node that was never started: nothing to supersede when an earlier node is reopened."""
    return rec["status"] == "pending" and rec["generation"] == 1 and not rec["units"]


def stale_preview(s: "Session", node: str) -> list[str]:
    """What `_invalidate(node)` would return: `node` and every later node that consumes what it produces. Pure."""
    return G.dependents(s.graph, s.order, node)


def superseded_preview(s: "Session", node: str) -> list[str]:
    """The part of `stale_preview(node)` that `_invalidate` really supersedes (a node that never ran is left alone). Pure."""
    return [n for n in stale_preview(s, node) if not _never_ran(s.state["nodes"][n])]


def _invalidate(s: Session, node: str, reason: str) -> list[str]:
    stale = stale_preview(s, node)
    for n in stale:
        rec = s.state["nodes"][n]
        if _never_ran(rec):
            continue  # never ran: nothing to supersede
        rec["status"] = "stale"
        rec["generation"] += 1
        rec["verdict"] = None
        if n != stale[0]:
            rec["attempts"] = 0
        mark_superseded(s, n, node, rec["generation"])
    if any(s.spec(n).get("issuesReceipts") for n in stale):
        s.state["receipts"] = {}
    s.ticket.log({"event": "invalidate", "node": node, "stale": stale, "reason": reason})
    return stale


def cmd_invalidate(args: argparse.Namespace) -> None:
    s = Session(args)
    if not args.reason:
        raise HarnessError("--reason is required: name what is wrong in the earlier artifact")
    stale = _invalidate(s, args.node, args.reason)
    kept = [n for n in s.order if n not in stale]
    s.save()
    out({"stale": stale, "kept": kept, "nextPending": next_pending(s.state, s.order)})


def cmd_worktree(args: argparse.Namespace) -> None:
    s = Session(args)
    repo = next((r for r in s.ctx["repositories"] if r["id"] == args.repo), None)
    if repo is None:
        raise HarnessError(f"unknown repo {args.repo}")
    if not Path(args.path).is_dir():
        raise HarnessError(f"G7: worktree path does not exist: {args.path}")
    given = Path(args.path).resolve()
    expected = worktree_path(s.profile, s.pdir, repo["id"], s.ctx["ticket"])
    # the repository itself is allowed (work in place, no worktree); any other folder must be the ticket's
    # slot under paths.worktreeRoot, so worktrees never scatter next to the repositories
    if given != Path(repo["path"]).resolve() and given != expected:
        raise HarnessError(f"G7: worktree must be {expected.as_posix()} (paths.worktreeRoot/<repoId>-<ticket>), "
                           f"not {given.as_posix()}")
    repo["worktree"] = str(given)
    repo["worktreeRel"] = portable(Path(repo["worktree"]), s.pdir)
    head = gitid.run(repo["worktree"], ["rev-parse", "HEAD"], gitid.identity(s.profile, repo["id"]))
    if head.returncode != 0:
        raise HarnessError(f"G7: worktree is not a git checkout: {repo['worktree']}")
    repo["startHead"] = head.stdout.strip()
    s.save()
    s.ticket.log({"event": "worktree", "repo": args.repo, "path": repo["worktree"]})
    from . import worktree_hooks
    hooks = worktree_hooks.install(given, s.pdir)
    out({"ok": True, "repo": args.repo, "worktree": repo["worktree"], "hooks": hooks})


def cmd_handback(args: argparse.Namespace) -> None:
    """Start an after-PR review round: the review nodes enter the trace (or restart), the main graph is untouched
    until triage-review reopens the earliest invalidated node."""
    s = Session(args)
    out(do_handback(s, Path(args.review_file)))


def do_handback(s: "Session", src: Path) -> dict[str, Any]:
    if not settled(s.state, "deliverable-ready"):
        raise HarnessError("handback needs a delivered ticket (deliverable-ready done)")
    if not src.is_file():
        raise HarnessError(f"review file not found: {src}")
    s.ctx["reviewRound"] += 1
    s.ctx["maturity"] = "pr_opened"
    rnd = s.ctx["reviewRound"]
    dest = s.ticket.dir / "reviews" / f"round-{rnd}{src.suffix or '.md'}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    for node in ("read-review", "triage-review", "respond-review"):
        rec = s.state["nodes"].setdefault(node, new_node_record())
        if rec["units"] or rec["status"] == "done":
            rec.update({"status": "pending", "generation": rec["generation"] + 1, "verdict": None, "attempts": 0})
    s.save()
    s.ticket.log({"event": "handback", "round": rnd, "file": str(dest), "digest": sha256_file(dest)})
    order = G.trace(s.graph, s.ctx)
    return {"ok": True, "reviewRound": rnd, "reviewFile": str(dest), "nextPending": next_pending(s.state, order)}


def _delivery(s: "Session") -> dict[str, Any]:
    return resolve_adapters(HOME, s.pdir, s.profile, s.ctx)["delivery"]


def cmd_ship_request(args: argparse.Namespace) -> None:
    """kaneki-ship step 1: the exact request the user approves (printed in full; its digest is the approval)."""
    from . import mygit
    s = Session(args)
    delivery = _delivery(s)
    backend = SHIP.resolve_backend(s.profile, delivery, args.backend)
    root = mygit.require_root(HOME) if backend == "my-git" else None
    request = SHIP.build_request(s, args.repo, delivery, backend=backend, mygit_root=root)
    digest = SHIP.request_digest(request)
    path = s.ticket.dir / "ship" / f"{args.repo}-request.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({**request, "requestDigest": digest}, indent=2, ensure_ascii=False), encoding="utf-8")
    s.ticket.log({"event": "ship-request", "repo": args.repo, "requestDigest": digest})
    approve_with = f"ship --ticket {s.ctx['ticket']} --repo {args.repo} --approve {digest}"
    if args.backend:
        approve_with += f" --backend {args.backend}"
    out({"requestDigest": digest, "requestFile": str(path), "repo": args.repo, "branch": request["branch"],
         "base": request["base"], "files": request["paths"], "identity": request["identity"],
         "commitMessage": request["commitMessage"], "push": request["push"], "pr": request["pr"], "via": backend,
         "approveWith": approve_with})


def cmd_ship(args: argparse.Namespace) -> None:
    """kaneki-ship step 2: only with the digest of the request the user approved; refuses any drift since.
    With `native-git` (default), commits, pushes, and opens the PR directly. With `my-git`, stages and prints
    the /my-git:* steps."""
    from . import mygit
    s = Session(args)
    delivery = _delivery(s)
    backend = SHIP.resolve_backend(s.profile, delivery, args.backend)
    root = mygit.require_root(HOME) if backend == "my-git" else None
    request = SHIP.build_request(s, args.repo, delivery, backend=backend, mygit_root=root)
    digest = SHIP.request_digest(request)
    if args.approve != digest:
        raise HarnessError("G0: the request changed since it was approved (or the digest is wrong): run ship-request "
                           "again, show it to the user, and approve the new digest")
    if args.no_pr:
        request["pr"] = None
    if backend == "my-git":
        assert root is not None
        result = SHIP.stage(s, request, root)
        s.save()
        s.ticket.log({"event": "ship", "repo": args.repo, "approval": digest, "tree": result["tree"], "via": "my-git"})
    else:
        result = SHIP.execute_native(s, request)
        s.save()
        s.ticket.log({"event": "ship", "repo": args.repo, "approval": digest, "tree": result["tree"], "via": "native-git"})
        s.ticket.log({"event": "ship-record", "repo": args.repo, "commit": result["commit"], "approval": digest,
                      "pushed": result["pushed"], "pr": result.get("pr")})
    out(result)


def cmd_ship_record(args: argparse.Namespace) -> None:
    """kaneki-ship step 3: after /my-git:commit or manual PR/push, verify/record commit, push and PR link."""
    s = Session(args)
    result = SHIP.record_ship(s, args.repo, args.pr)
    s.save()
    s.ticket.log({"event": "ship-record", "repo": args.repo, "commit": result["commit"], "approval": result["approval"],
                  "pushed": result["pushed"], "pr": result.get("pr")})
    out(result)


def cmd_review_fetch(args: argparse.Namespace) -> None:
    """Export the PR review with the delivery adapter's CLI (GitHub) and start the review round (handback)."""
    s = Session(args)
    src = SHIP.fetch_review(s, args.repo, args.pr)
    res = do_handback(s, src)
    s.ticket.log({"event": "review-fetch", "repo": args.repo, "file": str(src)})
    out({**res, "fetched": str(src)})


def cmd_receipts(args: argparse.Namespace) -> None:
    s = Session(args)
    if args.reissue:
        if not settled(s.state, "final-review"):
            raise HarnessError("re-issue needs a closed final-review")
        rec = issue_receipts(s.state, s.profile["delivery"].get("receiptTtlHours", 72), s.excludes)
        s.save()
        s.ticket.log({"event": "receipts-reissued", "repos": sorted(rec)})
    problems = check_receipts(s.state, s.profile["delivery"].get("minHoursLeft", 24), s.excludes)
    out({"receipts": s.state.get("receipts", {}), "shippable": not problems, "problems": problems})


def cmd_git(args: argparse.Namespace) -> int:
    """The one way nodes run git: the project identity (and SSH command) applied per call, in the right checkout."""
    git_args = list(args.git_args)
    if git_args and git_args[0] == "--":
        git_args = git_args[1:]
    gitid.check_args(git_args)
    if args.ticket:
        s = Session(args)
        repo = next((r for r in s.ctx["repositories"] if r["id"] == args.repo), None)
        if repo is None:
            raise HarnessError(f"unknown repo {args.repo}")
        path, profile = repo.get("worktree") or repo["path"], s.profile
        s.ticket.log({"event": "git", "repo": args.repo, "args": git_args})
    else:
        profile = load_profile(profile_path(args))
        repo = next((r for r in profile["repositories"] if r["id"] == args.repo), None)
        if repo is None:
            raise HarnessError(f"unknown repo {args.repo}")
        path = _abs(repo["path"], profile)
    res = gitid.run(path, git_args, gitid.identity(profile, args.repo), capture=False)
    return res.returncode


def cmd_lang(args: argparse.Namespace) -> None:
    """Per-ticket output languages. Set only when the user explicitly asks in the prompt; otherwise the profile
    (and, where the profile is silent, English) applies."""
    s = Session(args)
    override = dict(s.ctx.get("locale") or {})
    changes = parse_lang_settings(args.set)
    for key in args.clear or []:
        if key not in LOCALE_KEYS:
            raise HarnessError(f"unknown language key {key}")
        override.pop(key, None)
    override.update(changes)
    if args.set or args.clear:
        if override:
            s.ctx["locale"] = override
        else:
            s.ctx.pop("locale", None)
        s.save()
        s.ticket.log({"event": "lang", "set": changes, "cleared": args.clear or [], "reason": args.reason})
    out({"ticket": s.ctx["ticket"],
         "languages": {k: {"language": language(s.profile, k, override), "source": language_source(s.profile, k, override)}
                       for k in LOCALE_KEYS}})


def cmd_install(args: argparse.Namespace) -> None:
    from .install import install
    out(install(HOME, Path(args.target).resolve(), force=args.force, dry_run=args.dry_run))


def cmd_tickets(args: argparse.Namespace) -> None:
    from . import board
    from .contract import CONTRACT_VERSION
    out({"contractVersion": CONTRACT_VERSION, "tickets": board.list_tickets(profile_path(args))})


def cmd_status_of(args: argparse.Namespace) -> None:
    """The ticket a file belongs to (its ticket folder, or a ticket worktree `<repoId>-<ticket>`), as a board summary."""
    from . import board
    ppath = profile_path(args)
    prof = load_profile(ppath)
    root = ticket_root(prof, ppath.parent)
    target = Path(args.path).resolve()
    ticket = None
    try:
        ticket = target.relative_to(root.resolve()).parts[0]
    except (ValueError, IndexError):
        wt_root = worktree_root(prof, ppath.parent).resolve()
        try:
            folder = target.relative_to(wt_root).parts[0]
            ticket = next((d.name for d in root.iterdir() if d.is_dir() and folder.endswith("-" + d.name)), None) if root.is_dir() else None
        except (ValueError, IndexError):
            pass
    if not ticket or not (root / ticket / "state.json").is_file():
        out({"ticket": None, "path": str(target), "hint": "not inside a ticket folder or ticket worktree; see `sdlc tickets`"})
        return
    summary = board.summarize_ticket(ppath, ticket)
    summary.pop("events", None)
    out(summary)


def cmd_ticket_detail(args: argparse.Namespace) -> None:
    """One ticket as the desktop app's `TicketDetail`: the board summary plus node cards, trace and loop-backs.
    Read-only; a ticket that fails to load is the usual rc 2 error."""
    from . import board
    out(board.summarize(Session(args), events_limit=args.events, detail=True))


def cmd_gate_request(args: argparse.Namespace) -> None:
    """What the gate on a node would accept, as the desktop app's `GateRequest`. Read-only; never closes anything."""
    from . import board
    out(board.gate_request(Session(args), args.node))


def cmd_contract(args: argparse.Namespace) -> None:
    """The contract version the desktop app checks at start-up; needs no profile or ticket and writes nothing."""
    from .contract import contract_info
    out(contract_info())


def cmd_web(args: argparse.Namespace) -> None:
    from . import web
    out(web.serve(profile_path(args), args.port, args.open))


def cmd_zed_setup(args: argparse.Namespace) -> None:
    from . import zed
    out(zed.setup(HOME, Path(args.target).resolve(), dry_run=args.dry_run))


def cmd_digest(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.is_file():
        raise HarnessError(f"not a file: {path}")
    d = sha256_file(path)
    if args.expect and args.expect != d:
        raise HarnessError(f"digest mismatch for {path}: {d} != {args.expect}")
    out({"path": str(path), "sha256": d, "bytes": path.stat().st_size})


# --- parser ------------------------------------------------------------------------------------------

def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sdlc", description="Generic SDLC ticket harness driver")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", help="project profile (default: $SDLC_PROFILE or ./sdlc.profile.json)")
    ticketed = argparse.ArgumentParser(add_help=False, parents=[common])
    ticketed.add_argument("--ticket", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", parents=[common], help="check profile, adapters, rulepacks, extensions").set_defaults(fn=cmd_validate)

    x = sub.add_parser("init", parents=[ticketed], help="create ticket state and trace")
    x.add_argument("--kind", required=True, choices=["bug", "feature"])
    x.add_argument("--repo", action="append", help="limit to these profile repositories (repeatable)")
    x.add_argument("--family", action="append", help="capability family declared in the profile (repeatable)")
    x.add_argument("--surface", action="append", help="extra surface beyond the repositories' defaults")
    x.add_argument("--force", action="store_true")
    x.add_argument("--lang", action="append", help="explicit per-ticket language, e.g. delivery=ja (only when the user asked)")
    x.add_argument("--lane", default="full", help="full (every node, worktree) or lite (small change: scope, change in place, quick review, drafts)")
    x.set_defaults(fn=cmd_init)

    x = sub.add_parser("lane", parents=[ticketed], help="show the ticket's lane, or move it (lite -> full) with --set and --reason")
    x.add_argument("--set")
    x.add_argument("--reason")
    x.set_defaults(fn=cmd_lane)

    x = sub.add_parser("status", parents=[ticketed], help="trace, node states, next pending node (read-only)")
    x.add_argument("--compact", action="store_true")
    x.add_argument("--accept-current-state", action="store_true",
                   help="record that state.json was intentionally changed outside the driver (needs --reason)")
    x.add_argument("--reason")
    x.set_defaults(fn=cmd_status)

    x = sub.add_parser("plan", parents=[ticketed], help="read-only plan for one node (default: next pending)")
    x.add_argument("--node")
    x.add_argument("--card", action="store_true", help="short form without rule ids")
    x.set_defaults(fn=cmd_plan)

    x = sub.add_parser("packet", parents=[ticketed], help="render one dispatch packet")
    x.add_argument("--node", required=True)
    x.add_argument("--unit", required=True)
    x.add_argument("--facts", help="facts JSON: weakestClaim, observed, doubts, settled, authorizedPaths, findingsToFix")
    x.set_defaults(fn=cmd_packet)

    x = sub.add_parser("record", parents=[ticketed], help="record one unit result (worker or reviewer)")
    x.add_argument("--node", required=True)
    x.add_argument("--unit", required=True)
    x.add_argument("--result", required=True)
    x.add_argument("--model", required=True)
    x.add_argument("--status", default="ok", choices=["ok", "blocked", "partial"])
    x.add_argument("--tokens", type=int)
    x.add_argument("--verdict", choices=["PASS", "FAIL", "APPROVED", "NEEDS CHANGES"])
    x.add_argument("--failure-class", choices=["record", "artifact"])
    x.set_defaults(fn=cmd_record)

    x = sub.add_parser("gate", parents=[ticketed], help="close (or loop back) a node")
    x.add_argument("--node", required=True)
    x.add_argument("--status", required=True, choices=["approved", "needs-changes", "rejected", "stopped"])
    x.add_argument("--gate", default="G0")
    x.add_argument("--actor", default="controller", choices=["controller", "user"])
    x.add_argument("--reopen", help="triage-review: earliest node the accepted findings invalidate, or 'none'")
    x.add_argument("--note")
    x.set_defaults(fn=cmd_gate)

    x = sub.add_parser("invalidate", parents=[ticketed], help="stale a node and only the later nodes consuming its output")
    x.add_argument("--node", required=True)
    x.add_argument("--reason", required=True)
    x.set_defaults(fn=cmd_invalidate)

    x = sub.add_parser("worktree", parents=[ticketed], help="bind a repository to the ticket worktree")
    x.add_argument("--repo", required=True)
    x.add_argument("--path", required=True)
    x.set_defaults(fn=cmd_worktree)

    x = sub.add_parser("handback", parents=[ticketed], help="start an after-PR review round from an exported review")
    x.add_argument("--review-file", required=True)
    x.set_defaults(fn=cmd_handback)

    x = sub.add_parser("ship-request", parents=[ticketed], help="kaneki-ship: build the exact commit/push/PR request for the user to approve")
    x.add_argument("--repo", required=True)
    x.add_argument("--backend", choices=["native-git", "my-git"], help="ship backend: native-git (default) or my-git")
    x.set_defaults(fn=cmd_ship_request)

    x = sub.add_parser("ship", parents=[ticketed], help="kaneki-ship: execute (native-git) or stage (my-git) the approved request (needs the approved digest)")
    x.add_argument("--repo", required=True)
    x.add_argument("--approve", required=True, help="requestDigest of the request the user approved")
    x.add_argument("--no-pr", action="store_true", help="push but do not open a PR/MR")
    x.add_argument("--backend", choices=["native-git", "my-git"], help="ship backend: native-git (default) or my-git")
    x.set_defaults(fn=cmd_ship)

    x = sub.add_parser("ship-record", parents=[ticketed], help="kaneki-ship: verify/record the committed, pushed, or manually opened PR state")
    x.add_argument("--repo", required=True)
    x.add_argument("--pr", help="the PR/MR URL (or the compare link a person submitted)")
    x.set_defaults(fn=cmd_ship_record)

    x = sub.add_parser("review-fetch", parents=[ticketed], help="fetch the PR review (GitHub) and start a review round")
    x.add_argument("--repo", required=True)
    x.add_argument("--pr", help="PR number (default: the one kaneki-ship opened)")
    x.set_defaults(fn=cmd_review_fetch)

    x = sub.add_parser("receipts", parents=[ticketed], help="show receipts; --reissue after a TTL lapse")
    x.add_argument("--reissue", action="store_true")
    x.set_defaults(fn=cmd_receipts)

    x = sub.add_parser("onboard-scan", help="kaneki-onboarding step 1: detect profile candidates with evidence")
    x.add_argument("--root", required=True, help="project directory (a repo, or a folder holding repos)")
    x.add_argument("--repo", action="append", help="extra repository outside the root (repeatable)")
    x.add_argument("--out", required=True, help="candidates JSON to write")
    x.set_defaults(fn=cmd_onboard_scan)

    x = sub.add_parser("onboard-verify", help="kaneki-onboarding step 3: run detected read-only checks, record results")
    x.add_argument("--candidates", required=True)
    x.add_argument("--allow-tests", action="store_true", help="also run test commands (ask the user first: G6)")
    x.add_argument("--allow-build", action="store_true", help="also run build commands")
    x.add_argument("--timeout", type=int, default=300)
    x.set_defaults(fn=cmd_onboard_verify)

    x = sub.add_parser("onboard-write", help="kaneki-onboarding step 5: write profile + evidence sidecar + project adapters")
    x.add_argument("--candidates", required=True)
    x.add_argument("--answers", help="JSON {field: value} of the user's answers")
    x.add_argument("--profile", help="target profile (default: <root>/sdlc.profile.json)")
    x.add_argument("--refresh", action="store_true", help="update an existing profile; values you set are never overwritten")
    x.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    x.set_defaults(fn=cmd_onboard_write)

    x = sub.add_parser("git", parents=[common], help="run git in a repository as the project identity (output is git's own, not JSON)")
    x.add_argument("--ticket", help="run in this ticket's worktree and log the call")
    x.add_argument("--repo", required=True)
    x.add_argument("git_args", nargs=argparse.REMAINDER, help="-- <git arguments>")
    x.set_defaults(fn=cmd_git)

    x = sub.add_parser("lang", parents=[ticketed], help="show or set this ticket's output languages (default English)")
    x.add_argument("--set", action="append", help="key=lang, e.g. delivery=ja; only when the user explicitly asked")
    x.add_argument("--clear", action="append", help="drop a ticket override (back to the profile / English)")
    x.add_argument("--reason", help="the user's words that asked for the change")
    x.set_defaults(fn=cmd_lang)

    x = sub.add_parser("install", help="install the kaneki skills, agents and guard into <target>/.claude, and the my-git plugin from the same checkout")
    x.add_argument("--target", required=True, help="project folder (or a .claude folder, e.g. ~/.claude for all projects)")
    x.add_argument("--force", action="store_true", help="also overwrite installed files you edited")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_install)

    x = sub.add_parser("tickets", parents=[common], help="every ticket under the profile: lane, progress, next step")
    x.set_defaults(fn=cmd_tickets)

    x = sub.add_parser("status-of", parents=[common], help="status of the ticket a file belongs to (for editor tasks)")
    x.add_argument("--path", required=True)
    x.set_defaults(fn=cmd_status_of)

    x = sub.add_parser("ticket-detail", parents=[ticketed],
                       help="one ticket with node cards, trace and loop-backs, as JSON for the desktop app (read-only)")
    x.add_argument("--events", type=_non_negative, default=40, help="how many recent decision events to include")
    x.set_defaults(fn=cmd_ticket_detail)

    x = sub.add_parser("gate-request", parents=[ticketed],
                       help="what the gate on a node would accept, as JSON for the desktop app (read-only; default: next pending)")
    x.add_argument("--node")
    x.set_defaults(fn=cmd_gate_request)

    x = sub.add_parser("contract", help="the data contract version the desktop app checks (read-only, no profile)")
    x.set_defaults(fn=cmd_contract)

    x = sub.add_parser("web", parents=[common], help="local live board of every ticket (127.0.0.1, read-only); Ctrl-C stops it")
    x.add_argument("--port", type=int, default=8765)
    x.add_argument("--open", action="store_true", help="open it in the browser")
    x.set_defaults(fn=cmd_web)

    x = sub.add_parser("zed-setup", help="add kaneki tasks (board, tickets, status) to <target>/.zed/tasks.json")
    x.add_argument("--target", required=True, help="the folder you open in Zed (the workspace with sdlc.profile.json)")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_zed_setup)

    x = sub.add_parser("digest", help="sha256 of a file (never prints content)")
    x.add_argument("--path", required=True)
    x.add_argument("--expect")
    x.set_defaults(fn=cmd_digest)
    return p


READ_ONLY = {"status", "plan", "validate", "digest", "onboard-scan", "onboard-verify", "onboard-write", "install",
             "tickets", "status-of", "web", "zed-setup", "contract", "ticket-detail", "gate-request"}
NO_STATUS_REFRESH = READ_ONLY | {"git"}


def _ticket_lock(args: argparse.Namespace):
    """One writer per ticket: every command that changes a ticket holds its lock for the whole read-modify-write."""
    from contextlib import nullcontext
    if ((args.cmd in READ_ONLY and not (args.cmd == "status" and args.accept_current_state))
            or not getattr(args, "ticket", None) or (args.cmd == "lang" and not (args.set or args.clear))):
        return nullcontext()
    try:
        prof = load_profile(profile_path(args))
    except HarnessError:
        return nullcontext()  # the command itself reports the profile error
    return Ticket(ticket_root(prof, profile_path(args).parent), args.ticket).lock(args.cmd)


def main(argv: list[str] | None = None) -> int:
    # one JSON line in UTF-8 whatever the console code page (Windows defaults to cp1252 on a pipe)
    if hasattr(sys.stdout, "reconfigure") and (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    try:
        with _ticket_lock(args):
            rc = args.fn(args)
            if args.cmd not in NO_STATUS_REFRESH and getattr(args, "ticket", None):
                _refresh_status(args)
    except HarnessError as exc:
        out({"error": str(exc)})
        return 2
    return rc if isinstance(rc, int) else 0


def _refresh_status(args: argparse.Namespace) -> None:
    """STATUS.md next to state.json, rewritten after every command that changed the ticket. Best effort: a failure
    here never fails the command that already succeeded."""
    from . import board
    try:
        board.write_status(Session(argparse.Namespace(profile=args.profile, ticket=args.ticket)))
    except (HarnessError, OSError):
        pass


if __name__ == "__main__":
    sys.exit(main())
