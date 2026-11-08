"""Project profile: the one file that carries everything project-specific."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import gitid
from . import schema
from .errors import HarnessError

ADAPTER_KINDS = ("tracker", "stack", "surface", "delivery")
# Everything is English unless a language is set EXPLICITLY, key by key:
#   1. a per-ticket override the user asked for in the prompt (`sdlc lang --ticket T --set delivery=ja`), else
#   2. the project profile's `locale.<key>`, else
#   3. English.
# There is no inference (not from the repo, not from the language the user writes in) and no fallback between
# keys: setting `artifacts` never changes `tracker`, setting `delivery` never changes `review`.
#   chat      the controller talking to the user (questions, progress tables, summaries)
#   artifacts ticket files: analysis, plan, test cases, evidence report, reviews
#   delivery  commit message and PR body
#   review    replies to PR review comments
#   tracker   the reply posted on the ticket
LOCALE_KEYS = ("chat", "artifacts", "delivery", "review", "tracker")
DEFAULT_LANGUAGE = "en"
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
DRAFT_LOCALE = {"tracker-reply": "tracker"}  # every other draft (commit-msg, pr-body, ...) uses `delivery`


def language(profile: dict[str, Any], key: str, override: dict[str, str] | None = None) -> str:
    if override and override.get(key):
        return override[key]
    return (profile.get("locale") or {}).get(key, DEFAULT_LANGUAGE)


def language_source(profile: dict[str, Any], key: str, override: dict[str, str] | None = None) -> str:
    if override and override.get(key):
        return "ticket override (requested in the prompt)"
    return "project profile" if key in (profile.get("locale") or {}) else "default"


def parse_lang_settings(items: list[str] | None) -> dict[str, str]:
    """`key=lang` pairs from the command line (`delivery=ja`)."""
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, val = item.partition("=")
        if not sep or key not in LOCALE_KEYS or not LANG_RE.match(val):
            raise HarnessError(f"bad language setting {item!r}: use <key>=<tag>, key one of {', '.join(LOCALE_KEYS)}, tag like en, vi, ja")
        out[key] = val
    return out


def artifact_languages(profile: dict[str, Any], node: str, artifacts: list[str],
                       override: dict[str, str] | None = None) -> dict[str, str]:
    """Language of every file a node writes. Code, identifiers and code comments are not covered: they follow
    the repository's own convention."""
    out = {}
    for a in artifacts:
        if a.startswith("drafts/"):
            out[a] = language(profile, DRAFT_LOCALE.get(Path(a).stem, "delivery"), override)
        elif node == "respond-review":
            out[a] = language(profile, "review", override)
        else:
            out[a] = language(profile, "artifacts", override)
    return out
TIERS = ("routine", "deep")
TESTCASE_FORMATS = ("markdown", "xlsx", "csv", "gherkin")


