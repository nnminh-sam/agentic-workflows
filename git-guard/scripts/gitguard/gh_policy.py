"""github-cli rules GH001-GH007."""
from __future__ import annotations

import re

from . import commit_message
from .commands import HTTP_CLIENTS, Invocation, gh_path
from .contracts import Contract
from .decision import Verdict


def _flag_values(argv: list[str], short: str | None, long: str) -> list[str]:
    """Values of a flag given as -X v, -Xv, --long v or --long=v."""
    out = []
    for i, a in enumerate(argv):
        if a == long or (short and a == short):
            if i + 1 < len(argv):
                out.append(argv[i + 1])
        elif a.startswith(long + "="):
            out.append(a.split("=", 1)[1])
        elif short and a.startswith(short) and len(a) > len(short) and not a.startswith("--"):
            out.append(a[len(short):])
    return out


def _has(argv: list[str], *flags: str) -> bool:
    return any(a == f or a.startswith(f + "=") for a in argv for f in flags)


def check_http_client(inv: Invocation, c: Contract) -> Verdict:
    v = Verdict()
    r = c.rule("GH001")
    if not r.enabled or inv.program not in HTTP_CLIENTS:
        return v
    hosts = r.params.get("hosts", [])
    for a in inv.argv:
        for h in hosts:
            if re.search(rf"(^|[/@.]){re.escape(h)}(?=$|[/:?])", a, re.IGNORECASE):
                v.add("GH001", r.severity, f"{inv.program} call to {h}: use 'gh api' instead so the checked gh account is used")
                return v
    return v


def _owner_of(ref: str) -> str | None:
    ref = ref.strip()
    m = re.match(r"^(?:https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)?([^/\s]+)/[^/\s]+?(?:\.git)?/?$", ref)
    return m.group(1) if m else None


_REPO_ARG_CMDS = {"clone", "view", "create", "fork", "sync", "edit", "delete", "archive", "unarchive", "rename", "set-default"}


def check_gh(inv: Invocation, c: Contract, commit_contract: Contract) -> Verdict:
    v = Verdict()
    argv = inv.argv
    path = gh_path(inv)

    # GH002 command policy
    r = c.rule("GH002")
    if r.enabled:
        for entry in r.params.get("commands", []):
            want = entry["match"].split()
            if path[:len(want)] == want:
                if entry["decision"] != "allow":
                    v.add("GH002", entry["decision"], f"gh {entry['match']}: {entry.get('reason', 'restricted command')}")
                break

    # GH003 api mutations
    r = c.rule("GH003")
    if r.enabled and path[:1] == ["api"]:
        endpoint = path[1] if len(path) > 1 else ""
        methods = [m.upper() for m in _flag_values(argv, "-X", "--method")]
        fields = _flag_values(argv, "-f", "--raw-field") + _flag_values(argv, "-F", "--field")
        has_input = _has(argv, "--input")
        if endpoint == "graphql":
            if any(re.search(r"^\s*mutation\b|query=\s*mutation\b", f) for f in fields) or has_input:
                v.add("GH003", r.severity, "gh api graphql mutation")
        else:
            method = methods[-1] if methods else ("POST" if (fields or has_input) else "GET")
            if method == "DELETE":
                v.add("GH003", r.params.get("delete_severity", r.severity), f"gh api DELETE {endpoint}")
            elif method != "GET":
                v.add("GH003", r.severity, f"gh api {method} {endpoint}")

    # GH004 / GH005 pull requests
    if path in (["pr", "create"], ["pr", "edit"]):
        titles = _flag_values(argv, "-t", "--title")
        title_dynamic = any(
            w.dynamic and ((i > 0 and inv.args[i - 1].value in ("-t", "--title")) or w.value.startswith(("--title=", "-t")))
            for i, w in enumerate(inv.args))
        r4 = c.rule("GH004")
        if path == ["pr", "create"] and r4.enabled:
            filled = _has(argv, "--fill", "-f", "--fill-first", "--fill-verbose", "--web", "-w")
            body = _has(argv, "--body", "-b", "--body-file", "-F", "--template", "-T") or any(
                a.startswith(("-b", "-F", "-T")) and len(a) > 2 and not a.startswith("--") for a in argv)
            if not filled and not (titles and body):
                missing = [x for x, ok in (("--title", bool(titles)), ("--body/--body-file", body)) if not ok]
                v.add("GH004", r4.severity, f"gh pr create would prompt interactively; pass {' and '.join(missing)} (or --fill)")
        r5 = c.rule("GH005")
        if r5.enabled and title_dynamic:
            v.add("GH005", "ask", "PR title uses shell expansion and cannot be checked")
        elif r5.enabled:
            for t in titles:
                res = commit_message.check(t, commit_contract, header_only=True, severity_override=r5.severity)
                for f in res.findings:
                    v.add("GH005", f.severity, f"PR title: {f.message} ({f.rule_id})")

    # GH006 repository owner scope
    r = c.rule("GH006")
    allowed = r.params.get("allowed_owners") or []
    if r.enabled and allowed:  # no profile owners -> nothing to scope against
        refs = _flag_values(argv, "-R", "--repo")
        if path[:1] == ["repo"] and len(path) == 2 and path[1] in _REPO_ARG_CMDS:
            pos = [a for a in argv[2:] if not a.startswith("-")]
            if pos and "/" in pos[0]:
                refs.append(pos[0])
        if path[:1] == ["api"] and len(path) > 1:
            m = re.match(r"^/?repos/([^/{]+)/", path[1])
            if m:
                refs.append(f"{m.group(1)}/_")
        for ref in refs:
            owner = _owner_of(ref)
            if owner and owner.lower() not in [o.lower() for o in allowed]:
                v.add("GH006", r.severity, f"targets repository '{ref}' outside allowed owners {allowed}")

    # GH007 repo create visibility
    r = c.rule("GH007")
    if r.enabled and path == ["repo", "create"]:
        if _has(argv, "--public"):
            v.add("GH007", r.params.get("public_severity", "ask"), "creates a PUBLIC repository")
        elif not _has(argv, "--private", "--internal"):
            v.add("GH007", r.severity, "gh repo create must pass --private, --public or --internal")
    return v
