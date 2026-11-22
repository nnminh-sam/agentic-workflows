"""Tests for 3-layer host/model resolution, role cards, enforcement modes, and onboarding cleanup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME / "tests"))

from harness import hosts as H  # noqa: E402
from harness import onboard as OB  # noqa: E402
from harness.profile import load_profile  # noqa: E402
from test_lifecycle import Base, run  # noqa: E402


class HostAndModelTests(Base):
    def _dummy_result(self, path_str: str) -> str:
        p = Path(path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("status: ok\n", encoding="utf-8")
        return path_str

    def test_all_bundled_host_presets_and_preambles(self) -> None:
        intents = ("DRIVER_CALL", "DISPATCH_UNIT", "RESUME_UNIT", "ASK_USER", "INSPECT_CODE")
        for host in H.KNOWN_HOSTS:
            preset = H.load_preset(HOME, host)
            self.assertEqual(preset["host"], host)
            preamble = H.load_preamble(HOME, host)
            for intent in intents:
                self.assertIn(intent, preamble, f"{host}/preamble.md missing {intent}")

    def test_profile_node_overrides_and_escalation(self) -> None:
        prof = json.loads(self.profile.read_text(encoding="utf-8"))
        prof["nodeOverrides"] = {
            "investigate": {"tier": "deep"},
            "coding": {"units": {"worker": {"tier": "routine"}}},
        }
        self.profile.write_text(json.dumps(prof), encoding="utf-8")
        self.assertEqual(load_profile(self.profile)["nodeOverrides"]["coding"]["units"]["worker"]["tier"], "routine")

        code, res = run("init", "--ticket", "HM-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        for node in ("prepare", "investigate", "fix-plan"):
            self.complete(res["ticket"] if "ticket" in res else "HM-1", node,
                          actor="user" if node == "fix-plan" else "controller")

        code, plan = run("plan", "--ticket", "HM-1", "--node", "coding", *self.p)
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan["units"][0]["tier"], "routine")
        self.assertEqual(plan["units"][0]["model"], "sonnet")

    def test_three_layer_precedence_and_cross_host_override(self) -> None:
        binding_dir = self.tmp / ".sdlc"
        binding_dir.mkdir()
        binding = H.load_preset(HOME, "antigravity")
        binding["nodeOverrides"] = {
            "code-review/semantic-review": {
                "dispatchMode": "subprocess",
                "runner": "codex",
                "model": "gpt-5.2-codex",
                "effort": "high",
                "enforcement": "strict",
            }
        }
        (binding_dir / "host.local.json").write_text(json.dumps(binding), encoding="utf-8")

        code, res = run("init", "--ticket", "HM-2", "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)

        # Layer 3 local binding wins over legacy profile models
        code, plan = run("plan", "--ticket", "HM-2", "--node", "prepare", *self.p)
        self.assertEqual(plan["units"][0]["model"], "flash_lite")
        self.assertEqual(plan["units"][0]["execution"]["host"], "antigravity")
        self.assertEqual(plan["units"][0]["execution"]["modelSelection"], "enum")

        # $SDLC_HOST overrides .sdlc/host.local.json
        os.environ["SDLC_HOST"] = "gemini"
        self.addCleanup(os.environ.pop, "SDLC_HOST", None)
        code, plan = run("plan", "--ticket", "HM-2", "--node", "prepare", *self.p)
        self.assertEqual(plan["units"][0]["model"], "gemini-2.5-flash-lite")
        self.assertEqual(plan["units"][0]["execution"]["contextWindow"], 1_000_000)

        # CLI --host beats $SDLC_HOST
        code, plan = run("plan", "--ticket", "HM-2", "--node", "prepare", "--host", "codex", *self.p)
        self.assertEqual(plan["units"][0]["model"], "gpt-5-codex-mini")
        self.assertEqual(plan["units"][0]["execution"]["effort"], "low")
        os.environ.pop("SDLC_HOST", None)

        # Per-unit cross-host override (code-review/semantic-review via codex)
        code, plan = run("plan", "--ticket", "HM-2", "--node", "code-review", *self.p)
        by_id = {u["id"]: u for u in plan["units"]}
        self.assertEqual(by_id["technical-review"]["execution"]["host"], "antigravity")
        self.assertEqual(by_id["semantic-review"]["execution"]["dispatchMode"], "subprocess")
        self.assertEqual(by_id["semantic-review"]["execution"]["runner"], "codex")
        self.assertEqual(by_id["semantic-review"]["execution"]["model"], "gpt-5.2-codex")
        self.assertEqual(by_id["semantic-review"]["execution"]["effort"], "high")

    def test_record_enforcement_strict_alias_and_inherit_ok(self) -> None:
        code, _ = run("init", "--ticket", "HM-3", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)

        # strict (codex): rejects mismatched model, accepts exact model
        code, pk = run("packet", "--ticket", "HM-3", "--node", "prepare", "--unit", "worker", "--host", "codex", *self.p)
        res_file = self._dummy_result(pk["resultPath"])
        code, err = run("record", "--ticket", "HM-3", "--node", "prepare", "--unit", "worker",
                        "--result", res_file, "--model", "gpt-5-mini", "--host", "codex", *self.p)
        self.assertEqual(code, 2, err)

        # alias-match (antigravity): flash-lite must NOT match routine 'flash', whereas 'gemini-2.5-pro' matches 'pro'
        self.complete("HM-3", "prepare")
        code, pk_inv = run("packet", "--ticket", "HM-3", "--node", "investigate", "--unit", "worker",
                           "--host", "antigravity", *self.p)
        inv_res = self._dummy_result(pk_inv["resultPath"])
        code, err = run("record", "--ticket", "HM-3", "--node", "investigate", "--unit", "worker",
                        "--result", inv_res, "--model", "flash_lite", "--host", "antigravity", *self.p)
        self.assertEqual(code, 2, err)

        code, rec_ok = run("record", "--ticket", "HM-3", "--node", "investigate", "--unit", "worker",
                           "--result", inv_res, "--model", "gemini-2.5-pro", "--tokens", "42",
                           "--host", "antigravity", *self.p)
        self.assertEqual(code, 0, rec_ok)
        self.assertEqual(rec_ok["recorded"]["plannedModel"], "pro")
        self.assertNotIn("modelMismatch", rec_ok["recorded"])

        # inherit-ok (cursor): accepts active model, marks modelMismatch, renders warning indicator in STATUS.md
        code, pk_inv2 = run("packet", "--ticket", "HM-3", "--node", "investigate", "--unit", "worker",
                            "--host", "cursor", *self.p)
        inv_res2 = self._dummy_result(pk_inv2["resultPath"])
        code, rec_cur = run("record", "--ticket", "HM-3", "--node", "investigate", "--unit", "worker",
                            "--result", inv_res2, "--model", "claude-sonnet-4-6", "--tokens", "99",
                            "--host", "cursor", *self.p)
        self.assertEqual(code, 0, rec_cur)
        self.assertTrue(rec_cur["recorded"]["modelMismatch"])
        status_md = (self.tdir("HM-3") / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("⚠", status_md)
        self.assertIn("Model verification", status_md)

    def test_packet_embeds_role_card_and_avoids_claude_tool_names(self) -> None:
        code, _ = run("init", "--ticket", "HM-4", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        self.complete("HM-4", "prepare")
        code, pk = run("packet", "--ticket", "HM-4", "--node", "investigate", "--unit", "worker",
                       "--host", "antigravity", *self.p)
        self.assertEqual(code, 0, pk)
        self.assertTrue(pk["roleCard"].endswith("playbooks/roles/deep.md"))
        text = Path(pk["path"]).read_text(encoding="utf-8")
        self.assertIn("## Role card", text)
        self.assertIn("Adversarial reasoning protocol", text)
        self.assertIn("Attack the weakest claim first", text)
        self.assertIn("host **antigravity**", text)
        for bad in ("Grep-with-line-numbers", "AskUserQuestion", "$CLAUDE_SKILL_DIR"):
            self.assertNotIn(bad, text)

    def test_sdlc_host_command_inspect_and_set(self) -> None:
        code, info = run("host", *self.p)
        self.assertEqual(code, 0, info)
        self.assertEqual(info["activeHost"], "profile")

        code, set_res = run("host", "--set", "antigravity", *self.p)
        self.assertEqual(code, 0, set_res)
        self.assertTrue((self.tmp / ".sdlc" / "host.local.json").is_file())

        code, info2 = run("host", "--node", "code-review", "--unit", "semantic-review", *self.p)
        self.assertEqual(code, 0, info2)
        self.assertEqual(info2["activeHost"], "antigravity")
        self.assertEqual(info2["unitExecution"]["model"], "pro")


class OnboardHostAndOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sdlc-onboard-hosts-"))
        self.repo = self.root / "app"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "pyproject.toml").write_text("[project]\nname = 'app'\n[tool.pytest.ini_options]\n", encoding="utf-8")
        (self.repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (self.root / "compose.yml").write_text(
            "services:\n"
            "  pg1:\n    image: postgres:16\n"
            "  pg2:\n    image: mysql:8\n"
            "  mq:\n    image: rabbitmq:3\n"
            "  kv:\n    image: redis:7\n"
            "  doc:\n    image: mongo:7\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "LocalRepoUser"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "local@repo.test"], check=True)
        for i in range(5):
            (self.repo / "main.py").write_text(f"print('ok {i}')\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(self.repo), "add", "main.py"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "-c", f"user.email=dev{i}@b.c",
                            "-c", f"user.name=Dev{i}", "commit", "-qm", f"init {i}"], check=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_scan_emits_untruncated_options_abstract_defaults_and_q_host(self) -> None:
        cand = OB.scan(self.root, [])
        self.assertEqual(cand["proposal"]["models.routine"], "routine")
        self.assertEqual(cand["proposal"]["models.deep"], "deep")

        q_shared = next(q for q in cand["questions"] if q["id"] == "q-shared")
        self.assertGreaterEqual(len(q_shared["options"]), 6)

        q_ident = next(q for q in cand["questions"] if q["id"] == "q-git-identity")
        self.assertGreaterEqual(len(q_ident["options"]), 4)

        q_host = next(q for q in cand["questions"] if q["id"] == "q-host")
        self.assertEqual(q_host["field"], "_host")
        self.assertEqual(q_host["recommended"], "antigravity")
        self.assertEqual([o["value"] for o in q_host["options"]], list(H.KNOWN_HOSTS))

    def test_write_with_host_and_dry_run_safety(self) -> None:
        cand = OB.scan(self.root, [])
        profile_path = self.root / "sdlc.profile.json"
        answers = {
            "_host": "antigravity",
            "_gitignore": True,
            "git.user": {"name": "Dev", "email": "a@b.c"},
        }
        dry = OB.write(cand, answers, profile_path, HOME, dry_run=True)
        self.assertNotIn("hostBinding", dry)
        self.assertFalse((self.root / ".sdlc").exists())

        wrote = OB.write(cand, answers, profile_path, HOME, dry_run=False)
        self.assertTrue((self.root / ".sdlc" / "host.local.json").is_file())
        self.assertEqual(wrote["hostBinding"], str(self.root / ".sdlc" / "host.local.json"))
        prof = load_profile(profile_path)
        self.assertNotIn("_host", prof)
        self.assertEqual(prof["models"], {"routine": "routine", "deep": "deep"})
        gi = (self.root / ".gitignore").read_text(encoding="utf-8")
        for line in (".sdlc/tickets/", ".sdlc/host.local.json", ".sdlc/ship-approvals.json"):
            self.assertIn(line, gi)


if __name__ == "__main__":
    unittest.main()