def load_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HarnessError(f"profile not found: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    validate_profile(profile)  # friendly messages first, then the full contract
    schema.validate(profile, "project-profile.schema.json", f"profile {path.name}")
    profile["_dir"] = str(path.parent.resolve())  # runtime only; never written back
    return profile


def _abs(raw: str, profile: dict[str, Any]) -> str:
    """Repository paths in a profile are relative to the profile file (or absolute)."""
    p = Path(raw)
    if not p.is_absolute() and profile.get("_dir"):
        p = Path(profile["_dir"]) / p
    return str(p.resolve()) if profile.get("_dir") else raw


# Ticket worktrees live together in one folder, never loose next to the repositories:
#   <worktreeRoot>/<repoId>-<ticket>
# `paths.worktreeRoot` is relative to the profile file (or absolute). It must not be inside a repository,
# or every worktree would show up as untracked content of that repository.
DEFAULT_WORKTREE_ROOT = "worktrees"


def worktree_root(profile: dict[str, Any], profile_dir: Path) -> Path:
    raw = Path(profile.get("paths", {}).get("worktreeRoot") or DEFAULT_WORKTREE_ROOT)
    return (raw if raw.is_absolute() else Path(profile_dir) / raw).resolve()


def worktree_path(profile: dict[str, Any], profile_dir: Path, repo_id: str, ticket: str) -> Path:
    return worktree_root(profile, profile_dir) / f"{repo_id}-{ticket}"


def worktree_root_errors(profile: dict[str, Any], profile_dir: Path) -> list[str]:
    root = worktree_root(profile, profile_dir)
    errors = []
    for repo in profile.get("repositories", []):
        rp = Path(repo["path"])
        rp = (rp if rp.is_absolute() else Path(profile_dir) / rp).resolve()
        if root == rp or rp in root.parents:
            errors.append(f"paths.worktreeRoot ({root}) is inside repository {repo['id']} ({rp}); "
                          f"put it outside every repository, e.g. next to the profile in a workspace folder")
    return errors


def validate_profile(p: dict[str, Any]) -> None:
    """Light structural check mirroring contracts/project-profile.schema.json (no third-party validator)."""
    errors: list[str] = []
    if p.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    for key in ("project", "paths", "models", "tracker", "repositories", "rulepacks", "testcase", "evidence", "delivery"):
        if key not in p:
            errors.append(f"missing '{key}'")
    models = p.get("models", {})
    for tier in TIERS:
        if not models.get(tier):
            errors.append(f"models.{tier} is required")
    if not p.get("paths", {}).get("ticketRoot"):
        errors.append("paths.ticketRoot is required")
    repo_ids = set()
    for repo in p.get("repositories", []):
        for key in ("id", "path", "stack", "surfaces"):
            if key not in repo:
                errors.append(f"repository {repo.get('id', '?')}: missing '{key}'")
        if repo.get("id") in repo_ids:
            errors.append(f"duplicate repository id {repo['id']}")
        repo_ids.add(repo.get("id"))
        if not repo.get("surfaces"):
            errors.append(f"repository {repo.get('id', '?')}: at least one surface")
    if not p.get("repositories"):
        errors.append("at least one repository")
    fmt = p.get("testcase", {}).get("format")
    if fmt not in TESTCASE_FORMATS:
        errors.append(f"testcase.format must be one of {TESTCASE_FORMATS}")
    declared = set(p.get("evidence", {}).get("surfaces", {}))
    for repo in p.get("repositories", []):
        for surface in repo.get("surfaces", []):
            if surface not in declared:
                errors.append(f"surface '{surface}' of {repo.get('id')} has no evidence.surfaces entry (use {{\"adapter\": \"none\", \"reason\": ...}} to opt out)")
    for fam in p.get("families", []):
        if "id" not in fam:
            errors.append("family without id")
    errors += gitid.validate(p)
    for key, val in (p.get("locale") or {}).items():
        if key not in LOCALE_KEYS:
            errors.append(f"locale.{key} is unknown (keys: {', '.join(LOCALE_KEYS)})")
        elif not isinstance(val, str) or not LANG_RE.match(val):
            errors.append(f"locale.{key} must be a language tag such as en, vi, ja, pt-BR")
    known = {"schemaVersion", "meta", "project", "locale", "paths", "models", "tracker", "repositories", "rulepacks",
             "testcase", "evidence", "delivery", "families", "gates", "environment", "git"}
    extra = sorted(k for k in p if k not in known and not k.startswith("_"))
    if extra:
        errors.append(f"unknown top-level keys {extra}")
    if errors:
        raise HarnessError("invalid profile: " + "; ".join(errors))


def find_adapter(home: Path, profile_dir: Path, kind: str, ref: str) -> dict[str, Any]:
    """`ref` is a bundled adapter id (adapters/<kind>/<id>.json) or a path relative to the profile."""
    if kind not in ADAPTER_KINDS:
        raise HarnessError(f"unknown adapter kind {kind}")
    candidates = [home / "adapters" / kind / f"{ref}.json", profile_dir / ref]
    for path in candidates:
        if path.is_file():
            adapter = json.loads(path.read_text(encoding="utf-8"))
            if adapter.get("kind") != kind:
                raise HarnessError(f"adapter {ref} declares kind {adapter.get('kind')}, expected {kind}")
            if adapter.get("extends"):
                # A project-local adapter overrides a bundled one field by field (dicts merge, lists replace).
                base = find_adapter(home, profile_dir, kind, adapter["extends"])
                base.pop("_path", None)
                adapter = _merge(base, adapter)
            schema.validate(adapter, "adapter.schema.json", f"{kind} adapter {ref}")
            adapter["_path"] = str(path)
            return adapter
    raise HarnessError(f"{kind} adapter not found: {ref}")


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def adapter_base_id(adapter: dict[str, Any]) -> str:
    return adapter.get("extends") or adapter["id"]


def resolve_adapters(home: Path, profile_dir: Path, profile: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tracker": find_adapter(home, profile_dir, "tracker", profile["tracker"]["adapter"]),
        "delivery": find_adapter(home, profile_dir, "delivery", profile["delivery"]["adapter"]),
        "stacks": {},
        "surfaces": {},
    }
    missing_drafts = [d for d in profile["delivery"].get("drafts", []) if d not in out["delivery"].get("drafts", {})]
    if missing_drafts:
        raise HarnessError(f"delivery adapter {out['delivery']['id']} has no template for drafts {missing_drafts}")
    repo_surfaces = {r["id"]: r["surfaces"] for r in profile["repositories"]}
    for repo in ctx["repositories"]:
        stack = find_adapter(home, profile_dir, "stack", repo.get("stackAdapter") or repo["stack"])
        if adapter_base_id(stack) != repo["stack"]:
            raise HarnessError(f"repository {repo['id']}: stackAdapter is for {adapter_base_id(stack)}, not {repo['stack']}")
        out["stacks"][repo["id"]] = stack
        allowed = set(stack.get("allowedSurfaces", []))
        bad = [s for s in repo_surfaces.get(repo["id"], []) if allowed and s not in allowed]
        if bad:
            raise HarnessError(f"repository {repo['id']}: stack {stack['id']} does not allow surfaces {bad}")
    for surface in ctx["surfaces"]:
        entry = profile["evidence"]["surfaces"][surface]
        if entry.get("adapter") == "none":
            out["surfaces"][surface] = {"id": "none", "kind": "surface", "notApplicable": entry.get("reason", "")}
        else:
            out["surfaces"][surface] = find_adapter(home, profile_dir, "surface", entry["adapter"])
    return out


def build_context(profile: dict[str, Any], ticket: str, kind: str, repos: list[str] | None,
                  families: list[str] | None, extra_surfaces: list[str] | None) -> dict[str, Any]:
    if kind not in ("bug", "feature"):
        raise HarnessError("kind must be bug or feature (a capability is a family, not a kind)")
    by_id = {r["id"]: r for r in profile["repositories"]}
    chosen = repos or list(by_id)
    unknown = [r for r in chosen if r not in by_id]
    if unknown:
        raise HarnessError(f"unknown repositories: {unknown}")
    known_families = {f["id"] for f in profile.get("families", [])}
    fams = sorted(set(families or []))
    if set(fams) - known_families:
        raise HarnessError(f"families not declared in the profile: {sorted(set(fams) - known_families)}")
    surfaces = sorted({s for r in chosen for s in by_id[r]["surfaces"]} | set(extra_surfaces or []))
    undeclared = [s for s in surfaces if s not in profile["evidence"]["surfaces"]]
    if undeclared:
        raise HarnessError(f"surfaces without an evidence adapter entry: {undeclared}")
    return {
        "ticket": ticket,
        "kind": kind,
        "maturity": "new",
        "reviewRound": 0,
        "families": fams,
        "surfaces": surfaces,
        "repositories": [{"id": r, "stack": by_id[r]["stack"], "path": _abs(by_id[r]["path"], profile),
                          "baseBranch": by_id[r].get("defaultBranch", "main"),
                          **({"stackAdapter": by_id[r]["stackAdapter"]} if by_id[r].get("stackAdapter") else {})}
                         for r in chosen],
    }
