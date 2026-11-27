"""External subprocess runner for Kaneki units (`sdlc dispatch`).

Builds a dispatch packet cleanly, launches an external CLI runner without shell=True, captures a log
under `<ticketDir>/logs/<dispatchId>.dispatch.log`, verifies exit code and result artifact, extracts
reported tokens and reviewer verdict when present, and calls `record_unit`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import HarnessError

RUNNER_TEMPLATES: dict[str, list[str]] = {
    "codex": ["codex", "exec", "-m", "{model}", "-c", "model_reasoning_effort={effort}", "{prompt}"],
    "claude": ["claude", "-p", "{prompt}", "--model", "{model}", "--output-format", "json"],
    "gemini": ["gemini", "-m", "{model}", "-p", "{prompt}", "--output-format", "json"],
    "opencode": ["opencode", "run", "-m", "{model}", "{prompt}"],
}

_VERDICT_RE = re.compile(
    r"(?im)^(?:#+\s*|[-*]\s*)?(?:\*\*)?verdict(?:\*\*)?\s*[:\-]\s*(?:\*\*)?(NEEDS\s+CHANGES|APPROVED|PASS|FAIL)\b"
)
_FAILURE_CLASS_RE = re.compile(
    r"(?im)failure[-_ ]?class(?:\*\*)?\s*[:\-]\s*(?:\*\*)?(record|artifact)\b"
)
# `codex exec` text output ends with `tokens used: N` (older releases) or `tokens used` with `N` on the next line.
_TOKEN_RE = re.compile(r"(?:total[_ ]tokens?|tokens used)(?:\s*[:=]\s*|[ \t]*\r?\n[ \t]*)([0-9][0-9,]*)\b", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Hosts whose units run only on the host's own subagents, never through `sdlc dispatch` or any runner.
# Antigravity's terms forbid third-party software wrapping its `agy` CLI on consumer plans (plan decision D6).
INTERACTIVE_ONLY_HOSTS: dict[str, str] = {
    "antigravity": "Antigravity's terms forbid third-party software wrapping its `agy` CLI on consumer plans; "
                   "run the unit with Antigravity's own subagents (invoke_subagent) inside the Antigravity session",
}


def check_dispatchable(host: str | None) -> None:
    """Refuse `sdlc dispatch` for an interactive-only host, whatever runner, command or commandTemplate is set."""
    why = INTERACTIVE_ONLY_HOSTS.get(host or "")
    if why:
        raise HarnessError(f"sdlc dispatch refuses host {host!r}: {why}. To dispatch through a CLI runner, pick "
                           "another host (--host, SDLC_HOST or `sdlc host --set`).")


def split_command(command: str, windows: bool | None = None) -> list[str]:
    """Split a `--command` string into an argv list without a shell.

    A JSON array of strings is taken as the argv as-is (the unambiguous form on every OS). Otherwise `shlex.split`
    applies on POSIX. On Windows a backslash is a path separator, not an escape, so `shlex.split` would eat it: there
    whitespace separates arguments, `"` or `'` quote a span (spaces kept, quotes removed, also mid-token as in
    `--x="a b"`), and every backslash is kept as written."""
    stripped = command.strip()
    if stripped.startswith("["):
        try:
            argv = json.loads(stripped)
        except json.JSONDecodeError:
            argv = None
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            return argv
        if argv is not None:
            raise HarnessError("--command as JSON must be a non-empty array of strings")
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return shlex.split(command)
    out: list[str] = []
    cur: list[str] = []
    in_token = False
    quote: str | None = None
    for ch in command:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            quote, in_token = ch, True
        elif ch.isspace():
            if in_token:
                out.append("".join(cur))
                cur, in_token = [], False
        else:
            cur.append(ch)
            in_token = True
    if quote:
        raise HarnessError(f"--command has an unclosed {quote} quote: {command}")
    if in_token:
        out.append("".join(cur))
    return out


def build_argv(execution: dict[str, Any], prompt: str, packet_path: str, result_path: str, *,
               tier: str, command_override: str | None = None, runner_override: str | None = None) -> list[str]:
    """Build argument vector for subprocess execution (never via shell=True)."""
    if command_override:
        template = split_command(command_override)
    elif not runner_override and execution.get("commandTemplate"):
        template = list(execution["commandTemplate"])
    else:
        runner_name = runner_override or execution.get("runner") or (
            execution.get("host") if execution.get("host") in RUNNER_TEMPLATES else None
        )
        if not runner_name or runner_name not in RUNNER_TEMPLATES:
            raise HarnessError(
                f"no subprocess runner or commandTemplate configured (host={execution.get('host')!r}); "
                "pass --runner, --command, or set runner/commandTemplate in .sdlc/host.local.json"
            )
        template = list(RUNNER_TEMPLATES[runner_name])

    model = execution.get("model") or ""
    effort = execution.get("effort") or ""
    subs = {
        "{prompt}": prompt,
        "{packet}": packet_path,
        "{result}": result_path,
        "{model}": model,
        "{effort}": str(effort),
        "{tier}": tier,
    }

    argv: list[str] = []
    i = 0
    while i < len(template):
        tok = template[i]
        next_tok = template[i + 1] if i + 1 < len(template) else ""
        # Omit `-c model_reasoning_effort={effort}` when effort is unset
        if tok == "-c" and "model_reasoning_effort={effort}" in next_tok and not effort:
            i += 2
            continue
        if "model_reasoning_effort={effort}" in tok and not effort:
            i += 1
            continue
        # Omit `-m {model}` / `--model {model}` when model is abstract `inherit`
        if tok in ("-m", "--model") and next_tok == "{model}" and (not model or model == "inherit"):
            i += 2
            continue
        expanded = tok
        for placeholder, value in subs.items():
            if placeholder in expanded:
                expanded = expanded.replace(placeholder, value)
        argv.append(expanded)
        i += 1

    if not argv:
        raise HarnessError("empty command vector for dispatch")
    return argv


def _usage_tokens(obj: Any) -> int | None:
    if isinstance(obj, dict):
        for key in ("total_tokens", "totalTokens"):
            val = obj.get(key)
            if isinstance(val, int) and val >= 0:
                return val
        parts = [obj.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                       "cache_creation_input_tokens", "prompt_tokens", "completion_tokens")
                 if isinstance(obj.get(k), int) and obj.get(k) >= 0]
        if parts:
            return sum(parts)
        for v in obj.values():
            found = _usage_tokens(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in reversed(obj):
            found = _usage_tokens(item)
            if found is not None:
                return found
    return None


def extract_tokens(stdout: str, stderr: str = "") -> int | None:
    """Extract total tokens from CLI JSON/JSONL output or textual summary."""
    stripped = stdout.strip()
    if stripped:
        try:
            tok = _usage_tokens(json.loads(stripped))
            if tok is not None:
                return tok
        except json.JSONDecodeError:
            for line in reversed(stripped.splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    tok = _usage_tokens(json.loads(line))
                    if tok is not None:
                        return tok
                except json.JSONDecodeError:
                    continue
    matches = _TOKEN_RE.findall(_ANSI_RE.sub("", stdout + "\n" + stderr))
    return int(matches[-1].replace(",", "")) if matches else None


def extract_verdict(result_text: str) -> tuple[str | None, str | None]:
    """Extract reviewer verdict (`PASS`/`FAIL`/`APPROVED`/`NEEDS CHANGES`) and optional `failureClass`."""
    vm = _VERDICT_RE.search(result_text)
    verdict = re.sub(r"\s+", " ", vm.group(1).upper()) if vm else None
    fm = _FAILURE_CLASS_RE.search(result_text)
    failure_class = fm.group(1).lower() if fm else None
    return verdict, failure_class


def run_dispatch(s: Any, node: str, unit_id: str, *, facts: str | None = None,
                 runner: str | None = None, command: str | None = None,
                 timeout: int = 900, dry_run: bool = False, no_record: bool = False) -> dict[str, Any]:
    """Build packet, execute subprocess runner, validate result, and record unit."""
    from . import cli

    # before the packet is rendered: a refused dispatch leaves nothing behind
    check_dispatchable(s.execution(node, unit_id)["host"])
    packet, exe = cli.build_packet(s, node, unit_id, facts)
    check_dispatchable(exe["host"])
    prompt = f"Read the packet at {packet['path']} (sha256 {packet['packetDigest']}) and execute it."
    argv = build_argv(
        exe,
        prompt,
        packet["path"],
        packet["resultPath"],
        tier=packet["tier"],
        command_override=command,
        runner_override=runner,
    )
    if dry_run:
        return {
            "dryRun": True,
            "dispatchId": packet["dispatchId"],
            "packet": packet,
            "argv": argv,
            "execution": packet["execution"],
        }

    binary = argv[0]
    if not (shutil.which(binary) or Path(binary).is_file()):
        raise HarnessError(f"dispatch runner executable not found: {binary}")

    logs_dir = s.ticket.dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{packet['dispatchId']}.dispatch.log"

    def give_up(message: str) -> HarnessError:
        """A dispatch that produced no usable result is still recorded (status abandoned, with what it spent), so no
        unit log is left without a record; the next dispatch of the unit is the next round."""
        if not no_record:
            try:
                cli.record_unit(s, node, unit_id, None, exe["model"], status="abandoned", reason=message)
            except HarnessError:
                pass
        return HarnessError(message)

    try:
        proc = subprocess.run(argv, cwd=str(s.pdir), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            f"$ {shlex.join(argv)}\nTIMEOUT after {timeout}s\n\n--- STDOUT ---\n{exc.stdout or ''}\n\n--- STDERR ---\n{exc.stderr or ''}\n",
            encoding="utf-8",
        )
        raise give_up(f"dispatch {packet['dispatchId']} timed out after {timeout}s; see {log_path}")

    log_path.write_text(
        f"$ {shlex.join(argv)}\nexit={proc.returncode}\n\n--- STDOUT ---\n{proc.stdout}\n\n--- STDERR ---\n{proc.stderr}\n",
        encoding="utf-8",
    )

    if proc.returncode != 0:
        raise give_up(f"dispatch {packet['dispatchId']} failed with exit code {proc.returncode}; see {log_path}")

    result_path = Path(packet["resultPath"])
    if not result_path.is_file():
        raise give_up(f"G7: dispatch produced no result file at {result_path}; see {log_path}")

    tokens = extract_tokens(proc.stdout, proc.stderr)
    unit_spec = s.unit(node, unit_id)
    verdict: str | None = None
    failure_class: str | None = None
    if unit_spec.get("role") == "reviewer":
        verdict, failure_class = extract_verdict(result_path.read_text(encoding="utf-8"))

    res: dict[str, Any] = {
        "ok": True,
        "dispatchId": packet["dispatchId"],
        "packet": packet,
        "argv": argv,
        "log": str(log_path),
        "tokens": tokens,
        "verdict": verdict,
        "failureClass": failure_class,
    }
    if not no_record:
        rec_out = cli.record_unit(
            s, node, unit_id, result_path, exe["model"],
            status="ok", tokens=tokens, verdict=verdict, failure_class=failure_class,
        )
        res["recorded"] = rec_out["recorded"]
        res["nodeTokens"] = rec_out["nodeTokens"]
        if "warning" in rec_out:
            res["warning"] = rec_out["warning"]
    return res
