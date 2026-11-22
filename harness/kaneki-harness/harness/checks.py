"""Gate checks on artifact content: required sections, structured parsers, citations, output language.

The graph declares per node `checks: {"<artifact>": {"sections": [...], "parsers": [...]}}`. Only the live text
(after the last `## SUPERSEDED:` line) is checked. Every function returns a list of problems; empty = pass.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

NL = chr(10)
VERDICT_RE = re.compile(r"^\s*\**Verdict:?\**\s*:?\s*(APPROVED|NEEDS CHANGES)\b", re.M | re.I)
CONFIDENCE_RE = re.compile(r"^\s*\**Confidence:?\**\s*:?\s*(High|Medium|Low)\b", re.M | re.I)
REOPEN_RE = re.compile(r"^\s*\**Reopen:?\**\s*:?\s*`?([a-z0-9-]+)`?", re.M | re.I)
RULE_LINE_RE = re.compile(r"\b(PASS|N/A|NA|FAIL)\b", re.I)
CITATION_RE = re.compile(r"`([A-Za-z0-9_.@/\\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def sections(text: str) -> dict[str, str]:
    """`## Heading` → body up to the next heading of level 1-2. Keys are the heading text, stripped."""
    out: dict[str, str] = {}
    current, buf = None, []
    for line in text.splitlines():
        m = re.match(r"^(#{1,2})\s+(.+?)\s*#*\s*$", line)
        if m:
            if current is not None:
                out[current] = NL.join(buf)
            current, buf = m.group(2).strip(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = NL.join(buf)
    return out


def find_section(secs: dict[str, str], wanted: str) -> str | None:
    """Heading match ignores case and trailing words: `## Condition → negative case (2 rows)` matches."""
    w = wanted.lstrip("#").strip().lower()
    for head, body in secs.items():
        if head.lower().startswith(w):
            return body
    return None


def _table_rows(body: str) -> list[list[str]]:
    rows = []
    for line in body.splitlines():
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # separator
        rows.append(cells)
    return rows[1:] if rows else []  # drop the header row


def _rule_coverage(body: str, rule_ids: list[str], forbid_fail: bool) -> list[str]:
    problems, missing, failed = [], [], []
    lines = body.splitlines()
    for rid in rule_ids:
        hit = [l for l in lines if re.search(rf"(?<![A-Za-z0-9-]){re.escape(rid)}(?![A-Za-z0-9-])", l) and RULE_LINE_RE.search(l)]
        if not hit:
            missing.append(rid)
        elif forbid_fail and any(RULE_LINE_RE.search(l).group(1).upper() == "FAIL" for l in hit):
            failed.append(rid)
    if missing:
        problems.append(f"no PASS / N/A / FAIL verdict for rules {missing}")
    if failed:
        problems.append(f"rules judged FAIL: {failed}")
    return problems


def check_artifact(text: str, spec: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    """ctx: ruleIds (list), status (gate status), reopen (str | None)."""
    problems: list[str] = []
    secs = sections(text)
    for wanted in spec.get("sections", []):
        if find_section(secs, wanted) is None:
            problems.append(f"missing section `{wanted}`")
    for parser in spec.get("parsers", []):
        if parser == "confidence":
            if not CONFIDENCE_RE.search(text):
                problems.append("no `Confidence: High|Medium|Low` line")
        elif parser == "conditions":
            body = find_section(secs, "## Condition → negative case")
            if body is not None and not _table_rows(body) and not re.search(r"no (where|join|predicate|branch|query )?condition", body, re.I):
                problems.append("`## Condition → negative case` has no rows (write 'No condition changes' if none)")
        elif parser in ("design-rules", "rule-self-check", "rule-verdicts"):
            heading = {"design-rules": "## Design rules", "rule-self-check": "## Rule self-check"}.get(parser)
            body = find_section(secs, heading) if heading else text
            if body is not None:
                problems += _rule_coverage(body, ctx.get("ruleIds", []), forbid_fail=parser == "rule-verdicts")
        elif parser == "verdict":
            verdicts = VERDICT_RE.findall(text)
            if not verdicts:
                problems.append("no `Verdict: APPROVED | NEEDS CHANGES` line")
            else:
                last = verdicts[-1].upper()
                if ctx.get("status") == "approved" and last != "APPROVED":
                    problems.append(f"the artifact's verdict is {last}, but the gate was asked to approve")
                if ctx.get("status") == "needs-changes" and last != "NEEDS CHANGES":
                    problems.append(f"the artifact's verdict is {last}, but the gate was asked for needs-changes")
        elif parser == "reopen":
            m = REOPEN_RE.findall(text)
            if not m:
                problems.append("no `Reopen: <node> | none` line")
            elif ctx.get("reopen") and m[-1].lower() != ctx["reopen"].lower():
                problems.append(f"the artifact says Reopen: {m[-1]}, the gate was given --reopen {ctx['reopen']}")
        else:
            problems.append(f"unknown parser {parser}")
    return problems


def citations(text: str, roots: list[Path]) -> list[str]:
    """Port (light) of wms-flow `verify-subject`: every backticked `path:line[-line]` must name an existing file in
    one of the roots (repository checkouts, the ticket folder) with that many lines."""
    broken = []
    for m in CITATION_RE.finditer(text):
        rel, a, b = m.group(1).replace("\\", "/"), int(m.group(2)), int(m.group(3) or m.group(2))
        if rel.startswith(("http", "www.")):
            continue
        target = next((r / rel for r in roots if (r / rel).is_file()), None)
        if target is None:
            broken.append(f"`{rel}:{m.group(2)}` (file not found)")
            continue
        try:
            with target.open("rb") as fh:
                n = sum(1 for _ in fh)
        except OSError:
            n = 0
        if a < 1 or b < a or b > n:
            broken.append(f"`{rel}:{m.group(2)}{'-' + m.group(3) if m.group(3) else ''}` (file has {n} lines)")
    return sorted(set(broken))


def script_language(text: str) -> str | None:
    """Rough script of a text: ja (CJK/kana), ko (Hangul), vi (Vietnamese diacritics), en (Latin). None if too
    short to judge. Code spans and fenced blocks are ignored."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 40:
        return None
    cjk = sum(1 for c in letters if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    hangul = sum(1 for c in letters if "가" <= c <= "힯")
    vi = sum(1 for c in letters if c in "ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉĩọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")
    if cjk / len(letters) > 0.2:
        return "ja"
    if hangul / len(letters) > 0.2:
        return "ko"
    if vi / len(letters) > 0.03:
        return "vi"
    return "en"


def language_mismatch(text: str, expected: str) -> str | None:
    family = {"ja": "ja", "zh": "ja", "ko": "ko", "vi": "vi", "en": "en"}.get(expected.split("-")[0])
    if family is None:
        return None  # a language the heuristic cannot tell apart: not checked
    got = script_language(text)
    if got is None or got == family:
        return None
    return f"expected `{expected}`, the text reads as `{got}`"


COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
COMMIT_EXEMPT_RES = (re.compile(r"^Merge "), re.compile(r'^Revert "'), re.compile(r"^(fixup|squash|amend)! "))
COMMIT_HEADER_RE = re.compile(r"^(?P<type>[^\s(:!]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<subject>.*)$")
COMMIT_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
COMMIT_FORBIDDEN_HEADER_RES = (re.compile(r"\bwip\b", re.I), re.compile(r"\bdo not merge\b", re.I),
                               re.compile(r"\btmp commit\b", re.I))
BREAKING_ANY_RE = re.compile(r"^breaking[ -]change\s*:", re.I)
BREAKING_OK_RE = re.compile(r"^BREAKING[ -]CHANGE: \S")
AI_TRAILER_KEYS = {"co-authored-by", "assisted-by", "generated-by", "ai-assisted"}
AI_COAUTHOR_RE = re.compile(
    r"\bclaude\b|anthropic|\bopenai\b|chatgpt|\bgpt-?\d|codex|copilot|gemini|\bbard\b"
    r"|cursor(\.sh|\s*agent)?\b|devin|aider|windsurf|codeium|\bllm\b|\bai\b"
    r"|noreply@anthropic\.com|noreply@openai\.com",
    re.I,
)
AI_LINE_RE = re.compile(
    r"generated (with|by) .*(claude|chatgpt|copilot|gemini|cursor|codex|\bai\b|llm)"
    r"|🤖"
    r"|(written|created|authored) (with|by) (claude|chatgpt|copilot|gemini|cursor|codex|an? ai|an? llm)",
    re.I,
)


def _cleanup_commit_message(message: str) -> str:
    """Mirror git's `whitespace` cleanup: strip trailing spaces, collapse blank runs, trim outer blanks."""
    lines = [ln.rstrip() for ln in message.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "" and (not out or out[-1] == ""):
            continue
        out.append(ln)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def commit_message(message: str) -> list[str]:
    """Validate Conventional Commits 1.0 + no AI attribution rules (`CM003`–`CM008`, `CM011`, `CM013`, `CM014`, `CM016`)."""
    msg = _cleanup_commit_message(message)
    if not msg:
        return ["CM003: commit message is empty"]
    lines = msg.split("\n")
    header = lines[0]
    if any(pat.search(header) for pat in COMMIT_EXEMPT_RES):
        return []
    problems: list[str] = []
    if len(header) > 72:
        problems.append(f"CM007: header is {len(header)} chars, max 72")
    for pat in COMMIT_FORBIDDEN_HEADER_RES:
        if pat.search(header):
            problems.append(f"CM014: header matches forbidden pattern /{pat.pattern}/")
            break
    m = COMMIT_HEADER_RE.match(header)
    if not m:
        problems.append(f"CM003: header '{header}' is not '<type>(<scope>)!: <subject>'")
    else:
        typ, scope, subject = m.group("type"), m.group("scope"), m.group("subject")
        if typ not in COMMIT_TYPES:
            problems.append(f"CM004: type '{typ}' is not one of: {', '.join(COMMIT_TYPES)}")
        if scope is not None and (not scope or not COMMIT_SCOPE_RE.match(scope)):
            problems.append(f"CM005: scope '{scope}' must match {COMMIT_SCOPE_RE.pattern}")
        if len(subject.strip()) < 3:
            problems.append(f"CM006: subject '{subject}' is shorter than 3 chars")
        if subject.rstrip().endswith("."):
            problems.append("CM008: subject must not end with a period")
    if len(lines) > 1 and lines[1].strip():
        problems.append("CM011: the second line must be blank (separate header and body)")
    for ln in lines[1:]:
        if BREAKING_ANY_RE.match(ln) and not BREAKING_OK_RE.match(ln):
            problems.append(f"CM013: write 'BREAKING CHANGE: <description>', not '{ln[:40]}'")
        key, sep, val = ln.partition(":")
        if sep and key.strip().lower() in AI_TRAILER_KEYS and AI_COAUTHOR_RE.search(val):
            problems.append(f"CM016: AI co-author/attribution trailer is not allowed: '{ln.strip()[:80]}'")
        elif AI_LINE_RE.search(ln):
            problems.append(f"CM016: AI attribution line is not allowed: '{ln.strip()[:80]}'")
    return problems
