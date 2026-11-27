"""`sdlc` driver. Every command prints one JSON object and exits 0, or prints {"error": ...} and exits 2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator

from . import checks as C
from . import ship as SHIP
from . import gitid
from . import graph as G
from . import hosts as H
from . import learning as L
from . import onboard as OB
from . import rules as R
from . import usage as U
from .errors import HarnessError
from .profile import (LOCALE_KEYS, _abs, artifact_languages, build_context, language, language_source,
                      load_profile, parse_lang_settings, resolve_adapters, worktree_path, worktree_root,
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
        self.cli_host = getattr(args, "host", None)
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
        self.ctx["reflect"] = L.reflect_mode(self.profile)  # the profile in force decides (off drops reflect)
        self.graph = G.load_graph(HOME, self.profile, self.pdir)
        self.order = G.trace(self.graph, self.ctx)
        for node in self.order:  # nodes that enter the trace later (review round) get a record
            self.state["nodes"].setdefault(node, new_node_record())
        self._signals: tuple[Any, dict[str, Any]] | None = None

    def spec(self, node: str) -> dict[str, Any]:
        if node not in self.order:
            raise HarnessError(f"{node} is not in this ticket's trace: {self.order}")
        spec = self.graph["nodes"][node]
        if G.is_inline(self.graph, self.ctx, node):  # this lane: the controller does it, no subagent, no reviewer
            spec = {**{k: v for k, v in spec.items() if k != "reviewer"}, "units": [], "pattern": "controller-inline"}
        if node == L.REFLECT and self.reflect_quiet():  # on-signal and nothing new went wrong: close without a unit
            spec = {**spec, "units": [], "artifacts": [], "checks": {}, "pattern": "no-signal"}
        return spec

    def signals(self) -> dict[str, Any]:
        """`sdlc reflect-signals` for this ticket, cached until the decision log or a ticket file it reads changes."""
        files = (self.ticket.log_path, self.ticket.dir / "questions.md", self.ticket.dir / "questions.json",
                 self.ticket.dir / "triage.md")
        key = (tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else None for p in files),
               self.ctx.get("reviewRound"))
        if self._signals is None or self._signals[0] != key:
            self._signals = (key, L.collect(self))
        return self._signals[1]

    def reflect_quiet(self) -> bool:
        return self.ctx.get("reflect") == "on-signal" and self.signals()["empty"]

    def artifacts(self, node: str, static: bool = False) -> list[str]:
        """Artifact names of a node. `static`: as the graph declares them, even when this round needs none
        (a quiet reflect), for marking superseded files."""
        names = []
        for name in (self.graph["nodes"][node] if static else self.spec(node)).get("artifacts", []):
            if name == "@delivery.drafts":
                names += [f"drafts/{d}.md" for d in self.profile["delivery"].get("drafts", [])]
            else:
                names.append(name)
        return names

    def model(self, tier: str) -> str:
        return self.execution("_tier", "_tier", tier)["model"]

    def execution(self, node: str, unit_id: str, tier: str | None = None) -> dict[str, Any]:
        eff_tier = tier if tier is not None else self.tier(node, unit_id)[0]
        return H.resolve_execution(HOME, self.pdir, self.profile, node, unit_id, eff_tier, cli_host=self.cli_host)

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
        """Effective tier of a unit and, when it moved up, why. Combines `profile.nodeOverrides` and graph escalation."""
        u = self.unit(node, unit_id)
        return H.effective_tier(self.profile, u, node, self.failed_rounds, self.graph["nodes"], self.order)

    def stage_problem(self, node: str, unit_id: str) -> str | None:
        """A staged unit (`after`) runs only once its predecessor recorded PASS at the current generation."""
        before = self.unit(node, unit_id).get("after")
        if not before:
            return None
        rec = self.state["nodes"][node]
        prev = [u for u in rec["units"] if u["unit"] == before and u["generation"] == rec["generation"]
                and u["status"] != "abandoned"]
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
    hooks = {}
    if not G.graph_uses_worktree(graph, ctx):  # in-place lanes: guard the checkout while this ticket holds it
        from . import worktree_hooks
        for repo in ctx["repositories"]:
            hooks[repo["id"]] = worktree_hooks.install(Path(repo["path"]), path.parent, in_place=True,
                                                       ticket=args.ticket, ticket_dir=ticket.dir, repo_id=repo["id"],
                                                       base_branch=repo.get("baseBranch"))
    out({"ok": True, "ticketDir": str(ticket.dir), "lane": args.lane, "trace": order, "nextPending": order[0],
         **({"hooks": hooks} if hooks else {}), **({"warnings": warnings} if warnings else {})})


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
    from . import board
    s = Session(args)
    rows = []
    tokens_total = 0
    for node in s.order:
        rec = s.state["nodes"][node]
        toks = sum(u.get("tokens") or 0 for u in rec["units"] if u["generation"] == rec["generation"])
        tokens_total += toks
        rows.append({"node": node, "layer": s.spec(node)["layer"], "status": rec["status"],
                     "generation": rec["generation"], "verdict": rec["verdict"], "tokens": toks,
                     "models": sorted({u["model"] for u in rec["units"] if u["generation"] == rec["generation"]}),
                     "hosts": sorted({board.unit_host(u) for u in rec["units"]
                                      if u["generation"] == rec["generation"]})})
    nxt = next_pending(s.state, s.order)
    # the same total as STATUS.md and token-usage.md (usage.ticket_tokens): measured from the logs when readable
    tt = U.ticket_tokens(s.ppath, s.ctx["ticket"], s.order, s.state["nodes"])
    for row in rows:
        row["tokensCurrentGeneration"] = row["tokens"]
        row["tokens"] = (tt["nodes"].get(row["node"]) or {}).get("tokens", 0)
    res = {"ticket": s.ctx["ticket"], "kind": s.ctx["kind"], "lane": G.lane_of(s.ctx), "reviewRound": s.ctx["reviewRound"],
           "settled": sum(1 for n in s.order if settled(s.state, n)), "traceLength": len(s.order),
           "nextPending": nxt, "tokensTotal": tt["total"]["tokens"], "tokens": U.compact(tt)}
    res["host"], res["hosts"] = board.ticket_hosts(s.state)
    if nxt:
        res["nextMissing"] = missing_prerequisites(s.graph, s.order, s.state, nxt)
    if not args.compact:
        res["nodes"] = rows
    out(res)


AGENT_TYPES = {"light": "kaneki-light", "routine": "kaneki-worker", "deep": "kaneki-deep"}
_PUBLIC_EXEC_KEYS = ("host", "dispatchMode", "model", "modelSelection", "effort", "enforcement", "aliases", "contextWindow")


def agent_type(tier: str) -> str:
    return AGENT_TYPES[tier]


def _public_execution(exe: dict[str, Any]) -> dict[str, Any]:
    out_exe = {k: exe[k] for k in _PUBLIC_EXEC_KEYS if k in exe}
    if exe.get("runner"):
        out_exe["runner"] = exe["runner"]
    return out_exe


def _node_card_path(card_rel: str) -> Path:
    if card_rel.startswith("references/nodes/"):
        canonical = HOME / "playbooks" / "nodes" / Path(card_rel).name
        if canonical.is_file():
            return canonical
    return HOME / "skills" / "kaneki-flow" / card_rel


def _role_card_path(tier: str) -> Path:
    return HOME / "playbooks" / "roles" / f"{tier}.md"


def _packet_template() -> str:
    canonical = HOME / "playbooks" / "packet-template.md"
    if canonical.is_file():
        return canonical.read_text(encoding="utf-8")
    return (HOME / "skills" / "kaneki-flow" / "scripts" / "packet-template.md").read_text(encoding="utf-8")


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
        exe = s.execution(node, u["id"], tier)
        role_path = _role_card_path(tier)
        row = {k: v for k, v in u.items() if k != "escalate"}
        row.update({
            "tier": tier,
            "model": exe["model"],
            "agentType": agent_type(tier),
            "roleCard": portable(role_path, HOME) if role_path.is_file() else f"playbooks/roles/{tier}.md",
            "execution": _public_execution(exe),
        })
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
    if node == L.REFLECT:
        report = s.signals()
        quiet = s.reflect_quiet()
        plan["reflect"] = {"mode": s.ctx.get("reflect"), "newSignals": report["newSignals"], "counts": report["counts"],
                           "noSignal": quiet, "signals": f"sdlc reflect-signals --ticket {s.ctx['ticket']}"}
        if quiet:
            plan["closeWith"] += " --note no-signal"
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


def build_packet(s: Session, node: str, unit_id: str, facts_arg: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Render one dispatch packet and return `(packet_payload, resolved_execution)`."""
    spec = s.spec(node)
    rec = s.state["nodes"][node]
    unit = s.unit(node, unit_id)
    staged = s.stage_problem(node, unit_id)
    if staged:
        raise HarnessError(staged)
    from . import budget as B
    budget = budget_gate(s, node)  # a refusal changes nothing
    if B.node_started(rec) is None:  # the round's wall clock (budgets.node.*.minutes) starts at its first packet
        now = datetime.now(timezone.utc).replace(microsecond=0)
        rec["startedAt"] = {"generation": rec["generation"], "at": now.isoformat()}
        s.save()
        mins = ((budget or {}).get("caps") or {}).get("node", {}) or {}
        if budget is not None and mins.get("minutes"):
            budget["deadline"] = datetime.fromtimestamp(now.timestamp() + mins["minutes"] * 60, timezone.utc).isoformat()
            budget["secondsLeft"] = int(mins["minutes"] * 60)
    tier, escalated = s.tier(node, unit_id)
    exe = s.execution(node, unit_id, tier)
    facts = json.loads(Path(facts_arg).read_text(encoding="utf-8")) if facts_arg else {}
    from . import schema
    schema.validate(facts, "facts.schema.json", f"facts {facts_arg}")
    rules = R.select(R.load_rulepacks(HOME, s.pdir, s.profile, s.ctx), s.ctx, spec.get("rulePhases", []))
    pdir = s.ticket.dir / "packets"
    pdir.mkdir(parents=True, exist_ok=True)
    round_no = 1 + sum(1 for u in rec["units"] if u["unit"] == unit_id and u["generation"] == rec["generation"])
    dispatch = f"{node}.g{rec['generation']}.{unit_id}.r{round_no}"
    rules_path = pdir / f"{dispatch}.rules.json"
    rules_path.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    result_path = s.ticket.dir / "results" / f"{dispatch}.md"
    adapters = _adapters_for(node, spec, resolve_adapters(HOME, s.pdir, s.profile, s.ctx))
    inputs = packet_inputs(s, node, pdir / f"{dispatch}.inputs")
    from . import learn as LS
    lessons = LS.packet_lessons(s, node, facts, rules, pdir / f"{dispatch}.lessons.json")
    signals_path = None
    if node == L.REFLECT:  # the unit reflects on exactly what `sdlc reflect-signals` reported, pinned by digest
        signals_path = pdir / f"{dispatch}.signals.json"
        signals_path.write_text(json.dumps(s.signals(), indent=2, ensure_ascii=False), encoding="utf-8")
        inputs.append({"token": "signals", "from": "sdlc reflect-signals", "file": str(signals_path),
                       "sha256": sha256_file(signals_path)})
    window = exe["contextWindow"]
    logs_dir = s.ticket.dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    card_path = _node_card_path(spec["card"])
    role_path = _role_card_path(tier)
    role_card_text = role_path.read_text(encoding="utf-8").strip() if role_path.is_file() else ""
    template = _packet_template()
    values = {
        "ticket": s.ctx["ticket"], "node": node, "generation": str(rec["generation"]), "unit": unit_id,
        "role": unit["role"], "tier": tier, "host": exe["host"], "model": exe["model"],
        "round": str(round_no), "dispatchId": dispatch, "purpose": spec["purpose"],
        "card": str(card_path), "roleCard": role_card_text,
        "artifacts": "\n".join(f"- `{s.ticket.dir / a}`" for a in s.artifacts(node)) or "- none (findings only)",
        "rulesPath": str(rules_path), "ruleCount": str(len(rules)), "resultPath": str(result_path),
        "adapters": json.dumps(adapters, indent=2, ensure_ascii=False),
        "inputs": NL.join(f"- `{i['file']}` ({i['token']}, from `{i['from']}`, sha256 `{i['sha256'][:12]}`)"
                          + (f" — live round only; full history in `{i['original']}`" if i.get("original") else "")
                          for i in inputs) or "- none (first node, or only the ticket and the repositories)",
        "contextWindow": f"{window:,}", "logsDir": str(logs_dir),
        "checks": _checks_block(s.spec(node)),
        "repositories": _repositories_block(s),
        "weakestClaim": facts.get("weakestClaim", "(controller: name it)"),
        "observed": _bullets(facts.get("observed")), "doubts": _bullets(facts.get("doubts")),
        "settled": _bullets(facts.get("settled")),
        "authorizedPaths": _bullets(facts.get("authorizedPaths")) if node != L.REFLECT else
        f"- `{s.ticket.dir}` only: write `reflection.md` and `proposals.json` there and nothing anywhere else. "
        "Proposals change nothing until the user approves them.",
        "findingsToFix": _bullets(facts.get("findingsToFix")),
        "lessons": lessons["text"],
        "languages": _languages_block(s, node),
        "git": _git_block(s, exe["host"]),
        "driver": driver_block(s.ppath, exe["host"]),
        "timeBudget": _time_budget_block(budget),
    }
    text = template
    for key, val in values.items():
        text = text.replace("{{" + key + "}}", val)
    path = pdir / f"{dispatch}.md"
    path.write_text(text, encoding="utf-8")
    # what the harness hands the unit, in tokens (~4 chars each): the packet (which includes roleCard),
    # its inputs, the rules and the node card
    handed = [path, rules_path, card_path] + [Path(i["file"]) for i in inputs]
    estimate = sum(f.stat().st_size for f in handed if f.is_file()) // 4
    res = {"path": str(path), "packetDigest": sha256_file(path), "agentType": agent_type(tier), "tier": tier,
           "model": exe["model"], "resultPath": str(result_path), "dispatchId": dispatch,
           "roleCard": portable(role_path, HOME), "execution": _public_execution(exe),
           "handedTokens": estimate, "contextWindow": window}
    if escalated:
        res["escalated"] = {"from": unit["tier"], "why": escalated}
    if budget is not None:
        res["budget"] = _budget_brief(budget)
        from . import budget as B
        warn = B.warning(budget)
        if warn:
            res["budgetWarning"] = warn
    if signals_path is not None:
        res["signals"] = str(signals_path)
    if lessons["lessons"] or lessons["learnedRules"]:
        res.update({k: lessons[k] for k in ("lessons", "learnedRules", "lessonsDigest")})
    if lessons["warning"]:
        res["lessonsWarning"] = lessons["warning"]
    if estimate > window // 4:
        res["contextWarning"] = (f"the packet and its inputs are ~{estimate:,} tokens, over a quarter of the {window:,}-token "
                                 f"window assumed for {tier}; trim the inputs or split the unit before dispatching")
    return res, exe


