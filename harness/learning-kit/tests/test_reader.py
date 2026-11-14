"""The reader: research.md -> one self-contained HTML page. Run: python -m unittest discover -s tests -v"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lk import reader, sources, topics  # noqa: E402
from lk.cli import main as lk_main  # noqa: E402
from lk.store import LkError, write_json  # noqa: E402

LONG = "A source snapshot about request flow and DNS resolution. " * 10

STEPS = {"title": "Two hops", "steps": [
    {"title": "DNS", "phase": "dns", "cost": "1 lookup", "body": "Ask the **resolver**.", "example": "a → 1.2.3.4",
     "cite": ["S0001"]},
    {"title": "TCP", "phase": "tcp", "body": "SYN, SYN-ACK, ACK."}]}
SEQ = {"title": "Seq", "actors": [{"id": "b", "label": "Browser"}, {"id": "w", "label": "Web server"}],
       "phases": {"dns": "DNS"},
       "messages": [{"from": "b", "to": "w", "label": "a fairly long label that has to wrap onto lines", "phase": "dns"},
                    {"from": "w", "to": "b", "label": "200 OK", "reply": True},
                    {"over": "w", "label": "render"}]}
ARCH = {"title": "Tiers", "panels": [
    {"title": "Before", "flow": [{"box": "Users", "kind": "client"}, {"arrow": "HTTP"},
                                 {"box": "Server", "kind": "web", "items": ["web", "db"]}], "cons": ["one box"]},
    {"title": "After", "flow": [{"box": "Web", "kind": "web"}, {"arrow": ""}, {"box": "DB", "kind": "data"}],
     "pros": ["scale apart"]}]}


def fence(lang, spec):
    return f"```{lang}\n{json.dumps(spec, indent=1)}\n```"


DOC = f"""# Web basics

> The point, in plain words [S0001].

## Core ideas

### Request flow  {{#request-flow}}

