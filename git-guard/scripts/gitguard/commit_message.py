"""Commit message convention checks (rules CM003-CM015) and message extraction from git commit."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

from .commands import GitCall
from .contracts import Contract
from .decision import Verdict

_HEADER = re.compile(r"^(?P<type>[^\s(:!]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<subject>.*)$")
_BREAKING_ANY = re.compile(r"^breaking[ -]change\s*:", re.IGNORECASE)
_BREAKING_OK = re.compile(r"^BREAKING[ -]CHANGE: \S")
HEADER_RULES = ("CM003", "CM004", "CM005", "CM006", "CM007", "CM008", "CM009", "CM010", "CM014")


def cleanup(message: str) -> str:
    """git's default 'whitespace' cleanup: strip trailing spaces, squeeze and trim blank lines."""
    lines = [ln.rstrip() for ln in message.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "" and (not out or out[-1] == ""):
            continue
        out.append(ln)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def check(message: str, contract: Contract, header_only: bool = False, severity_override: str | None = None) -> Verdict:
    """Validate a commit message (or a PR title when header_only) against the contract."""
    v = Verdict()
    msg = cleanup(message)
    lines = msg.split("\n") if msg else [""]
    header = lines[0]

    def sev(rid: str) -> str:
        s = contract.rule(rid).severity
        if severity_override and s in ("deny", "ask"):
            return severity_override
        return s

    def p(rid: str) -> dict:
        return contract.rule(rid).params

    for pat in contract.settings.get("exempt_header_patterns", []):
        if re.search(pat, header):
            return v

    if not header.strip():
        v.add("CM003", sev("CM003"), "message is empty")
        return v

    if len(header) > p("CM007").get("max", 72):
        v.add("CM007", sev("CM007"), f"header is {len(header)} chars, max {p('CM007').get('max', 72)}")

    for pat in p("CM014").get("patterns", []):
        if re.search(pat, header, re.IGNORECASE):
            v.add("CM014", sev("CM014"), f"header matches forbidden pattern /{pat}/")
            break

    types = contract.settings.get("types", [])
    m = _HEADER.match(header)
    if not m:
        hint = ""
        if re.match(r"^\w+(\([^)]*\))?!?:\S", header):
            hint = " (add one space after the colon)"
        elif re.match(r"^\w+ \(", header):
            hint = " (no space between type and scope)"
        elif ":" not in header:
            hint = f" (start with a type, e.g. '{types[0] if types else 'feat'}: ...')"
        v.add("CM003", sev("CM003"), f"header '{header}' is not '<type>(<scope>)!: <subject>'{hint}")
    else:
        typ, scope, subject = m.group("type"), m.group("scope"), m.group("subject")
        if typ not in types:
            v.add("CM004", sev("CM004"), f"type '{typ}' is not one of: {', '.join(types)}")
        if scope is not None:
            sp = p("CM005")
            allowed = sp.get("allowed_scopes", [])
            if not scope or not re.match(sp.get("scope_pattern", r"^.+$"), scope):
                v.add("CM005", sev("CM005"), f"scope '{scope}' does not match {sp.get('scope_pattern')}")
            elif allowed and scope not in allowed:
                v.add("CM005", sev("CM005"), f"scope '{scope}' is not one of: {', '.join(allowed)}")
        mn = p("CM006").get("min", 3)
        if len(subject.strip()) < mn:
            v.add("CM006", sev("CM006"), f"subject '{subject}' is shorter than {mn} chars")
        if subject.rstrip().endswith("."):
            v.add("CM008", sev("CM008"), "subject ends with a period")
        if subject[:1].isupper() and not subject[1:2].isupper():
            v.add("CM009", sev("CM009"), f"subject should start lower case: '{subject}'")
        first = subject.split(" ", 1)[0].lower()
        if first in [w.lower() for w in p("CM010").get("banned_first_words", [])]:
            v.add("CM010", sev("CM010"), f"use the imperative mood, not '{first}'")

    if header_only:
        return v

    if len(lines) > 1 and lines[1].strip():
        v.add("CM011", sev("CM011"), "the second line must be blank (separate header and body)")
    mx = p("CM012").get("max", 100)
    for n, ln in enumerate(lines[1:], start=2):
        if len(ln) > mx and "://" not in ln:
            v.add("CM012", sev("CM012"), f"line {n} is {len(ln)} chars, max {mx}")
            break
    for ln in lines[1:]:
        if _BREAKING_ANY.match(ln) and not _BREAKING_OK.match(ln):
            v.add("CM013", sev("CM013"), f"write 'BREAKING CHANGE: <description>', not '{ln[:40]}'")
    for key in p("CM015").get("trailers", []):
        if not any(re.match(rf"^{re.escape(key)}:\s*\S", ln, re.IGNORECASE) for ln in lines[1:]):
            v.add("CM015", sev("CM015"), f"missing trailer '{key}: ...'")
    return v


# ---------------------------------------------------------------- extraction
@dataclass
class Message:
    kind: str           # "text" | "none" | "reuse" | "dynamic" | "unreadable"
    text: str = ""
    detail: str = ""


def _short_opt_value(arg: str, letter: str) -> tuple[bool, str | None]:
    """For clustered short options like -am or -mfoo: (has letter, inline value or None)."""
    if not arg.startswith("-") or arg.startswith("--") or len(arg) < 2:
        return False, None
    body = arg[1:]
    idx = body.find(letter)
    if idx < 0 or not all(ch.isalpha() for ch in body[:idx]):
        return False, None
    tail = body[idx + 1:]
    return True, (tail if tail else None)


def extract(call: GitCall, read_file: Callable[[str], str] | None = None) -> Message:
    """Find the message a `git commit` would use."""
    read_file = read_file or (lambda p: open(p, encoding="utf-8").read())
    parts: list[str] = []
    dynamic = False
    file_msg: Message | None = None
    reuse = False
    no_edit = False
    words = call.args
    i = 0
    while i < len(words):
        w = words[i]
        a = w.value
        nxt = words[i + 1] if i + 1 < len(words) else None
        if a == "--":
            break
        if a in ("--message",) or a.startswith("--message="):
            val = a.split("=", 1)[1] if "=" in a else (nxt.value if nxt else "")
            src = w if "=" in a else nxt
            parts.append(val)
            dynamic = dynamic or bool(src and src.dynamic)
            i += 1 if "=" in a else 2
            continue
        if a in ("--file",) or a.startswith("--file="):
            path = a.split("=", 1)[1] if "=" in a else (nxt.value if nxt else "")
            file_msg = _read_msg_file(path, call, read_file)
            i += 1 if "=" in a else 2
            continue
        if a in ("--no-edit",):
            no_edit = True
        if a in ("--reuse-message", "--reedit-message", "--fixup", "--squash") or a.startswith(
                ("--reuse-message=", "--reedit-message=", "--fixup=", "--squash=")):
            reuse = True
            i += 1 if "=" in a else 2
            continue
        has_m, val = _short_opt_value(a, "m")
        if has_m:
            if val is None:
                parts.append(nxt.value if nxt else "")
                dynamic = dynamic or bool(nxt and nxt.dynamic)
                i += 2
            else:
                parts.append(val)
                dynamic = dynamic or w.dynamic
                i += 1
            continue
        has_f, val = _short_opt_value(a, "F")
        if has_f:
            path = val if val is not None else (nxt.value if nxt else "")
            file_msg = _read_msg_file(path, call, read_file)
            i += 1 if val is not None else 2
            continue
        for letter in ("C", "c"):
            has, val = _short_opt_value(a, letter)
            if has:
                reuse = True
                i += 1 if val is not None else 2
                break
        else:
            i += 1
            continue
    if parts:
        if dynamic:
            return Message("dynamic", "\n\n".join(parts), "message uses shell variables or command output")
        return Message("text", "\n\n".join(parts))
    if file_msg:
        return file_msg
    if reuse or no_edit:
        return Message("reuse")
    return Message("none")


def _read_msg_file(path: str, call: GitCall, read_file: Callable[[str], str]) -> Message:
    if path == "-":
        if call.stdin is None:
            return Message("dynamic", detail="message is read from stdin that the hook cannot see")
        return Message("text", call.stdin)
    full = path if os.path.isabs(path) else os.path.join(call.cwd, path)
    try:
        return Message("text", read_file(full))
    except OSError:
        return Message("unreadable", detail=f"message file not found yet: {path}")
