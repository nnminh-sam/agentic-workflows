"""Identity rules ID001-ID005: git author, pinned SSH key, allowed remotes, gh account."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Callable

from .commands import GitCall, Invocation
from .contracts import Contract
from .decision import Verdict
from .shellparse import BASH, lex

# runner(argv, cwd) -> (returncode, stdout)
Runner = Callable[[list[str], str], tuple[int, str]]

NETWORK_SUBS = {"push", "pull", "fetch", "clone", "ls-remote"}
_AUTHOR = re.compile(r"^\s*(?P<name>.*?)\s*<(?P<email>[^>]*)>\s*$")


def default_runner(argv: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, cwd=cwd if os.path.isdir(cwd) else None, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def _git_config(run: Runner, cwd: str, key: str) -> str | None:
    code, out = run(["git", "-C", cwd, "config", "--get", key], cwd)
    return out.strip() if code == 0 and out.strip() else None


def effective_author(call: GitCall, run: Runner, process_env: dict[str, str]) -> tuple[str | None, str | None]:
    name = email = None
    words = call.argv
    for i, a in enumerate(words):
        val = None
        if a.startswith("--author="):
            val = a.split("=", 1)[1]
        elif a == "--author" and i + 1 < len(words):
            val = words[i + 1]
        if val is not None:
            m = _AUTHOR.match(val)
            if m:
                return m.group("name"), m.group("email")
    env = {**process_env, **call.env}
    name = call.config.get("user.name") or env.get("GIT_AUTHOR_NAME") or _git_config(run, call.cwd, "user.name")
    email = call.config.get("user.email") or env.get("GIT_AUTHOR_EMAIL") or _git_config(run, call.cwd, "user.email")
    return name, email


_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def name_problem(name: str | None, params: dict) -> str | None:
    expected = params.get("expected") or []
    if not name:
        return "commit author name is unset"
    # macOS input can store names decomposed (NFD) while git config holds them composed (NFC).
    nfc = lambda v: unicodedata.normalize("NFC", v)
    if expected and nfc(name) not in [nfc(e) for e in expected]:
        return f"commit author name is '{name}', expected one of {expected}"
    return None


def email_problem(email: str | None, params: dict) -> str | None:
    expected = [e.lower() for e in (params.get("expected") or [])]
    pats = params.get("patterns") or []
    if not email:
        return "commit author email is unset"
    if expected or pats:
        if email.lower() in expected or any(re.fullmatch(p, email, re.IGNORECASE) for p in pats):
            return None
        allowed = (params.get("expected") or []) + [f"/{p}/" for p in pats]
        return f"commit author email is '{email}', expected one of {allowed}"
    if not _EMAIL.match(email):
        return f"commit author email '{email}' is not a valid email address"
    return None


def check_author(call: GitCall, c: Contract, run: Runner, process_env: dict[str, str]) -> Verdict:
    v = Verdict()
    r1, r2 = c.rule("ID001"), c.rule("ID002")
    if not (r1.enabled or r2.enabled):
        return v
    name, email = effective_author(call, run, process_env)
    prob = name_problem(name, r1.params) if r1.enabled else None
    if prob:
        want = (r1.params.get("expected") or ["<your name>"])[0]
        v.add("ID001", r1.severity, f"{prob}. Fix: git -C \"{call.cwd}\" config user.name \"{want}\"")
    prob = email_problem(email, r2.params) if r2.enabled else None
    if prob:
        want = (r2.params.get("expected") or ["<your email>"])[0]
        v.add("ID002", r2.severity, f"{prob}. Fix: git -C \"{call.cwd}\" config user.email \"{want}\"")
    return v


# ---------------------------------------------------------------- remotes & ssh
def is_local_url(url: str) -> bool:
    if url.startswith("file://"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", url):
        return True
    if "://" in url:
        return False
    # scp-like user@host:path has a ':' before any '/'
    head = url.split("/", 1)[0]
    return ":" not in head


def is_ssh_url(url: str) -> bool:
    return url.startswith("ssh://") or bool(re.match(r"^[\w.-]+@[\w.-]+:", url))


def parse_remote(url: str) -> tuple[str, str | None, str | None]:
    """(kind, host, owner) where kind is 'ssh', 'https', 'http', 'git' or 'other'."""
    m = re.match(r"^[\w.-]+@(?P<host>[\w.-]+):/?(?P<path>.*)$", url)  # scp-like git@host:owner/repo
    if m and "://" not in url:
        kind, host, path = "ssh", m.group("host"), m.group("path")
    else:
        m = re.match(r"^(?P<scheme>[a-z+]+)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.*)$", url, re.IGNORECASE)
        if not m:
            return "other", None, None
        scheme = m.group("scheme").lower()
        kind = "ssh" if scheme in ("ssh", "git+ssh", "ssh+git") else scheme if scheme in ("https", "http", "git") else "other"
        host, path = m.group("host"), m.group("path")
    owner = path.strip("/").split("/")[0] if "/" in path.strip("/") else None
    return kind, host.lower(), owner


_CLONE_WITH_VALUE = {"-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference",
                     "--reference-if-able", "--template", "-u", "--upload-pack", "-j", "--jobs", "--filter",
                     "--separate-git-dir", "--shallow-since", "--shallow-exclude", "--server-option", "--bundle-uri",
                     "--revision"}
_FETCH_WITH_VALUE = {"--depth", "--deepen", "--shallow-since", "--shallow-exclude", "-o", "--server-option",
                     "--upload-pack", "--receive-pack", "--exec", "-j", "--jobs", "--negotiation-tip",
                     "--recurse-submodules-default", "--refmap", "--push-option", "--strategy", "-s", "-X",
                     "--strategy-option", "--repo", "--force-with-lease"}


def _positionals(args: list[str], with_value: set[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out.extend(args[i + 1:])
            break
        if a.startswith("-"):
            i += 2 if (a in with_value and "=" not in a) else 1
            continue
        out.append(a)
        i += 1
    return out


def remote_urls(call: GitCall, run: Runner) -> list[str]:
    """URLs this git command would talk to (or set). Empty when unknown."""
    args = call.argv
    if call.sub == "clone":
        pos = _positionals(args, _CLONE_WITH_VALUE)
        return pos[:1]
    if call.sub == "remote":
        pos = _positionals(args, set())
        if pos[:1] == ["add"] and len(pos) >= 3:
            return [pos[2]]
        if pos[:1] == ["set-url"] and len(pos) >= 3:
            return [pos[2]]
        return []
    if call.sub in ("push", "pull", "fetch", "ls-remote"):
        pos = _positionals(args, _FETCH_WITH_VALUE)
        if call.sub == "fetch" and "--all" in args:
            code, out = run(["git", "-C", call.cwd, "remote"], call.cwd)
            names = out.split() if code == 0 else []
        else:
            names = pos[:1] or [_default_remote(call, run)]
        urls = []
        for name in names:
            if not name:
                continue
            if "/" in name or ":" in name or "\\" in name:
                urls.append(name)
                continue
            argv = ["git", "-C", call.cwd, "remote", "get-url"] + (["--push"] if call.sub == "push" else []) + [name]
            code, out = run(argv, call.cwd)
            if code == 0 and out.strip():
                urls.append(out.strip().splitlines()[0])
        return urls
    return []


def _default_remote(call: GitCall, run: Runner) -> str:
    code, branch = run(["git", "-C", call.cwd, "symbolic-ref", "--short", "-q", "HEAD"], call.cwd)
    if code == 0 and branch.strip():
        r = _git_config(run, call.cwd, f"branch.{branch.strip()}.remote")
        if r:
            return r
    return "origin"


def effective_ssh_command(call: GitCall, run: Runner, process_env: dict[str, str]) -> tuple[str | None, str]:
    env = {**process_env, **call.env}
    if env.get("GIT_SSH_COMMAND"):
        return env["GIT_SSH_COMMAND"], "GIT_SSH_COMMAND"
    if "core.sshcommand" in call.config:
        return call.config["core.sshcommand"], "-c core.sshCommand"
    if call.sub == "clone":
        args = call.argv
        for i, a in enumerate(args):
            kv = None
            if a in ("-c", "--config") and i + 1 < len(args):
                kv = args[i + 1]
            elif a.startswith("--config="):
                kv = a.split("=", 1)[1]
            if kv and kv.lower().startswith("core.sshcommand="):
                return kv.split("=", 1)[1], "clone --config core.sshCommand"
    cfg = _git_config(run, call.cwd, "core.sshCommand")
    if cfg:
        return cfg, "git config core.sshCommand"
    return None, ""


def _key_matches(key: str, wanted: str) -> bool:
    """Same file, or key ends with wanted on a path boundary (wanted may be a suffix like .ssh/id_x)."""
    norm = lambda s: os.path.expanduser(s).replace("\\", "/").lower().rstrip("/")
    raw = lambda s: s.replace("\\", "/").lower().rstrip("/")
    if norm(key) == norm(wanted):
        return True
    suffix = raw(wanted).lstrip("~").lstrip("/")
    return any(k == suffix or k.endswith("/" + suffix) for k in (raw(key), norm(key)))


def ssh_command_problems(cmd: str, key_path: str | None, required: list[str]) -> list[str]:
    """Problems with an ssh command; key_path None means any -i key is acceptable."""
    segs = lex(cmd, BASH)
    words = segs[0].values if segs else []
    problems = []
    key = None
    for i, w in enumerate(words):
        if w == "-i" and i + 1 < len(words):
            key = words[i + 1]
        elif w.startswith("-i") and len(w) > 2:
            key = w[2:]
    if not key:
        problems.append("no -i <key> in the ssh command")
    elif key_path and not _key_matches(key, key_path):
        problems.append(f"key '{key}' is not the profile's ssh_key '{key_path}'")
    opts = []
    for i, w in enumerate(words):
        if w == "-o" and i + 1 < len(words):
            opts.append(words[i + 1])
        elif w.startswith("-o") and len(w) > 2:
            opts.append(w[2:])
    canon = lambda s: re.sub(r"\s*[= ]\s*", "=", s.strip(), count=1).lower()
    have = {canon(o) for o in opts}
    for r in required:
        if canon(r) not in have:
            problems.append(f"missing -o {r}")
    return problems


def remote_problem(url: str, params: dict) -> str | None:
    if any(re.fullmatch(p, url) for p in params.get("extra_patterns") or []):
        return None
    kind, host, owner = parse_remote(url)
    if params.get("require_ssh", True) and kind != "ssh":
        return "use an SSH remote (git@<host>:<owner>/<repo>.git), not " + (kind.upper() if kind != "other" else "this URL form")
    hosts = [h.lower() for h in params.get("hosts") or []]
    if hosts and host not in hosts:
        return f"host '{host}' is not one of {hosts}"
    owners = params.get("allowed_owners") or []
    if owners and (owner or "").lower() not in [o.lower() for o in owners]:
        return f"owner '{owner}' is not one of {owners}"
    return None


def check_network(call: GitCall, c: Contract, run: Runner, process_env: dict[str, str]) -> Verdict:
    v = Verdict()
    r3, r4 = c.rule("ID003"), c.rule("ID004")
    if call.sub not in NETWORK_SUBS and call.sub != "remote":
        return v
    urls = [u for u in remote_urls(call, run) if not is_local_url(u)]
    if r4.enabled:
        for url in urls:
            prob = remote_problem(url, r4.params)
            if prob:
                v.add("ID004", r4.severity, f"remote '{url}' is not allowed: {prob}")
    if r3.enabled and call.sub in NETWORK_SUBS and any(is_ssh_url(u) for u in urls):
        cmd, src = effective_ssh_command(call, run, process_env)
        key = r3.params.get("key_path") or None
        required = r3.params.get("required_options", [])
        if not cmd:
            opts = " ".join(f"-o {o}" for o in required)
            v.add("ID003", r3.severity,
                  f"no SSH key is pinned for 'git {call.sub}'. Set it once per repo: "
                  f"git config core.sshCommand 'ssh -i \"{key or '<your key>'}\" {opts}' "
                  f"(for clone: git clone -c core.sshCommand='...' <url>)")
        else:
            probs = ssh_command_problems(cmd, key, required)
            if probs:
                v.add("ID003", r3.severity, f"SSH command from {src} is not pinned correctly: {'; '.join(probs)}")
    return v


# ---------------------------------------------------------------- gh account
def _gh_hosts_file(env: dict[str, str]) -> Path:
    if env.get("GH_CONFIG_DIR"):
        return Path(env["GH_CONFIG_DIR"]) / "hosts.yml"
    if os.name == "nt" and env.get("APPDATA"):
        return Path(env["APPDATA"]) / "GitHub CLI" / "hosts.yml"
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "gh" / "hosts.yml"


def cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "claude-git-guard"


def gh_active_login(host: str, env: dict[str, str], run: Runner, cwd: str, ttl: int = 300,
                    now: Callable[[], float] = time.time, use_cache: bool = True) -> tuple[str | None, str]:
    """Return (login or None, note). Cached by host, token env and hosts.yml mtime."""
    hosts = _gh_hosts_file(env)
    try:
        mtime = hosts.stat().st_mtime
    except OSError:
        mtime = 0
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
    key = hashlib.sha256(f"{host}|{hashlib.sha256(token.encode()).hexdigest()}|{mtime}".encode()).hexdigest()[:24]
    cfile = cache_dir() / f"gh-{key}.json"
    if use_cache and ttl > 0:
        try:
            data = json.loads(cfile.read_text(encoding="utf-8"))
            if now() - data["at"] < ttl:
                return data["login"], data["note"]
        except (OSError, ValueError, KeyError):
            pass
    code, out = run(["gh", "auth", "status", "--active", "--hostname", host, "--json", "hosts"], cwd)
    login, note = None, ""
    try:
        entries = json.loads(out)["hosts"].get(host, [])
        active = [e for e in entries if e.get("active")] or entries
        if active and active[0].get("state") == "success":
            login = active[0].get("login")
        elif active:
            note = f"gh auth for {host} is in state '{active[0].get('state')}'"
        else:
            note = f"gh is not logged in to {host}"
    except (ValueError, KeyError, AttributeError, TypeError):
        note = f"could not read 'gh auth status' (exit {code})"
    if use_cache and ttl > 0 and login:
        try:
            cache_dir().mkdir(parents=True, exist_ok=True)
            cfile.write_text(json.dumps({"at": now(), "login": login, "note": note}), encoding="utf-8")
        except OSError:
            pass
    return login, note


def clear_gh_cache() -> None:
    for f in cache_dir().glob("gh-*.json"):
        try:
            f.unlink()
        except OSError:
            pass


_GH_ACCOUNT_EXEMPT = (["auth"], ["help"], ["version"], ["--version"], ["completion"])


def check_gh_account(inv: Invocation, c: Contract, run: Runner, process_env: dict[str, str]) -> Verdict:
    v = Verdict()
    r = c.rule("ID005")
    argv = inv.argv
    if not r.enabled or not argv or any(argv[:len(e)] == e for e in _GH_ACCOUNT_EXEMPT):
        return v
    host = c.settings.get("gh_hostname", "github.com")
    for i, a in enumerate(argv):
        if a == "--hostname" and i + 1 < len(argv):
            host = argv[i + 1]
    env = {**process_env, **inv.env}
    login, note = gh_active_login(host, env, run, inv.cwd, ttl=int(c.settings.get("gh_status_cache_seconds", 300)))
    prob = gh_login_problem(login, note, host, r.params)
    if prob:
        v.add("ID005", r.severity, prob)
    return v


def gh_login_problem(login: str | None, note: str, host: str, params: dict) -> str | None:
    expected = params.get("expected_logins") or []
    if not login:
        return f"gh is not usable on {host} ({note}). Fix: gh auth login --hostname {host}"
    if expected and login.lower() not in [e.lower() for e in expected]:
        return (f"active gh account on {host} is '{login}', expected one of {expected}. "
                f"Fix: gh auth switch --hostname {host} --user {expected[0]}")
    return None
