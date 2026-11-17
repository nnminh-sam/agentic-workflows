"""Token usage written back into node artifacts and token-usage.md."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import usage as U  # noqa: E402
from harness.state import sha256_file  # noqa: E402


class TokenUsageTests(Base):
    def test_block_in_artifact_and_ticket_file(self) -> None:
        t = "TOK-1"
        code, res = run("init", "--ticket", t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        res = self.complete(t, "prepare")
        self.assertEqual(res["tokenUsage"]["total"], 1000)
        self.assertEqual(sorted(res["tokenUsage"]["writtenTo"]), ["environment.md", "ticket.md"])

        text = (self.tdir(t) / "ticket.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(U.BEGIN), 1)
        self.assertIn("## Token usage", text)
        self.assertIn("**Node total:** 1,000 tokens", text)
        # the digest the gate stores is of the file with the block in it
        state = json.loads((self.tdir(t) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["nodes"]["prepare"]["artifactDigests"]["ticket.md"], sha256_file(self.tdir(t) / "ticket.md"))

        usage = (self.tdir(t) / U.USAGE_FILE).read_text(encoding="utf-8")
        self.assertIn("**Ticket total:** 1,000 tokens", usage)
        self.assertIn("| prepare |", usage)
        self.assertIn("token-usage.md", (self.tdir(t) / "STATUS.md").read_text(encoding="utf-8"))

    def test_drafts_get_no_block(self) -> None:
        self.assertFalse(U.writes_block("delivery-drafts", "drafts/pr-body.md"))
        self.assertFalse(U.writes_block("respond-review", "review-reply.md"))
        self.assertTrue(U.writes_block("coding", "implementation.md"))

    def test_reclose_replaces_live_block_keeps_history(self) -> None:
        path = self.tmp / "a.md"
        path.write_text("# a\n\nbody\n", encoding="utf-8")
        rec = {"generation": 1, "units": [{"unit": "worker", "model": "opus", "generation": 1, "tokens": 5}]}
        U.write_node_block(path, "n", U.node_usage(rec))
        U.write_node_block(path, "n", U.node_usage(rec))
        self.assertEqual(path.read_text(encoding="utf-8").count(U.BEGIN), 1)
        path.write_text(path.read_text(encoding="utf-8") + "\n## SUPERSEDED: rerun\n\nnew body\n", encoding="utf-8")
        rec["generation"] = 2
        rec["units"].append({"unit": "worker", "model": "opus", "generation": 2, "tokens": None})
        U.write_node_block(path, "n", U.node_usage(rec))
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(U.BEGIN), 2)  # one in the superseded history, one live
        live = text.rsplit("## SUPERSEDED:", 1)[1]
        self.assertIn("1 round(s) unreported", live)
        self.assertIn("**Earlier generations:** 5 tokens", live)


if __name__ == "__main__":
    unittest.main()
