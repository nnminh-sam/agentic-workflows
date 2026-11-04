"""Load and validate the JSON contracts that hold every rule and its parameters."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from . import profile as profile_mod
from .decision import SEVERITIES

ROOT = paths.PACKAGE_ROOT
CONTRACTS_DIR = paths.DEFAULT_CONTRACTS_DIR  # shipped defaults
CONTRACT_FILES = {
    "identity": "identity.json",
    "commit-message": "commit-message.json",
    "github-cli": "github-cli.json",
}

# Rule ids the code implements, per contract. A contract may not invent new ids
# (there would be no code to run them) and may not drop one (use severity "off").
KNOWN_RULES = {
    "identity": {"ID001", "ID002", "ID003", "ID004", "ID005"},
    "commit-message": {f"CM{n:03d}" for n in range(1, 16)},
    "github-cli": {f"GH{n:03d}" for n in range(1, 8)},
}

# Params whose values are regexes (rule id -> param name -> "list" | "str").
REGEX_PARAMS = {
    "ID002": {"patterns": "list"},
    "ID004": {"extra_patterns": "list"},
    "CM005": {"scope_pattern": "str"},
    "CM014": {"patterns": "list"},
}


class ContractError(Exception):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    title: str
    description: str
    severity: str
    params: dict

    @property
    def enabled(self) -> bool:
        return self.severity != "off"


@dataclass(frozen=True)
class Contract:
    name: str
    data: dict
    profile: dict = field(default_factory=dict)  # name -> profile_mod.Value

    @property
    def settings(self) -> dict:
        return self.data.get("settings", {})

    def rule(self, rule_id: str) -> Rule:
        """The rule with ${profile} references in its params resolved."""
        for r in self.data["rules"]:
            if r["id"] == rule_id:
                regex_keys = REGEX_PARAMS.get(rule_id, {})
                params = {k: profile_mod.substitute(v, self.profile, regex=k in regex_keys)
                          for k, v in r.get("params", {}).items()}
                return Rule(r["id"], r["name"], r["title"], r["description"], r["severity"], params)
        raise KeyError(rule_id)


@dataclass(frozen=True)
class Contracts:
    identity: Contract
    commit: Contract
    gh: Contract

    @property
    def profile(self) -> dict:
        return self.identity.profile


def validate(name: str, data: dict) -> list[str]:
    """Return a list of problems; empty means the contract is valid."""
    errs: list[str] = []
    for key in ("contract", "version", "title", "description", "rules"):
        if key not in data:
            errs.append(f"{name}: missing top-level key '{key}'")
    if errs:
        return errs
    if data["contract"] != name:
        errs.append(f"{name}: 'contract' is '{data['contract']}', expected '{name}'")
    if not isinstance(data.get("settings", {}), dict):
        errs.append(f"{name}: 'settings' must be an object")
    seen: set[str] = set()
    for i, r in enumerate(data["rules"]):
        where = f"{name}: rules[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{where} must be an object")
            continue
        for key in ("id", "name", "title", "description", "severity"):
            if not isinstance(r.get(key), str) or not r.get(key):
                errs.append(f"{where} needs a non-empty string '{key}'")
        rid = r.get("id")
        if rid in seen:
            errs.append(f"{where}: duplicate id {rid}")
        seen.add(rid)
        if r.get("severity") not in SEVERITIES:
            errs.append(f"{where} ({rid}): severity must be one of {SEVERITIES}")
        params = r.get("params", {})
        if not isinstance(params, dict):
            errs.append(f"{where} ({rid}): params must be an object")
            continue
        for pname, kind in REGEX_PARAMS.get(rid, {}).items():
            vals = params.get(pname, [] if kind == "list" else "")
            vals = vals if kind == "list" else [vals]
            for v in vals:
                try:
                    re.compile(profile_mod.VAR.sub("x", v))
                except (re.error, TypeError) as e:
                    errs.append(f"{where} ({rid}): bad regex in {pname}: {v!r} ({e})")
    missing = KNOWN_RULES[name] - seen
    unknown = seen - KNOWN_RULES[name]
    if missing:
        errs.append(f"{name}: missing rules {sorted(missing)} (set severity 'off' instead of deleting)")
    if unknown:
        errs.append(f"{name}: unknown rule ids {sorted(unknown)}")
    errs.extend(_validate_specific(name, data))
    return errs


def validate_all(raw: dict[str, dict]) -> list[str]:
    """Validate every contract plus cross-contract ${variable} references."""
    errs = [e for name, data in raw.items() for e in validate(name, data)]
    declared = raw.get("identity", {}).get("profile_variables", {})
    for name, data in raw.items():
        for r in data.get("rules", []):
            for var in sorted(profile_mod.references(r.get("params", {})) - set(declared)):
                errs.append(f"{name}: {r.get('id')} uses ${{{var}}}, which identity.profile_variables does not declare")
    return errs


def _validate_specific(name: str, data: dict) -> list[str]:
    errs: list[str] = []
    if name == "identity":
        pv = data.get("profile_variables")
        if not isinstance(pv, dict):
            errs.append("identity: 'profile_variables' must be an object")
        else:
            for var, decl in pv.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
                    errs.append(f"identity: bad profile variable name '{var}'")
                if not isinstance(decl, dict) or decl.get("type") not in ("string", "list"):
                    errs.append(f"identity: profile variable '{var}' needs type 'string' or 'list'")
                elif not isinstance(decl.get("env", ""), str):
                    errs.append(f"identity: profile variable '{var}' env must be a string")
    if name == "commit-message":
        types = data.get("settings", {}).get("types")
        if not (isinstance(types, list) and types and all(isinstance(t, str) for t in types)):
            errs.append("commit-message: settings.types must be a non-empty list of strings")
        for p in data.get("settings", {}).get("exempt_header_patterns", []):
            try:
                re.compile(p)
            except re.error as e:
                errs.append(f"commit-message: bad exempt_header_patterns regex {p!r} ({e})")
    if name == "github-cli":
        for r in data["rules"]:
            if r.get("id") == "GH002":
                for j, c in enumerate(r.get("params", {}).get("commands", [])):
                    if c.get("decision") not in ("deny", "ask", "allow"):
                        errs.append(f"github-cli: GH002 commands[{j}] decision must be deny/ask/allow")
                    if not str(c.get("match", "")).strip():
                        errs.append(f"github-cli: GH002 commands[{j}] needs a 'match'")
            for key in ("delete_severity", "public_severity"):
                v = r.get("params", {}).get(key)
                if v is not None and v not in SEVERITIES:
                    errs.append(f"github-cli: {r.get('id')} {key} must be one of {SEVERITIES}")
    return errs


def contract_paths(directory: Path | None = None) -> dict[str, Path]:
    """Each contract's file: <directory>/<file>, or (default) the project override if any, else the shipped one."""
    return {name: (directory / fname if directory is not None else paths.contract_path(fname))
            for name, fname in CONTRACT_FILES.items()}


def load_raw(directory: Path | None = None) -> dict[str, dict]:
    out = {}
    for name, path in contract_paths(directory).items():
        try:
            out[name] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ContractError(f"contract file not found: {path}")
        except json.JSONDecodeError as e:
            raise ContractError(f"{path.name}: invalid JSON ({e})")
    return out


def build(raw: dict[str, dict], profile: dict) -> Contracts:
    return Contracts(
        identity=Contract("identity", raw["identity"], profile),
        commit=Contract("commit-message", raw["commit-message"], profile),
        gh=Contract("github-cli", raw["github-cli"], profile),
    )


def load(directory: Path | None = None, env: dict | None = None,
         profile_files: tuple[Path, ...] | None = None) -> Contracts:
    """Load, validate, and bind the contracts to the current user's profile."""
    raw = load_raw(directory)
    errs = validate_all(raw)
    if errs:
        raise ContractError("invalid contracts:\n  " + "\n  ".join(errs))
    try:
        kw = {} if profile_files is None else {"files": profile_files}
        prof = profile_mod.load(raw["identity"]["profile_variables"], env, **kw)
    except profile_mod.ProfileError as e:
        raise ContractError(f"invalid profile: {e}")
    return build(raw, prof)
