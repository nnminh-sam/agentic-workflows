"""The cross-topic "things I got wrong" log: mistakes.md for reading, mistakes.jsonl for tools."""
from __future__ import annotations

from . import paths
from .store import append_jsonl, append_text, today


def _clip(s: str, n: int = 400) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def record(entry: dict) -> None:
    """entry: uid, origin, topic, concept, prompt, answer, expected, feedback, confidence, session."""
    confident = (entry.get("confidence") or 0) >= 3
    rec = {**entry, "date": today().isoformat(), "confident": confident}
    append_jsonl(paths.mistakes_jsonl(), rec)
    if not paths.mistakes_md().exists():
        append_text(paths.mistakes_md(), "# Things I got wrong\n\nAppended by learning-kit after every quiz. "
                                         "Reread monthly. ⚠ = I was sure and wrong.\n")
    flag = " ⚠ confident" if confident else ""
    append_text(paths.mistakes_md(), (
        f"\n## {rec['date']} · {entry.get('topic')} · {entry.get('concept')}{flag}\n\n"
        f"**Q:** {_clip(entry.get('prompt', ''))}\n\n"
        f"**I said:** {_clip(entry.get('answer', ''))}\n\n"
        f"**Correct:** {_clip(entry.get('expected', ''))}\n\n"
        f"**Why:** {_clip(entry.get('feedback', ''), 600)}\n"
    ))
