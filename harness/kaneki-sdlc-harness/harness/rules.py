"""Rulepacks: review rules as data, selected per node phase and per ticket applicability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import schema
from .errors import HarnessError

PHASES = ("design", "code", "test", "evidence", "delivery")


def load_rulepacks(home: Path, profile_dir: Path, profile: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    refs = list(profile.get("rulepacks", []))
    # stack adapters may name their own rulepack; families too
    for repo in ctx["repositories"]:
        refs.append(f"stack:{repo['stack']}")
    for fam in profile.get("families", []):
        if fam["id"] in ctx["families"] and fam.get("rulepack"):
            refs.append(fam["rulepack"])
    rules: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for ref in dict.fromkeys(refs):  # keep order, drop duplicates
        pack = _read_pack(home, profile_dir, ref)
        if pack is None:
            continue
        for rule in pack["rules"]:
            if rule["id"] in seen_ids:
                raise HarnessError(f"duplicate rule id {rule['id']} (pack {pack['packId']})")
            if rule.get("phase") not in PHASES:
                raise HarnessError(f"rule {rule['id']}: phase must be one of {PHASES}")
            seen_ids.add(rule["id"])
            rules.append({**rule, "pack": pack["packId"]})
    return rules


def _read_pack(home: Path, profile_dir: Path, ref: str) -> dict[str, Any] | None:
    if ref.startswith("stack:"):
        path = home / "rulepacks" / "stack" / f"{ref[6:]}.json"
        if not path.exists():
            return None  # optional
        pack = json.loads(path.read_text(encoding="utf-8"))
        schema.validate(pack, "rulepack.schema.json", f"rulepack {ref}")
        return pack
    for path in (home / "rulepacks" / f"{ref}.json", profile_dir / ref):
        if path.is_file():
            pack = json.loads(path.read_text(encoding="utf-8"))
            schema.validate(pack, "rulepack.schema.json", f"rulepack {ref}")
            return pack
    raise HarnessError(f"rulepack not found: {ref}")


def applies(rule: dict[str, Any], ctx: dict[str, Any]) -> bool:
    cond = rule.get("appliesTo") or {}
    if "kinds" in cond and ctx["kind"] not in cond["kinds"]:
        return False
    if "stacks" in cond and not set(cond["stacks"]) & {r["stack"] for r in ctx["repositories"]}:
        return False
    if "surfaces" in cond and not set(cond["surfaces"]) & set(ctx["surfaces"]):
        return False
    if "families" in cond and not set(cond["families"]) & set(ctx["families"]):
        return False
    return True


def select(rules: list[dict[str, Any]], ctx: dict[str, Any], phases: list[str]) -> list[dict[str, Any]]:
    return [r for r in rules if r["phase"] in phases and applies(r, ctx)]
