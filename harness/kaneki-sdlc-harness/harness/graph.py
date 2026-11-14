"""Lifecycle graph: load the base graph plus family extensions, derive a ticket's trace, walk dependents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import schema
from .errors import HarnessError

SLOT_PREFIX = "@slot:"


def load_graph(home: Path, profile: dict[str, Any], profile_dir: Path) -> dict[str, Any]:
    graph = json.loads((home / "graph" / "lifecycle.json").read_text(encoding="utf-8"))
    schema.validate(graph, "graph.schema.json", "graph/lifecycle.json")
    order: list[str] = []
    nodes: dict[str, Any] = dict(graph["nodes"])
    extensions = _load_extensions(home, profile, profile_dir)
    for item in graph["order"]:
        if not item.startswith(SLOT_PREFIX):
            order.append(item)
            continue
        slot = item[len(SLOT_PREFIX):]
        for ext in extensions:
            if ext.get("slot") != slot:
                continue
            for node_id in ext["order"]:
                if node_id in nodes:
                    raise HarnessError(f"extension {ext['familyId']} redefines node {node_id}")
                spec = dict(ext["nodes"][node_id])
                when = dict(spec.get("when") or {})
                when.setdefault("family", ext["familyId"])
                spec["when"] = when
                spec.setdefault("layer", "implementation")
                spec["family"] = ext["familyId"]
                card = Path(spec["card"])
                if not card.is_absolute():  # extension cards live next to the extension file
                    spec["card"] = str((Path(ext["_dir"]) / card).resolve())
                nodes[node_id] = spec
                order.append(node_id)
    # `feeds`: an extension node hands its tokens to named core nodes, so they wait for it (inert when the
    # family is off, because no node in the trace produces the token).
    for node_id, spec in list(nodes.items()):
        for target in spec.get("feeds", []):
            if target not in nodes:
                raise HarnessError(f"{node_id} feeds unknown node {target}")
            nodes[target] = {**nodes[target], "requires": list(dict.fromkeys(nodes[target].get("requires", []) + spec["produces"]))}
    graph["order"] = order
    graph["nodes"] = nodes
    for node_id in order:
        if node_id not in nodes:
            raise HarnessError(f"graph order names unknown node {node_id}")
    return graph


def _load_extensions(home: Path, profile: dict[str, Any], profile_dir: Path) -> list[dict[str, Any]]:
    out = []
    for family in profile.get("families", []):
        ref = family.get("nodes")
        if not ref:
            continue  # a family may only switch rules/templates, adding no node
        path = (profile_dir / ref) if not Path(ref).is_absolute() else Path(ref)
        if not path.exists():
            path = home / ref
        if not path.exists():
            raise HarnessError(f"family {family['id']}: extension file not found: {ref}")
        ext = json.loads(path.read_text(encoding="utf-8"))
        schema.validate(ext, "extension.schema.json", f"family {family['id']} extension {ref}")
        ext["familyId"] = family["id"]
        ext["_dir"] = str(path.parent)
        out.append(ext)
    return out


def when_matches(cond: dict[str, Any] | None, ctx: dict[str, Any]) -> bool:
    """Every key must match. A list value means any-of. Keys: kind, family, surface, stack, reviewRound."""
    if not cond:
        return True
    for key, want in cond.items():
        wants = want if isinstance(want, list) else [want]
        if key == "kind":
            ok = ctx.get("kind") in wants
        elif key == "family":
            ok = bool(set(wants) & set(ctx.get("families", [])))
        elif key == "surface":
            ok = bool(set(wants) & set(ctx.get("surfaces", [])))
        elif key == "stack":
            ok = bool(set(wants) & {r["stack"] for r in ctx.get("repositories", [])})
        elif key == "reviewRound":
            ok = (ctx.get("reviewRound", 0) > 0) == bool(want)
        else:
            raise HarnessError(f"unknown 'when' key: {key}")
        if not ok:
            return False
    return True


def trace(graph: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    return [n for n in graph["order"] if when_matches(graph["nodes"][n].get("when"), ctx)]


def dependents(graph: dict[str, Any], order: list[str], start: str) -> list[str]:
    """`start` plus every later node that (transitively) consumes a token it produces."""
    if start not in order:
        raise HarnessError(f"{start} is not in this ticket's trace")
    stale = [start]
    tokens = set(graph["nodes"][start].get("produces", []))
    for node_id in order[order.index(start) + 1:]:
        spec = graph["nodes"][node_id]
        if tokens & set(spec.get("requires", [])):
            stale.append(node_id)
            tokens |= set(spec.get("produces", []))
    return stale
