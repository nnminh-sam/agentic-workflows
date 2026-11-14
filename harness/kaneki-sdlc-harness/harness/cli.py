"""`sdlc` driver. Every command prints one JSON object and exits 0, or prints {"error": ...} and exits 2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import checks as C
from . import ship as SHIP
from . import gitid
from . import graph as G
from . import onboard as OB
from . import rules as R
from .errors import HarnessError
from .profile import (LOCALE_KEYS, _abs, artifact_languages, build_context, language, language_source, load_profile,
                      parse_lang_settings, resolve_adapters, worktree_path, worktree_root, worktree_root_errors)
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
        return self.graph["nodes"][node]

    def artifacts(self, node: str) -> list[str]:
        names = []
        for name in self.spec(node).get("artifacts", []):
            if name == "@delivery.drafts":
                names += [f"drafts/{d}.md" for d in self.profile["delivery"].get("drafts", [])]
            else:
                names.append(name)
        return names

    def model(self, tier: str) -> str:
        return self.profile["models"][tier]

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
    override = parse_lang_settings(args.lang)
    if override:
        ctx["locale"] = override
    graph = G.load_graph(HOME, profile, path.parent)
    resolve_adapters(HOME, path.parent, profile, ctx)  # fail-closed now, not at node 6 (G7)
    wt_errors = worktree_root_errors(profile, path.parent)
    if wt_errors:
        raise HarnessError("G7: " + "; ".join(wt_errors))
    order = G.trace(graph, ctx)
    for repo in ctx["repositories"]:
        head = gitid.run(repo["path"], ["rev-parse", "HEAD"], gitid.identity(profile, repo["id"]))
        if head.returncode != 0:
            raise HarnessError(f"G7: {repo['id']}: not a git checkout with a commit: {repo['path']}")
        repo["startHead"] = head.stdout.strip()
    state = {"schemaVersion": 1, "graphId": graph["graphId"], "profilePath": portable(path, ticket.dir), "createdAt": now(),
             "context": ctx, "nodes": {n: new_node_record() for n in order}, "receipts": {}}
    ticket.save(state)
    ticket.log({"event": "init", "kind": args.kind, "families": ctx["families"], "surfaces": ctx["surfaces"],
                **({"locale": override} if override else {})})
    out({"ok": True, "ticketDir": str(ticket.dir), "trace": order, "nextPending": order[0]})


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
    res = {"ticket": s.ctx["ticket"], "kind": s.ctx["kind"], "reviewRound": s.ctx["reviewRound"],
           "settled": sum(1 for n in s.order if settled(s.state, n)), "traceLength": len(s.order),
           "nextPending": nxt, "tokensTotal": tokens_total}
    if nxt:
        res["nextMissing"] = missing_prerequisites(s.graph, s.order, s.state, nxt)
    if not args.compact:
        res["nodes"] = rows
    out(res)


def cmd_plan(args: argparse.Namespace) -> None:
    s = Session(args)
    node = args.node or next_pending(s.state, s.order)
    if node is None:
        out({"done": True})
        return
    spec = s.spec(node)
    adapters = resolve_adapters(HOME, s.pdir, s.profile, s.ctx)
    rules = R.select(R.load_rulepacks(HOME, s.pdir, s.profile, s.ctx), s.ctx, spec.get("rulePhases", []))
    units = [{**u, "model": s.model(u["tier"]), "agentType": "kaneki-deep" if u["tier"] == "deep" else "kaneki-worker"}
             for u in spec.get("units", [])]
    if spec.get("reviewer"):
        tier = spec["reviewer"]["tier"]
        units.append({"id": "reviewer", "role": "reviewer", "tier": tier, "model": s.model(tier),
                      "agentType": "kaneki-deep" if tier == "deep" else "kaneki-worker"})
    rec = s.state["nodes"][node]
    plan = {
        "node": node, "layer": spec["layer"], "purpose": spec["purpose"], "generation": rec["generation"],
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
    if node in ("prepare",):
        picked["tracker"] = adapters["tracker"]["_path"]
        picked["stacks"] = {k: v["_path"] for k, v in adapters["stacks"].items()}
    if spec.get("mutability") == "source" or node in ("code-review", "final-review", "investigate"):
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
    unit_ids = [u["id"] for u in spec.get("units", [])] + (["reviewer"] if spec.get("reviewer") else [])
    if args.unit not in unit_ids:
        raise HarnessError(f"{args.node} has no unit {args.unit}; planned: {unit_ids}")
    unit = next((u for u in spec["units"] if u["id"] == args.unit), {"id": "reviewer", "role": "reviewer", "tier": spec.get("reviewer", {}).get("tier", "deep")})
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
    template = (HOME / "skills" / "kaneki-flow" / "scripts" / "packet-template.md").read_text(encoding="utf-8")
    values = {
        "ticket": s.ctx["ticket"], "node": args.node, "generation": str(rec["generation"]), "unit": args.unit,
        "role": unit["role"], "round": str(round_no), "dispatchId": dispatch, "purpose": spec["purpose"],
        "card": str(HOME / "skills" / "kaneki-flow" / spec["card"]),
        "artifacts": "\n".join(f"- `{s.ticket.dir / a}`" for a in s.artifacts(args.node)) or "- none (findings only)",
        "rulesPath": str(rules_path), "ruleCount": str(len(rules)), "resultPath": str(result_path),
        "adapters": json.dumps(adapters, indent=2, ensure_ascii=False),
        "inputs": NL.join(f"- `{i['file']}` ({i['token']}, from `{i['from']}`, sha256 `{i['sha256'][:12]}`)"
                          for i in node_inputs(s, args.node)) or "- none (first node, or only the ticket and the repositories)",
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
    out({"path": str(path), "packetDigest": sha256_file(path), "agentType": "kaneki-deep" if unit["tier"] == "deep" else "kaneki-worker",
         "model": s.model(unit["tier"]), "resultPath": str(result_path), "dispatchId": dispatch})


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
    unit_ids = [u["id"] for u in spec.get("units", [])] + (["reviewer"] if spec.get("reviewer") else [])
    if args.unit not in unit_ids:
        raise HarnessError(f"{args.node} has no unit {args.unit}")
    result = Path(args.result)
    if not result.is_file():
        raise HarnessError(f"G7: result file missing: {result} (the controller never fills a missing unit)")
    tier = next((u["tier"] for u in spec.get("units", []) if u["id"] == args.unit), spec.get("reviewer", {}).get("tier"))
    if args.model != s.model(tier):
        raise HarnessError(f"{args.node}/{args.unit} is planned on {s.model(tier)} ({tier}); a {args.model} record is refused")
    entry = {"unit": args.unit, "generation": rec["generation"], "model": args.model, "status": args.status,
             "result": str(result), "resultDigest": sha256_file(result), "tokens": args.tokens,
             "verdict": args.verdict, "failureClass": args.failure_class, "at": now()}
    rec["units"].append(entry)
    rec["status"] = "in_progress" if rec["status"] in ("pending", "stale") else rec["status"]
    s.save()
    s.ticket.log({"event": "record", "node": args.node, **entry})
    out({"ok": True, "recorded": entry})


def cmd_gate(args: argparse.Namespace) -> None:
    s = Session(args)
    node = args.node
    spec = s.spec(node)
    rec = s.state["nodes"][node]
    if rec["status"] == "done":
        raise HarnessError(f"{node} already closed at generation {rec['generation']} (gate calls are not re-asked)")
    missing = missing_prerequisites(s.graph, s.order, s.state, node)
    if missing:
        raise HarnessError(f"G7: {node} is not ready; missing {missing}")
    cap = s.profile.get("gates", {}).get("loopCap", 2)
    current = [u for u in rec["units"] if u["generation"] == rec["generation"]]
    result: dict[str, Any] = {"node": node}

    if args.status == "needs-changes":
        back = spec.get("loopBackTo")
        if not back:
            raise HarnessError(f"{node} has no loop-back; use rejected + invalidate")
        attempts = rec["attempts"] + 1
        if attempts >= cap and args.actor != "user":
            raise HarnessError(f"G4: {node} NEEDS CHANGES after {attempts} rounds (cap {cap}); ask the user, then repeat with --actor user")
        problems = artifact_problems(s, node, "needs-changes", None)
        if problems:
            raise HarnessError(f"G7: {node}: " + "; ".join(problems))
        stale = _invalidate(s, back, f"{node} NEEDS CHANGES: {args.note or ''}".strip())
        rec["attempts"] = attempts  # _invalidate resets dependents' counters; the looping node keeps its own
        result.update({"verdict": "needs-changes", "attempts": attempts, "reopened": back, "stale": stale})
    elif args.status == "approved":
        planned = [u["id"] for u in spec.get("units", [])] + (["reviewer"] if spec.get("reviewer") else [])
        done_units = {u["unit"] for u in current if u["status"] == "ok"}
        lacking = [u for u in planned if u not in done_units]
        if lacking:
            raise HarnessError(f"G7: {node} has no ok record for units {lacking} at generation {rec['generation']}")
        if spec.get("reviewer"):
            last_rev = [u for u in current if u["unit"] == "reviewer"][-1]
            if last_rev.get("verdict") == "FAIL":
                raise HarnessError(f"{node}: last reviewer verdict is FAIL ({last_rev.get('failureClass')}); fix and re-review")
        absent = [a for a in s.artifacts(node) if not (s.ticket.dir / a).is_file()]
        if absent:
            raise HarnessError(f"G7: {node} artifacts missing: {absent}")
        not_rewritten = [a for a in s.artifacts(node) if a.endswith(".md") and not live_text(s.ticket.dir / a).strip()]
        if not_rewritten:
            raise HarnessError(f"G7: {node} artifacts not rewritten since a rerun superseded them: {not_rewritten} "
                               f"(replace the file, or write the new round below its last `{SUPERSEDED_HEADER}` line)")
        problems = artifact_problems(s, node, "approved", args.reopen)
        if problems:
            raise HarnessError(f"G7: {node}: " + "; ".join(problems))
        broken = {a: C.citations(live_text(s.ticket.dir / a), citation_roots(s)) for a in s.artifacts(node) if a.endswith(".md")}
        broken = {a: b for a, b in broken.items() if b}
        if broken and spec.get("subjectNode"):
            raise HarnessError(f"G7: {node}: citations that do not resolve (receipt node): {broken}")
        if broken:
            result["citationWarnings"] = broken
        wrong_lang = language_problems(s, node)
        if wrong_lang and args.actor != "user":
            raise HarnessError(f"G5: {node}: output language does not match the configured one: {wrong_lang}. "
                               "Rewrite it, or if the user confirms the text is right, repeat with --actor user")
        boundary = spec["boundary"]
        if boundary == "user" and node in ("fix-plan", "feature-plan") and s.profile.get("gates", {}).get("autoSkipPlanApproval"):
            boundary = "controller"
        if boundary == "user" and args.actor != "user":
            raise HarnessError(f"G0: {node} closes on the user's approval; ask Proceed / Re-run / Stop, then --actor user")
        if spec.get("reopens"):
            if not args.reopen:
                raise HarnessError(f"{node}: pass --reopen <earliest invalidated node> or --reopen none")
        if spec.get("subjectNode") or spec.get("checksReceipts"):
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
            if commit_problems:
                ident = gitid.identity(s.profile)
                raise HarnessError(f"G7: commit rule broken {commit_problems}. Nodes never commit; only kaneki-ship does, as "
                                   f"{ident['name']} <{ident['email']}>. Undo with `sdlc git --ticket {s.ctx['ticket']} --repo <id> "
                                   "-- reset --soft <startHead>` (keeps the changes), then close the node again.")
        if spec.get("subjectNode"):
            rec["subject"] = {r["id"]: repo_subject(r.get("worktree") or r["path"], s.excludes) for r in s.ctx["repositories"]}
            result["subject"] = rec["subject"]
        if spec.get("issuesReceipts"):
            ttl = s.profile["delivery"].get("receiptTtlHours", 72)
            result["receipts"] = issue_receipts(s.state, ttl, s.excludes)
        if spec.get("checksReceipts"):
            problems = check_receipts(s.state, s.profile["delivery"].get("minHoursLeft", 24), s.excludes)
            if problems:
                raise HarnessError("G7: receipts not shippable: " + "; ".join(problems))
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


def _invalidate(s: Session, node: str, reason: str) -> list[str]:
    stale = G.dependents(s.graph, s.order, node)
    for n in stale:
        rec = s.state["nodes"][n]
        if rec["status"] == "pending" and rec["generation"] == 1 and not rec["units"]:
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
    out({"ok": True, "repo": args.repo, "worktree": repo["worktree"]})


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
    s = Session(args)
    request = SHIP.build_request(s, args.repo, _delivery(s))
    digest = SHIP.request_digest(request)
    path = s.ticket.dir / "ship" / f"{args.repo}-request.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({**request, "requestDigest": digest}, indent=2, ensure_ascii=False), encoding="utf-8")
    s.ticket.log({"event": "ship-request", "repo": args.repo, "requestDigest": digest})
    out({"requestDigest": digest, "requestFile": str(path), "repo": args.repo, "branch": request["branch"],
         "base": request["base"], "files": request["paths"], "identity": request["identity"],
         "commitMessage": request["commitMessage"], "push": request["push"], "pr": request["pr"],
         "approveWith": f"ship --ticket {s.ctx['ticket']} --repo {args.repo} --approve {digest}"})


def cmd_ship(args: argparse.Namespace) -> None:
    """kaneki-ship step 2: only with the digest of the request the user approved; refuses any drift since."""
    s = Session(args)
    delivery = _delivery(s)
    request = SHIP.build_request(s, args.repo, delivery)
    digest = SHIP.request_digest(request)
    if args.approve != digest:
        raise HarnessError("G0: the request changed since it was approved (or the digest is wrong): run ship-request "
                           "again, show it to the user, and approve the new digest")
    result = SHIP.run_ship(s, request, delivery, open_pr=not args.no_pr)
    s.save()
    s.ticket.log({"event": "ship", "repo": args.repo, "commit": result["commit"], "approval": digest,
                  "pushed": result["pushed"], "pr": result.get("pr")})
    if result.get("pushError"):
        raise HarnessError(f"G7: committed {result['commit'][:10]} as the project identity, but the push failed: "
                           f"{result['pushError']}. Fix access, then run ship-request/ship again (it pushes the same branch).")
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


def cmd_digest(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.is_file():
        raise HarnessError(f"not a file: {path}")
    d = sha256_file(path)
    if args.expect and args.expect != d:
        raise HarnessError(f"digest mismatch for {path}: {d} != {args.expect}")
    out({"path": str(path), "sha256": d, "bytes": path.stat().st_size})


# --- parser ------------------------------------------------------------------------------------------

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
    x.set_defaults(fn=cmd_init)

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
    x.set_defaults(fn=cmd_ship_request)

    x = sub.add_parser("ship", parents=[ticketed], help="kaneki-ship: commit as the project identity, push, open the PR (needs the approved digest)")
    x.add_argument("--repo", required=True)
    x.add_argument("--approve", required=True, help="requestDigest of the request the user approved")
    x.add_argument("--no-pr", action="store_true", help="push but do not open a PR/MR")
    x.set_defaults(fn=cmd_ship)

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

    x = sub.add_parser("install", help="install the kaneki skills, agents and guard into <target>/.claude")
    x.add_argument("--target", required=True, help="project folder (or a .claude folder, e.g. ~/.claude for all projects)")
    x.add_argument("--force", action="store_true", help="also overwrite installed files you edited")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(fn=cmd_install)

    x = sub.add_parser("digest", help="sha256 of a file (never prints content)")
    x.add_argument("--path", required=True)
    x.add_argument("--expect")
    x.set_defaults(fn=cmd_digest)
    return p


READ_ONLY = {"status", "plan", "validate", "digest", "onboard-scan", "onboard-verify", "onboard-write", "install"}


def _ticket_lock(args: argparse.Namespace):
    """One writer per ticket: every command that changes a ticket holds its lock for the whole read-modify-write."""
    from contextlib import nullcontext
    if args.cmd in READ_ONLY or not getattr(args, "ticket", None) or (args.cmd == "lang" and not (args.set or args.clear)):
        return nullcontext()
    try:
        prof = load_profile(profile_path(args))
    except HarnessError:
        return nullcontext()  # the command itself reports the profile error
    return Ticket(ticket_root(prof, profile_path(args).parent), args.ticket).lock(args.cmd)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with _ticket_lock(args):
            rc = args.fn(args)
    except HarnessError as exc:
        out({"error": str(exc)})
        return 2
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