def budget_gate(s: Session, node: str) -> dict[str, Any] | None:
    """G8: refuse a packet while a budget is over (profile `budgets`, harness/budget.py). Returns the check (None
    without a budgets block) so the packet can carry the level and a warning. The refusal is an error only: no
    state change, no event."""
    from . import budget as B
    if not B.enabled(s.profile):
        return None
    events, _ = s.ticket.verify_log()
    rec = s.state["nodes"][node]
    res = B.check(s.ppath, s.ctx["ticket"], node, prof=s.profile, events=events,
                  generation=rec["generation"], started=B.node_started(rec))
    if not res["allowed"]:
        raise HarnessError(B.refusal(res))
    return res


def _budget_brief(res: dict[str, Any]) -> dict[str, Any]:
    return {k: res.get(k) for k in ("level", "allowed", "over", "usage", "caps", "override", "source", "deadline",
                                    "secondsLeft")}


def _time_budget_block(res: dict[str, Any] | None) -> str:
    """The packet's wall-clock line (A4): empty without a minutes cap."""
    if not res or not res.get("deadline"):
        return ""
    mins = ((res.get("caps") or {}).get("node") or {}).get("minutes")
    return (f"\nTime cap: this node's round ends at **{res['deadline']}** (UTC, {mins} minutes from its first packet). "
            "Do not start long work you cannot finish by then; when it passes, stop, write the result file with what is "
            "done and what is left, and hand back. The driver refuses new packets for this node after the cap.\n")


def cmd_budget(args: argparse.Namespace) -> None:
    """`sdlc budget --ticket T [--node N]`: caps, measured usage and level (read-only). With `--allow-node N --reason R
    --actor user`: the user lets that node through G8 for its current round (a budget-override decision event)."""
    from . import budget as B
    s = Session(args)
    node = args.allow_node or args.node or next_pending(s.state, s.order)
    if node is not None and node not in s.order:
        raise HarnessError(f"{node} is not in this ticket's trace: {s.order}")
    if not B.enabled(s.profile):
        if args.allow_node:
            raise HarnessError("G8: this profile turns budgets off (an empty budgets block), so there is nothing to override")
        out({"ok": True, "ticket": s.ctx["ticket"], "node": node, "configured": False, "level": "ok",
             "note": "the profile's budgets block is empty: no budget checks"})
        return
    events, _ = s.ticket.verify_log()
    gen = s.state["nodes"][node]["generation"] if node else None
    res = B.check(s.ppath, s.ctx["ticket"], node, prof=s.profile, events=events, generation=gen,
                  started=B.node_started(s.state["nodes"][node]) if node else None)
    if not args.allow_node:
        out({"ok": True, **res, "warning": B.warning(res), "status": B.line(res)})
        return
    if args.actor != "user":
        raise HarnessError("G8: letting a node through its budget is the user's decision: repeat with --actor user")
    if not (args.reason or "").strip():
        raise HarnessError("--reason is required: say why this node may spend past the budget")
    scopes = args.scope or res["over"]
    if not scopes:
        raise HarnessError(f"G8: {node} is not over any budget ({res['level']}); nothing to override "
                           "(pass --scope ticket|node|daily|all to record one ahead)")
    ev = s.ticket.log({"event": "budget-override", "node": node, "generation": gen, "scopes": sorted(set(scopes)),
                       "reason": args.reason.strip(), "actor": "user",
                       "usage": {k: {"usd": v["usd"], "tokens": v["tokens"]} for k, v in res["usage"].items()}})
    out({"ok": True, "ticket": s.ctx["ticket"], "node": node, "generation": gen, "override": ev["scopes"],
         "note": f"G8 lets {node} through at generation {gen} only; a rerun or loop-back needs a new decision"})


def cmd_packet(args: argparse.Namespace) -> None:
    s = Session(args)
    res, _ = build_packet(s, args.node, args.unit, args.facts)
    out(res)


def cmd_dispatch(args: argparse.Namespace) -> None:
    from .dispatch import run_dispatch
    s = Session(args)
    out(run_dispatch(s, args.node, args.unit, facts=args.facts, runner=args.runner, command=args.command,
                     timeout=args.timeout, dry_run=args.dry_run, no_record=args.no_record))


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


def launcher_cmd(profile_file: Path, host: str | None = None, windows: bool | None = None) -> str:
    """The command a unit runs to call the driver, as display text for packets (never stored in state).

    Windows (PowerShell, where `sh` is not on PATH): `& "<project>\\.sdlc\\bin\\sdlc-<host>.cmd"` when that per-host
    launcher exists, else `sdlc.cmd`, else Python on the bundled `sdlc.py` (user-scope `~/.claude` install).
    POSIX: `sh "<project>/.sdlc/bin/sdlc-<host>"`, else `sh .../sdlc`, else the bundled `sdlc` shim.
    """
    windows = (os.name == "nt") if windows is None else windows
    bin_dir = profile_file.parent / ".sdlc" / "bin"
    bundled = HOME / "skills" / "kaneki-flow" / "scripts"
    names = ([f"sdlc-{host}"] if host and host != "profile" else []) + ["sdlc"]
    if windows:
        def win(p: Path) -> str:
            return str(PureWindowsPath(p.as_posix()))
        shim = next((bin_dir / f"{n}.cmd" for n in names if (bin_dir / f"{n}.cmd").is_file()), None)
        head = f'& "{win(shim)}"' if shim else f'python "{win(bundled / "sdlc.py")}"'
        return f'{head} --profile "{win(profile_file)}"'
    shim = next((bin_dir / n for n in names if (bin_dir / n).is_file()), None)
    return f'sh "{(shim or bundled / "sdlc").as_posix()}" --profile "{profile_file.as_posix()}"'


def posix_launcher_cmd(profile_file: Path, host: str | None = None) -> str:
    """The sh form of the launcher with absolute forward-slash paths: it works in a POSIX shell and in Git Bash / MSYS
    on Windows, where the `.cmd` launcher would go through cmd.exe (and can wait on its console)."""
    bin_dir = profile_file.parent / ".sdlc" / "bin"
    names = ([f"sdlc-{host}"] if host and host != "profile" else []) + ["sdlc"]
    shim = next((bin_dir / n for n in names if (bin_dir / n).is_file()), None)
    target = shim or HOME / "skills" / "kaneki-flow" / "scripts" / "sdlc"
    return f'sh "{target.resolve().as_posix()}" --profile "{profile_file.resolve().as_posix()}"'


def driver_block(profile_file: Path, host: str | None = None, windows: bool | None = None) -> str:
    """The packet's Driver section: the absolute driver command for each shell a unit may run (never a bare `sdlc`,
    which is not on PATH)."""
    windows = (os.name == "nt") if windows is None else windows
    lines = [f"- Bash / sh{' (Git Bash on Windows too)' if windows else ''}: `{posix_launcher_cmd(profile_file, host)}`"]
    if windows:
        lines.append(f"- PowerShell: `{launcher_cmd(profile_file, host, windows=True)}`")
        lines.append("- From Bash never run the `.cmd` launcher (it goes through cmd.exe); use the sh form.")
    return NL.join(lines)


