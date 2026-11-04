"""Turn a command line into the git / gh / http-client invocations it would run."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .shellparse import BASH, POWERSHELL, Segment, Word, lex

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_PS_ENV = re.compile(r"^\$env:([A-Za-z_][A-Za-z0-9_]*)$", re.IGNORECASE)
_PREFIXES = {"env", "command", "sudo", "exec", "time", "nohup", "&", "builtin"}
_CD = {"cd", "set-location", "sl", "pushd", "push-location", "chdir"}
_SHELLS_BASH = {"bash", "sh", "zsh", "dash"}
_SHELLS_PS = {"powershell", "pwsh"}
HTTP_CLIENTS = {"curl", "wget", "http", "https", "xh", "invoke-webrequest", "invoke-restmethod", "iwr", "irm"}


@dataclass
class Invocation:
    program: str            # normalised: "git", "gh", "curl", ... or the bare name
    args: list[Word]        # words after the program
    env: dict[str, str]     # env overrides set by the command line itself
    cwd: str                # directory the command would run in
    stdin: str | None = None

    @property
    def argv(self) -> list[str]:
        return [w.value for w in self.args]


@dataclass
class _State:
    cwd: str
    env: dict[str, str] = field(default_factory=dict)


def program_name(word: str) -> str:
    base = re.split(r"[\\/]", word)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    return base


def _resolve_dir(cwd: str, target: str) -> str:
    if target in ("", "~"):
        return os.path.expanduser("~")
    target = os.path.expanduser(target)
    if re.match(r"^/[a-zA-Z]/", target) and os.name == "nt":  # git-bash /d/path
        target = f"{target[1]}:{target[2:]}"
    return os.path.normpath(os.path.join(cwd, target))


def _from_segment(seg: Segment, st: _State, dialect: str, out: list[Invocation], depth: int) -> None:
    words = list(seg.words)
    if not words:
        return
    # PowerShell: $env:NAME = value
    m = _PS_ENV.match(words[0].raw)
    if m and len(words) >= 3 and words[1].value == "=":
        st.env[m.group(1)] = words[2].value
        return
    # bash: export NAME=value ...
    if words[0].value == "export":
        for w in words[1:]:
            a = _ASSIGN.match(w.value)
            if a:
                st.env[a.group(1)] = a.group(2)
        return
    local_env: dict[str, str] = {}
    # leading NAME=value assignments and wrappers (env, sudo, & ...)
    while words:
        a = _ASSIGN.match(words[0].value) if dialect == BASH else None
        if a:
            local_env[a.group(1)] = a.group(2)
            words.pop(0)
        elif words[0].value in _PREFIXES or program_name(words[0].value) == "env":
            words.pop(0)
            while words and words[0].value.startswith("-") and words[0].value != "-":
                words.pop(0)
        else:
            break
    if not words:
        st.env.update(local_env)  # bare assignment sets a shell variable
        return
    prog = program_name(words[0].value)
    rest = words[1:]
    if prog in _CD:
        args = [w.value for w in rest if not w.value.startswith("-")]
        st.cwd = _resolve_dir(st.cwd, args[0] if args else "~")
        return
    if depth < 3 and (prog in _SHELLS_BASH or prog in _SHELLS_PS):
        flags = {"-c"} if prog in _SHELLS_BASH else {"-c", "-command", "/c"}
        for k, w in enumerate(rest[:-1]):
            if w.value.lower() in flags or (prog in _SHELLS_BASH and re.fullmatch(r"-[a-z]*c", w.value)):
                inner_dialect = BASH if prog in _SHELLS_BASH else POWERSHELL
                env = {**st.env, **local_env}
                for inv in parse(rest[k + 1].value, inner_dialect, st.cwd, env, depth + 1):
                    out.append(inv)
                return
    out.append(Invocation(prog, rest, {**st.env, **local_env}, st.cwd, seg.stdin))


def parse(command: str, dialect: str, cwd: str, env: dict[str, str] | None = None, depth: int = 0) -> list[Invocation]:
    st = _State(cwd=cwd, env=dict(env or {}))
    out: list[Invocation] = []
    for seg in lex(command, dialect):
        _from_segment(seg, st, dialect, out, depth)
    return out


# ---------------------------------------------------------------- git
@dataclass
class GitCall:
    sub: str
    args: list[Word]
    config: dict[str, str]  # -c key=value overrides (keys lower-cased)
    cwd: str
    env: dict[str, str]
    stdin: str | None

    @property
    def argv(self) -> list[str]:
        return [w.value for w in self.args]


_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env", "--super-prefix", "--list-cmds"}


def git_call(inv: Invocation) -> GitCall | None:
    words = inv.args
    cwd, config = inv.cwd, {}
    i = 0
    while i < len(words):
        v = words[i].value
        if v == "-C" and i + 1 < len(words):
            cwd = _resolve_dir(cwd, words[i + 1].value)
            i += 2
        elif v == "-c" and i + 1 < len(words):
            k, _, val = words[i + 1].value.partition("=")
            config[k.lower()] = val
            i += 2
        elif v in _GIT_GLOBAL_WITH_VALUE and i + 1 < len(words):
            i += 2
        elif v.startswith("-"):
            i += 1
        else:
            return GitCall(v, words[i + 1:], config, cwd, inv.env, inv.stdin)
    return None


def gh_path(inv: Invocation) -> list[str]:
    """Leading command words of a gh call, e.g. ['pr', 'create']."""
    path = []
    for v in inv.argv:
        if v.startswith("-"):
            break
        path.append(v)
        if len(path) == 2:
            break
    return path
