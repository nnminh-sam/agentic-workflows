"""Where things live: kit (code) vs home (your learning data), plus config."""
from __future__ import annotations

import copy
import os
from pathlib import Path

from .store import read_json

KIT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = KIT_DIR / "web"
SKILLS_SRC = KIT_DIR / "skills"
POINTER = Path.home() / ".learning-kit.json"

DEFAULT_CONFIG: dict = {
    "grader": {
        # Headless Claude Code grades free-recall answers. --json-schema is appended at runtime.
        "command": [
            "claude", "-p",
            "--model", "opus",
            "--effort", "medium",
            "--tools", "",
            "--no-session-persistence",
            "--output-format", "json",
        ],
        "timeout_sec": 300,
        "batch_size": 8,
        # "auto": pass --json-schema unless the command is a Windows .cmd/.bat shim (cmd.exe mangles
        # quoted JSON); true/false to force. Without it the reply is parsed from text.
        "json_schema": "auto",
    },
    "gate": {
        "pass_score": 0.8,          # overall (and per-concept) bar
        "free_recall_pass": 0.7,    # an explain/predict answer counts as "correct" at this score
        "max_rounds_per_day": 3,
        "min_items_per_concept": 2,
    },
    "srs": {
        "desired_retention": 0.9,
        "max_interval_days": 365,
        "daily_limit": 30,
        "mistake_extras": 3,        # not-yet-due confident mistakes mixed into a recall session
    },
    "server": {"host": "127.0.0.1", "port": 8765, "idle_timeout_min": 120},
    "speech_lang": "en-US",
    # Which model/effort each subagent role uses (read by the skills, not by Python).
    "agents": {
        "collect":   {"model": "sonnet", "effort": "medium"},
        "aggregate": {"model": "opus", "effort": "medium"},
        "quiz_author": {"model": "opus", "effort": "medium"},
        "quiz_review": {"model": "opus", "effort": "medium"},
        "reteach":   {"model": "opus", "effort": "medium"},
        "note_review": {"model": "opus", "effort": "medium"},
        "reword":    {"model": "opus", "effort": "medium"},
    },
    "depth": {
        "aware": {"sources": [3, 5], "facets": 2, "items_per_concept": [1, 2]},
        "use":   {"sources": [6, 10], "facets": 3, "items_per_concept": [2, 3]},
        "deep":  {"sources": [10, 15], "facets": 5, "items_per_concept": [3, 4]},
    },
}


def home() -> Path:
    """Data home: $LK_HOME, else the pointer file written by `lk install --home`, else ~/learning."""
    env = os.environ.get("LK_HOME")
    if env:
        return Path(os.path.expandvars(env)).expanduser()
    ptr = read_json(POINTER, {}) or {}
    if ptr.get("home"):
        return Path(os.path.expandvars(ptr["home"])).expanduser()
    return Path.home() / "learning"


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config() -> dict:
    return _deep_merge(DEFAULT_CONFIG, read_json(home() / "config.json", {}) or {})


def topics_dir() -> Path:
    return home() / "topics"


def topic_dir(slug: str) -> Path:
    return topics_dir() / slug


def sources_dir() -> Path:
    return home() / "sources"


def sessions_dir() -> Path:
    return home() / "sessions"


def srs_file() -> Path:
    return home() / "srs.json"


def srs_log() -> Path:
    return home() / "srs-log.jsonl"


def mistakes_md() -> Path:
    return home() / "mistakes.md"


def mistakes_jsonl() -> Path:
    return home() / "mistakes.jsonl"
