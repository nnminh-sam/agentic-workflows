#!/usr/bin/env python3
"""Validate a haise doc against the skill's contract.

The outline is free: only structure that every doc shares is enforced (shell,
language, filled tokens, unique ids, citations that resolve, offline-only).

Usage: python check_doc.py <file.html>
Exit 0 = no errors (warnings may remain), 1 = errors, 2 = bad usage.
"""
import pathlib
import re
import sys
from html.parser import HTMLParser

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "template.html"

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ가-힯]")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SVG_VOID = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "stop", "use"}
SAFE_TAGS = {"code", "pre", "kbd", "samp", "script", "style"}
SAFE_CLASSES = {"orig", "src-quote", "src-loc", "src-title", "no-i18n"}
SAFE_IDS = {"glossary", "sources"}


class Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ids = []
        self.cites = []          # (href, section_id)
        self.sources = []        # li ids inside ol.sources
        self.src_parts = {}      # src id -> set of part classes seen
        self.sections = []       # section ids in order
        self.figures = 0
        self.fig_no_caption = 0
        self.figcap_no_cite = 0
        self.external = []
        self.cjk_hits = []
        self.lang = None
        self.in_main = False
        self._fig = None         # [has_caption, caption_has_cite, in_caption]

    def _ctx(self, attr):
        for _, a in reversed(self.stack):
            if attr(a):
                return a
        return None

    def _section(self):
        for t, a in reversed(self.stack):
            if t == "section":
                return a.get("id", "?")
        return None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        a["_tag"] = tag
        a["_cls"] = set(a.get("class", "").split())
        if tag == "html":
            self.lang = a.get("lang", "")
        if "id" in a and a["id"]:
            self.ids.append(a["id"])
        if tag == "main" and a.get("id") == "content":
            self.in_main = True
        if tag == "section" and self.in_main and not any(t == "section" for t, _ in self.stack):
            self.sections.append(a.get("id", ""))
        if tag == "a" and "cite" in a["_cls"]:
            self.cites.append((a.get("href", ""), self._section()))
            if self._fig and self._fig[2]:
                self._fig[1] = True
        if tag == "li" and any(t == "ol" and "sources" in x["_cls"] for t, x in self.stack):
            if a.get("id"):
                self.sources.append(a["id"])
                self.src_parts[a["id"]] = set()
        for part in ("src-loc", "src-quote", "src-title", "src-type"):
            if part in a["_cls"]:
                for t, x in reversed(self.stack):
                    if t == "li" and x.get("id") in self.src_parts:
                        self.src_parts[x["id"]].add(part)
                        break
        if tag == "figure" and "fig" in a["_cls"]:
            self.figures += 1
            self._fig = [False, False, False]
        if tag == "figcaption" and self._fig:
            self._fig[0] = True
            self._fig[2] = True
        if tag == "script" and a.get("src"):
            self.external.append(f"<script src={a['src']}>")
        if tag == "link" and "stylesheet" in a.get("rel", ""):
            self.external.append(f"<link href={a.get('href')}>")
        if tag in ("img", "image", "iframe") and re.match(r"https?:", a.get("src", "") or a.get("href", "")):
            self.external.append(f"<{tag} {a.get('src') or a.get('href')}>")
        if tag in VOID or (tag in SVG_VOID and self.get_starttag_text().rstrip().endswith("/>")):
            return
        self.stack.append((tag, a))

    def handle_startendtag(self, tag, attrs):
        # self-closing: record attributes but never push
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag and tag not in VOID and tag not in SVG_VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "figcaption" and self._fig:
            self._fig[2] = False
        if tag == "figure" and self._fig:
            if not self._fig[0]:
                self.fig_no_caption += 1
            elif not self._fig[1]:
                self.figcap_no_cite += 1
            self._fig = None
        if tag == "main":
            self.in_main = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not CJK.search(data):
            return
        safe = self._ctx(lambda a: a["_tag"] in SAFE_TAGS
                         or a["_cls"] & SAFE_CLASSES
                         or a.get("id") in SAFE_IDS
                         or re.match(r"(ja|zh|ko)", a.get("lang", "")))
        if not safe and any(t in ("head", "body") for t, _ in self.stack):
            line, _ = self.getpos()
            self.cjk_hits.append((line, data.strip()[:60], self._section()))


