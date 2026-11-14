"""kaneki-onboarding: detect a project's profile from evidence, verify it by running, write it with provenance.

Three steps, each a driver command:
  scan    deterministic detectors → candidates.json (every value + evidence + confidence, plus questions)
  verify  run the detected read-only commands; record exit codes in candidates.json
  write   candidates + answers (+ existing profile on refresh) → sdlc.profile.json, the evidence sidecar,
          and project-local stack adapters; user-set values are never overwritten.

Never reads `.env` (only `.env.example`-style files, key names and URL values), never runs install or
mutating commands, never runs tests unless allowed.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import HarnessError

GENERATOR = "kaneki-onboarding@0.1.0"
SKIP_DIRS = {"node_modules", "vendor", ".git", ".sdlc", "dist", "build", ".venv", "venv", "__pycache__", ".dart_tool"}
DEFAULT_SURFACE_ADAPTER = {"web": "web-playwright", "api": "api-http", "db": "db-sql", "cli": "cli", "batch": "cli",
                           "mobile": "mobile-flutter", "device": "device-manual"}
DRAFTS_BY_DELIVERY = {"github-pr": ["commit-msg", "pr-body", "tracker-reply"], "gitlab-mr": ["commit-msg", "pr-body", "tracker-reply"], "manual": ["commit-msg", "pr-body"]}
BUNDLED_STACKS = {"node-ts", "python", "php-laravel", "flutter-dart"}
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.dist", ".env.template")
DB_IMAGES = re.compile(r"postgres|mysql|mariadb|mongo|redis|rabbitmq|mssql|sqlserver|oracle", re.I)

ROLE_PATTERNS = [  # order matters: the first match wins
    ("install", re.compile(r"\b(npm (ci|install|i)\b|yarn( install)?\s*$|pnpm (i|install)\b|pip install|poetry install|composer install|bundle install|flutter pub get|dart pub get|go mod download|apt-get|brew install)")),
    ("mutating", re.compile(r"(--fix\b|--write\b|\bmigrate\b|db:seed|\bdeploy\b|\bpublish\b|git push|docker (compose )?up|rm -rf)")),
    ("test", re.compile(r"\b(test|tests|pytest|phpunit|jest|vitest|mocha|go test)\b")),
    ("lint", re.compile(r"\b(lint|eslint|ruff check|ruff\b|flake8|pylint|phpstan|psalm|analyze|golangci-lint|mypy|tsc --noEmit|typecheck)\b")),
    ("format", re.compile(r"\b(prettier|black|ruff format|pint|dart format|gofmt|format)\b")),
    ("build", re.compile(r"\b(build|tsc|compile)\b")),
    ("doctor", re.compile(r"(--version\b|\bversion\s*$|\bdoctor\b)")),
]


# --- small helpers -----------------------------------------------------------------------------------

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git(path: Path, *args: str) -> str | None:
    try:
        # git emits UTF-8; decoding with the locale (cp1252/cp932 on Windows) breaks on non-ASCII commits
        res = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def find_bash() -> list[str] | None:
    """The same shell on every OS, because CI commands are written for bash.
    Windows: Git Bash (next to git.exe), never System32\\bash.exe (that is the WSL launcher, another OS)."""
    import os
    import shutil
    if os.environ.get("SDLC_BASH"):
        return [os.environ["SDLC_BASH"]]
    if os.name != "nt":
        for sh in ("/bin/bash", "/usr/bin/bash", "/bin/sh"):
            if Path(sh).exists():
                return [sh]
        return None
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parent.parent  # .../Git/cmd/git.exe → .../Git
        for cand in (git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"):
            if cand.exists():
                return [str(cand)]
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return [found]
    return None


def run_command(cmd: str, cwd: str, timeout: int) -> tuple[subprocess.CompletedProcess[str], str]:
    bash = find_bash()
    kw: dict[str, Any] = {"cwd": cwd, "capture_output": True, "text": True, "encoding": "utf-8",
                          "errors": "replace", "timeout": timeout}
    if bash:
        return subprocess.run([*bash, "-c", cmd], **kw), Path(bash[0]).name
    return subprocess.run(cmd, shell=True, **kw), "system shell"


def find_file(folder: Path, name: str) -> Path | None:
    """Case-insensitive lookup (README.md vs Readme.md), so Linux finds what Windows and macOS find."""
    exact = folder / name
    if exact.is_file():
        return exact
    try:
        return next((p for p in folder.iterdir() if p.is_file() and p.name.lower() == name.lower()), None)
    except OSError:
        return None


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _line_of(path: Path, needle: str) -> int:
    for i, line in enumerate(_read(path).splitlines(), 1):
        if needle in line:
            return i
    return 1


def _rel(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        return rel or "."
    except ValueError:
        return path.resolve().as_posix()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return s or "project"


def classify(cmd: str) -> str:
    for role, pat in ROLE_PATTERNS:
        if pat.search(cmd):
            return role
    return "other"


def ev(confidence: str, *evidence: str, status: str = "detected") -> dict[str, Any]:
    return {"confidence": confidence, "evidence": [e for e in evidence if e], "status": status}


# --- detectors ---------------------------------------------------------------------------------------

def find_repos(root: Path, extra: list[str]) -> list[Path]:
    repos: list[Path] = []
    if (root / ".git").exists():
        repos.append(root)
    else:
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in SKIP_DIRS and not child.name.startswith("."):
                if (child / ".git").exists():
                    repos.append(child)
                else:
                    repos += [g for g in sorted(child.iterdir()) if g.is_dir() and g.name not in SKIP_DIRS and (g / ".git").exists()]
    for e in extra:
        p = Path(e).resolve()
        if not (p / ".git").exists():
            raise HarnessError(f"--repo {e} is not a git checkout")
        if p not in [r.resolve() for r in repos]:
            repos.append(p)
    return repos


def detect_stack(repo: Path) -> tuple[str, str, list[str], list[str]]:
    """(stack, confidence, evidence, alternatives)"""
    hits = []
    if (repo / "pubspec.yaml").exists():
        hits.append(("flutter-dart", "high", "pubspec.yaml"))
    if (repo / "composer.json").exists():
        hits.append(("php-laravel", "high", "composer.json + artisan") if (repo / "artisan").exists() else ("php", "medium", "composer.json"))
    if (repo / "package.json").exists():
        pkg = _read(repo / "package.json")
        ts = (repo / "tsconfig.json").exists() or '"typescript"' in pkg
        hits.append(("node-ts", "high" if ts else "medium", "package.json" + (" + tsconfig.json" if (repo / "tsconfig.json").exists() else "")))
    for f in ("pyproject.toml", "requirements.txt", "setup.py"):
        if (repo / f).exists():
            hits.append(("python", "high", f))
            break
    if (repo / "go.mod").exists():
        hits.append(("go", "high", "go.mod"))
    for f in ("pom.xml", "build.gradle", "build.gradle.kts"):
        if (repo / f).exists():
            hits.append(("java", "high", f))
            break
    if list(repo.glob("*.sln")) or list(repo.glob("*.csproj")):
        hits.append(("csharp-dotnet", "high", "*.sln / *.csproj"))
    if not hits:
        return "unknown", "low", [], []
    stack, conf, evid = hits[0]
    alts = [h[0] for h in hits[1:]]
    if alts:
        conf = "medium"
    return stack, conf, [f"{evid} found"], alts


def _walk_files(top: Path, name_re: re.Pattern[str], limit: int) -> list[Path]:
    """os.walk with SKIP_DIRS pruned (rglob would descend into node_modules first)."""
    import os
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        out += [Path(dirpath) / f for f in filenames if name_re.search(f)]
        if len(out) >= limit:
            break
    return out[:limit]


def _deps(repo: Path) -> tuple[set[str], dict[str, Any]]:
    pkg: dict[str, Any] = {}
    if (repo / "package.json").exists():
        try:
            pkg = json.loads(_read(repo / "package.json"))
        except json.JSONDecodeError:
            pkg = {}
    names = set(pkg.get("dependencies", {})) | set(pkg.get("devDependencies", {}))
    for f in ("pyproject.toml", "requirements.txt"):
        text = _read(repo / f).lower()
        names |= set(re.findall(r"^\s*\"?([a-z0-9_.-]+)", text, re.M))
    return names, pkg


def detect_surfaces(repo: Path, stack: str) -> tuple[list[str], dict[str, list[str]]]:
    deps, pkg = _deps(repo)
    found: dict[str, list[str]] = {}

    def add(surface: str, why: str) -> None:
        found.setdefault(surface, []).append(why)

    if stack == "node-ts" or (repo / "package.json").exists():
        for d in ("express", "fastify", "koa", "@nestjs/core", "@hapi/hapi", "hono"):
            if d in deps:
                add("api", f"package.json depends on {d}")
        for d in ("react", "vue", "svelte", "next", "nuxt", "@angular/core"):
            if d in deps:
                add("web", f"package.json depends on {d}")
        for d in ("pg", "mysql", "mysql2", "prisma", "@prisma/client", "typeorm", "knex", "sequelize", "mongoose", "mongodb"):
            if d in deps:
                add("db", f"package.json depends on {d}")
        if pkg.get("bin"):
            add("cli", "package.json has a bin field")
    if stack == "python":
        for d in ("fastapi", "flask", "django", "starlette"):
            if d in deps:
                add("api", f"depends on {d}")
        if "django" in deps:
            add("web", "depends on django")
        for d in ("sqlalchemy", "psycopg", "psycopg2", "psycopg2-binary", "pymysql", "django"):
            if d in deps:
                add("db", f"depends on {d}")
        for d in ("click", "typer"):
            if d in deps:
                add("cli", f"depends on {d}")
    if stack == "php-laravel":
        if (repo / "routes" / "web.php").exists():
            add("web", "routes/web.php exists")
        if (repo / "routes" / "api.php").exists():
            add("api", "routes/api.php exists")
        if (repo / "app" / "Console" / "Commands").is_dir():
            add("batch", "app/Console/Commands exists")
        if (repo / "database" / "migrations").is_dir():
            add("db", "database/migrations exists")
    if stack == "flutter-dart":
        add("mobile", "pubspec.yaml (Flutter app)")
    if not found:
        fallback = {"php-laravel": "web", "flutter-dart": "mobile"}.get(stack, "cli")
        add(fallback, f"fallback for stack {stack}: nothing more specific detected")
    return sorted(found), found


def ci_commands(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    files = sorted((repo / ".github" / "workflows").glob("*.y*ml")) if (repo / ".github" / "workflows").is_dir() else []
    for f in files:
        lines = _read(f).splitlines()
        i = 0
        while i < len(lines):
            m = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", lines[i])
            if m:
                indent, val = len(m.group(1)), m.group(2).strip()
                if val in ("|", ">", "|-", ">-", ""):
                    j = i + 1
                    while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                        if lines[j].strip() and not lines[j].strip().startswith("#"):
                            out.append(_cmd(lines[j].strip(), "ci", f"{_rel(f, repo)}:{j + 1}"))
                        j += 1
                    i = j
                    continue
                out.append(_cmd(val.strip("'\""), "ci", f"{_rel(f, repo)}:{i + 1}"))
            i += 1
    gl = repo / ".gitlab-ci.yml"
    if gl.exists():
        lines = _read(gl).splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)script:\s*$", line)
            if not m:
                continue
            for j in range(i + 1, len(lines)):
                n = re.match(r"^(\s*)-\s+(.*)$", lines[j])
                if not n or len(n.group(1)) < len(m.group(1)):
                    break
                out.append(_cmd(n.group(2).strip("'\""), "ci", f".gitlab-ci.yml:{j + 1}"))
    return out


def script_commands(repo: Path) -> list[dict[str, Any]]:
    out = []
    pj = repo / "package.json"
    if pj.exists():
        try:
            scripts = json.loads(_read(pj)).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        pm = "pnpm" if (repo / "pnpm-lock.yaml").exists() else "yarn" if (repo / "yarn.lock").exists() else "npm"
        for name in scripts:
            if re.match(r"^(lint|test|build|format|typecheck|check)(:.*)?$", name):
                cmd = f"{pm} test" if name == "test" else f"{pm} run {name}"
                needle = '"' + name + '"'
                out.append(_cmd(cmd, "package-script", f"package.json:{_line_of(pj, needle)}"))
    return out


def _cmd(cmd: str, source: str, evidence: str) -> dict[str, Any]:
    return {"cmd": cmd, "role": classify(cmd), "source": source, "evidence": evidence,
            "runnable": "${{" not in cmd and "$CI_" not in cmd and "secrets." not in cmd}


def env_examples(repo: Path) -> tuple[list[str], dict[str, str], list[str]]:
    keys: list[str] = []
    urls: dict[str, str] = {}
    files: list[str] = []
    for name in ENV_EXAMPLES:
        f = repo / name
        if not f.exists():
            continue
        files.append(name)
        for line in _read(f).splitlines():
            m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if not m:
                continue
            keys.append(m.group(1))
            val = m.group(2).strip().strip("'\"")
            if re.search(r"URL|HOST", m.group(1)) and re.match(r"^https?://[^\s@]+$", val):
                urls[m.group(1)] = val
    return sorted(set(keys)), urls, files


def compose_services(root: Path) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    for f in sorted(root.glob("docker-compose*.y*ml")) + sorted(root.glob("compose*.y*ml")):
        lines = _read(f).splitlines()
        in_services, cur = False, None
        for i, line in enumerate(lines, 1):
            if re.match(r"^services:\s*$", line):
                in_services = True
                continue
            if in_services and re.match(r"^\S", line):
                in_services = False
            if not in_services:
                continue
            m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
            if m:
                cur = {"name": m.group(1), "image": "", "ports": [], "evidence": f"{f.name}:{i}"}
                services.append(cur)
                continue
            if cur is None:
                continue
            im = re.match(r"^\s+image:\s*(\S+)", line)
            if im:
                cur["image"] = im.group(1)
            pm = re.match(r"^\s+-\s*\"?(\d+):(\d+)\"?", line)
            if pm:
                cur["ports"].append(int(pm.group(1)))
    return services


def detect_language(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    cjk = sum(1 for c in letters if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    vi = sum(1 for c in letters if c in "ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉĩọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")
    if cjk / len(letters) > 0.05:
        return "ja"
    if vi / len(letters) > 0.02:
        return "vi"
    return "en"


def parse_remote(url: str | None) -> dict[str, str]:
    if not url:
        return {}
    m = re.match(r"^(?:git@|ssh://git@|https?://)([^/:]+)[/:]([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not m:
        return {"raw": url}
    return {"host": m.group(1), "owner": m.group(2), "name": m.group(3), "slug": f"{m.group(2)}/{m.group(3)}"}


def git_identity_candidates(repos: list[Path]) -> list[dict[str, str]]:
    """Accounts this project could commit as, most specific first: repo-local config, global config,
    then the most frequent recent authors. Only shown to the user as options; never applied unasked."""
    from collections import Counter
    from .gitid import EMAIL_RE
    cands: list[dict[str, str]] = []

    import unicodedata

    def key(name: str, email: str) -> tuple[str, str]:
        # git config and commit authors may store the same name in different Unicode forms (NFC vs NFD: "Nguyễn")
        return unicodedata.normalize("NFC", name).strip().casefold(), email.strip().lower()

    def add(name: str | None, email: str | None, source: str, repo: str = "") -> None:
        if name and email and EMAIL_RE.match(email.strip()) and not any(
                key(c["name"], c["email"]) == key(name, email) for c in cands):
            cands.append({"name": unicodedata.normalize("NFC", name).strip(), "email": email.strip(),
                          "source": source, "repo": repo})

    for rp in repos:
        add(_git(rp, "config", "--local", "--get", "user.name"), _git(rp, "config", "--local", "--get", "user.email"),
            f"repo-local git config of `{rp.name}`", rp.name)
    add(_git(repos[0], "config", "--global", "--get", "user.name"), _git(repos[0], "config", "--global", "--get", "user.email"),
        "your global git config")
    authors: Counter[tuple[str, str]] = Counter()
    for rp in repos:
        for line in (_git(rp, "log", "-n", "200", "--format=%an%x09%ae") or "").splitlines():
            if "\t" in line:
                authors[tuple(line.split("\t", 1))] += 1  # type: ignore[index]
    for (name, email), count in authors.most_common(2):
        add(name, email, f"author of {count} of the last commits")
    return cands


def tracker_signals(root: Path, repos: list[Path]) -> dict[str, Any]:
    keys: dict[str, int] = {}
    hashes = 0
    samples: list[str] = []
    for repo in repos:
        subjects = (_git(repo, "log", "-n", "300", "--format=%s") or "").splitlines()
        branches = (_git(repo, "branch", "-a", "--format=%(refname:short)") or "").splitlines()
        for text in subjects + branches:
            for k, _n in re.findall(r"\b([A-Z][A-Z0-9_]{1,9})-(\d+)\b", text):
                keys[k] = keys.get(k, 0) + 1
                if len(samples) < 3:
                    samples.append(text[:80])
            hashes += len(re.findall(r"(?:^|\s|\()#(\d+)\b", text))
    hosts: dict[str, str] = {}
    docs = [p for p in (find_file(root, "README.md"), find_file(root, "CONTRIBUTING.md")) if p]
    for repo in repos:
        docs += [p for p in (find_file(repo, "README.md"), find_file(repo, "CONTRIBUTING.md")) if p and p not in docs]
        if (repo / "docs").is_dir():
            docs += sorted((repo / "docs").rglob("*.md"))[:50]
    for f in docs:
        text = _read(f, 200_000)
        for pat, name in ((r"[a-z0-9-]+\.atlassian\.net", "jira"), (r"[a-z0-9-]+\.backlog\.(jp|com)", "backlog"), (r"linear\.app", "linear")):
            m = re.search(pat, text)
            if m and name not in hosts:
                hosts[name] = f"{_rel(f, root)}:{_line_of(f, m.group(0))} mentions {m.group(0)}"
    templates = [r for r in repos if (r / ".github" / "ISSUE_TEMPLATE").is_dir()]
    top = max(keys, key=keys.get) if keys else None
    return {"keys": keys, "topKey": top, "hashRefs": hashes, "samples": samples, "hosts": hosts,
            "issueTemplates": [_rel(r / ".github" / "ISSUE_TEMPLATE", root) for r in templates]}


# --- scan --------------------------------------------------------------------------------------------

def scan(root: Path, extra_repos: list[str]) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise HarnessError(f"not a directory: {root}")
    repo_paths = find_repos(root, extra_repos)
    if not repo_paths:
        raise HarnessError(f"G7: no git repository found in {root} or one level below; pass --repo <path>")
    P: dict[str, Any] = {}      # proposal: flat path -> value
    E: dict[str, Any] = {}      # evidence: flat path -> {confidence, evidence[], status}
    Q: list[dict[str, Any]] = []
    gaps: list[str] = []
    repos_info: dict[str, Any] = {}

    readme = find_file(root, "README.md") or find_file(repo_paths[0], "README.md")
    pid = _slug(root.name)
    P["project.id"], E["project.id"] = pid, ev("high", f"project folder name `{root.name}`")
    title = None
    if readme:
        m = re.search(r"^#\s+(.+)$", _read(readme), re.M)
        title = m.group(1).strip() if m else None
    P["project.name"] = title or root.name
    E["project.name"] = ev("medium", f"{_rel(readme, root)}:{_line_of(readme, '# ')} first heading") if title else ev("low", "folder name", status="default")

    used_ids: set[str] = set()
    remotes: list[dict[str, str]] = []
    all_services = compose_services(root)
    for rp in repo_paths:
        rid = _slug(rp.name if rp != root else root.name)
        while rid in used_ids:
            rid += "-2"
        used_ids.add(rid)
        base = f"repositories.{rid}"
        P[f"{base}.path"], E[f"{base}.path"] = _rel(rp, root), ev("high", f"`{_rel(rp, root)}/.git` exists")
        stack, sconf, sevid, alts = detect_stack(rp)
        P[f"{base}.stack"] = stack
        E[f"{base}.stack"] = ev(sconf, *sevid, *(f"also matches {a}" for a in alts))
        if stack not in BUNDLED_STACKS:
            gaps.append(f"{rid}: stack `{stack}` has no bundled adapter; a draft adapter will be generated (review it)")
        surfaces, why = detect_surfaces(rp, stack)
        P[f"{base}.surfaces"] = surfaces
        weak = all("fallback" in w for ws in why.values() for w in ws)
        E[f"{base}.surfaces"] = ev("low" if weak else "medium", *(f"{s}: {'; '.join(w)}" for s, w in why.items()))
        remote = _git(rp, "remote", "get-url", "origin")
        if remote:
            P[f"{base}.remote"] = remote
            E[f"{base}.remote"] = ev("high", "`git remote get-url origin`")
            remotes.append(parse_remote(remote))
        head = _git(rp, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if head:
            P[f"{base}.defaultBranch"], E[f"{base}.defaultBranch"] = head.split("/", 1)[-1], ev("high", "`git symbolic-ref refs/remotes/origin/HEAD`")
        else:
            cur = _git(rp, "rev-parse", "--abbrev-ref", "HEAD") or "main"
            P[f"{base}.defaultBranch"], E[f"{base}.defaultBranch"] = cur, ev("medium", f"current branch `{cur}` (origin/HEAD not set)")
        cmds = list({c["cmd"]: c for c in reversed(ci_commands(rp) + script_commands(rp))}.values())[::-1]
        # same command in CI and in package scripts → one entry, CI evidence wins (it already runs there)
        cmds = sorted(cmds, key=lambda c: c["source"] != "ci")
        if not any(c["role"] in ("lint", "test") for c in cmds):
            gaps.append(f"{rid}: no lint or test command found in CI or package scripts; the bundled adapter defaults will be used unverified")
        if cmds or stack not in BUNDLED_STACKS:
            P[f"{base}.stackAdapter"] = f".sdlc/adapters/stack/{rid}.json"
            E[f"{base}.stackAdapter"] = ev("high", f"{len(cmds)} commands detected for this repo")
        keys, urls, envfiles = env_examples(rp)
        test_docs = [_rel(p, rp) for p in _walk_files(rp, re.compile(r"(test|テスト|仕様書).*\.xlsx$", re.I), 10)]
        repos_info[rid] = {"path": str(rp), "stack": stack, "stackAlternatives": alts, "surfaceReasons": why,
                           "commands": cmds, "envFiles": envfiles, "envKeys": keys, "envUrls": urls, "testDocs": test_docs}

    # tracker
    sig = tracker_signals(root, repo_paths)
    gh = next((r for r in remotes if r.get("host") == "github.com"), None)
    tracker, tconf, tev = "local-file", "low", ["no tracker signal found"]
    if "jira" in sig["hosts"]:
        tracker, tconf, tev = "jira", "high" if sig["topKey"] else "medium", [sig["hosts"]["jira"]]
    elif "backlog" in sig["hosts"]:
        tracker, tconf, tev = "backlog", "high" if sig["topKey"] else "medium", [sig["hosts"]["backlog"]]
    elif sig["topKey"]:
        tracker, tconf, tev = "jira", "low", [f"ticket keys `{sig['topKey']}-n` in {sig['keys'][sig['topKey']]} commit subjects/branches (Jira or Backlog)"]
    elif gh and (sig["hashRefs"] or sig["issueTemplates"]):
        tracker, tconf, tev = "github-issues", "medium", [f"{sig['hashRefs']} `#n` references", *sig["issueTemplates"]]
    P["tracker.adapter"], E["tracker.adapter"] = tracker, ev(tconf, *tev, *(f"e.g. \"{s}\"" for s in sig["samples"][:2]))
    cfg: dict[str, Any] = {}
    if tracker == "github-issues" and gh:
        cfg = {"repo": gh["slug"]}
    elif tracker == "jira" and sig["topKey"]:
        cfg = {"project": sig["topKey"]}
    elif tracker == "backlog" and sig["topKey"]:
        cfg = {"projectKey": sig["topKey"]}
    P["tracker.config"], E["tracker.config"] = cfg, ev("medium" if cfg else "low", "derived from the tracker evidence", status="detected" if cfg else "default")
    if "linear" in sig["hosts"]:
        gaps.append("Linear mentioned in docs but there is no bundled linear adapter")
    if tconf != "high":
        opts = [{"label": t, "value": t} for t in dict.fromkeys([tracker, "github-issues", "jira", "backlog", "local-file"])]
        Q.append({"id": "q-tracker", "field": "tracker.adapter", "question": "Which tracker holds this project's tickets?",
                  "options": opts[:4], "recommended": tracker, "why": "; ".join(E["tracker.adapter"]["evidence"])})
    if tracker in ("jira", "backlog"):
        need = "site URL" if tracker == "jira" else "space and the API key env var name (apiKeyEnv)"
        gaps.append(f"tracker.config for {tracker} needs the {need}; see adapters/tracker/{tracker}.json")

    # delivery
    if gh:
        P["delivery.adapter"], E["delivery.adapter"] = "github-pr", ev("high", f"origin is github.com/{gh['slug']}")
    else:
        hosts = sorted({r.get("host", "?") for r in remotes}) or ["no remote"]
        P["delivery.adapter"], E["delivery.adapter"] = "manual", ev("medium", f"remote host(s): {', '.join(hosts)} (no bundled adapter)")
        if any("gitlab" in h for h in hosts):
            P["delivery.adapter"], E["delivery.adapter"] = "gitlab-mr", ev("high", f"remote host(s): {', '.join(hosts)}")
    P["delivery.drafts"] = DRAFTS_BY_DELIVERY[P["delivery.adapter"]]
    E["delivery.drafts"] = ev("high", f"templates offered by adapters/delivery/{P['delivery.adapter']}.json", status="default")
    for rp in repo_paths:
        for t in (".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md", "docs/pull_request_template.md"):
            if (rp / t).exists():
                gaps.append(f"{_rel(rp / t, root)} exists: point the pr-body draft at it (project delivery adapter `extends: {P['delivery.adapter']}`)")

    # surfaces → evidence runners
    all_surfaces = sorted({s for k, v in P.items() if k.endswith(".surfaces") for s in v})
    for s in all_surfaces:
        if s in DEFAULT_SURFACE_ADAPTER:
            entry: dict[str, Any] = {"adapter": DEFAULT_SURFACE_ADAPTER[s]}
            url = next((u for info in repos_info.values() for k, u in info["envUrls"].items()), None)
            if s in ("web", "api") and url:
                entry["config"] = {"baseUrl": url}
            P[f"evidence.surfaces.{s}"] = entry
            E[f"evidence.surfaces.{s}"] = ev("medium", f"bundled default runner for `{s}`" + (f"; baseUrl from .env example ({url})" if url else ""))
        else:
            P[f"evidence.surfaces.{s}"] = {"adapter": "none", "reason": f"no bundled runner for {s}; set one"}
            E[f"evidence.surfaces.{s}"] = ev("low", "no bundled surface adapter", status="default")
    weak_repos = [k.split(".")[1] for k, e in E.items() if k.endswith(".surfaces") and e["confidence"] == "low"]
    for rid in weak_repos:
        Q.append({"id": f"q-surfaces-{rid}", "field": f"repositories.{rid}.surfaces", "multiSelect": True,
                  "question": f"Which surfaces is `{rid}` verified on?", "recommended": P[f"repositories.{rid}.surfaces"][0],
                  "options": [{"label": s, "value": s} for s in ("web", "api", "cli", "batch")],
                  "why": "; ".join(E[f"repositories.{rid}.surfaces"]["evidence"])})
    if any(P.get(f"repositories.{rid}.stack") == "flutter-dart" for rid in repos_info):
        Q.append({"id": "q-device", "field": "_device", "question": "Is the mobile app also verified on real hardware (handheld, scanner)?",
                  "options": [{"label": "No, emulator only", "value": False}, {"label": "Yes, real device", "value": True}],
                  "recommended": "No, emulator only", "why": "Real devices add the `device` surface, which always needs your go (G6)."})

    # shared state
    dbs = [s for s in all_services if DB_IMAGES.search(s["image"] or s["name"])]
    db_keys = sorted({k for info in repos_info.values() for k in info["envKeys"] if re.search(r"DATABASE|DB_|REDIS|MONGO|QUEUE|AMQP", k)})
    P["environment.sharedState"] = []
    E["environment.sharedState"] = ev("low", *(f"{s['name']} ({s['image']}) in {s['evidence']}" for s in dbs),
                                      *(f"env key {k}" for k in db_keys[:5]), status="default")
    if dbs:
        Q.append({"id": "q-shared", "field": "environment.sharedState", "multiSelect": True,
                  "question": "Which of these data stores are shared with other people (writes to them will need your go)?",
                  "options": [{"label": s["name"], "value": s["name"]} for s in dbs][:3] + [{"label": "None (all local)", "value": None}],
                  "recommended": "None (all local)",
                  "why": "Compose services are usually local containers; " + "; ".join(E["environment.sharedState"]["evidence"][:3])})
    elif db_keys:
        Q.append({"id": "q-shared", "field": "environment.sharedState", "multiSelect": False,
                  "question": f"The repos read {', '.join(db_keys[:3])}. Does that point at a database shared with other people?",
                  "options": [{"label": "Yes, a shared dev database", "value": ["shared dev database"]},
                              {"label": "No, local only", "value": []}],
                  "recommended": "Yes, a shared dev database",
                  "why": "No local container defines it, so it most likely points at a server."})
    P["environment.doctor"], E["environment.doctor"] = [], ev("high", "per-repo commands go to the project stack adapters", status="default")

    # locale
    texts = [_read(readme)] if readme else []
    for rp in repo_paths:
        texts.append(_git(rp, "log", "-n", "100", "--format=%s") or "")
    repo_lang = detect_language("\n".join(texts))
    # Everything is English by default. The detected repository language is only a hint in the question: it is
    # never written to the profile unless the user explicitly chooses another language.
    hint = (f" The README and recent commits look like `{repo_lang}`; that does not change anything unless you choose it."
            if repo_lang != "en" else "")
    Q.append({"id": "q-lang", "field": "_languages",
              "question": "Output language: keep everything in English (commit messages, PR text, ticket artifacts, replies)?",
              "options": [{"label": "Yes, English for everything", "value": "default",
                           "description": "Nothing is written to the profile; English is the default."},
                          {"label": "No, set languages per audience", "value": "custom",
                           "description": "Ask per audience: chat, artifacts, delivery (commit/PR), review replies, tracker replies."}],
              "recommended": "Yes, English for everything",
              "why": "Skills and node instructions are always English; only explicitly set outputs change." + hint,
              "followUp": [{"field": f"locale.{k}", "question": q} for k, q in (
                  ("chat", "Language the controller uses with you (questions, progress, summaries)?"),
                  ("artifacts", "Language of ticket artifacts (analysis, plan, test cases, evidence report)?"),
                  ("delivery", "Language of commit messages and PR descriptions?"),
                  ("review", "Language of replies to PR review comments?"),
                  ("tracker", "Language of replies posted on the ticket tracker?"))]})

    # git identity: every git command of every node runs as this account
    ident_cands = git_identity_candidates(repo_paths)
    if ident_cands:
        best = ident_cands[0]
        P["git.user.name"], P["git.user.email"] = best["name"], best["email"]
        E["git.user.name"] = E["git.user.email"] = ev("medium", f"{best['source']}", status="detected")
    else:
        gaps.append("git identity: no repo/global git config and no commit authors found; you must give a name and email")
    differing = {c["repo"] for c in ident_cands if c.get("repo") and c["source"].startswith("repo-local") and c["email"] != (ident_cands[0]["email"] if ident_cands else "")}
    if differing:
        gaps.append(f"repo-local git identity differs in {sorted(differing)}; set repositories.<id>.git.user if those repos must use another account")
    Q.append({"id": "q-git-identity", "field": "git.user", "question": "Which git account (name and email) must every commit of this project use?",
              "options": [{"label": f"{c['name']} <{c['email']}>", "value": {"name": c["name"], "email": c["email"]},
                           "description": c["source"]} for c in ident_cands[:3]] or
                         [{"label": "Type it (Other)", "value": None, "description": "no identity detected"}],
              "recommended": f"{ident_cands[0]['name']} <{ident_cands[0]['email']}>" if ident_cands else "Type it (Other)",
              "why": "Applied to every git command the nodes run (commits, worktrees, push); commits by anyone else fail the gate."})
    ssh_remotes = [rid for rid in repos_info if (P.get(f"repositories.{rid}.remote") or "").startswith(("git@", "ssh://"))]
    configured_ssh = next((v for v in (_git(rp, "config", "--get", "core.sshCommand") for rp in repo_paths) if v), None)
    if configured_ssh:
        P["git.sshCommand"], E["git.sshCommand"] = configured_ssh, ev("medium", "`git config core.sshCommand` of the repository")
    if ssh_remotes or configured_ssh:
        Q.append({"id": "q-git-ssh", "field": "git.sshCommand", "question": "Which SSH key should git use for this project's remotes?",
                  "options": ([{"label": "The configured core.sshCommand", "value": configured_ssh, "description": configured_ssh}] if configured_ssh else [])
                             + [{"label": "Default ssh (agent / ~/.ssh)", "value": None,
                                 "description": "or choose Other and type e.g. ssh -i <key path> -o IdentitiesOnly=yes"}],
                  "recommended": "The configured core.sshCommand" if configured_ssh else "Default ssh (agent / ~/.ssh)",
                  "why": "Remotes use SSH; pinning a key keeps pushes on the project's account. Only the key path is stored, never the key."})

    # testcase format
    docs = [f"{rid}: {d}" for rid, info in repos_info.items() for d in info["testDocs"]]
    P["testcase.format"], P["testcase.template"] = "markdown", "templates/testcase.md"
    E["testcase.format"] = ev("medium" if not docs else "low", "bundled default", *docs, status="default")
    E["testcase.template"] = ev("high", "bundled template", status="default")
    if docs:
        Q.append({"id": "q-testcase", "field": "testcase.format", "question": "Test spec workbooks exist in the repo. Which format should test cases use?",
                  "options": [{"label": "markdown", "value": "markdown"}, {"label": "xlsx", "value": "xlsx"}],
                  "recommended": "xlsx", "why": "; ".join(docs[:3])})

    # defaults
    for key, val in (("paths.ticketRoot", ".sdlc/tickets"), ("paths.branchPrefix", "ticket/"), ("models.routine", "sonnet"),
                     ("models.deep", "opus"), ("rulepacks", ["core"]), ("delivery.receiptTtlHours", 72),
                     ("delivery.minHoursLeft", 24), ("gates.loopCap", 2), ("gates.autoSkipPlanApproval", False)):
        P[key], E[key] = val, ev("high", "harness default", status="default")
    # ticket worktrees go together in one folder outside every repository (never loose next to the repos)
    in_root = any(k.startswith("repositories.") and k.endswith(".path") and v == "." for k, v in P.items())
    if in_root:  # the profile sits inside the repository: a subfolder would nest worktrees in the repo
        P["paths.worktreeRoot"] = f"../{root.name}-worktrees"
        E["paths.worktreeRoot"] = ev("medium", "the repository is the onboarding root, so worktrees go beside it in one folder",
                                     status="default")
        gaps.append(f"the repository is the onboarding root: ticket worktrees go to ../{root.name}-worktrees/. "
                    f"For a self-contained layout, onboard from a workspace folder that holds the repository "
                    f"(worktrees then go to <workspace>/worktrees/)")
    else:
        P["paths.worktreeRoot"], E["paths.worktreeRoot"] = "worktrees", ev("high", "harness default", status="default")
    gi = root / ".gitignore"
    ignored = gi.exists() and re.search(r"^/?\.sdlc(/|/tickets/?)?\s*$", _read(gi), re.M)
    if not ignored:
        Q.append({"id": "q-gitignore", "field": "_gitignore", "question": "Add `.sdlc/tickets/` to the project .gitignore? Ticket folders can hold customer data.",
                  "options": [{"label": "Yes, ignore it", "value": True}, {"label": "No, commit ticket folders", "value": False}],
                  "recommended": "Yes, ignore it", "why": ".gitignore has no .sdlc entry" if gi.exists() else "no .gitignore at the root"})

    return {"schemaVersion": 1, "generator": GENERATOR, "scannedAt": now(), "root": str(root),
            "proposal": P, "evidence": E, "repos": repos_info, "services": all_services,
            "trackerSignals": {k: v for k, v in sig.items() if k != "samples"},
            "questions": Q, "gaps": gaps, "verification": []}


# --- verify ------------------------------------------------------------------------------------------

def verify(cand: dict[str, Any], allow_tests: bool, allow_build: bool, timeout: int = 300) -> list[dict[str, Any]]:
    results = []
    allowed = {"doctor", "lint", "format"} | ({"test"} if allow_tests else set()) | ({"build"} if allow_build else set())
    for rid, info in cand["repos"].items():
        seen = set()
        for c in info["commands"]:
            if c["cmd"] in seen:
                continue
            seen.add(c["cmd"])
            row = {"repo": rid, "cmd": c["cmd"], "role": c["role"], "evidence": c["evidence"], "at": now()}
            if not c["runnable"]:
                row.update(ran=False, reason="uses CI variables")
            elif c["role"] in ("install", "mutating"):
                row.update(ran=False, reason=f"{c['role']}: never run by onboarding")
            elif c["role"] == "other":
                row.update(ran=False, reason="not a recognised check; not run")
            elif c["role"] not in allowed:
                row.update(ran=False, reason=f"{c['role']}: needs --allow-{'tests' if c['role'] == 'test' else 'build'}")
            else:
                try:
                    res, shell = run_command(c["cmd"], info["path"], timeout)
                    tail = ((res.stdout or "") + (res.stderr or "")).strip().splitlines()[-5:]
                    row.update(ran=True, exit=res.returncode, ok=res.returncode == 0, tail=tail, shell=shell)
                except subprocess.TimeoutExpired:
                    row.update(ran=True, exit=None, ok=False, tail=[f"timeout after {timeout}s"])
            results.append(row)
    return results


# --- profile assembly --------------------------------------------------------------------------------

LEAF_DICT_PREFIXES = ("tracker.config", "evidence.surfaces.", "delivery.config")


def flatten(profile: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def walk(prefix: str, val: Any) -> None:
        if prefix == "repositories" and isinstance(val, list):
            for repo in val:
                for k, v in repo.items():
                    if k != "id":
                        out[f"repositories.{repo['id']}.{k}"] = v
            return
        if isinstance(val, dict) and prefix and (prefix == "tracker.config" or prefix == "delivery.config"
                                                 or (prefix.startswith("evidence.surfaces.") and prefix.count(".") == 2)):
            out[prefix] = val
            return
        if isinstance(val, dict):
            for k, v in val.items():
                if not k.startswith("_"):
                    walk(f"{prefix}.{k}" if prefix else k, v)
            return
        out[prefix] = val

    walk("", profile)
    return out


def unflatten(flat: dict[str, Any]) -> dict[str, Any]:
    prof: dict[str, Any] = {}
    repos: dict[str, dict[str, Any]] = {}
    for path, val in flat.items():
        parts = path.split(".")
        if parts[0] == "repositories":
            repos.setdefault(parts[1], {"id": parts[1]})[parts[2]] = val
            continue
        if parts[0] == "evidence" and len(parts) == 3:
            prof.setdefault("evidence", {}).setdefault("surfaces", {})[parts[2]] = val
            continue
        cur = prof
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    order = ["schemaVersion", "meta", "project", "locale", "git", "paths", "models", "tracker", "repositories", "rulepacks",
             "testcase", "evidence", "delivery", "families", "gates", "environment"]
    prof["repositories"] = [repos[k] for k in repos]
    return {k: prof[k] for k in order if k in prof} | {k: v for k, v in prof.items() if k not in order}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stack_adapter_for(rid: str, info: dict[str, Any], stack: str, surfaces: list[str], verification: list[dict[str, Any]]) -> dict[str, Any]:
    ok_cmds = {v["cmd"] for v in verification if v["repo"] == rid and v.get("ok")}
    failed = {v["cmd"] for v in verification if v["repo"] == rid and v.get("ran") and not v.get("ok")}

    def pick(role: str) -> str | None:
        cands = [c for c in info["commands"] if c["role"] == role and c["runnable"] and c["cmd"] not in failed]
        cands.sort(key=lambda c: (c["cmd"] not in ok_cmds, c["source"] != "ci"))
        return cands[0]["cmd"] if cands else None

    commands = {k: v for k, v in {"lint": pick("lint"), "format": pick("format"), "build": pick("build"), "unitTest": pick("test")}.items() if v}
    doctor = [c["cmd"] for c in info["commands"] if c["role"] == "doctor" and c["runnable"] and c["cmd"] not in failed]
    if doctor:
        commands["doctor"] = list(dict.fromkeys(doctor))
    adapter: dict[str, Any] = {"kind": "stack", "id": f"{stack}@{rid}", "generatedBy": GENERATOR,
                               "description": f"Project adapter for repository `{rid}`: commands detected from its CI and scripts."}
    if stack in BUNDLED_STACKS:
        adapter["extends"] = stack
    else:
        adapter.update({"id": stack, "draft": True, "allowedSurfaces": surfaces,
                        "description": f"DRAFT for `{rid}`: no bundled `{stack}` adapter. Review commands, worktree and conventions.",
                        "worktree": "{git} worktree add {worktreeRoot}/{repoId}-{ticket} -b {branchPrefix}{ticket} {base}",
                        "conventions": [], "rulepack": f"stack:{stack}"})
        commands = {"doctor": [], "lint": None, "format": None, "build": None, "unitTest": None} | commands
    adapter["commands"] = commands
    chosen = {v for v in commands.values() if isinstance(v, str)} | {d for d in commands.get("doctor") or []}
    adapter["commandEvidence"] = {c["cmd"]: {"evidence": c["evidence"], "verified": c["cmd"] in ok_cmds}
                                  for c in info["commands"] if c["cmd"] in chosen}
    return adapter


LOCALE_FIELDS = {f"locale.{k}" for k in ("chat", "artifacts", "delivery", "review", "tracker")}
# fields an answer of `null` removes ("use the default"): the default ssh, and English for a language key
OPTIONAL_ANSWER_FIELDS = {"git.sshCommand"} | LOCALE_FIELDS


def normalize_answers(answers: dict[str, Any]) -> dict[str, Any]:
    """Answers use profile paths. An object for a non-leaf path (e.g. `git.user: {name, email}`) is split into
    leaf paths, so refresh ownership and the evidence sidecar work per field."""
    out: dict[str, Any] = {}
    for key, val in answers.items():
        if key == "_languages":
            if val == "default":  # "English for everything": drop every language key (also on refresh)
                out.update({f: None for f in LOCALE_FIELDS if f not in answers})
            continue
        leaf_dict = key in ("tracker.config", "delivery.config") or (key.startswith("evidence.surfaces.") and key.count(".") == 2)
        if isinstance(val, dict) and not leaf_dict and not key.startswith("repositories."):
            for sub, v in val.items():
                out[f"{key}.{sub}"] = v
        else:
            out[key] = val
    return out


def write(cand: dict[str, Any], answers: dict[str, Any], profile_path: Path, home: Path, dry_run: bool = False) -> dict[str, Any]:
    root = Path(cand["root"])
    sidecar_path = profile_path.with_name(profile_path.stem + ".evidence.json")
    existing = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else None
    old_side = json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.exists() else {"fields": {}, "files": {}}
    proposed = dict(cand["proposal"])
    evidence = {k: dict(v) for k, v in cand["evidence"].items()}
    answers = normalize_answers(answers)

    # answers: user decisions win and are marked as user-owned
    actions: list[str] = []
    for key, val in answers.items():
        if val is None and key in OPTIONAL_ANSWER_FIELDS:  # "use the default": drop the field
            proposed.pop(key, None)
            evidence.pop(key, None)
            continue
        if key == "_gitignore":
            if val:
                actions.append("gitignore")
            continue
        if key == "_device":
            if val:
                for k in [k for k in proposed if k.endswith(".stack") and proposed[k] == "flutter-dart"]:
                    sk = k.replace(".stack", ".surfaces")
                    proposed[sk] = sorted(set(proposed[sk]) | {"device"})
                proposed["evidence.surfaces.device"] = {"adapter": "device-manual"}
                evidence["evidence.surfaces.device"] = ev("high", "answered by you", status="user")
            continue
        if key == "environment.sharedState" and isinstance(val, list):
            val = [v for v in val if v]
        proposed[key] = val
        evidence[key] = ev("high", "answered by you", status="user")

    # surfaces chosen by answers need runner entries
    for s in sorted({s for k, v in proposed.items() if k.endswith(".surfaces") for s in v}):
        if f"evidence.surfaces.{s}" not in proposed:
            proposed[f"evidence.surfaces.{s}"] = {"adapter": DEFAULT_SURFACE_ADAPTER.get(s, "none"),
                                                  **({} if s in DEFAULT_SURFACE_ADAPTER else {"reason": f"no bundled runner for {s}"})}
            evidence[f"evidence.surfaces.{s}"] = ev("medium", f"default runner for `{s}`")

    # refresh: never overwrite a value the user set (answered, or hand-edited = not in the old sidecar)
    diff: list[dict[str, Any]] = []
    if existing:
        old_flat = flatten(existing)
        for key in sorted(set(old_flat) | set(proposed)):
            if key.startswith("meta."):
                continue
            if key in answers and answers[key] is None and key in OPTIONAL_ANSWER_FIELDS:
                if key in old_flat:
                    diff.append({"field": key, "action": "removed", "old": old_flat[key]})
                continue
            recorded = old_side["fields"].get(key)
            # yours = answered by you, absent from the sidecar (hand-added), or changed since onboarding wrote it
            user_owned = key in old_flat and (recorded is None or recorded.get("status") == "user"
                                              or ("value" in recorded and recorded["value"] != old_flat[key]))
            if key in answers:
                user_owned = False  # a fresh answer replaces even a user value
            if key in old_flat and user_owned:
                if key in proposed and proposed[key] != old_flat[key]:
                    diff.append({"field": key, "action": "kept-yours", "yours": old_flat[key], "detected": proposed[key]})
                proposed[key] = old_flat[key]
                evidence[key] = ev("high", "set by you in the profile", status="user")
            elif key in old_flat and key not in proposed:
                proposed[key] = old_flat[key]
                diff.append({"field": key, "action": "no-longer-detected", "kept": old_flat[key]})
            elif key in old_flat and proposed[key] != old_flat[key]:
                diff.append({"field": key, "action": "updated", "old": old_flat[key], "new": proposed[key]})
            elif key not in old_flat:
                diff.append({"field": key, "action": "added", "new": proposed[key]})

    profile = unflatten(proposed)
    profile = {"schemaVersion": 1, "meta": {"generator": GENERATOR,
                                            "generatedAt": (existing or {}).get("meta", {}).get("generatedAt", now()),
                                            **({"refreshedAt": now()} if existing else {}),
                                            "evidenceFile": sidecar_path.name}} | {k: v for k, v in profile.items() if k != "schemaVersion"}
    if existing and existing.get("families"):
        profile["families"] = existing["families"]

    # project stack adapters
    files: dict[str, Any] = {}
    adapter_notes = []
    for repo in profile["repositories"]:
        ref = repo.get("stackAdapter")
        if not ref:
            continue
        info = cand["repos"].get(repo["id"])
        target = profile_path.parent / ref
        if info is None:
            continue
        text = json.dumps(stack_adapter_for(repo["id"], info, repo["stack"], repo["surfaces"], cand.get("verification", [])), indent=2, ensure_ascii=False) + "\n"
        if target.exists():
            current = target.read_text(encoding="utf-8")
            recorded = old_side.get("files", {}).get(ref)
            if recorded and _digest(current) != recorded:
                adapter_notes.append(f"{ref}: edited by you since the last onboarding, kept")
                files[ref] = recorded
                continue
            if not recorded and "generatedBy" not in current:
                adapter_notes.append(f"{ref}: not generated by onboarding, kept")
                continue
        files[ref] = _digest(text)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    flat_written = flatten(profile)
    sidecar = {"schemaVersion": 1, "generator": GENERATOR, "writtenAt": now(), "profile": profile_path.name,
               "fields": {k: {**evidence.get(k, ev("low", "no evidence recorded", status="default")), "value": v}
                          for k, v in flat_written.items() if not k.startswith("meta.")},
               "verification": cand.get("verification", []), "files": files, "gaps": cand.get("gaps", []), "diff": diff}
    sidecar["open"] = sorted(k for k, v in sidecar["fields"].items() if v["status"] == "default" or v["confidence"] == "low")

    if not dry_run:
        profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if "gitignore" in actions:
            gi = root / ".gitignore"
            text = _read(gi) if gi.exists() else ""
            if ".sdlc/tickets/" not in text:
                gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".sdlc/tickets/\n", encoding="utf-8")
    return {"profile": str(profile_path), "evidenceFile": str(sidecar_path), "dryRun": dry_run, "diff": diff,
            "adapters": files, "adapterNotes": adapter_notes, "open": sidecar["open"], "gaps": sidecar["gaps"],
            "actions": actions, "_profile": profile}
