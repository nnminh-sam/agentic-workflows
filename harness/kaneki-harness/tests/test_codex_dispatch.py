"""M1 step 8 (code side): `sdlc dispatch` on host codex runs `codex exec -m MODEL -c model_reasoning_effort=EFFORT
PROMPT`, and the unit's tokens come from codex's text summary (`tokens used`) or its JSON usage. The model ids are
read from hosts/codex/tiers.json, never written here (the user checks them by hand)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import HOME, Base, run  # noqa: E402

from harness import dispatch as D  # noqa: E402

NO_HOST_ENV = {k: v for k, v in os.environ.items() if k != "SDLC_HOST"}
CODEX_TIERS = json.loads((HOME / "hosts" / "codex" / "tiers.json").read_text(encoding="utf-8"))["tiers"]


class CodexArgvTests(Base):
    def test_dry_run_argv_matches_the_documented_form_per_tier(self) -> None:
        code, _ = run("init", "--ticket", "CX-1", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            code, dry = run("dispatch", "--ticket", "CX-1", "--node", "prepare", "--unit", "worker",
                            "--host", "codex", "--dry-run", *self.p)
        self.assertEqual(code, 0, dry)
        tier = dry["packet"]["tier"]
        cfg = CODEX_TIERS[tier]
        prompt = f"Read the packet at {dry['packet']['path']} (sha256 {dry['packet']['packetDigest']}) and execute it."
        self.assertEqual(dry["argv"], ["codex", "exec", "-m", cfg["model"], "-c",
                                       f"model_reasoning_effort={cfg['effort']}", prompt])

    def test_build_argv_for_every_codex_tier(self) -> None:
        for tier, cfg in CODEX_TIERS.items():
            exe = {"host": "codex", "runner": "codex", "model": cfg["model"], "effort": cfg["effort"]}
            self.assertEqual(D.build_argv(exe, "P", "/p.md", "/r.md", tier=tier),
                             ["codex", "exec", "-m", cfg["model"], "-c", f"model_reasoning_effort={cfg['effort']}", "P"],
                             tier)


class CodexTokenTests(unittest.TestCase):
    def test_text_summary_old_and_new_forms(self) -> None:
        self.assertEqual(D.extract_tokens("done\n", "[ts] tokens used: 12,345\n"), 12345)
        self.assertEqual(D.extract_tokens("done\n", "codex\nok\ntokens used\n1,234\n"), 1234)
        self.assertEqual(D.extract_tokens("", "tokens used\r\n987\r\n"), 987)
        self.assertEqual(D.extract_tokens("", "\x1b[35m\x1b[3mtokens used\x1b[0m\n4,321\n"), 4321)

    def test_last_summary_wins(self) -> None:
        self.assertEqual(D.extract_tokens("", "tokens used: 10\nmore work\ntokens used\n250\n"), 250)

    def test_json_usage(self) -> None:
        jsonl = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
            json.dumps({"type": "turn.completed",
                        "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50}}),
        ])
        self.assertEqual(D.extract_tokens(jsonl), 1050)  # cached input is part of input, not added again
        self.assertEqual(D.extract_tokens(json.dumps({"info": {"total_token_usage": {"total_tokens": 777}}})), 777)

    def test_no_count(self) -> None:
        self.assertIsNone(D.extract_tokens("all done", "no summary"))
        self.assertIsNone(D.extract_tokens("", "tokens used\n\nnext"))


class CodexDispatchEndToEndTests(Base):
    def test_text_token_summary_reaches_the_record(self) -> None:
        code, _ = run("init", "--ticket", "CX-2", "--kind", "bug", *self.p)
        self.assertEqual(code, 0)
        fake = self.tmp / "fake_codex.py"
        fake.write_text(
            "import pathlib, sys\n"
            "res = pathlib.Path(sys.argv[1])\n"
            "res.parent.mkdir(parents=True, exist_ok=True)\n"
            "res.write_text('status: ok\\n')\n"
            "print('final answer')\n"
            "sys.stderr.write('tokens used\\n2,468\\n')\n",
            encoding="utf-8",
        )
        cmd = json.dumps([sys.executable, str(fake), "{result}"])
        with mock.patch.dict(os.environ, NO_HOST_ENV, clear=True):
            code, res = run("dispatch", "--ticket", "CX-2", "--node", "prepare", "--unit", "worker",
                            "--host", "codex", "--command", cmd, *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["tokens"], 2468)
        self.assertEqual(res["recorded"]["tokens"], 2468)
        self.assertEqual(res["recorded"]["host"], "codex")


if __name__ == "__main__":
    unittest.main()