def shell_block(text, kind, ident):
    m = re.search(rf'<{kind} id="{ident}">(.*?)</{kind}>', text, re.S)
    return m.group(1) if m else None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    errors, warns = [], []

    # shell integrity (consistency across all haise docs)
    if 'data-haise="doc"' not in text:
        errors.append('missing data-haise="doc" marker: doc was not scaffolded from the template')
    for kind, ident in (("style", "haise-shell-css"), ("script", "haise-shell-js")):
        mine, ref = shell_block(text, kind, ident), shell_block(tpl, kind, ident)
        if mine is None:
            errors.append(f"shell block #{ident} missing")
        elif mine.replace("\r\n", "\n") != ref.replace("\r\n", "\n"):
            errors.append(f"shell block #{ident} differs from assets/template.html (never edit the shell per document; put doc-specific styles in inline style attributes or SVG)")

    leftovers = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
    if leftovers:
        errors.append("unfilled template tokens: " + ", ".join(leftovers))

    d = Doc()
    d.feed(text)

    if d.lang not in ("en", "vi"):
        errors.append(f'<html lang="{d.lang}"> must be "en" or "vi"')
    dup = sorted({i for i in d.ids if d.ids.count(i) > 1})
    if dup:
        errors.append("duplicate ids: " + ", ".join(dup))
    if not d.sections:
        errors.append('no content: add at least one <section id="..."><h2>…</h2>…</section> inside <main>')
    has_sources = bool(d.sources) or "sources" in d.sections
    if d.cites and "sources" not in d.sections:
        errors.append('the doc cites sources but has no <section id="sources">')
    if "sources" in d.sections and not d.sources:
        errors.append('empty sources: ol.sources needs at least one <li id="src-N">, or drop the section')
    if "sources" in d.sections and d.sections[-1] != "sources":
        warns.append('<section id="sources"> should be the last section')
    if d.figures == 0:
        warns.append('no <figure class="fig">: add a diagram/chart/comparison wherever a picture beats a paragraph, or state why none helps')
    if d.external:
        errors.append("external resources break offline viewing: " + "; ".join(d.external))

    src_set = set(d.sources)
    cited = set()
    for href, sec in d.cites:
        sid = href.lstrip("#")
        cited.add(sid)
        if sid not in src_set:
            errors.append(f'citation {href} (section "{sec}") has no matching source entry')
    for s in d.sources:
        if s not in cited:
            warns.append(f"source #{s} is never cited in the body")
        missing = {"src-title", "src-loc", "src-quote"} - d.src_parts.get(s, set())
        if missing:
            warns.append(f"source #{s} lacks: " + ", ".join(sorted(missing)))
    if d.fig_no_caption:
        warns.append(f"{d.fig_no_caption} figure(s) without <figcaption>")
    if has_sources and d.figcap_no_cite:
        warns.append(f"{d.figcap_no_cite} figure caption(s) without a source citation")

    # keyword rule: <strong>translated</strong> <span class="orig">(original)</span>
    body = text.split('<main id="content"', 1)[-1]
    origs = re.findall(r'(.{0,40})<span class="orig"', body, re.S)
    bad_orig = [o for o in origs if not re.search(r"</strong>\s*$", o)]
    if bad_orig:
        warns.append(f'{len(bad_orig)} <span class="orig"> not directly after </strong> (pattern: <strong class="kw">term</strong> <span class="orig" lang="ja">(原語)</span>)')
    g0 = body.find('id="glossary"')
    g1 = body.find("</section>", g0) if g0 >= 0 else -1
    if origs and g0 >= 0 and body.count("</tr>", g0, g1) <= 1:
        warns.append("glossary table is empty but the doc has translated keywords")

    if d.cjk_hits:
        warns.append(f"{len(d.cjk_hits)} untranslated CJK text run(s) outside code/.orig/glossary/sources:")
        for line, snip, sec in d.cjk_hits[:25]:
            warns.append(f"    line {line} [{sec}]: {snip}")

    for e in errors:
        print("ERROR  " + e)
    for w in warns:
        print("WARN   " + w)
    print(f"\n{path.name}: {len(errors)} error(s), {len(warns)} warning(s); "
          f"{len(d.sections)} sections, {d.figures} figures, {len(d.cites)} citations, {len(d.sources)} sources")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
