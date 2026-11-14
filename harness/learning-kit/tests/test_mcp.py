"""MCP server tests. Skipped unless the optional `mcp` SDK is installed (pip install -e ".[mcp]", Python >= 3.10)."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kit import ROOT, Base  # noqa: E402

from lk import sessions  # noqa: E402

try:
    import anyio
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False


def data(result):
    if result.is_error:
        raise AssertionError(result.content[0].text)
    return result.structured_content if result.structured_content else json.loads(result.content[0].text)


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class TestMcp(Base):
    def run_client(self, fn, server=None):
        from lk import mcp_server

        async def go():
            async with Client(server or mcp_server.build()) as c:
                return await fn(c)
        return anyio.run(go)

    def test_tools_and_resources(self):
        slug = self.make_topic()

        async def fn(c):
            names = {t.name for t in (await c.list_tools()).tools}
            self.assertTrue({"overview", "get_path", "get_topic", "read_doc", "start_quiz", "finish_quiz"} <= names)
            for t in (await c.list_tools()).tools:
                if t.name in ("overview", "get_topic", "read_doc"):
                    self.assertTrue(t.annotations.read_only_hint)
            ov = data(await c.call_tool("overview", {}))
            self.assertEqual(ov["topics"][0]["slug"], slug)
            t = data(await c.call_tool("get_topic", {"topic": "MVCC"}))  # case-insensitive slugs
            self.assertEqual(t["stage"], "quiz")
            doc = data(await c.call_tool("read_doc", {"topic": slug}))
            self.assertIn("MVCC", doc["markdown"])
            err = await c.call_tool("get_topic", {"topic": "nope"})
            self.assertTrue(err.is_error)
            self.assertIn("does not exist", err.content[0].text)
            bad = await c.call_tool("read_doc", {"topic": slug, "file": "../../config.json"})
            self.assertTrue(bad.is_error)
            res = await c.read_resource(f"lk://topics/{slug}/docs/research.md")
            self.assertIn("MVCC", res.contents[0].text)
            src = data(await c.call_tool("read_source", {"source_id": "S0001"}))
            self.assertIn("Untrusted", src["note"])
        self.run_client(fn)

    def test_chat_quiz_practice(self):
        slug = self.make_topic()

        async def fn(c):
            gate = await c.call_tool("start_quiz", {"kind": "gate", "topic": slug})
            self.assertTrue(gate.is_error)  # gates belong in the browser
            st = data(await c.call_tool("start_quiz", {"kind": "practice", "topic": slug, "n": 4}))
            sid = st["session"]
            q = st["first"]
            full = {e["uid"]: e["item"] for e in sessions.load(sid)["items"]}
            seen = 0
            while not q["done"]:
                it = full[q["question"]["uid"]]
                self.assertNotIn("answer", q["question"])
                self.assertNotIn("rubric", q["question"])
                resp = {"mcq": lambda: {"choice": it["answer"]},
                        "cloze": lambda: {"blanks": [b[0] for b in it["blanks"]]},
                        "explain": lambda: {"text": "correct"}}[it["type"]]()
                q = data(await c.call_tool("answer_question", {"session": sid, "uid": q["question"]["uid"],
                                                               "response": resp, "confidence": 3}))
                seen += 1
            self.assertEqual(seen, 4)
            r = data(await c.call_tool("finish_quiz", {"session": sid}))
            self.assertEqual(r["status"], "applied")
            self.assertEqual(r["summary"]["n"], 4)
            self.assertEqual(r["mode"], "practice")
        self.run_client(fn)

    def test_stdio_end_to_end(self):
        """`lk mcp` as Claude Code launches it: a subprocess speaking MCP on stdio."""
        self.make_topic()
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "lk.py"), "mcp"],
                                       env={**os.environ, "LK_HOME": str(self.tmp)})

        async def fn(c):
            return data(await c.call_tool("overview", {}))
        ov = self.run_client(fn, params)
        self.assertEqual(len(ov["topics"]), 1)


if __name__ == "__main__":
    unittest.main()
