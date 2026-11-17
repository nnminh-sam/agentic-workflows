"""kaneki-onboarding: scan → verify → write → refresh on a two-repo fixture project."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import run  # noqa: E402

CI = """name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npm run lint
      - name: tests
        run: |
          npm test
          echo ${{ secrets.TOKEN }}
"""

COMPOSE = """services:
  api:
    build: ./api
    ports:
      - "3000:3000"
  db:
    image: postgres:16
    ports:
      - "5432:5432"
"""


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


class OnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sdlc-onb-")) / "acme"
        api, web = self.root / "api", self.root / "web"
        for repo in (api, web):
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (api / "package.json").write_text(json.dumps({
            "name": "acme-api", "scripts": {"lint": "eslint .", "test": "jest", "build": "tsc"},
            "dependencies": {"express": "^4", "pg": "^8"}, "devDependencies": {"typescript": "^5", "jest": "^29"}}, indent=2))
        (api / "tsconfig.json").write_text("{}")
        (api / ".github" / "workflows").mkdir(parents=True)
        (api / ".github" / "workflows" / "ci.yml").write_text(CI)
        (api / ".env.example").write_text("DATABASE_URL=\nAPP_URL=http://localhost:3000\nAPI_KEY=changeme\n")
        (api / ".env").write_text("API_KEY=super-secret-value\n")
        (web / "package.json").write_text(json.dumps({"name": "acme-web", "dependencies": {"react": "^18"},
                                                      "devDependencies": {"@playwright/test": "^1"}}))
        (self.root / "docker-compose.yml").write_text(COMPOSE)
        # mixed case on purpose: Linux (case-sensitive) must find it like Windows and macOS do
        (self.root / "Readme.md").write_text("# Acme Shop\n\nOrders and payments.\n", encoding="utf-8")
        git(api, "remote", "add", "origin", "git@github.com:acme/api.git")
        subprocess.run(["git", "-C", str(api), "config", "user.name", "Acme Dev"], check=True)      # repo-local identity
        subprocess.run(["git", "-C", str(api), "config", "user.email", "dev@acme.test"], check=True)
        git(web, "remote", "add", "origin", "https://github.com/acme/web.git")
        git(api, "add", ".")
        git(api, "commit", "-qm", "ACME-12 add orders endpoint")
        (api / "README.md").write_text("api\n")
        git(api, "add", ".")
        git(api, "commit", "-qm", "ACME-13 fix order totals")
        git(web, "add", ".")
        git(web, "commit", "-qm", "ACME-14 注文画面を追加 (order page)")  # non-ASCII commit subject
        self.cand = self.root.parent / "candidates.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def scan(self) -> dict:
        code, res = run("onboard-scan", "--root", str(self.root), "--out", str(self.cand))
        self.assertEqual(code, 0, res)
        return json.loads(self.cand.read_text(encoding="utf-8"))

    def test_scan_detects_with_evidence_and_never_reads_env(self) -> None:
        c = self.scan()
        P, E = c["proposal"], c["evidence"]
        self.assertEqual(P["repositories.api.stack"], "node-ts")
        self.assertEqual(E["repositories.api.stack"]["confidence"], "high")
        self.assertEqual(P["repositories.api.surfaces"], ["api", "db"])
        self.assertEqual(P["repositories.web.surfaces"], ["web"])
        cmds = {x["cmd"]: x for x in c["repos"]["api"]["commands"]}
        self.assertEqual(cmds["npm run lint"]["role"], "lint")
        self.assertEqual(cmds["npm run lint"]["evidence"], ".github/workflows/ci.yml:9")
        self.assertEqual(cmds["npm ci"]["role"], "install")
        self.assertFalse(cmds["echo ${{ secrets.TOKEN }}"]["runnable"])
        self.assertEqual(P["delivery.adapter"], "github-pr")
        self.assertEqual(P["tracker.adapter"], "jira")            # ACME-n keys, no host mention → low confidence
        self.assertEqual(E["tracker.adapter"]["confidence"], "low")
        self.assertEqual(P["evidence.surfaces.api"]["config"]["baseUrl"], "http://localhost:3000")
        self.assertEqual(P["project.name"], "Acme Shop")
        qids = {q["id"] for q in c["questions"]}
        self.assertTrue({"q-tracker", "q-shared", "q-gitignore"} <= qids)
        self.assertNotIn("super-secret-value", self.cand.read_text(encoding="utf-8"))
        self.assertNotIn("changeme", self.cand.read_text(encoding="utf-8"))  # only URL values are kept
        # git identity: detected from the repo-local config, but always asked
        self.assertEqual((P["git.user.name"], P["git.user.email"]), ("Acme Dev", "dev@acme.test"))
        q = next(q for q in c["questions"] if q["id"] == "q-git-identity")
        self.assertEqual(q["recommended"], "Acme Dev <dev@acme.test>")
        self.assertIn("q-git-ssh", qids)                                     # api remote is git@github.com
        # output languages are asked, one question per audience
        # languages: nothing detected is ever proposed; one question, English recommended
        self.assertFalse([k for k in P if k.startswith("locale.")])
        q = next(q for q in c["questions"] if q["id"] == "q-lang")
        self.assertEqual(q["recommended"], "Yes, English for everything")

    def test_identity_candidates_merge_unicode_forms(self) -> None:
        import unicodedata
        from harness.onboard import git_identity_candidates
        api = self.root / "api"
        nfc = "Nguyễn Minh"
        subprocess.run(["git", "-C", str(api), "config", "user.name", nfc], check=True)
        (api / "x.txt").write_text("x")
        git(api, "add", ".")
        subprocess.run(["git", "-C", str(api), "-c", f"user.name={unicodedata.normalize('NFD', nfc)}",
                        "-c", "user.email=dev@acme.test", "commit", "-qm", "ACME-20 x"], check=True)
        cands = git_identity_candidates([api])
        self.assertEqual(sum(1 for c in cands if c["email"] == "dev@acme.test"), 1)

    def test_verify_runs_only_safe_commands(self) -> None:
        c = self.scan()
        py = f'"{sys.executable}"'
        c["repos"]["api"]["commands"] = [
            {"cmd": f"{py} --version", "role": "doctor", "source": "ci", "evidence": "x:1", "runnable": True},
            {"cmd": f'{py} -c "import sys; sys.exit(3)" lint', "role": "lint", "source": "ci", "evidence": "x:2", "runnable": True},
            {"cmd": "npm ci", "role": "install", "source": "ci", "evidence": "x:3", "runnable": True},
            {"cmd": "jest", "role": "test", "source": "ci", "evidence": "x:4", "runnable": True},
        ]
        c["repos"]["web"]["commands"] = []
        self.cand.write_text(json.dumps(c))
        code, res = run("onboard-verify", "--candidates", str(self.cand))
        self.assertEqual(code, 0, res)
        self.assertEqual(res["ran"], 2)
        self.assertEqual(res["ok"], 1)
        self.assertEqual(res["failed"][0]["exit"], 3)
        reasons = {s["cmd"]: s["reason"] for s in res["skipped"]}
        self.assertIn("never run", reasons["npm ci"])
        self.assertIn("--allow-tests", reasons["jest"])

    def test_write_validates_and_drives_a_ticket(self) -> None:
        self.scan()
        answers = self.root.parent / "answers.json"
        answers.write_text(json.dumps({"tracker.adapter": "jira", "environment.sharedState": [None], "_gitignore": True,
                                       "git.user": {"name": "Acme Dev", "email": "dev@acme.test"}, "git.sshCommand": None,
                                       "locale.delivery": "ja"}))
        code, res = run("onboard-write", "--candidates", str(self.cand), "--answers", str(answers))
        self.assertEqual(code, 0, res)
        self.assertTrue(res["validation"]["ok"], res["validation"])
        profile = json.loads((self.root / "sdlc.profile.json").read_text(encoding="utf-8"))
        api = next(r for r in profile["repositories"] if r["id"] == "api")
        self.assertEqual(api["path"], "api")
        adapter = json.loads((self.root / api["stackAdapter"]).read_text(encoding="utf-8"))
        self.assertEqual(adapter["extends"], "node-ts")
        self.assertEqual(adapter["commands"]["lint"], "npm run lint")
        self.assertEqual(profile["git"], {"user": {"name": "Acme Dev", "email": "dev@acme.test"}})  # sshCommand dropped
        self.assertEqual(profile["locale"], {"delivery": "ja"})          # only what was explicitly answered
        side = json.loads((self.root / "sdlc.profile.evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(side["fields"]["tracker.adapter"]["status"], "user")
        self.assertEqual(side["fields"]["git.user.email"]["status"], "user")
        self.assertIn(".sdlc/tickets/", (self.root / ".gitignore").read_text())
        # the generated profile drives the real lifecycle
        code, res = run("init", "--ticket", "ACME-15", "--kind", "bug", "--profile", str(self.root / "sdlc.profile.json"))
        self.assertEqual(code, 0, res)
        self.assertTrue((self.root / ".sdlc" / "tickets" / "ACME-15" / "state.json").is_file())
        code, plan = run("plan", "--ticket", "ACME-15", "--node", "coding", "--profile", str(self.root / "sdlc.profile.json"))
        self.assertTrue(plan["adapters"]["stacks"]["api"].endswith("api.json"))

    def test_refresh_keeps_user_values_and_user_edited_adapters(self) -> None:
        self.scan()
        code, _ = run("onboard-write", "--candidates", str(self.cand))
        self.assertEqual(code, 0)
        prof_path = self.root / "sdlc.profile.json"
        code, res = run("onboard-write", "--candidates", str(self.cand))
        self.assertEqual(code, 2)                                  # never overwrite without --refresh
        prof = json.loads(prof_path.read_text(encoding="utf-8"))
        prof["project"]["name"] = "My Shop"                        # hand edit
        prof_path.write_text(json.dumps(prof, indent=2))
        adapter_path = self.root / ".sdlc" / "adapters" / "stack" / "api.json"
        adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
        adapter["commands"]["lint"] = "npm run lint -- --max-warnings 0"
        adapter_path.write_text(json.dumps(adapter, indent=2))
        (self.root / "api" / "pyproject.toml").write_text("[project]\nname='x'\n")   # drift: new signal
        self.scan()
        code, res = run("onboard-write", "--candidates", str(self.cand), "--refresh")
        self.assertEqual(code, 0, res)
        prof = json.loads(prof_path.read_text(encoding="utf-8"))
        self.assertEqual(prof["project"]["name"], "My Shop")
        self.assertIn({"field": "project.name", "action": "kept-yours", "yours": "My Shop", "detected": "Acme Shop"}, res["diff"])
        self.assertIn("edited by you", " ".join(res["adapterNotes"]))
        self.assertEqual(json.loads(adapter_path.read_text(encoding="utf-8"))["commands"]["lint"], "npm run lint -- --max-warnings 0")
        self.assertIn("refreshedAt", prof["meta"])

    def test_english_default_writes_no_locale_and_refresh_can_reset(self) -> None:
        self.scan()
        answers = self.root.parent / "answers.json"
        ident = {"git.user": {"name": "Acme Dev", "email": "dev@acme.test"}}
        answers.write_text(json.dumps({**ident, "_languages": "default"}))
        code, res = run("onboard-write", "--candidates", str(self.cand), "--answers", str(answers))
        self.assertEqual(code, 0, res)
        prof_path = self.root / "sdlc.profile.json"
        self.assertNotIn("locale", json.loads(prof_path.read_text(encoding="utf-8")))
        # later the user explicitly configures Japanese replies, then goes back to English for everything
        answers.write_text(json.dumps({**ident, "_languages": "custom", "locale.tracker": "ja"}))
        run("onboard-write", "--candidates", str(self.cand), "--answers", str(answers), "--refresh")
        self.assertEqual(json.loads(prof_path.read_text(encoding="utf-8"))["locale"], {"tracker": "ja"})
        answers.write_text(json.dumps({**ident, "_languages": "default"}))
        code, res = run("onboard-write", "--candidates", str(self.cand), "--answers", str(answers), "--refresh")
        self.assertEqual(code, 0, res)
        self.assertNotIn("locale", json.loads(prof_path.read_text(encoding="utf-8")))
        self.assertIn({"field": "locale.tracker", "action": "removed", "old": "ja"}, res["diff"])

    def test_dry_run_writes_nothing(self) -> None:
        self.scan()
        code, res = run("onboard-write", "--candidates", str(self.cand), "--dry-run")
        self.assertEqual(code, 0, res)
        self.assertFalse((self.root / "sdlc.profile.json").exists())
        self.assertFalse((self.root / ".sdlc").exists())


if __name__ == "__main__":
    unittest.main()
