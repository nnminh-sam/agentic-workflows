"""Per-user profile: the account-specific values the shared contracts refer to as ${variable}.

Each variable declared in identity.json "profile_variables" is resolved, first match wins, from:
  1. its environment variable (e.g. GIT_GUARD_GITHUB_USER; lists are comma-separated),
  2. <project>/.claude/git-guard/profile.local.json (per project, git-ignored;
     <hook dir>/profile.local.json when vendored into .claude/hooks/git-guard),
  3. ~/.claude/git-guard/profile.json (per user, all projects).
Unset variables resolve to None; rules then fall back to presence / consistency checks.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

PROJECT_PROFILE = paths.PROJECT_PROFILE
USER_PROFILE = paths.USER_PROFILE
VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ProfileError(Exception):
    pass


@dataclass(frozen=True)
class Value:
    value: object  # str | list[str] | None
    source: str    # "env:NAME" | file path | "unset"


def _norm(kind: str, raw, choices: list | None = None) -> object:
    if raw is None or raw == "" or raw == []:
        return None
    if kind == "list":
        items = raw if isinstance(raw, list) else str(raw).split(",")
        items = [str(x).strip() for x in items if str(x).strip()]
        return items or None
    if isinstance(raw, list):
        raise ProfileError(f"expected a string, got a list: {raw!r}")
    value = str(raw).strip() or None
    if value is not None and choices and value not in choices:
        raise ProfileError(f"'{value}' is not one of {choices}")
    return value


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"{path}: {e}")
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: must be a JSON object")
    return data


def load(variables: dict, env: dict | None = None,
         files: tuple[Path, ...] = (PROJECT_PROFILE, USER_PROFILE)) -> dict[str, Value]:
    env = dict(os.environ if env is None else env)
    docs = [(str(p), _read(p)) for p in files]
    out: dict[str, Value] = {}
    for name, decl in variables.items():
        kind = decl.get("type", "string")
        choices = decl.get("choices")
        ev = decl.get("env")
        try:
            if ev and env.get(ev, "").strip():
                out[name] = Value(_norm(kind, env[ev], choices), f"env:{ev}")
                continue
            for src, doc in docs:
                if doc.get(name) not in (None, "", []):
                    out[name] = Value(_norm(kind, doc[name], choices), src)
                    break
            else:
                out[name] = Value(None, "unset")
        except ProfileError as e:
            raise ProfileError(f"profile variable '{name}': {e}")
    return out


def substitute(obj, profile: dict[str, Value], regex: bool = False):
    """Replace ${var} references in a params value.

    * "${var}" alone -> the value (list or string), or None when unset.
    * a list item "${var}" -> spliced in (lists flatten); dropped when unset.
    * "${var}" embedded in a longer string -> substituted (re.escape'd when regex); None/dropped when unset.
    """
    if isinstance(obj, dict):
        return {k: substitute(v, profile, regex) for k, v in obj.items()}
    if isinstance(obj, list):
        out = []
        for item in obj:
            r = substitute(item, profile, regex)
            if r is None:
                continue
            out.extend(r if isinstance(r, list) else [r])
        return out
    if not isinstance(obj, str) or "${" not in obj:
        return obj
    whole = VAR.fullmatch(obj)
    if whole:
        v = profile.get(whole.group(1))
        return None if v is None else v.value
    missing = False

    def rep(m):
        nonlocal missing
        v = profile.get(m.group(1))
        if v is None or v.value is None:
            missing = True
            return ""
        val = v.value if isinstance(v.value, str) else ",".join(v.value)
        return re.escape(val) if regex else val
    s = VAR.sub(rep, obj)
    return None if missing else s


def references(obj) -> set[str]:
    """All ${var} names used anywhere inside obj."""
    if isinstance(obj, dict):
        return set().union(*(references(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(references(v) for v in obj)) if obj else set()
    return set(VAR.findall(obj)) if isinstance(obj, str) else set()