def _languages_block(s: Session, node: str) -> str:
    override = s.ctx.get("locale")
    langs = artifact_languages(s.profile, node, s.artifacts(node), override)
    lines = [f"- `{a}`: **{lang}**" for a, lang in langs.items()] or [f"- findings and notes: **{language(s.profile, 'artifacts', override)}**"]
    lines.append(f"- result file and reply to the controller: **en** (the controller translates for the user)")
    lines.append("- code, identifiers and code comments: follow the repository's existing language (English when unsure)")
    return "\n".join(lines)


def _git_block(s: Session, host: str | None = None) -> str:
    ident = gitid.identity(s.profile)
    cmd = launcher_cmd(s.ppath, host)
    per_repo = [f"- `{r['id']}`: {gitid.identity(s.profile, r['id'])['name']} <{gitid.identity(s.profile, r['id'])['email']}>"
                for r in s.ctx["repositories"] if gitid.identity(s.profile, r["id"]) != ident]
    lines = [f"- Project identity: **{ident['name']} <{ident['email']}>**" + (" (custom SSH command configured)" if ident.get("sshCommand") else ""),
             *per_repo,
             f"- Run **every** git command as: `{cmd} git --ticket {s.ctx['ticket']} --repo <repo-id> -- <git args>` "
             "(from Bash, the sh form of the Driver section in place of the launcher)",
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


def unit_dispatch_id(s: Session, node: str, unit_id: str, result_path: Path | None = None) -> str:
    """The dispatch id (`<node>.g<gen>.<unit>.r<round>`) the next record of this unit closes: the round build_packet
    numbered (1 + the unit's records at this generation), or the result file's own name when it names this unit."""
    rec = s.state["nodes"][node]
    round_no = 1 + sum(1 for u in rec["units"] if u["unit"] == unit_id and u["generation"] == rec["generation"])
    if result_path is not None and result_path.suffix == ".md":
        from .sessions import split_dispatch
        ident = split_dispatch(result_path.stem, s.order)
        if ident and (ident["node"], ident["unit"], ident["generation"]) == (node, unit_id, rec["generation"]):
            return result_path.stem
    return f"{node}.g{rec['generation']}.{unit_id}.r{round_no}"


def measure_dispatch(s: Session, dispatch_id: str) -> dict[str, Any] | None:
    """What a dispatch consumed, measured from its own logs (cost.dispatch_usage); None when no log is readable here."""
    from . import cost
    try:
        return cost.dispatch_usage(s.ppath, s.ctx["ticket"], dispatch_id)
    except Exception:  # noqa: BLE001 — a record never fails on a measurement
        return None


def _measured_fields(meas: dict[str, Any] | None, reported: int | None, abandoned: bool) -> dict[str, Any]:
    """The token fields of a unit record. `tokens` is what the unit consumed, measured by the driver from the unit's own
    log(s) (`tokensSource: measured`); without a readable log it falls back to the agent's `--tokens`
    (`tokensSource: reported`). `contextAtEnd` keeps the `--tokens` value: the agent's last-turn context size.
    When one packet ran in several sessions, every session but the last is listed in `abandonedAttempts`."""
    fields: dict[str, Any] = {"contextAtEnd": reported}
    if meas is None:
        fields.update(tokens=reported, tokensSource="reported" if reported is not None else None)
        return fields
    fields.update(tokens=meas["tokens"], tokensSource="measured",
                  tokensMeasured={k: meas[k] for k in ("input", "cacheWrite", "cacheRead", "output", "usd", "messages")}
                  | {"usdSource": "+".join(meas["sources"]) or None, "sessions": len(meas["attempts"])})
    gone = meas["attempts"] if abandoned else meas["attempts"][:-1]
    if gone and not abandoned:
        fields["abandonedAttempts"] = [{"session": a["session"], "tokens": a["tokens"], "usd": a["usd"],
                                        "first": a["first"], "last": a["last"]} for a in gone]
    return fields


def record_unit(s: Session, node: str, unit_id: str, result_path: Path | None, model: str, *,
                status: str = "ok", tokens: int | None = None, verdict: str | None = None,
                failure_class: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """Record one dispatch of a unit. `status=abandoned` records a dispatch that produced no usable result (a run
    stopped, timed out or given up): no result file, a `reason`, and the tokens it spent, so no unit log is left
    without a record; the next packet of the unit is the next round."""
    s.spec(node)
    rec = s.state["nodes"][node]
    s.unit(node, unit_id)
    abandoned = status == "abandoned"
    if abandoned:
        if not (reason or "").strip():
            raise HarnessError("--reason is required with --status abandoned: say why the dispatch was given up")
        if verdict:
            raise HarnessError("an abandoned dispatch has no verdict")
    elif result_path is None or not result_path.is_file():
        raise HarnessError(f"G7: result file missing: {result_path} (the controller never fills a missing unit)")
    staged = None if abandoned else s.stage_problem(node, unit_id)
    if staged:
        raise HarnessError(staged)
    tier, _ = s.tier(node, unit_id)
    exe = s.execution(node, unit_id, tier)
    accepted, mismatch, warn = H.model_matches(model, exe)
    if not accepted:
        raise HarnessError(f"{node}/{unit_id} is planned on {exe['model']} ({tier}); a {model} record is refused")
    dispatch_id = unit_dispatch_id(s, node, unit_id, result_path)
    have_result = result_path is not None and result_path.is_file()
    entry: dict[str, Any] = {
        "unit": unit_id, "generation": rec["generation"], "model": model, "tier": tier, "status": status,
        "dispatchId": dispatch_id,
        "result": str(result_path) if have_result else None,
        "resultDigest": sha256_file(result_path) if have_result else None,
        **_measured_fields(measure_dispatch(s, dispatch_id), tokens, abandoned),
        "verdict": verdict, "failureClass": failure_class, "at": now(),
    }
    if abandoned:
        entry["reason"] = reason.strip()
    if exe["host"] != "profile":
        entry["host"] = exe["host"]
    if exe.get("effort"):
        entry["effort"] = exe["effort"]
    if model != exe["model"]:
        entry["plannedModel"] = exe["model"]
    if mismatch:
        entry["modelMismatch"] = True
    from . import learn as LS
    round_no = 1 + sum(1 for u in rec["units"] if u["unit"] == unit_id and u["generation"] == rec["generation"])
    # the lesson ids and learned rules this round's packet handed over (build_packet's sidecar), so the retro can
    # measure their effect from the ticket's own log
    entry.update(LS.unit_lessons(s.ticket.dir / "packets" / f"{node}.g{rec['generation']}.{unit_id}.r{round_no}.lessons.json"))
    previous = _last_recorded_unit(s)
    rec["units"].append(entry)
    rec["status"] = "in_progress" if rec["status"] in ("pending", "stale") else rec["status"]
    s.save()
    s.ticket.log({"event": "record", "node": node, **entry})
    host = entry.get("host", "profile")
    if previous is not None and previous.get("host", "profile") != host:
        s.ticket.log({"event": "host-switch", "node": node, "unit": unit_id, "from": previous.get("host", "profile"),
                      "to": host, "previousNode": previous.get("node"), "previousUnit": previous.get("unit")})
    U.write_ticket_file(s.ticket.dir, s.ctx["ticket"], s.order, s.state["nodes"],
                        totals=U.ticket_tokens(s.ppath, s.ctx["ticket"], s.order, s.state["nodes"]))
    payload: dict[str, Any] = {"ok": True, "recorded": entry, "nodeTokens": U.node_usage(rec)["total"]}
    if warn:
        payload["warning"] = warn
    return payload


def _last_recorded_unit(s: Session) -> dict[str, Any] | None:
    """The ticket's most recent `record` event (any node, any generation), from the hash-chained log. A unit
    recorded on the `profile` binding has no `host` key; callers read it as `profile`."""
    events, _ = s.ticket.verify_log()
    return next((e for e in reversed(events) if e.get("event") == "record"), None)


def cmd_record(args: argparse.Namespace) -> None:
    s = Session(args)
    if not args.result and args.status != "abandoned":
        raise HarnessError("--result is required (only --status abandoned records a dispatch without a result)")
    out(record_unit(s, args.node, args.unit, Path(args.result) if args.result else None, args.model,
                    status=args.status, tokens=args.tokens, verdict=args.verdict, failure_class=args.failure_class,
                    reason=args.reason))


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
        attempts = loop_rounds(s, spec["loopBackTo"], rec["attempts"])
        cap = s.profile.get("gates", {}).get("loopCap", 2)
        if attempts >= cap:
            yield (f"G4: {node} NEEDS CHANGES after {attempts} rounds (cap {cap}, counting manual reopens of "
                   f"{spec['loopBackTo']}); ask the user, then repeat with --actor user"), True
        problems = artifact_problems(s, node, "needs-changes", None)
        if problems:
            yield f"G7: {node}: " + "; ".join(problems), False
    elif status == "approved":
        if node in ("fix-plan", "feature-plan", "scope"):
            from . import questions as Q
            high = Q.open_high(s.ticket.dir)
            if high:
                yield (f"G1: {node} cannot close while HIGH questions are open: {high}. Ask them in one batch "
                       f"(`sdlc questions --ticket {s.ctx['ticket']}`), then record each with `sdlc answer ... --actor user`"), False
        planned = [u["id"] for u in s.units(node)]
        done_units = {u["unit"] for u in current if u["status"] == "ok"}
        lacking = [u for u in planned if u not in done_units]
        if lacking:
            yield f"G7: {node} has no ok record for units {lacking} at generation {rec['generation']}", False
        elif spec.get("reviewer"):
            last_rev = [u for u in current if u["unit"] == "reviewer" and u["status"] != "abandoned"][-1]
            if last_rev.get("verdict") == "FAIL":
                yield f"{node}: last reviewer verdict is FAIL ({last_rev.get('failureClass')}); fix and re-review", False
        if "unrecorded" not in memo:
            memo["unrecorded"] = unrecorded_dispatches(s, node)
        if memo["unrecorded"]:
            yield (f"G7: {node} has unit logs with no record at generation {rec['generation']}: "
                   f"{memo['unrecorded']}. Record each dispatch, including one given up: "
                   f"`sdlc record --ticket {s.ctx['ticket']} --node {node} --unit <unit> --model <model> "
                   "--status abandoned --reason '<why>'`"), False
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
            roots, search = citation_roots(s), citation_search_roots(s)
            broken = {a: C.citations(live_text(s.ticket.dir / a), roots, search) for a in s.artifacts(node) if a.endswith(".md")}
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


def unrecorded_dispatches(s: "Session", node: str) -> list[str]:
    """Dispatch ids of this node's current generation that have a unit log (cost.py's exact packet match) but no
    record. Round R of a unit is recorded once the unit has R records at that generation (build_packet's numbering),
    so an abandoned run recorded with `--status abandoned` counts. A re-dispatch of the same packet is one dispatch;
    its extra sessions land in that record's `abandonedAttempts`. [] when the logs cannot be read."""
    from . import cost
    from .sessions import split_dispatch
    rec = s.state["nodes"][node]
    try:
        data = cost.rows(s.ppath, s.ctx["ticket"])
    except Exception:  # noqa: BLE001 — unreadable logs never block a gate
        return []
    found = sorted({r["dispatchId"] for r in data["rows"] if r["ticket"] == s.ctx["ticket"] and r["node"] == node
                    and r["kind"] in ("unit", "dispatch") and r["dispatchId"]})
    out = []
    for did in found:
        ident = split_dispatch(did, s.order)
        if not ident or ident["generation"] != rec["generation"]:
            continue
        recorded = sum(1 for u in rec["units"] if u["unit"] == ident["unit"] and u["generation"] == rec["generation"])
        if recorded < ident["round"]:
            out.append(did)
    return out


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

    event: dict[str, Any] = {"event": "gate", "gate": args.gate, "actor": args.actor, "node": node,
                             "generation": rec["generation"], "verdict": result["verdict"], "note": args.note}
    if args.reopen and args.status == "approved":
        event["reopen"] = args.reopen
    if result.get("citationWarnings"):
        event["citationWarnings"] = result["citationWarnings"]
    if node == L.REFLECT and args.status == "approved":
        # the signals this close covered: a later round (PR review) reflects only on what is new
        event["covered"] = L.new_ids(s.signals())
        if s.reflect_quiet() and not (args.note or "").startswith("no-signal"):
            event["note"] = "no-signal: nothing new went wrong; no unit dispatched" + (f" ({args.note})" if args.note else "")
        result["covered"] = len(event["covered"])
    s.ticket.log(event)
    if args.status == "approved":
        auto = _auto_close_reflect(s, node)
        if auto:
            result["autoClosed"] = auto
    s.save()
    result["tokenUsageFile"] = str(U.write_ticket_file(s.ticket.dir, s.ctx["ticket"], s.order, s.state["nodes"],
                        totals=U.ticket_tokens(s.ppath, s.ctx["ticket"], s.order, s.state["nodes"])))
    result["nextPending"] = next_pending(s.state, s.order)
    out(result)


def _auto_close_reflect(s: "Session", after: str) -> dict[str, Any] | None:
    """learning.reflect on-signal: when reflect is next, ready, has no unit at this generation and nothing new went
    wrong, the driver closes it in the same command (no dispatch, a `no-signal` gate note). Ship never waits on it."""
    if after == L.REFLECT or L.REFLECT not in s.order or s.ctx.get("reflect") != "on-signal":
        return None
    if next_pending(s.state, s.order) != L.REFLECT:
        return None
    rec = s.state["nodes"][L.REFLECT]
    if any(u["generation"] == rec["generation"] for u in rec["units"]) or not s.reflect_quiet():
        return None
    if next(gate_readiness(s, L.REFLECT, "approved", "controller", None), None):
        return None
    note = f"no-signal: nothing new went wrong (closed after {after}); no unit dispatched"
    rec.update({"status": "done", "verdict": "approved", "closedAt": now(), "artifactDigests": {}})
    s.ticket.log({"event": "gate", "gate": "G0", "actor": "controller", "node": L.REFLECT,
                  "generation": rec["generation"], "verdict": "approved", "note": note, "auto": True, "covered": []})
    return {"node": L.REFLECT, "verdict": "approved", "note": note}


def node_rule_ids(s: "Session", node: str) -> list[str]:
    spec = s.spec(node)
    return [r["id"] for r in R.select(R.load_rulepacks(HOME, s.pdir, s.profile, s.ctx), s.ctx, spec.get("rulePhases", []))]


def artifact_problems(s: "Session", node: str, status: str, reopen: str | None) -> list[str]:
    spec = s.spec(node)
    out = []
    ctx: dict[str, Any] = {"ruleIds": node_rule_ids(s, node), "status": status, "reopen": reopen,
                           "ticketDir": s.ticket.dir}
    if any("proposals" in (c.get("parsers") or []) for c in (spec.get("checks") or {}).values()):
        ctx["proposals"] = proposal_context(s)
    for name, check in (spec.get("checks") or {}).items():
        path = s.ticket.dir / name
        if not path.is_file():
            out.append(f"{name} is missing")
            continue
        out += [f"{name}: {p}" for p in C.check_artifact(live_text(path), check, ctx)]
    out += draft_problems(s, node)
    if spec.get("issuesReceipts"):
        out += unreviewed_untracked(s, node)
    if "baseline.json" in s.artifacts(node):
        out += baseline_problems(s)
    return out


def baseline_problems(s: "Session") -> list[str]:
    """B3: prepare leaves a ready-to-build checkout: baseline.json is the driver's record (`sdlc baseline`), with
    every repository installed. Failing tests or lint there are the baseline, not a refusal."""
    f = s.ticket.dir / "baseline.json"
    if not f.is_file():
        return []  # the artifact presence check names it
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return [f"baseline.json is not the driver's record: run `sdlc baseline --ticket {s.ctx['ticket']}`"]
    blocked = {r.get("repo"): r.get("blocked") for r in data.get("repos") or [] if r.get("blocked")}
    if blocked or data.get("ok") is False:
        return [f"baseline.json: install failed {blocked}: coding would be blocked; fix the environment, then rerun "
                "`sdlc baseline`"]
    return []


def unreviewed_untracked(s: "Session", node: str) -> list[str]:
    """B5: untracked files ship with the change (they are part of the receipt's subject). The subject node's review
    must name each one, so a file a tool generated (feature-flags: AGENTS.md and CLAUDE.md written by `next dev`) is
    caught at the node that issues the receipts, not after. Names count by repo-relative path or by file name."""
    from .state import worktree_snapshot
    text = "\n".join(live_text(s.ticket.dir / a) for a in s.artifacts(node)
                     if a.endswith(".md") and (s.ticket.dir / a).is_file())
    out = []
    for r in s.ctx["repositories"]:
        path = r.get("worktree") or r["path"]
        try:
            untracked = worktree_snapshot(path, s.excludes)["untracked"]
        except Exception:  # noqa: BLE001 — git problems are reported by the subject/receipt checks
            continue
        missing = [rel for rel in sorted(untracked) if rel not in text and Path(rel).name not in text]
        if missing:
            out.append(f"{r['id']}: untracked files ship with the change but the review does not name them: {missing}. "
                       "Name each one the change needs; move any other out of the worktree (a tool wrote it) "
                       "before closing")
    return out


def draft_problems(s: "Session", node: str) -> list[str]:
    """B5: the rules kaneki-ship applies, checked where the drafts are written (delivery-drafts), so the ship request
    passes first time: drafts/commit-msg.md against the commit-message rules, drafts/pr-body.md against my-git's PR
    rules (title = the commit header; GH005, GH008 sections, GH009 no AI attribution)."""
    names = s.artifacts(node)
    msg_file, body_file = s.ticket.dir / "drafts" / "commit-msg.md", s.ticket.dir / "drafts" / "pr-body.md"
    out: list[str] = []
    if "drafts/commit-msg.md" in names:
        out += ship_readiness(s)
    message = live_text(msg_file) if "drafts/commit-msg.md" in names and msg_file.is_file() else None
    if message is not None:
        out += [f"drafts/commit-msg.md: {p}" for p in draft_message_problems(s, message)]
    if "drafts/pr-body.md" in names and body_file.is_file():
        out += [f"drafts/pr-body.md: {p}" for p in draft_pr_problems(s, (message or "").strip().splitlines()[0] if (message or "").strip() else "",
                                                                         live_text(body_file))]
    return out


def ship_readiness(s: "Session") -> list[str]:
    """B5: the ship path itself (my-git registered for the project when it is the backend)."""
    from . import mygit
    try:
        backend = SHIP.resolve_backend(s.profile, _delivery(s))
    except HarnessError:
        return []  # draft_message_problems reports a backend problem once
    return mygit.readiness(HOME, s.pdir) if backend == "my-git" else []


def draft_pr_problems(s: "Session", title: str, body: str) -> list[str]:
    from . import mygit
    try:
        backend = SHIP.resolve_backend(s.profile, _delivery(s))
    except HarnessError:
        return []  # draft_message_problems reports the backend problem once
    root = mygit.find_root(HOME) if backend == "my-git" else None
    if root is None or not body.strip():
        return [] if body.strip() else ["empty"]
    return mygit.check_pr(root, title, body)


def proposal_context(s: "Session") -> dict[str, Any]:
    """What the reflect gate checks proposals.json against (learning.proposal_problems)."""
    events, _ = s.ticket.verify_log()
    stacks = {r["stack"] for r in s.profile["repositories"]} | {p.stem for p in (HOME / "adapters" / "stack").glob("*.json")}
    files = {p.relative_to(s.ticket.dir).as_posix() for pat in ("*", "*/*") for p in s.ticket.dir.glob(pat) if p.is_file()}
    return {"ticket": s.ctx["ticket"], "nodes": set(s.graph["nodes"]), "stacks": stacks, "logLines": len(events),
            "roots": citation_roots(s), "ticketFiles": files}


def citation_roots(s: "Session") -> list[Path]:
    """Where a cited `path:line` may live: each repository checkout, the ticket folder, and the project folder (so
    `.sdlc/tickets/<id>/logs/x.log:12` resolves). A bare file name that is unique under these roots also resolves
    (checks.citations)."""
    return [Path(r.get("worktree") or r["path"]) for r in s.ctx["repositories"]] + [s.ticket.dir, s.pdir]


def citation_search_roots(s: "Session") -> list[Path]:
    """Where a bare cited file name is looked up: the repository checkouts and the ticket folder (not the whole
    project folder, which holds other checkouts of the same repository)."""
    return [Path(r.get("worktree") or r["path"]) for r in s.ctx["repositories"]] + [s.ticket.dir]


def check_report(s: "Session", node: str, unit_id: str | None = None) -> dict[str, Any]:
    """`sdlc check`: the gate's G7 content checks of one node as a read-only dry run, for a unit to run before it hands
    back: the unit's result file (and a reviewer's `Verdict:` line), the node's artifacts (presence, live text,
    required sections, `Verdict:` / `Confidence:` / `Reopen:` lines, rule verdicts, the proposals.json schema),
    citations, output language, the commit rule and the repository's commit-message rules for drafts. Record and
    approval checks (unit records, G0 / G4 / G5 asks) are the gate's alone. Nothing is written."""
    spec = s.spec(node)
    problems: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []
    role = None
    dispatch_id = None
    md_files: list[Path] = []
    if unit_id:
        unit = s.unit(node, unit_id)
        role = unit.get("role")
        dispatch_id = unit_dispatch_id(s, node, unit_id)
        result = s.ticket.dir / "results" / f"{dispatch_id}.md"
        checked.append(str(result))
        if not result.is_file():
            problems.append(f"result file missing: {result}")
        else:
            md_files.append(result)
            if role == "reviewer":
                from .dispatch import extract_verdict
                if extract_verdict(result.read_text(encoding="utf-8"))[0] is None:
                    problems.append(f"{result.name}: no `Verdict: PASS | FAIL` (or APPROVED | NEEDS CHANGES) line")
    if role != "reviewer":  # a reviewer reads the artifacts, the worker leaves them
        names = s.artifacts(node)
        checked += [str(s.ticket.dir / a) for a in names]
        absent = [a for a in names if not (s.ticket.dir / a).is_file()]
        if absent:
            problems.append(f"artifacts missing: {absent}")
        stale = [a for a in names if a.endswith(".md") and a not in absent and not live_text(s.ticket.dir / a).strip()]
        if stale:
            problems.append(f"artifacts not rewritten since a rerun superseded them: {stale} (replace the file, or write "
                            f"the new round below its last `{SUPERSEDED_HEADER}` line)")
        problems += [p for p in artifact_problems(s, node, "", None) if not p.endswith(" is missing")]
        md_files += [s.ticket.dir / a for a in names if a.endswith(".md") and a not in absent]
        problems += [f"output language: {a}: {why}" for a, why in language_problems(s, node).items()
                     if a not in absent]
    roots, search = citation_roots(s), citation_search_roots(s)
    for f in md_files:
        broken = C.citations(live_text(f), roots, search)
        if broken:
            (problems if spec.get("subjectNode") else warnings).append(f"{f.name}: citations that do not resolve: {broken}")
    if (spec.get("subjectNode") or spec.get("checksReceipts")) and role != "reviewer":
        try:
            commit = _commit_rule_problem(s)
        except HarnessError as exc:
            commit = str(exc)
        if commit:
            problems.append(commit)
    return {"ok": not problems, "ticket": s.ctx["ticket"], "node": node, "unit": unit_id, "dispatchId": dispatch_id,
            "problems": problems, "warnings": warnings, "checked": checked,
            "note": "the gate also needs each planned unit's record and, where the node says so, the user's approval"}


def draft_message_problems(s: "Session", message: str) -> list[str]:
    """The commit-message rules kaneki-ship will apply to drafts/commit-msg.md (my-git's when that is the backend)."""
    from . import mygit
    if not message.strip():
        return ["empty"]
    try:
        backend = SHIP.resolve_backend(s.profile, _delivery(s))
    except HarnessError as exc:
        return [str(exc)]
    if backend == "my-git":
        root = mygit.find_root(HOME)
        if root is None:
            return ["the ship backend is my-git, but the my-git plugin is not installed (kaneki-onboarding checks it)"]
        return mygit.check_message(root, message)
    return C.commit_message(message)


def cmd_check(args: argparse.Namespace) -> int:
    s = Session(args)
    res = check_report(s, args.node, args.unit)
    out(res)
    return 0 if res["ok"] else 1


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
DATA_ARTIFACTS = ("testcases.json", "evidence.json", "baseline.json")
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
    for name in s.artifacts(node, static=True):
        path = s.ticket.dir / name
        if name in DATA_ARTIFACTS and path.is_file() and not path.is_symlink():
            aside = s.ticket.dir / "superseded" / f"{path.stem}.g{generation - 1}{path.suffix}"
            aside.parent.mkdir(parents=True, exist_ok=True)
            path.replace(aside)  # data cannot carry a SUPERSEDED line: the rerun writes it again
            marked.append(name)
            continue
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


def reopens(s: "Session", node: str) -> int:
    """How often `node` itself was reopened in this round (a review loop-back or a manual invalidate). Never reset by
    later invalidations, so manual reopens count toward the loop cap (D5); a new PR review round starts again at 0."""
    return int((s.state["nodes"].get(node) or {}).get("reopens", 0))


def loop_rounds(s: "Session", target: str, attempts: int = 0) -> int:
    """The round a new reopen of `target` would start: the larger of the reviewer's own count and target's reopens."""
    return max(attempts, reopens(s, target)) + 1


def _invalidate(s: Session, node: str, reason: str, actor: str = "controller") -> list[str]:
    stale = stale_preview(s, node)
    for n in stale:
        rec = s.state["nodes"][n]
        if _never_ran(rec):
            continue  # never ran: nothing to supersede
        rec["status"] = "stale"
        rec["generation"] += 1
        rec["verdict"] = None
        if n == stale[0]:
            rec["reopens"] = rec.get("reopens", 0) + 1
        else:
            rec["attempts"] = 0
        mark_superseded(s, n, node, rec["generation"])
    if any(s.spec(n).get("issuesReceipts") for n in stale):
        s.state["receipts"] = {}
    s.ticket.log({"event": "invalidate", "node": node, "stale": stale, "reason": reason, "actor": actor})
    return stale


def cmd_invalidate(args: argparse.Namespace) -> None:
    s = Session(args)
    if not args.reason:
        raise HarnessError("--reason is required: name what is wrong in the earlier artifact")
    if args.node not in s.state["nodes"]:
        raise HarnessError(f"unknown node {args.node}")
    cap = s.profile.get("gates", {}).get("loopCap", 2)
    rounds = loop_rounds(s, args.node)
    if rounds >= cap and args.actor != "user" and not _never_ran(s.state["nodes"][args.node]):
        raise HarnessError(f"G4: reopening {args.node} would be round {rounds} (loop cap {cap}, counting manual "
                           "reopens); ask the user, then repeat with --actor user")
    stale = _invalidate(s, args.node, args.reason, args.actor)
    kept = [n for n in s.order if n not in stale]
    s.save()
    out({"stale": stale, "kept": kept, "nextPending": next_pending(s.state, s.order)})


def cmd_env(args: argparse.Namespace) -> int:
    """`sdlc env check|up|status|down|exec` (harness/env.py): the harness-owned evidence environment."""
    from . import env as E
    s = Session(args)
    if args.action == "exec":
        argv = list(args.argv or [])
        if argv and argv[0] == "--":
            argv = argv[1:]
        return E.exec_in(s, argv, args.repo)
    if args.action == "check":
        res = E.check(s, args.repo)
    elif args.action == "up":
        res = E.up(s, args.repo, args.timeout)
    elif args.action == "status":
        res = E.status(s)
    else:
        res = E.down(s, args.drop_db)
    out(res)
    return 0 if res.get("ok") else 1


def cmd_evidence(args: argparse.Namespace) -> int:
    """`sdlc evidence run|status` (harness/evidence.py): the driver runs the cases, smoke first, by failure class."""
    from . import evidence as EV
    s = Session(args)
    if args.action == "status":
        run = EV.load_run(s.ticket.dir)
        res = {"ok": True, "ticket": s.ctx["ticket"], "summary": EV.summary(run.get("cases") or {}),
               "stopped": run.get("stopped"), "escalations": run.get("escalations") or [], "cases": run.get("cases") or {}}
        out(res)
        return 0
    if args.action == "helper":
        return _evidence_helper(s, args)
    from . import steps as ST
    tc = EV.load_testcases(s.ticket.dir)
    if args.cases:
        cases = EV.load_cases(Path(args.cases))
    elif tc is not None:
        cases = EV.cases_from_testcases(s.ticket.dir, tc, ST.load_kit(ST.kit_path(s)))
    else:
        raise HarnessError("no testcases.json in the ticket and no --cases <file>")
    if cases:
        res = EV.execute(s.ticket.dir, cases, EV.env_runner(s, args.repo), rerun=args.rerun, deadline=EV.deadline_of(s))
    else:  # every case manual or out of scope: evidence.json records that
        res = {"ok": True, "stopped": None, "escalations": [], "summary": {}, "file": None}
    if tc is not None:
        data = EV.build_evidence(s.ticket.dir, s.ctx["ticket"], tc)
        res["checks"] = EV.evidence_summary(data)
        res["evidenceFile"] = str(s.ticket.dir / "evidence.json")
    out({"ticket": s.ctx["ticket"], **res})
    return 0 if res["ok"] else 1


def _evidence_helper(s: "Session", args: argparse.Namespace) -> int:
    """`sdlc evidence helper --repo R`: copy the stack's evidence() helper into the repository's checkout (C3), once."""
    import shutil
    repo = next((r for r in s.ctx["repositories"] if r["id"] == args.repo), None) if args.repo else s.ctx["repositories"][0]
    if repo is None:
        raise HarnessError(f"unknown repo {args.repo}")
    stack = resolve_adapters(HOME, s.pdir, s.profile, s.ctx)["stacks"][repo["id"]]
    helper = stack.get("evidenceHelper")
    if not helper:
        raise HarnessError(f"G7: stack {stack.get('id')} has no evidenceHelper (adapters/stack/*.json)")
    dest = Path(repo.get("worktree") or repo["path"]) / (args.dest or helper["dest"])
    src = HOME / helper["source"]
    same = dest.is_file() and dest.read_bytes() == src.read_bytes()
    if not same:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    out({"ok": True, "repo": repo["id"], "path": str(dest), "written": not same, "import": helper.get("import"),
         "call": helper.get("call")})
    return 0


def cmd_report(args: argparse.Namespace) -> None:
    """`sdlc report`: the read-only tester notebook from testcases.json + evidence.json (harness/report.py)."""
    from . import report as RP
    s = Session(args)
    out(RP.write(s, Path(args.out) if args.out else None))


def cmd_baseline(args: argparse.Namespace) -> int:
    """`sdlc baseline` (B3): install + lint / unit tests / build on each checkout, recorded in baseline.json."""
    from . import env as E
    s = Session(args)
    stacks = resolve_adapters(HOME, s.pdir, s.profile, s.ctx)["stacks"]
    res = E.baseline(s, stacks, args.timeout)
    out({"ok": res["ok"], "ticket": res["ticket"], "summary": res["summary"], "file": str(s.ticket.dir / E.BASELINE_FILE),
         "blocked": {r["repo"]: r["blocked"] for r in res["repos"] if r.get("blocked")}})
    return 0 if res["ok"] else 1


def cmd_ask(args: argparse.Namespace) -> None:
    """`sdlc ask`: add one question for the user to the ticket's batch (D7, harness/questions.py)."""
    from . import questions as Q
    s = Session(args)
    if args.node not in s.state["nodes"]:
        raise HarnessError(f"unknown node {args.node}")
    row = Q.add(s.ticket.dir, s.ctx["ticket"], s.state, node=args.node, cls=args.cls, severity=args.severity,
                question=args.question, recommendation=args.recommend, why=args.why, options=args.option)
    if not row.get("duplicate"):
        s.ticket.log({"event": "question", "id": row["id"], "node": args.node, "class": row["class"],
                      "severity": row["severity"], "late": row["late"]})
    res = {"ok": True, **row}
    if row.get("late"):
        res["warning"] = ("asked after the plan was approved: decide it from the requirements with citations unless it "
                          "really changes scope; it is reported to reflect as a late question")
    out(res)


def cmd_questions(args: argparse.Namespace) -> None:
    """`sdlc questions`: the open questions as one batch (read-only)."""
    from . import questions as Q
    s = Session(args)
    out({"ok": True, "ticket": s.ctx["ticket"], **Q.batch(s.ticket.dir)})


def cmd_answer(args: argparse.Namespace) -> None:
    from . import questions as Q
    s = Session(args)
    q = Q.answer(s.ticket.dir, s.ctx["ticket"], args.id, text=args.answer, actor=args.actor, adopt=args.adopt)
    s.ticket.log({"event": "answer", "id": q["id"], "status": q["status"], "actor": q["by"], "severity": q["severity"]})
    out({"ok": True, **q, "openHigh": Q.open_high(s.ticket.dir)})


def cmd_worktree(args: argparse.Namespace) -> None:
    s = Session(args)
    repo = next((r for r in s.ctx["repositories"] if r["id"] == args.repo), None)
    if repo is None:
        raise HarnessError(f"unknown repo {args.repo}")
    from . import worktree_hooks
    if args.release:
        # end the in-place git protection of this ticket (the user's call: it lifts the branch/history guard)
        if args.actor != "user":
            raise HarnessError("G0: releasing the in-place git protection is the user's decision: repeat with --actor user")
        checkout = Path(repo.get("worktree") or repo["path"])
        res = worktree_hooks.release(checkout, s.ctx["ticket"])
        s.ticket.log({"event": "worktree", "action": "release", "actor": "user", "repo": args.repo, "path": str(checkout),
                      **res})
        out({"ok": True, "repo": args.repo, "checkout": str(checkout), **res})
        return
    if not args.path:
        raise HarnessError("--path is required (or --release --actor user to end in-place protection)")
    if not Path(args.path).is_dir():
        raise HarnessError(f"G7: worktree path does not exist: {args.path}")
    given = Path(args.path).resolve()
    expected = worktree_path(s.profile, s.pdir, repo["id"], s.ctx["ticket"])
    # the repository itself is allowed (work in place, no worktree); any other folder must be the ticket's
    # slot under paths.worktreeRoot, so worktrees never scatter next to the repositories
    if given != Path(repo["path"]).resolve() and given != expected:
        raise HarnessError(f"G7: worktree must be {expected.as_posix()} (paths.worktreeRoot/<repoId>-<ticket>), "
                           f"not {given.as_posix()}")
    in_place = given == Path(repo["path"]).resolve()
    previous = Path(repo["worktree"]).resolve() if repo.get("worktree") else None
    repo["worktree"] = str(given)
    repo["worktreeRel"] = portable(Path(repo["worktree"]), s.pdir)
    head = gitid.run(repo["worktree"], ["rev-parse", "HEAD"], gitid.identity(s.profile, repo["id"]))
    if head.returncode != 0:
        raise HarnessError(f"G7: worktree is not a git checkout: {repo['worktree']}")
    repo["startHead"] = head.stdout.strip()
    s.save()
    s.ticket.log({"event": "worktree", "repo": args.repo, "path": repo["worktree"]})
    if in_place:  # the repository's own checkout: guarded only while this ticket holds it
        hooks = worktree_hooks.install(given, s.pdir, in_place=True, ticket=s.ctx["ticket"], ticket_dir=s.ticket.dir,
                                       repo_id=repo["id"], base_branch=repo.get("baseBranch"))
    else:
        hooks = worktree_hooks.install(given, s.pdir, base_branch=repo.get("baseBranch"))
        if previous is not None and previous == Path(repo["path"]).resolve():
            hooks["releasedInPlace"] = worktree_hooks.release(previous, s.ctx["ticket"])  # moved off the checkout
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
    for rec in s.state["nodes"].values():
        rec["reopens"] = 0  # a PR review round has its own loop cap
    dest = s.ticket.dir / "reviews" / f"round-{rnd}{src.suffix or '.md'}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    # every PR round is reflected: reflect restarts with the review nodes (when learning.reflect keeps it in the trace)
    restart = ("read-review", "triage-review", "respond-review") + ((L.REFLECT,) if L.REFLECT in s.order else ())
    for node in restart:
        rec = s.state["nodes"].setdefault(node, new_node_record())
        if rec["units"] or rec["status"] == "done":
            rec.update({"status": "pending", "generation": rec["generation"] + 1, "verdict": None, "attempts": 0})
            if node == L.REFLECT:
                mark_superseded(s, node, "handback", rec["generation"])
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
    out(install(HOME, Path(args.target).resolve(), host=args.host, force=args.force,
                dry_run=args.dry_run, with_my_git=args.with_my_git))


def cmd_host(args: argparse.Namespace) -> None:
    ppath = profile_path(args)
    pdir = ppath.parent
    if args.set:
        if args.set not in H.KNOWN_HOSTS:
            raise HarnessError(f"unknown host {args.set!r}; known: {', '.join(H.KNOWN_HOSTS)}")
        written_path, _ = H.write_local_binding(HOME, pdir, args.set, overwrite=True)
        local = H.load_local_binding(pdir)
        out({"ok": True, "host": args.set, "bindingPath": str(written_path), "tiers": (local or {}).get("tiers", {})})
        return
    prof = load_profile(ppath)
    local = H.load_local_binding(pdir)
    active, source = H.active_host(pdir, cli_host=args.host, binding=local)
    res: dict[str, Any] = {
        "activeHost": active or "profile",
        "source": source,
        "localBindingPath": str(H.local_binding_path(pdir)),
        "localBindingExists": H.local_binding_path(pdir).is_file(),
        "tiers": {t: _public_execution(H.resolve_execution(HOME, pdir, prof, "_host", "_host", t, cli_host=args.host))
                  for t in ("light", "routine", "deep")},
    }
    if args.node:
        unit_id = args.unit or "worker"
        graph = G.load_graph(HOME, prof, pdir)
        spec = graph["nodes"].get(args.node)
        if not spec:
            raise HarnessError(f"unknown node {args.node!r}")
        tier = next((u["tier"] for u in spec.get("units", []) if u["id"] == unit_id), None)
        if tier is None and unit_id == "reviewer" and spec.get("reviewer"):
            tier = spec["reviewer"]["tier"]
        if tier is None:
            tier = "routine"
        fake_unit = {"id": unit_id, "tier": tier}
        eff_tier, _ = H.effective_tier(prof, fake_unit, args.node, lambda _: 0, graph["nodes"], [])
        res["unitExecution"] = _public_execution(H.resolve_execution(HOME, pdir, prof, args.node, unit_id, eff_tier, cli_host=args.host))
        res["unitExecution"]["tier"] = eff_tier
    out(res)


def cmd_tickets(args: argparse.Namespace) -> None:
    from . import board
    from .contract import CONTRACT_VERSION
    out({"contractVersion": CONTRACT_VERSION, "tickets": board.list_tickets(profile_path(args), measured=True)})


def ticket_of_path(ppath: Path, path: Path) -> str | None:
    """The ticket a file belongs to (its ticket folder, or a ticket worktree `<repoId>-<ticket>`), or None."""
    prof = load_profile(ppath)
    root = ticket_root(prof, ppath.parent)
    target = path.resolve()
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
    return ticket if ticket and (root / ticket / "state.json").is_file() else None


def cmd_status_of(args: argparse.Namespace) -> None:
    """The ticket a file belongs to (its ticket folder, or a ticket worktree `<repoId>-<ticket>`), as a board summary."""
    from . import board
    ppath = profile_path(args)
    target = Path(args.path).resolve()
    ticket = ticket_of_path(ppath, target)
    if not ticket:
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


def cmd_decide(args: argparse.Namespace) -> int:
    """A person closes a user gate at a terminal: gate-request shown as text, a choice, y/N, then `sdlc gate --actor
    user` run in-process through `main`. Refuses without an interactive terminal (harness/decide.py)."""
    from . import board, decide

    def request(ticket: str, node: str | None) -> dict[str, Any]:
        return board.gate_request(Session(argparse.Namespace(profile=args.profile, ticket=ticket, host=args.host)), node)

    return decide.run(args, request, main)


def cmd_transcripts(args: argparse.Namespace) -> None:
    """Agent sessions per node of one ticket, from the agents' own logs; steps only with --node. Read-only."""
    from . import sessions
    out(sessions.transcripts(profile_path(args), args.ticket, args.node, max_steps=args.max_steps))


def cmd_reflect_signals(args: argparse.Namespace) -> None:
    """What went wrong on a ticket, for the reflect node (one JSON line). Read-only."""
    s = Session(args)
    out(s.signals())


def _learn_session(args: argparse.Namespace) -> Session:
    return Session(argparse.Namespace(profile=args.profile, ticket=args.ticket, host=getattr(args, "host", None)))


def cmd_learn(args: argparse.Namespace) -> int | None:
    """`sdlc learn`: review reflect proposals (people only, D10), the workspace learning store, retro (harness/learn.py)."""
    from . import learn as LS
    ppath = profile_path(args)
    pdir = ppath.parent
    act = args.learn_cmd
    if act == "list":
        prof = load_profile(ppath)
        out(LS.list_learning(pdir, ticket_root(prof, pdir), args.ticket, args.status))
    elif act == "apply":
        out(LS.apply(_learn_session(args), args.id, args.actor, args.note, args.phase, args.severity))
        _refresh_status(args)
    elif act == "reject":
        out(LS.reject(_learn_session(args), args.id, args.actor, args.reason))
        _refresh_status(args)
    elif act == "revert":
        out(LS.revert(pdir, args.id, args.actor, args.reason))
    elif act == "verify":
        if args.accept_current:
            out(LS.accept_current(pdir, args.reason))
        else:
            store = LS.Store(pdir)
            problems = store.problems()
            out({"ok": not problems, "store": str(store.dir), "problems": problems or None})
    elif act == "retro":
        prof = load_profile(ppath)
        out(LS.retro(pdir, ticket_root(prof, pdir), prof, G.load_graph(HOME, prof, pdir), args.since))
    elif act == "review":
        prof = load_profile(ppath)

        def rows() -> list[dict[str, Any]]:
            return LS.list_learning(pdir, ticket_root(prof, pdir), args.ticket, "proposed")["proposals"]

        return LS.review(args, rows, main)
    return None


def cmd_cost(args: argparse.Namespace) -> None:
    """Measured usage and USD from the agents' own logs (harness/cost.py). Read-only for tickets; caches under the
    user config dir. One JSON line with --json, else a short table."""
    from . import cost
    if args.init_prices:
        out(cost.init_prices())
        return
    if not args.ticket and not args.since:
        raise HarnessError("cost needs --ticket T or --since DATE (or --init-prices)")
    if args.node and not args.ticket:
        raise HarnessError("--node needs --ticket")
    if args.until and not args.since:
        raise HarnessError("--until needs --since")
    if args.ticket:
        rep = cost.ticket_report(profile_path(args), args.ticket, args.node, since=args.since, until=args.until)
    else:
        rep = cost.range_report(profile_path(args), args.since, args.until)
    if args.json:
        out(rep)
    else:
        print(cost.render_text(rep))


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


def _runner(args: argparse.Namespace) -> Any:
    from .runner import Runner
    return Runner(profile_path(args))


def cmd_run(args: argparse.Namespace) -> None:
    """Run the ticket's next node(s) unattended in a fresh agent session each (harness/runner.py), or --stop it."""
    r = _runner(args)
    if args.stop:
        out(r.stop(args.ticket))
        return
    host = args.host if args.host and args.host != "profile" else None
    out(r.run(args.ticket, max_nodes=args.max_nodes, host=host, trigger=args.trigger))


def cmd_tick(args: argparse.Namespace) -> None:
    host = args.host if args.host and args.host != "profile" else None
    out(_runner(args).tick(host=host, foreground=args.foreground, recover=not args.no_recover))


def cmd_recover(args: argparse.Namespace) -> None:
    out(_runner(args).recover(restart=not args.no_restart, foreground=args.foreground))


def cmd_autonomy(args: argparse.Namespace) -> None:
    out(_runner(args).autonomy(args.ticket, args.set, args.reason))


def cmd_unpark(args: argparse.Namespace) -> None:
    out(_runner(args).unpark(args.ticket, args.reason))


def cmd_schedule(args: argparse.Namespace) -> int | None:
    """Scheduled tick / recover (harness/schedule.py): Windows Task Scheduler, or crontab on macOS/Linux."""
    from . import schedule as SCH
    ppath = profile_path(args)
    if args.run_pass:
        return SCH.main_pass(ppath, args.run_pass)
    if args.install:
        out(SCH.install(ppath, every=args.every, dry_run=args.dry_run, write=args.write, platform=args.platform,
                        show_console=args.show_console))
    elif args.uninstall:
        out(SCH.uninstall(ppath, platform=args.platform))
    else:
        out(SCH.status(ppath, platform=args.platform))
    return None


def cmd_digest(args: argparse.Namespace) -> None:
    if args.file and args.path_opt and Path(args.file) != Path(args.path_opt):
        raise HarnessError(f"two different files: {args.file} and --path {args.path_opt}")
    if not (args.file or args.path_opt):
        raise HarnessError("name the file: sdlc digest <path> [--expect <sha256>]")
    path = Path(args.file or args.path_opt)
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
    common.add_argument("--host", choices=[*H.KNOWN_HOSTS, "profile"],
                        help="agent host binding override (default: $SDLC_HOST, .sdlc/host.local.json, or profile)")
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

    x = sub.add_parser("dispatch", parents=[ticketed],
                       help="build packet, spawn an external CLI runner, verify output, and record the unit "
                            "(refused for host antigravity: its units run on its own subagents)")
    x.add_argument("--node", required=True)
    x.add_argument("--unit", required=True)
    x.add_argument("--facts", help="facts JSON passed to packet")
    x.add_argument("--runner", choices=["codex", "claude", "gemini", "opencode"],
                   help="override runner CLI template from .sdlc/host.local.json")
    x.add_argument("--command", help="custom command template: a JSON array of strings, or a string split shell-style (backslashes kept on "
                        "Windows); placeholders {prompt},{packet},{result},{model},{effort},{tier}")
    x.add_argument("--timeout", type=int, default=900, help="subprocess timeout in seconds (default: 900)")
    x.add_argument("--dry-run", action="store_true", help="render packet and print resolved argv without executing")
    x.add_argument("--no-record", action="store_true", help="execute subprocess without calling sdlc record")
    x.set_defaults(fn=cmd_dispatch)

    x = sub.add_parser("record", parents=[ticketed], help="record one unit result (worker or reviewer)")
    x.add_argument("--node", required=True)
    x.add_argument("--unit", required=True)
    x.add_argument("--result", help="the unit's result file (required unless --status abandoned)")
    x.add_argument("--model", required=True)
    x.add_argument("--status", default="ok", choices=["ok", "blocked", "partial", "abandoned"],
                   help="abandoned: a dispatch given up without a usable result (needs --reason); its tokens are recorded")
    x.add_argument("--reason", help="why the dispatch was abandoned")
    x.add_argument("--tokens", type=int, help="the agent's own last-turn context size (kept as contextAtEnd); the driver "
                                              "measures what the unit consumed from its log")
    x.add_argument("--verdict", choices=["PASS", "FAIL", "APPROVED", "NEEDS CHANGES"])
    x.add_argument("--failure-class", choices=["record", "artifact"])
    x.set_defaults(fn=cmd_record)

    x = sub.add_parser("check", parents=[ticketed],
                       help="the gate's content checks of a node as a read-only dry run (run it before handing back)")
    x.add_argument("--node", required=True)
    x.add_argument("--unit", help="also check this unit's result file of its open round")
    x.set_defaults(fn=cmd_check)

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
    x.add_argument("--actor", default="controller", choices=["controller", "user"],
                   help="user: a person decided the reopen (needed past the loop cap, G4)")
    x.set_defaults(fn=cmd_invalidate)

    x = sub.add_parser("env", parents=[ticketed], help="the harness-owned evidence environment: check (preflight), "
                                                        "up, status, down, exec -- <cmd>")
    x.add_argument("action", choices=["check", "up", "status", "down", "exec"])
    x.add_argument("--repo", help="the repository whose worktree runs the app (default: environment.evidence.repo)")
    x.add_argument("--timeout", type=int, default=900, help="seconds per migrate / seed command")
    x.add_argument("--drop-db", action="store_true", help="down: also drop the ticket's database")
    x.add_argument("argv", nargs="*", help="exec: the command to run with the env loaded, after `--`")
    x.set_defaults(fn=cmd_env)

    x = sub.add_parser("evidence", parents=[ticketed], help="run evidence cases (smoke first, resumable, failure "
                                                             "classes, breaker) or show the last run")
    x.add_argument("action", choices=["run", "status", "helper"])
    x.add_argument("--cases", help="run: JSON file {\"cases\": [{\"id\", \"surface\", \"command\", \"smoke\"?}]} "
                                   "(default: the ticket's testcases.json)")
    x.add_argument("--dest", help="helper: where to write it in the checkout (default: the stack adapter's)")
    x.add_argument("--rerun", nargs="*", default=[], help="case ids to run again (after a step fix)")
    x.add_argument("--repo", help="the repository whose worktree runs the cases (default: environment.evidence.repo)")
    x.set_defaults(fn=cmd_evidence)

    x = sub.add_parser("report", parents=[ticketed], help="the read-only tester notebook (tester-notebook.html) from "
                                                           "testcases.json and evidence.json")
    x.add_argument("--out", help="write the page here (default: <ticket>/tester-notebook.html)")
    x.set_defaults(fn=cmd_report)

    x = sub.add_parser("baseline", parents=[ticketed], help="install, then lint / unit tests / build on each checkout "
                                                             "as the ticket found it (baseline.json)")
    x.add_argument("--timeout", type=int, help="seconds per command (default environment.baseline.timeoutSeconds or 900)")
    x.set_defaults(fn=cmd_baseline)

    x = sub.add_parser("ask", parents=[ticketed], help="add a question for the user to the ticket's one batch (D7)")
    x.add_argument("--node", required=True, help="the node that needs the answer")
    x.add_argument("--class", dest="cls", required=True, choices=["business", "infra", "critical-logic", "judgeable"],
                   help="judgeable is refused: a judge decides those with citations")
    x.add_argument("--severity", required=True, choices=["HIGH", "LOW"])
    x.add_argument("--question", required=True)
    x.add_argument("--recommend", required=True, help="the recommended answer (asked first)")
    x.add_argument("--why", help="what changes with the answer")
    x.add_argument("--option", action="append", help="another option (repeatable)")
    x.set_defaults(fn=cmd_ask)

    x = sub.add_parser("questions", parents=[ticketed], help="the open questions as one batch for the user (read-only)")
    x.set_defaults(fn=cmd_questions)

    x = sub.add_parser("answer", parents=[ticketed], help="record the user's answer to a question (or adopt a LOW one)")
    x.add_argument("--id", required=True)
    x.add_argument("--answer")
    x.add_argument("--adopt", action="store_true", help="take the recommendation (LOW: controller; HIGH: --actor user)")
    x.add_argument("--actor", default="controller", choices=["controller", "user"])
    x.set_defaults(fn=cmd_answer)

    x = sub.add_parser("worktree", parents=[ticketed], help="bind a repository to the ticket worktree (installs the git-safety hooks)")
    x.add_argument("--repo", required=True)
    x.add_argument("--path", help="the ticket's worktree slot, or the repository itself to work in place")
    x.add_argument("--release", action="store_true",
                   help="end this ticket's in-place git protection of the checkout (needs --actor user)")
    x.add_argument("--actor", default="controller", choices=["controller", "user"])
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

    x = sub.add_parser("install", help="install kaneki playbooks/skills and the `.sdlc/bin` driver launchers for a host")
    x.add_argument("--target", required=True, help="project folder (or ~/.claude when --host claude)")
    x.add_argument("--host", choices=list(H.KNOWN_HOSTS), required=True,
                   help="target AI host; run install once per host to set up several agents in one workspace")
    x.add_argument("--with-my-git", action="store_true",
                   help="also register the bundled my-git plugin (only valid with --host claude)")
    x.add_argument("--force", action="store_true", help="also overwrite installed files and host binding you edited")
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

    x = sub.add_parser("decide", parents=[common],
                       help="close a user gate yourself at a terminal: pick, read, choose, confirm (people only, needs a TTY)")
    x.add_argument("--ticket", help="the ticket (default: pick from the tickets waiting on you)")
    x.add_argument("--node", help="the node (default: the one the ticket waits on)")
    x.add_argument("--path", help="infer the ticket from a file inside it (editor tasks); falls back to the picker")
    x.set_defaults(fn=cmd_decide)

    x = sub.add_parser("transcripts", parents=[ticketed],
                       help="agent sessions per node from Claude/Codex/Antigravity logs and dispatch logs (read-only)")
    x.add_argument("--node", help="one node, with the steps of each session")
    x.add_argument("--max-steps", type=_non_negative, default=200, help="steps kept per session (first + most recent)")
    x.set_defaults(fn=cmd_transcripts)

    x = sub.add_parser("reflect-signals", parents=[ticketed],
                       help="what went wrong on the ticket (loop-backs, escalations, gate firings, PR findings, ...) "
                            "for the reflect node (read-only)")
    x.set_defaults(fn=cmd_reflect_signals)

    x = sub.add_parser("cost", parents=[common],
                       help="measured tokens and USD per ticket/node from the agents' own logs (read-only)")
    x.add_argument("--ticket", help="one ticket")
    x.add_argument("--node", help="one node of --ticket")
    x.add_argument("--since", help="first local day (YYYY-MM-DD, today, yesterday); without --ticket: every ticket")
    x.add_argument("--until", help="last local day, inclusive (default: --since and later)")
    x.add_argument("--json", action="store_true", help="one JSON line instead of the table")
    x.add_argument("--init-prices", action="store_true",
                   help="write a price-table template (null prices, unverified) to the user config if missing")
    x.set_defaults(fn=cmd_cost)

    x = sub.add_parser("budget", parents=[ticketed],
                       help="budget caps, measured usage and level of a ticket (read-only); --allow-node lets one node "
                            "through G8 (--actor user)")
    x.add_argument("--node", help="node to check (default: next pending)")
    x.add_argument("--allow-node", help="the user lets this node through G8 for its current round")
    x.add_argument("--scope", action="append", choices=["ticket", "node", "daily", "all"],
                   help="scope(s) the override covers (default: the scopes over now)")
    x.add_argument("--reason")
    x.add_argument("--actor", default="controller", choices=["controller", "user"])
    x.set_defaults(fn=cmd_budget)

    x = sub.add_parser("learn", help="review reflect proposals (people only), lessons, learned rules, retro")
    lsub = x.add_subparsers(dest="learn_cmd", required=True)
    y = lsub.add_parser("list", parents=[common], help="proposals of every ticket joined with the learning log, and lessons")
    y.add_argument("--ticket")
    y.add_argument("--status", default="proposed", choices=["proposed", "applied", "rejected", "all"])
    y.set_defaults(fn=cmd_learn)
    y = lsub.add_parser("apply", parents=[ticketed], help="apply one proposal (people only: --actor user)")
    y.add_argument("--id", required=True, help="proposal id in the ticket's proposals.json")
    y.add_argument("--actor", required=True, help="must be `user`: a person approved it (D10)")
    y.add_argument("--note")
    y.add_argument("--phase", choices=list(R.PHASES), help="rule proposals: the review phase (default: from scope nodes)")
    y.add_argument("--severity", default="medium", choices=["critical", "high", "medium", "low"],
                   help="rule proposals: severity of the learned rule (default medium, non-blocking)")
    y.set_defaults(fn=cmd_learn)
    y = lsub.add_parser("reject", parents=[ticketed], help="reject one proposal (people only: --actor user)")
    y.add_argument("--id", required=True)
    y.add_argument("--actor", required=True)
    y.add_argument("--reason", required=True)
    y.set_defaults(fn=cmd_learn)
    y = lsub.add_parser("revert", parents=[common], help="retire a lesson (and remove its learned rule); --actor user")
    y.add_argument("--id", required=True, help="lesson id (<ticket>-<proposal id>)")
    y.add_argument("--actor", required=True)
    y.add_argument("--reason", required=True)
    y.set_defaults(fn=cmd_learn)
    y = lsub.add_parser("review", parents=[common],
                        help="walk pending proposals at a terminal: apply / reject / skip (TTY only)")
    y.add_argument("--ticket")
    y.set_defaults(fn=cmd_learn)
    y = lsub.add_parser("retro", parents=[common],
                        help="read-only effect report of active lessons and learned rules (never retires anything)")
    y.add_argument("--since", help="only tickets started on or after this day (YYYY-MM-DD)")
    y.set_defaults(fn=cmd_learn)
    y = lsub.add_parser("verify", parents=[common], help="check the learning store; --accept-current after a hand edit")
    y.add_argument("--accept-current", action="store_true")
    y.add_argument("--reason")
    y.set_defaults(fn=cmd_learn)

    x = sub.add_parser("contract", help="the data contract version the desktop app checks (read-only, no profile)")
    x.set_defaults(fn=cmd_contract)

    x = sub.add_parser("web", parents=[common], help="local live board of every ticket (127.0.0.1, read-only); Ctrl-C stops it")
    x.add_argument("--port", type=int, default=8765)
    x.add_argument("--open", action="store_true", help="open it in the browser")
    x.set_defaults(fn=cmd_web)

    x = sub.add_parser("zed-setup", help="add kaneki tasks (board, tickets, status, decide, review learning) to <target>/.zed/tasks.json")
    x.add_argument("--target", required=True, help="the folder you open in Zed (the workspace with sdlc.profile.json)")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_zed_setup)

    x = sub.add_parser("digest", parents=[common], help="sha256 of a file (never prints content): sdlc digest <path>")
    x.add_argument("file", nargs="?", help="the file (or --path)")
    x.add_argument("--path", dest="path_opt", help="the file (same as the positional argument)")
    x.add_argument("--expect", help="fail unless the sha256 is this one")
    x.set_defaults(fn=cmd_digest)

    x = sub.add_parser("run", parents=[ticketed],
                       help="run the ticket's next node unattended (claude --bg or codex exec, one node per fresh session); "
                            "parks at user gates; never Antigravity")
    x.add_argument("--max-nodes", type=int, default=1, help="nodes to run one after another (default 1)")
    x.add_argument("--stop", action="store_true", help="stop the running node's session and release the run lease")
    x.add_argument("--trigger", default="manual", choices=["manual", "tick", "recover"], help=argparse.SUPPRESS)
    x.set_defaults(fn=cmd_run)

    x = sub.add_parser("tick", parents=[common],
                       help="one scheduler pass: recover, then start at most one node per opted-in ticket "
                            "(maxConcurrent in .sdlc/runner.local.json)")
    x.add_argument("--foreground", action="store_true", help="run the nodes in this process instead of detached runs")
    x.add_argument("--no-recover", action="store_true", help="skip the recover pass")
    x.set_defaults(fn=cmd_tick)

    x = sub.add_parser("recover", parents=[common],
                       help="release leases whose runner is gone; restart orphaned nodes once (opted-in tickets)")
    x.add_argument("--no-restart", action="store_true", help="only release / park, never restart")
    x.add_argument("--foreground", action="store_true", help="restart in this process instead of a detached run")
    x.set_defaults(fn=cmd_recover)

    x = sub.add_parser("autonomy", parents=[ticketed],
                       help="show or set whether tick may run this ticket unattended (off until you turn it on)")
    x.add_argument("--set", choices=["on", "off"])
    x.add_argument("--reason")
    x.set_defaults(fn=cmd_autonomy)

    x = sub.add_parser("unpark", parents=[ticketed], help="let the runner continue a parked ticket")
    x.add_argument("--reason")
    x.set_defaults(fn=cmd_unpark)

    x = sub.add_parser("schedule", parents=[common],
                       help="run tick every N minutes and recover at logon: Windows Task Scheduler (per user, no "
                            "admin) or crontab lines on macOS/Linux; --status, --uninstall")
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true", help="create the scheduled tick and recover for this workspace")
    g.add_argument("--status", action="store_true", help="whether they exist, next / last run, last log lines")
    g.add_argument("--uninstall", action="store_true", help="remove only the tasks / crontab lines this created")
    g.add_argument("--pass", dest="run_pass", choices=["tick", "recover"], help=argparse.SUPPRESS)
    x.add_argument("--every", type=int, default=15, help="minutes between ticks (default 15)")
    x.add_argument("--dry-run", action="store_true", help="print the exact commands / crontab lines, change nothing")
    x.add_argument("--write", action="store_true", help="macOS/Linux: also add the lines to your crontab")
    x.add_argument("--platform", choices=["windows", "posix"], help="default: this machine's")
    x.add_argument("--show-console", action="store_true",
                   help="Windows: run the wrapper directly instead of through conhost.exe --headless (a console "
                        "window then flashes at each pass)")
    x.set_defaults(fn=cmd_schedule)

    x = sub.add_parser("host", parents=[common], help="show or set the local agent host binding (.sdlc/host.local.json)")
    x.add_argument("--set", choices=list(H.KNOWN_HOSTS), help="write/switch .sdlc/host.local.json to this host preset")
    x.add_argument("--node", help="also show resolved execution for this graph node")
    x.add_argument("--unit", help="unit id within --node (default: worker)")
    x.set_defaults(fn=cmd_host)
    return p


READ_ONLY = {"status", "plan", "check", "validate", "digest", "host", "onboard-scan", "onboard-verify", "onboard-write", "install",
             "tickets", "status-of", "web", "zed-setup", "contract", "ticket-detail", "gate-request",
             "transcripts", "cost", "budget", "reflect-signals", "questions",
             "env", "baseline", "evidence", "report",  # own files only (env.json, baseline.json); no lock while a server starts or tests run
             "learn",  # the workspace learning store has its own lock; apply/reject refresh STATUS.md themselves
             "decide"}  # decide takes the lock only for the gate it runs (through main), never while it waits for you
NO_STATUS_REFRESH = READ_ONLY | {"git"}
# Commands that take the ticket lock themselves, only around each write: a run lasts as long as its node, and the
# agent's own `sdlc` calls need the lock meanwhile (harness/runner.py).
SELF_LOCKING = {"run", "tick", "recover"}


def _ticket_lock(args: argparse.Namespace):
    """One writer per ticket: every command that changes a ticket holds its lock for the whole read-modify-write."""
    from contextlib import nullcontext
    if args.cmd in SELF_LOCKING:
        return nullcontext()
    if ((args.cmd in READ_ONLY and not (args.cmd == "status" and args.accept_current_state))
            and not (args.cmd == "budget" and args.allow_node)
            or not getattr(args, "ticket", None) or (args.cmd == "lang" and not (args.set or args.clear))):
        return nullcontext()
    try:
        prof = load_profile(profile_path(args))
    except HarnessError:
        return nullcontext()  # the command itself reports the profile error
    return Ticket(ticket_root(prof, profile_path(args).parent), args.ticket).lock(args.cmd)


GLOBAL_OPTIONS = ("--profile", "--host")


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 — argparse keeps subparsers only here
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    return {}


def hoist_global_options(parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """`sdlc --profile P [--host H] <cmd> [<sub>] ...` → `sdlc <cmd> [<sub>] --profile P [--host H] ...`.

    Launchers and packets put the profile before the command (`<launcher> --profile P git ...`), but argparse only
    knows these options on each subcommand. Leading global options move behind the (sub)command; a command that does
    not take one (contract, install ...) simply does not get it."""
    lead: list[list[str]] = []
    i = 0
    while i < len(argv) and argv[i].split("=", 1)[0] in GLOBAL_OPTIONS:
        if "=" in argv[i]:
            lead.append([argv[i]])
            i += 1
        elif i + 1 < len(argv):
            lead.append(argv[i:i + 2])
            i += 2
        else:
            break
    if not lead or i >= len(argv):
        return argv
    head, rest = [argv[i]], argv[i + 1:]
    target = _subcommands(parser).get(argv[i])
    if target is None:
        return argv  # let argparse name the unknown command
    nested = _subcommands(target)
    if nested and rest and rest[0] in nested:  # sdlc learn list ...
        head.append(rest[0])
        target, rest = nested[rest[0]], rest[1:]
    takes = target._option_string_actions  # noqa: SLF001
    moved = [tok for pair in lead if pair[0].split("=", 1)[0] in takes for tok in pair]
    return head + moved + rest


def main(argv: list[str] | None = None) -> int:
    # one JSON line in UTF-8 whatever the console code page (Windows defaults to cp1252 on a pipe)
    if hasattr(sys.stdout, "reconfigure") and (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(hoist_global_options(parser, sys.argv[1:] if argv is None else list(argv)))
    try:
        with _ticket_lock(args):
            rc = args.fn(args)
            if ((args.cmd not in NO_STATUS_REFRESH or (args.cmd == "budget" and args.allow_node))
                    and getattr(args, "ticket", None)):
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
        board.write_status(Session(argparse.Namespace(profile=args.profile, ticket=args.ticket,
                                                       host=getattr(args, "host", None))))
    except (HarnessError, OSError):
        pass


if __name__ == "__main__":
    sys.exit(main())
