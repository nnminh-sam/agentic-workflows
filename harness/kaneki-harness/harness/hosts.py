"""Agent host resolution: 3-layer model, dispatch, and context-window bindings."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from . import schema
from .errors import HarnessError
from .profile import DEFAULT_CONTEXT_WINDOW, context_window as profile_context_window, tier_model as profile_tier_model

KNOWN_HOSTS = ("antigravity", "claude", "codex", "gemini", "cursor", "opencode", "generic")
HOST_LOCAL_REL = Path(".sdlc") / "host.local.json"
TIER_RANK = {"light": 0, "routine": 1, "deep": 2}
_TOKEN_SPLIT = re.compile(r"[-_[\]\s:/]+")


def host_dir(home: Path, host: str) -> Path:
    if host not in KNOWN_HOSTS:
        raise HarnessError(f"unknown host {host!r}; known: {', '.join(KNOWN_HOSTS)}")
    return home / "hosts" / host


def load_preset(home: Path, host: str) -> dict[str, Any]:
    path = host_dir(home, host) / "tiers.json"
    if not path.is_file():
        raise HarnessError(f"missing host preset: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema.validate(data, "host-binding.schema.json", f"host preset {host}/tiers.json")
    return data


def load_preamble(home: Path, host: str) -> str:
    path = host_dir(home, host) / "preamble.md"
    if not path.is_file():
        raise HarnessError(f"missing host preamble: {path}")
    return path.read_text(encoding="utf-8")


def local_binding_path(pdir: Path) -> Path:
    return pdir / HOST_LOCAL_REL


def load_local_binding(pdir: Path) -> dict[str, Any] | None:
    path = local_binding_path(pdir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    schema.validate(data, "host-binding.schema.json", str(HOST_LOCAL_REL))
    return data


def write_local_binding(home: Path, pdir: Path, host: str, overwrite: bool = False) -> tuple[Path, bool]:
    """Initialize `.sdlc/host.local.json` from `hosts/<host>/tiers.json`. Returns `(path, wrote)`."""
    path = local_binding_path(pdir)
    if path.exists() and not overwrite:
        return path, False
    preset = load_preset(home, host)
    schema.validate(preset, "host-binding.schema.json", f"host binding for {host}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, True


def active_host(pdir: Path, cli_host: str | None = None,
                binding: dict[str, Any] | None = None) -> tuple[str | None, str]:
    """Determine the active host and where it came from (`cli`, `env`, `local`, or `profile`)."""
    if cli_host:
        if cli_host == "profile":
            return None, "profile"
        if cli_host not in KNOWN_HOSTS:
            raise HarnessError(f"unknown host {cli_host!r}; known: {', '.join(KNOWN_HOSTS)}")
        return cli_host, "cli"
    env_host = (os.environ.get("SDLC_HOST") or "").strip()
    if env_host:
        if env_host == "profile":
            return None, "profile"
        if env_host not in KNOWN_HOSTS:
            raise HarnessError(f"unknown $SDLC_HOST {env_host!r}; known: {', '.join(KNOWN_HOSTS)}")
        return env_host, "env"
    if binding and binding.get("host"):
        return binding["host"], "local"
    return None, "profile"


def effective_tier(profile: dict[str, Any], unit: dict[str, Any], node: str,
                   failed_rounds_fn: Callable[[str | None], int],
                   graph_nodes: dict[str, Any], order: list[str]) -> tuple[str, str | None]:
    """Effective tier of a unit, combining `profile.nodeOverrides` and graph review-failure escalation."""
    p_nodes = profile.get("nodeOverrides") or {}
    p_node = p_nodes.get(node) or {}
    p_unit = (p_node.get("units") or {}).get(unit["id"]) or {}
    base_tier = p_unit.get("tier") or p_node.get("tier") or unit["tier"]

    esc = unit.get("escalate")
    if not esc:
        return base_tier, None
    review = esc.get("reviewNode") or next(
        (n for n in order if graph_nodes[n].get("loopBackTo") == node), None
    )
    after = (
        p_unit.get("escalateAfter")
        or p_node.get("escalateAfter")
        or esc.get("afterFails")
        or profile.get("gates", {}).get("escalateAfter", 2)
    )
    fails = failed_rounds_fn(review) if review else 0
    if fails >= after and TIER_RANK[esc["tier"]] > TIER_RANK[base_tier]:
        return esc["tier"], f"{review} failed {fails} round(s) (escalates after {after})"
    return base_tier, None


def _tier_cfg(tiers: dict[str, Any], tier: str) -> dict[str, Any]:
    if tier == "light" and "light" not in tiers:
        return tiers["routine"]
    return tiers[tier]


def resolve_execution(home: Path, pdir: Path, profile: dict[str, Any], node: str, unit_id: str,
                      tier: str, cli_host: str | None = None) -> dict[str, Any]:
    """Resolve host, dispatchMode, model, effort, aliases, enforcement, and contextWindow."""
    local = load_local_binding(pdir)
    host_name, source = active_host(pdir, cli_host=cli_host, binding=local)

    if host_name is None:
        model = profile_tier_model(profile, tier)
        return {
            "host": "profile",
            "source": "profile",
            "dispatchMode": "native",
            "model": model,
            "modelSelection": "explicit",
            "effort": None,
            "enforcement": "strict",
            "runner": None,
            "commandTemplate": None,
            "aliases": [model],
            "contextWindow": profile_context_window(profile, tier),
        }

    preset = load_preset(home, host_name)
    binding = local if (local and local.get("host") == host_name) else preset
    tcfg = _tier_cfg(binding["tiers"], tier)

    resolved: dict[str, Any] = {
        "host": host_name,
        "source": source,
        "dispatchMode": binding.get("dispatchMode", preset.get("dispatchMode", "native")),
        "model": tcfg["model"],
        "modelSelection": binding.get("modelSelection", preset.get("modelSelection", "explicit")),
        "effort": tcfg.get("effort"),
        "enforcement": binding.get("enforcement", preset.get("enforcement", "alias-match")),
        "runner": binding.get("runner", preset.get("runner")),
        "commandTemplate": binding.get("commandTemplate", preset.get("commandTemplate")),
        "aliases": list(dict.fromkeys([tcfg["model"], *(tcfg.get("aliases") or [])])),
        "contextWindow": int(
            (profile.get("contextWindow") or {}).get(tier)
            or tcfg.get("contextWindow")
            or DEFAULT_CONTEXT_WINDOW
        ),
    }

    # Layer in local per-node or per-unit overrides (`node` then `node/unit_id`)
    overrides = (local or {}).get("nodeOverrides") or {}
    for key in (node, f"{node}/{unit_id}"):
        ov = overrides.get(key)
        if not ov:
            continue
        # If an override switches runner/host without explicitly specifying model, pull defaults from that host preset
        alt_host = ov.get("host") or (ov.get("runner") if ov.get("runner") in KNOWN_HOSTS else None)
        if alt_host and alt_host != resolved["host"] and "model" not in ov:
            alt_preset = load_preset(home, alt_host)
            alt_tcfg = _tier_cfg(alt_preset["tiers"], tier)
            resolved["model"] = alt_tcfg["model"]
            resolved["effort"] = alt_tcfg.get("effort")
            resolved["enforcement"] = ov.get("enforcement", alt_preset.get("enforcement", resolved["enforcement"]))
            resolved["modelSelection"] = ov.get("modelSelection", alt_preset.get("modelSelection", resolved["modelSelection"]))
            resolved["aliases"] = list(dict.fromkeys([alt_tcfg["model"], *(alt_tcfg.get("aliases") or [])]))
            if "contextWindow" not in (profile.get("contextWindow") or {}) and alt_tcfg.get("contextWindow"):
                resolved["contextWindow"] = int(alt_tcfg["contextWindow"])
        for field in ("host", "dispatchMode", "runner", "commandTemplate", "model",
                      "effort", "contextWindow", "enforcement", "modelSelection"):
            if field in ov:
                resolved[field] = ov[field]
        if "aliases" in ov:
            resolved["aliases"] = list(dict.fromkeys([resolved["model"], *ov["aliases"]]))
        elif "model" in ov:
            resolved["aliases"] = [resolved["model"]]

    if resolved["model"] not in resolved["aliases"]:
        resolved["aliases"] = [resolved["model"], *resolved["aliases"]]
    return resolved


def _tokens(name: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(name.lower()) if t}


def _matches_alias(recorded: str, aliases: list[str]) -> bool:
    rec_low = recorded.strip().lower()
    if not rec_low:
        return False
    rec_toks = _tokens(rec_low)
    for alias in aliases:
        a_low = alias.strip().lower()
        if a_low == "inherit":
            continue
        if rec_low == a_low:
            return True
        a_toks = _tokens(a_low)
        # Accept versioned/suffixed variants of family aliases (e.g. sonnet -> claude-sonnet-4-6,
        # pro -> gemini-2.5-pro, flash -> gemini-3-flash) without letting flash match flash-lite.
        if a_toks and a_toks <= rec_toks:
            if "flash" in a_toks and "lite" not in a_toks and "lite" in rec_toks:
                continue
            return True
    return False


def model_matches(recorded_model: str, execution: dict[str, Any]) -> tuple[bool, bool, str | None]:
    """Validate `--model` against resolved execution policy.
    Returns `(accepted, model_mismatch, warning)`.
    """
    planned = execution["model"]
    enforcement = execution.get("enforcement", "strict")
    aliases = execution.get("aliases") or [planned]

    if recorded_model == planned:
        if planned == "inherit" and enforcement == "inherit-ok":
            return True, True, f"inherited host model {recorded_model!r} unverified for execution"
        return True, False, None

    if enforcement == "strict":
        return False, True, None

    if _matches_alias(recorded_model, aliases):
        return True, False, None

    if enforcement == "alias-match":
        if recorded_model.strip().lower() == "inherit" and any(a.lower() == "inherit" for a in aliases):
            return True, True, f"unit ran on 'inherit' instead of planned {planned!r}; tier unverified"
        return False, True, None

    # enforcement == "inherit-ok"
    return True, True, f"inherited host model {recorded_model!r} differs from or cannot verify {planned!r}"
