"""`sdlc contract`: the data contract version the desktop app checks at start-up (P1-T02, plan H2)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import HOME, run  # noqa: E402

import harness  # noqa: E402
from harness import contract  # noqa: E402
from harness.cli import READ_ONLY, build_parser  # noqa: E402


def _subcommands() -> set[str]:
    parser = build_parser()
    action = next(a for a in parser._actions if a.dest == "cmd")  # the subparsers action
    return set(action.choices)


class ContractTests(unittest.TestCase):
    def test_output_shape_and_types(self) -> None:
        code, data = run("contract")
        self.assertEqual(code, 0)
        self.assertEqual(set(data), {"contractVersion", "harnessVersion", "commands"})
        self.assertIs(type(data["contractVersion"]), int)  # an int, not a bool or a string
        self.assertIsInstance(data["harnessVersion"], str)
        self.assertIsInstance(data["commands"], list)
        self.assertTrue(data["commands"])
        self.assertTrue(all(isinstance(c, str) for c in data["commands"]))
        self.assertEqual(len(data["commands"]), len(set(data["commands"])))

    def test_versions_come_from_their_single_declarations(self) -> None:
        _, data = run("contract")
        self.assertEqual(data["harnessVersion"], harness.__version__)
        self.assertIs(type(contract.CONTRACT_VERSION), int)
        self.assertEqual(data["contractVersion"], contract.CONTRACT_VERSION)
        self.assertEqual(data["contractVersion"], 1)

    def test_commands_are_real_read_only_subcommands(self) -> None:
        _, data = run("contract")
        self.assertIn("contract", data["commands"])
        self.assertIn("tickets", data["commands"])
        known = _subcommands()
        for name in data["commands"]:
            self.assertIn(name, known, f"{name} is not an sdlc subcommand")
            self.assertIn(name, READ_ONLY, f"{name} is not read-only")

    def test_contract_is_read_only(self) -> None:
        self.assertIn("contract", READ_ONLY)

    def test_needs_no_profile_or_ticket_and_writes_nothing(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="sdlc-contract-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        env = {k: v for k, v in os.environ.items() if k not in ("SDLC_PROFILE", "SDLC_HOME")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        code = f"import sys; sys.path.insert(0, {str(HOME)!r}); from harness.cli import main; sys.exit(main(['contract']))"
        proc = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["contractVersion"], contract.CONTRACT_VERSION)
        self.assertEqual(list(tmp.iterdir()), [])  # no profile, ticket, STATUS.md or lock file appeared


if __name__ == "__main__":
    unittest.main()