Short paragraph with `code`, **bold**, *italic* and a [link](https://example.org) [S0001] [S0001].

{fence("lk-steps", STEPS)}

1. First item
   continues here
2. Second item
   - nested bullet

> [!example] Real numbers
> 56 ms per round trip.

| A | B |
|---|---|
| 1 | 2 |

### DNS  {{#dns}}

{fence("lk-sequence", SEQ)}

{fence("lk-arch", ARCH)}
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lk-test-"))
        os.environ["LK_HOME"] = str(self.tmp)
        write_json(self.tmp / "config.json", {})
        sources.add("https://example.com/web", "Web doc", LONG, ["web"], "docs", 3)

    def tearDown(self):
        os.environ.pop("LK_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def topic(self, md=DOC):
        topics.create("web", "Web", "use")
        d = topics.tdir("web")
        (d / "prior.md").write_text("I think DNS turns names into addresses.", encoding="utf-8")
        topics.advance("web", "research")
        (d / "research.md").write_text(md, encoding="utf-8")
        topics.merge_concepts("web", [
            {"id": "request-flow", "name": "Request flow", "core": True, "source_ids": ["S0001"],
             "summary": "Hops in a fixed order."},
            {"id": "dns", "name": "DNS", "core": False, "source_ids": ["S0001"]}])
        return d

    def render(self, md):
        ctx = reader.Ctx(sources.by_id(), {})
        return reader.render(md, ctx), ctx


class TestMarkdown(Base):
    def test_blocks_and_inline(self):
        page, ctx = self.render(DOC)
        self.assertIn("<title>Web basics</title>", page)
        self.assertIn('<blockquote class="lead">', page)
        self.assertIn("<code>code</code>", page)
        self.assertIn("<strong>bold</strong>", page)
        self.assertIn("<em>italic</em>", page)
        self.assertIn('href="https://example.org"', page)
        self.assertIn("<li>First item continues here</li>", page)
        self.assertIn("<ul><li>nested bullet</li></ul>", page)
        self.assertIn('class="callout c-example"', page)
        self.assertIn("<th>A</th>", page)
        self.assertEqual(ctx.errors, [])
        self.assertEqual(ctx.visuals, 3)

    def test_everything_is_escaped(self):
        page, _ = self.render("# T\n\n<script>alert(1)</script> [x](javascript:alert(1)) `<b>`\n")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn('href="javascript', page)
        self.assertIn("<code>&lt;b&gt;</code>", page)

    def test_citations_link_to_sources_and_flag_unknown(self):
        page, ctx = self.render("# T\n\nA claim [S0001]. Another [S0999].\n")
        self.assertIn('href="https://example.com/web"', page)
        self.assertIn("S0001 · Web doc", page)
        self.assertIn('class="cite missing"', page)
        self.assertTrue(any("S0999" in w for w in ctx.warnings))

    def test_long_paragraph_warning(self):
        _, ctx = self.render("# T\n\n" + "word " * 130 + "\n")
        self.assertTrue(any("long paragraph" in w for w in ctx.warnings))

    def test_fenced_code_does_not_split_sections(self):
        page, _ = self.render("# T\n\n## One\n\n```\n## not a heading\n```\n")
        self.assertEqual(page.count("<section id="), 1)
        self.assertIn("## not a heading", page)


class TestVisuals(Base):
    def test_render_each_kind(self):
        ctx = reader.Ctx(sources.by_id(), {})
        steps = reader.visual("lk-steps", json.dumps(STEPS), ctx)
        self.assertIn('class="step" data-phase="dns"', steps)
        self.assertIn('<span class="chip">1 lookup</span>', steps)
        seq = reader.visual("lk-sequence", json.dumps(SEQ), ctx)
        self.assertIn("<svg", seq)
        self.assertGreater(seq.count("<tspan"), 3)          # the long label wrapped
        self.assertIn('class="sq-arrow reply"', seq)
        self.assertIn('class="sq-band" data-phase="dns"', seq)
        arch = reader.visual("lk-arch", json.dumps(ARCH), ctx)
        self.assertIn('class="arch-box k-data"', arch)
        self.assertIn('<li class="pro">scale apart</li>', arch)
        # the same phase key gets the same colour in every block
        self.assertIn("--ph:var(--ph0)", steps)
        self.assertIn("--ph:var(--ph0)", seq)
        self.assertEqual(ctx.errors, [])

    def test_validation_errors_are_specific(self):
        cases = [
            ("lk-steps", {"steps": [{"title": "only one"}]}, "2-12"),
            ("lk-steps", {"steps": [{"title": "a", "colour": 1}, {"title": "b"}]}, "unknown fields"),
            ("lk-sequence", {"actors": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                             "messages": [{"from": "a", "to": "zz", "label": "x"}]}, "unknown actor 'zz'"),
            ("lk-arch", {"panels": [{"flow": [{"box": "x", "kind": "rainbow"}]}]}, "kind"),
            ("lk-arch", {"panels": [{"flow": [{"box": "x", "arrow": "y"}]}]}, "either"),
        ]
        for lang, spec, needle in cases:
            errs = reader.validate_visual(lang, spec)
            self.assertTrue(any(needle in e for e in errs), (lang, errs))

    def test_broken_block_renders_error_and_keeps_source(self):
        page, ctx = self.render("# T\n\n```lk-steps\n{not json\n```\n")
        self.assertEqual(len(ctx.errors), 1)
        self.assertIn("Diagram error", page)
        self.assertIn("{not json", page)


class TestTopic(Base):
    def test_check_build_and_concepts(self):
        d = self.topic()
        r = reader.check("web")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["visuals"], 3)
        b = reader.build("web")
        html = (d / "research.html").read_text(encoding="utf-8")
        self.assertTrue(b["url"].startswith("file:"))
        self.assertIn("core concept", html)                 # badge from concepts.json
        self.assertIn("Hops in a fixed order.", html)       # concept summary shown as "In short"
        self.assertIn('href="#request-flow"', html)         # TOC entry
        self.assertNotIn("\r\n", html)

    def test_missing_concept_anchor_is_a_warning(self):
        self.topic(DOC.replace("{#dns}", ""))
        self.assertTrue(any("'dns'" in w for w in reader.check("web")["warnings"]))

    def test_read_stage_refuses_broken_visuals(self):
        self.topic(DOC + "\n```lk-arch\n{\"panels\": []}\n```\n" + LONG)
        with self.assertRaises(LkError) as e:
            topics.advance("web", "read")
        self.assertIn("visual blocks", str(e.exception))

    def test_file_must_stay_in_topic(self):
        self.topic()
        with self.assertRaises(LkError):
            reader.doc_path("web", "../../config.json")

    def test_cli(self):
        d = self.topic()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(lk_main(["read", "web", "--check"]), 0)
        self.assertTrue(json.loads(buf.getvalue())["ok"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(lk_main(["read", "WEB", "--no-open", "--out", str(self.tmp / "x.html")]), 0)
        self.assertTrue((self.tmp / "x.html").is_file())
        (d / "reteach").mkdir(exist_ok=True)
        (d / "reteach" / "round-1.md").write_text("# Round 1\n\n" + fence("lk-steps", {"steps": [{"title": "a"}]}),
                                                  encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(lk_main(["read", "web", "--file", "reteach/round-1.md", "--check"]), 2)


if __name__ == "__main__":
    unittest.main()
