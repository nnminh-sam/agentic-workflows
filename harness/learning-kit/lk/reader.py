"""The reader: turn a topic's research.md (or a reteach doc) into one self-contained HTML page.

research.md stays the single source of truth: agents read it, the quiz author cites it, and it is
readable in any editor. This module only *renders* it, deterministically, so the page can be rebuilt at
any time and never drifts from the Markdown.

On top of ordinary Markdown (headings, lists, tables, code, blockquotes), the page understands:

- callouts            > [!example] Optional title          (note, tip, example, warning,
                                                            misconception, interview, tldr, question)
- citations           [S0012] -> a chip linking to the source, with its title on hover
- concept headings    ### Name {#concept-id} -> anchor + "core concept" badge + the concept's summary
- visual blocks       fenced JSON with the language lk-steps, lk-sequence or lk-arch (see VISUALS below
                      and docs/authoring/visuals.md)

Everything is escaped: research text comes from untrusted web pages, so no raw HTML passes through, and
links are limited to http(s) and in-page anchors.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from . import paths, sources, topics
from .store import LkError, read_json, slugify, write_text

WORDS_PER_MIN = 220
LONG_PARAGRAPH_WORDS = 110          # warn above this: long paragraphs are what makes reading tiring
VISUALS = ("lk-steps", "lk-sequence", "lk-arch")
CALLOUTS = {
    "note": "Note", "tip": "Tip", "example": "Example", "warning": "Watch out",
    "misconception": "Common mix-up", "interview": "Interview angle", "tldr": "In short",
    "question": "Check yourself",
}
ARCH_KINDS = ("client", "edge", "web", "app", "data", "cache", "external", "queue", "plain")
N_PHASE_COLORS = 6


def esc(s) -> str:
    return html.escape(str(s), quote=True)


# ===================================================================== context

class Ctx:
    """Per-render state: the source index, concepts, phase colours, and the problems found."""

    def __init__(self, sources_by_id: dict | None = None, concepts: dict | None = None):
        self.sources = sources_by_id or {}
        self.concepts = concepts or {}
        self.phases: dict[str, int] = {}     # phase key -> colour index, in first-seen order
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.cited: set[str] = set()
        self.anchors: set[str] = set()
        self.tagged: set[str] = set()      # concept ids that have a {#id} heading
        self.visuals = 0

    def phase(self, key) -> int | None:
        if not key:
            return None
        key = str(key)
        if key not in self.phases:
            self.phases[key] = len(self.phases) % N_PHASE_COLORS
        return self.phases[key]

    def anchor(self, base: str) -> str:
        a = slugify(base) or "section"
        a0, n = a, 2
        while a in self.anchors:
            a, n = f"{a0}-{n}", n + 1
        self.anchors.add(a)
        return a


# ===================================================================== inline markdown

_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_CITE = re.compile(r"\[(S\d{4})\]")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITAL = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_CITE_RUN = re.compile(r"(<a class=\"cite\"[^>]*>[^<]*</a>)(?:\s+(?=<a class=\"cite\"))")


def _safe_url(u: str) -> str | None:
    u = u.strip()
    if u.startswith("#") or re.match(r"^https?://", u, re.I):
        return u
    return None


def _cite_html(sid: str, ctx: Ctx) -> str:
    ctx.cited.add(sid)
    s = ctx.sources.get(sid)
    if not s:
        ctx.warnings.append(f"citation [{sid}] is not in the source bank")
        return f'<a class="cite missing" data-tip="{esc(sid)}: not in the source bank">{esc(sid)}</a>'
    tip = f"{sid} · {s.get('title') or s.get('url')}" + (f" · {s['kind']}" if s.get("kind") else "")
    url = _safe_url(s.get("url", "")) or "#"
    return (f'<a class="cite" href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'data-tip="{esc(tip)}">{esc(sid)}</a>')


def inline(text: str, ctx: Ctx) -> str:
    """Escape, then apply code spans, links, citations, bold and italic. Code spans are protected."""
    parts = _CODE.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<code>{esc(part)}</code>")
            continue
        holes: list[str] = []

        def hole(h: str) -> str:
            holes.append(h)
            return f"\x00{len(holes) - 1}\x00"

        s = _CITE.sub(lambda m: hole(_cite_html(m.group(1), ctx)), part)

        def link(m):
            url = _safe_url(m.group(2))
            if not url:
                return m.group(0)
            ext = ' target="_blank" rel="noopener noreferrer"' if not url.startswith("#") else ""
            return hole(f'<a href="{esc(url)}"{ext}>{esc(m.group(1))}</a>')

        s = _LINK.sub(link, s)
        s = esc(s)
        s = _BOLD.sub(r"<strong>\1</strong>", s)
        s = _ITAL.sub(r"<em>\1</em>", s)
        s = re.sub("\x00(\\d+)\x00", lambda m: holes[int(m.group(1))], s)
        out.append(s)
    res = "".join(out)
    # "[S0012] [S0013]" -> one tight group of chips
    return _CITE_RUN.sub(r"\1", res)


# ===================================================================== block markdown

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w-]*)\s*$")
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([\w-]+)\})?\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CALLOUT = re.compile(r"^\[!(\w+)\]\s*(.*)$")


def _is_block_start(line: str) -> bool:
    return bool(_FENCE.match(line) or _HEAD.match(line) or line.lstrip().startswith(">")
                or _LIST.match(line) or _HR.match(line) or line.lstrip().startswith("|"))


def _dedent(lines: list[str]) -> list[str]:
    ind = [len(x) - len(x.lstrip()) for x in lines if x.strip()]
    n = min(ind) if ind else 0
    return [x[n:] if x.strip() else "" for x in lines]


def _words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def blocks(lines: list[str], ctx: Ctx, depth: int = 0) -> str:
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = _FENCE.match(line)
        if m:
            fence, lang = m.group(2), m.group(3).lower()
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            body = _dedent(body) if m.group(1) else body
            text = "\n".join(body)
            if lang in VISUALS:
                out.append(visual(lang, text, ctx))
            else:
                cls = f' class="lang-{esc(lang)}"' if lang else ""
                out.append(f"<pre><code{cls}>{esc(text)}</code></pre>")
            continue

        m = _HEAD.match(line)
        if m:
            out.append(heading(len(m.group(1)), m.group(2), m.group(3), ctx))
            i += 1
            continue

        if _HR.match(line):
            out.append("<hr>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            q = []
            while i < n and lines[i].lstrip().startswith(">"):
                q.append(re.sub(r"^\s*> ?", "", lines[i]))
                i += 1
            out.append(blockquote(q, ctx))
            continue

        if line.lstrip().startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            rows = [line]
            i += 2
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(table(rows, ctx))
            continue

        m = _LIST.match(line)
        if m:
            i, h = list_block(lines, i, ctx, depth)
            out.append(h)
            continue

        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not _is_block_start(lines[i]):
            para.append(lines[i].strip())
            i += 1
        text = " ".join(para)
        w = _words(text)
        if w > LONG_PARAGRAPH_WORDS:
            ctx.warnings.append(f"long paragraph ({w} words): split it, or turn it into steps or a list: "
                                f"\"{text[:60]}…\"")
        out.append(f"<p>{inline(text, ctx)}</p>")
    return "\n".join(out)


def heading(level: int, text: str, cid: str | None, ctx: Ctx) -> str:
    level = min(level, 6)
    anchor = ctx.anchor(cid or text)
    badge, summary = "", ""
    c = ctx.concepts.get(cid) if cid else None
    if cid and c is None and ctx.concepts:
        ctx.warnings.append(f"heading anchor {{#{cid}}} is not a concept id")
    if c:
        ctx.tagged.add(cid)
        badge = ('<span class="badge core">core concept</span>' if c.get("core")
                 else '<span class="badge">concept</span>')
        if c.get("summary"):
            summary = f'<p class="concept-summary"><span>In short</span>{inline(c["summary"], ctx)}</p>'
    return (f'<h{level} id="{esc(anchor)}"{" data-concept=" + chr(34) + esc(cid) + chr(34) if c else ""}>'
            f'{inline(text, ctx)}{badge}<a class="hash" href="#{esc(anchor)}" aria-label="Link to this section">#</a>'
            f"</h{level}>{summary}")


def blockquote(q: list[str], ctx: Ctx) -> str:
    first = q[0].strip() if q else ""
    m = _CALLOUT.match(first)
    if m and m.group(1).lower() in CALLOUTS:
        kind = m.group(1).lower()
        title = m.group(2).strip() or CALLOUTS[kind]
        inner = blocks(q[1:], ctx)
        return (f'<aside class="callout c-{kind}"><div class="callout-title"><span class="callout-kind">'
                f'{esc(CALLOUTS[kind])}</span>{"" if title == CALLOUTS[kind] else " " + inline(title, ctx)}</div>'
                f"{inner}</aside>")
    if m:
        ctx.warnings.append(f"unknown callout [!{m.group(1)}]; use one of {', '.join(CALLOUTS)}")
    return f"<blockquote>{blocks(q, ctx)}</blockquote>"


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def table(rows: list[str], ctx: Ctx) -> str:
    head = _cells(rows[0])
    th = "".join(f"<th>{inline(c, ctx)}</th>" for c in head)
    body = []
    for r in rows[1:]:
        cells = _cells(r)
        cells += [""] * (len(head) - len(cells))
        body.append("<tr>" + "".join(f"<td>{inline(c, ctx)}</td>" for c in cells[:len(head)]) + "</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def list_block(lines: list[str], i: int, ctx: Ctx, depth: int) -> tuple[int, str]:
    m = _LIST.match(lines[i])
    base = len(m.group(1))
    ordered = m.group(2)[0].isdigit()
    start = int(m.group(2)[:-1]) if ordered else 1
    items: list[list[str]] = []
    n = len(lines)
    while i < n:
        m = _LIST.match(lines[i])
        if not (m and len(m.group(1)) == base and m.group(2)[0].isdigit() == ordered):
            break
        content = [m.group(3)]
        i += 1
        while i < n:
            ln = lines[i]
            if not ln.strip():
                # a blank line continues the item only if the next text line is indented past the marker
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and len(lines[j]) - len(lines[j].lstrip()) > base:
                    content.extend([""] * (j - i))
                    i = j
                    continue
                break
            ind = len(ln) - len(ln.lstrip())
            if ind > base:
                content.append(ln)
            elif _LIST.match(ln) or _is_block_start(ln):
                break
            else:
                content.append(ln)  # lazy continuation
            i += 1
        items.append(content)
    lis = []
    for content in items:
        first, rest = content[0], _dedent(content[1:])
        inner = blocks([first] + rest, ctx, depth + 1)
        if inner.startswith("<p>") and inner.count("<p>") == 1 and inner.endswith("</p>"):
            inner = inner[3:-4]          # tight item
        elif inner.startswith("<p>"):
            inner = re.sub(r"^<p>(.*?)</p>", r"\1", inner, count=1, flags=re.S)
        lis.append(f"<li>{inner}</li>")
    tag = "ol" if ordered else "ul"
    st = f' start="{start}"' if ordered and start != 1 else ""
    return i, f"<{tag}{st}>{''.join(lis)}</{tag}>"


# ===================================================================== visual blocks

def visual(lang: str, text: str, ctx: Ctx) -> str:
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as e:
        return _visual_error(lang, f"not valid JSON: {e}", text, ctx)
    errs = validate_visual(lang, spec)
    if errs:
        return _visual_error(lang, "; ".join(errs), text, ctx)
    ctx.visuals += 1
    return {"lk-steps": steps_html, "lk-sequence": sequence_svg, "lk-arch": arch_html}[lang](spec, ctx)


def _visual_error(lang: str, msg: str, text: str, ctx: Ctx) -> str:
    ctx.errors.append(f"{lang}: {msg}")
    return (f'<div class="callout c-warning"><div class="callout-title"><span class="callout-kind">'
            f"Diagram error</span> {esc(lang)}: {esc(msg)}</div><pre><code>{esc(text)}</code></pre></div>")


def _need(cond: bool, msg: str, errs: list[str]) -> None:
    if not cond:
        errs.append(msg)


def _str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate_visual(lang: str, spec) -> list[str]:
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["the block must be a JSON object"]
    _need(isinstance(spec.get("title", ""), str), "title must be a string", errs)
    if lang == "lk-steps":
        steps = spec.get("steps")
        _need(isinstance(steps, list) and 2 <= len(steps) <= 12, "steps: a list of 2-12 steps", errs)
        for k, s in enumerate(steps if isinstance(steps, list) else []):
            if not (isinstance(s, dict) and isinstance(s.get("title"), str) and s["title"].strip()):
                errs.append(f"steps[{k}]: needs a title")
                continue
            bad = set(s) - {"title", "body", "example", "cost", "phase", "cite"}
            _need(not bad, f"steps[{k}]: unknown fields {sorted(bad)}", errs)
            for f in ("body", "example", "cost", "phase"):
                _need(isinstance(s.get(f, ""), str), f"steps[{k}].{f} must be a string", errs)
            _need(_str_list(s.get("cite", [])), f"steps[{k}].cite must be a list of source ids", errs)
    elif lang == "lk-sequence":
        actors = spec.get("actors")
        _need(isinstance(actors, list) and 2 <= len(actors) <= 7, "actors: a list of 2-7 actors", errs)
        ids = set()
        for k, a in enumerate(actors if isinstance(actors, list) else []):
            if not (isinstance(a, dict) and isinstance(a.get("id"), str) and isinstance(a.get("label"), str)):
                errs.append(f"actors[{k}]: needs id and label")
                continue
            _need(a["id"] not in ids, f"actors[{k}]: duplicate id {a['id']}", errs)
            ids.add(a["id"])
        msgs = spec.get("messages")
        _need(isinstance(msgs, list) and 1 <= len(msgs) <= 30, "messages: a list of 1-30 messages", errs)
        for k, m in enumerate(msgs if isinstance(msgs, list) else []):
            if not isinstance(m, dict) or not isinstance(m.get("label"), str):
                errs.append(f"messages[{k}]: needs a label")
                continue
            bad = set(m) - {"from", "to", "over", "label", "note", "phase", "reply", "both"}
            _need(not bad, f"messages[{k}]: unknown fields {sorted(bad)}", errs)
            if "over" in m:
                _need(m["over"] in ids, f"messages[{k}].over: unknown actor {m['over']!r}", errs)
            else:
                _need(m.get("from") in ids, f"messages[{k}].from: unknown actor {m.get('from')!r}", errs)
                _need(m.get("to") in ids, f"messages[{k}].to: unknown actor {m.get('to')!r}", errs)
                _need(m.get("from") != m.get("to"), f"messages[{k}]: from == to (use \"over\" for a note)", errs)
        _need(isinstance(spec.get("phases", {}), dict), "phases must be an object {key: label}", errs)
    elif lang == "lk-arch":
        panels = spec.get("panels")
        _need(isinstance(panels, list) and 1 <= len(panels) <= 3, "panels: a list of 1-3 panels", errs)
        for k, p in enumerate(panels if isinstance(panels, list) else []):
            if not isinstance(p, dict) or not isinstance(p.get("flow"), list) or not p["flow"]:
                errs.append(f"panels[{k}]: needs a non-empty flow")
                continue
            for f in ("pros", "cons"):
                _need(_str_list(p.get(f, [])), f"panels[{k}].{f} must be a list of strings", errs)
            for j, node in enumerate(p["flow"]):
                where = f"panels[{k}].flow[{j}]"
                if not isinstance(node, dict) or ("box" in node) == ("arrow" in node):
                    errs.append(f"{where}: each entry is either {{\"box\": ...}} or {{\"arrow\": ...}}")
                    continue
                if "box" in node:
                    _need(node.get("kind", "plain") in ARCH_KINDS, f"{where}.kind: one of {', '.join(ARCH_KINDS)}", errs)
                    _need(_str_list(node.get("items", [])), f"{where}.items must be a list of strings", errs)
    return errs


def _cites(ids, ctx: Ctx) -> str:
    if not ids:
        return ""
    return '<span class="cites">' + "".join(_cite_html(s, ctx) for s in ids) + "</span>"


def steps_html(spec: dict, ctx: Ctx) -> str:
    title = f'<figcaption>{inline(spec["title"], ctx)}</figcaption>' if spec.get("title") else ""
    lis = []
    for k, s in enumerate(spec["steps"], 1):
        ph = ctx.phase(s.get("phase"))
        attrs = f' data-phase="{esc(s["phase"])}" style="--ph:var(--ph{ph});--ph-bg:var(--ph{ph}-bg)"' if ph is not None else ""
        cost = f'<span class="chip">{inline(s["cost"], ctx)}</span>' if s.get("cost") else ""
        body = blocks(s["body"].split("\n"), ctx) if s.get("body") else ""
        ex = (f'<div class="step-ex"><span>Example</span><code>{esc(s["example"])}</code></div>'
              if s.get("example") else "")
        lis.append(f'<li class="step"{attrs}><div class="step-n">{k}</div><div class="step-main">'
                   f'<div class="step-head"><strong>{inline(s["title"], ctx)}</strong>{cost}</div>'
                   f'{body}{ex}{_cites(s.get("cite"), ctx)}</div></li>')
    return f'<figure class="lk-steps">{title}<ol>{"".join(lis)}</ol></figure>'


def _tw(s: str, size: float = 13) -> float:
    """Rough text width in px for a sans font: good enough to size columns."""
    wide = sum(1 for ch in s if ord(ch) > 0x2E80)
    return (len(s) - wide) * size * 0.56 + wide * size


def _wrap(s: str, width: float, size: float = 13) -> list[str]:
    """Greedy word wrap by estimated pixel width (an over-long single word keeps its own line)."""
    lines: list[str] = []
    cur = ""
    for w in s.split():
        t = f"{cur} {w}".strip()
        if cur and _tw(t, size) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines or [""]


def _tspans(lines: list[str], x: float, y_last: float, lh: float) -> str:
    """Text lines whose LAST line sits at y_last, so a wrapped label grows upwards, away from its arrow."""
    y0 = y_last - lh * (len(lines) - 1)
    return "".join(f'<tspan x="{x:.0f}" y="{y0 + lh * k:.0f}">{esc(t)}</tspan>' for k, t in enumerate(lines))


def sequence_svg(spec: dict, ctx: Ctx) -> str:
    actors = spec["actors"]
    msgs = spec["messages"]
    labels = spec.get("phases", {}) or {}
    col = {a["id"]: k for k, a in enumerate(actors)}
    lh = 16
    # Columns are capped so 5 actors fit the reading column at full text size. Long labels wrap instead
    # of widening the diagram: a wide SVG gets scaled down until its text is unreadable.
    cw = min(max([150.0] + [_tw(a["label"], 13) + 24 for a in actors]), 190.0)
    left = 84 if any(m.get("phase") for m in msgs) else 12
    W = int(left + cw * len(actors) + 8)
    x = {a["id"]: left + cw * (k + 0.5) for k, a in enumerate(actors)}
    actor_lines = {a["id"]: _wrap(a["label"], cw - 28, 13) for a in actors}
    box_h = 14 + lh * max(len(v) for v in actor_lines.values())
    top = 8 + box_h + 14

    rows = []                         # (y, height, label lines) per message
    y = top
    for m in msgs:
        if "over" in m:
            lines = _wrap(m["label"], cw * 1.5, 12)
            h = 22 + lh * len(lines)
        else:
            span = abs(col[m["to"]] - col[m["from"]])
            lines = _wrap(m["label"], span * cw - 18)
            h = 30 + lh * len(lines) + (14 if m.get("note") else 0)
        rows.append((y, h, lines))
        y += h
    H = int(y + 16)

    g: list[str] = []
    k = 0
    while k < len(msgs):              # one band per run of consecutive messages in the same phase
        p = msgs[k].get("phase")
        j = k
        while j + 1 < len(msgs) and msgs[j + 1].get("phase") == p:
            j += 1
        if p:
            ci = ctx.phase(p)
            y0, y1 = rows[k][0] + 3, rows[j][0] + rows[j][1] - 3
            g.append(f'<g class="sq-band" data-phase="{esc(p)}" style="--ph:var(--ph{ci});--ph-bg:var(--ph{ci}-bg)">'
                     f'<rect x="4" y="{y0}" width="{W - 8}" height="{y1 - y0}" rx="8" class="band-bg"/>'
                     f'<rect x="4" y="{y0}" width="4" height="{y1 - y0}" rx="2" class="band-edge"/>'
                     f'<text x="16" y="{(y0 + y1) / 2 + 4:.0f}" class="band-label">{esc(labels.get(p, p))}</text></g>')
        k = j + 1
    for a in actors:
        cx, al = x[a["id"]], actor_lines[a["id"]]
        bw = min(cw - 10, max(_tw(t, 13) for t in al) + 22)
        g.append(f'<line x1="{cx:.0f}" y1="{8 + box_h}" x2="{cx:.0f}" y2="{H - 6}" class="sq-life"/>'
                 f'<rect x="{cx - bw / 2:.0f}" y="8" width="{bw:.0f}" height="{box_h}" rx="8" class="sq-actor"/>'
                 f'<text text-anchor="middle" class="sq-actor-t">'
                 f'{_tspans(al, cx, 8 + box_h / 2 + 5 + lh * (len(al) - 1) / 2, lh)}</text>')
    for (ry, rh, lines), m in zip(rows, msgs):
        ph = f' data-phase="{esc(m["phase"])}"' if m.get("phase") else ""
        if "over" in m:
            cx = x[m["over"]]
            tw = max(_tw(t, 12) for t in lines) + 20
            bh = 8 + lh * len(lines)
            by = ry + (rh - bh) / 2
            g.append(f'<g class="sq-msg"{ph}><rect x="{cx - tw / 2:.0f}" y="{by:.0f}" width="{tw:.0f}" height="{bh:.0f}" '
                     f'rx="6" class="sq-note"/><text text-anchor="middle" class="sq-note-t">'
                     f'{_tspans(lines, cx, by + bh - 9, lh)}</text></g>')
            continue
        ay = ry + 14 + lh * len(lines)
        x1, x2 = x[m["from"]], x[m["to"]]
        d = 1 if x2 > x1 else -1
        cls = "sq-arrow reply" if m.get("reply") else "sq-arrow"
        start = ' marker-start="url(#sq-head)"' if m.get("both") else ""
        mid = (x1 + x2) / 2
        note = (f'<text x="{mid:.0f}" y="{ay + 16:.0f}" text-anchor="middle" class="sq-sub">{esc(m["note"])}</text>'
                if m.get("note") else "")
        g.append(f'<g class="sq-msg"{ph}><line x1="{x1 + d * (8 if start else 3):.0f}" y1="{ay:.0f}" '
                 f'x2="{x2 - d * 8:.0f}" y2="{ay:.0f}" class="{cls}" marker-end="url(#sq-head)"{start}/>'
                 f'<text text-anchor="middle" class="sq-label">{_tspans(lines, mid, ay - 7, lh)}</text>{note}</g>')
    defs = ('<defs><marker id="sq-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="sq-head"/></marker></defs>')
    title = f'<figcaption>{inline(spec["title"], ctx)}</figcaption>' if spec.get("title") else ""
    aria = esc(spec.get("title") or "Sequence diagram")
    return (f'<figure class="lk-seq">{title}<div class="scroll-x"><svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'style="max-width:100%;height:auto;min-width:{min(W, 560)}px" role="img" aria-label="{aria}">'
            f'{defs}{"".join(g)}</svg></div></figure>')


def arch_html(spec: dict, ctx: Ctx) -> str:
    panels = []
    for p in spec["panels"]:
        flow = []
        for node in p["flow"]:
            if "arrow" in node:
                flow.append(f'<div class="arch-arrow"><span>{inline(node["arrow"], ctx)}</span></div>')
                continue
            kind = node.get("kind", "plain")
            sub = f'<div class="arch-sub">{inline(node["sub"], ctx)}</div>' if node.get("sub") else ""
            items = ("<ul>" + "".join(f"<li>{inline(t, ctx)}</li>" for t in node["items"]) + "</ul>"
                     if node.get("items") else "")
            flow.append(f'<div class="arch-box k-{kind}"><div class="arch-name">{inline(node["box"], ctx)}</div>'
                        f"{sub}{items}</div>")
        notes = "".join(f'<li class="pro">{inline(t, ctx)}</li>' for t in p.get("pros", []))
        notes += "".join(f'<li class="con">{inline(t, ctx)}</li>' for t in p.get("cons", []))
        head = f'<div class="arch-title">{inline(p["title"], ctx)}</div>' if p.get("title") else ""
        panels.append(f'<div class="arch-panel">{head}<div class="arch-flow">{"".join(flow)}</div>'
                      f'{"<ul class=" + chr(34) + "arch-notes" + chr(34) + ">" + notes + "</ul>" if notes else ""}</div>')
    title = f'<figcaption>{inline(spec["title"], ctx)}</figcaption>' if spec.get("title") else ""
    return f'<figure class="lk-arch n{len(panels)}">{title}<div class="arch-panels">{"".join(panels)}</div></figure>'


# ===================================================================== document

def _split_sections(body_lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Split on level-2 headings (outside fences) so each becomes a <section> with its own reading time."""
    secs: list[tuple[str | None, list[str]]] = [(None, [])]
    fence = None
    for ln in body_lines:
        m = _FENCE.match(ln)
        if m:
            fence = None if fence and m.group(2).startswith(fence) else (fence or m.group(2))
        if not fence and re.match(r"^##\s", ln):
            secs.append((ln, []))
        else:
            secs[-1][1].append(ln)
    return secs


def _prose_words(lines: list[str]) -> int:
    words, fence = 0, None
    for ln in lines:
        m = _FENCE.match(ln)
        if m:
            fence = None if fence else m.group(2)
            continue
        if not fence:
            words += _words(re.sub(r"\[S\d{4}\]", "", ln))
    return words


def render(md: str, ctx: Ctx, meta: dict | None = None) -> str:
    meta = {} if meta is None else meta
    lines = md.replace("\r\n", "\n").split("\n")
    title = meta.get("title") or "Research"
    if lines and _HEAD.match(lines[0]) and lines[0].startswith("# "):
        title = _HEAD.match(lines[0]).group(2)
        lines = lines[1:]
    secs = _split_sections(lines)
    total_words = 0
    parts, toc = [], []
    for head, body in secs:
        words = _prose_words(body) + (_words(head) if head else 0)
        total_words += words
        mins = max(1, round(words / WORDS_PER_MIN))
        if head is None:
            inner = blocks(body, ctx)
            if inner.startswith("<blockquote>"):
                inner = inner.replace("<blockquote>", '<blockquote class="lead">', 1)
            parts.append(f'<section class="intro">{inner}</section>')
            continue
        h = heading(2, _HEAD.match(head).group(2), _HEAD.match(head).group(3), ctx)
        anchor = re.search(r'id="([^"]+)"', h).group(1)
        sub = []
        for ln in body:
            mm = _HEAD.match(ln)
            if mm and len(mm.group(1)) == 3:
                sub.append((mm.group(2), mm.group(3)))
        h = h.replace("</h2>", f'<span class="rt">{mins} min</span></h2>', 1)
        inner = blocks(body, ctx)
        parts.append(f'<section id="sec-{esc(anchor)}">{h}{inner}</section>')
        toc.append((anchor, _HEAD.match(head).group(2), mins, sub))
    toc_html = []
    for anchor, text, mins, sub in toc:
        kids = ""
        if sub:
            items = []
            for t, cid in sub:
                a = slugify(cid or t)
                core = ctx.concepts.get(cid, {}).get("core") if cid else False
                items.append(f'<li><a href="#{esc(a)}">{inline(t, Ctx())}{" <b class=" + chr(34) + "dot" + chr(34) + " title=" + chr(34) + "core concept" + chr(34) + "></b>" if core else ""}</a></li>')
            kids = f'<ul>{"".join(items)}</ul>'
        toc_html.append(f'<li><a href="#{esc(anchor)}">{inline(text, Ctx())}<span class="rt">{mins}m</span></a>{kids}</li>')
    for cid in ctx.concepts:
        if cid not in ctx.tagged:
            ctx.warnings.append(f"concept '{cid}' has no heading anchor {{#{cid}}} in the document")
    minutes = max(1, round(total_words / WORDS_PER_MIN))
    sub_bits = [b for b in (meta.get("subtitle"), f"{minutes} min read", f"{ctx.visuals} diagrams" if ctx.visuals else None) if b]
    page = (TEMPLATE
            .replace("%%TITLE%%", esc(title))
            .replace("%%TITLE_HTML%%", inline(title, Ctx()))
            .replace("%%SUBTITLE%%", " · ".join(esc(b) for b in sub_bits))
            .replace("%%TOC%%", "".join(toc_html))
            .replace("%%BODY%%", "\n".join(parts))
            .replace("%%CSS%%", CSS)
            .replace("%%JS%%", JS))
    meta["words"], meta["minutes"] = total_words, minutes
    return page


# ===================================================================== topic entry points

def _ctx_for(slug: str) -> Ctx:
    return Ctx(sources.by_id(), topics.concept_map(slug))


def doc_path(slug: str, file: str | None = None) -> Path:
    d = topics.tdir(slug)
    p = (d / (file or "research.md")).resolve()
    if d.resolve() not in p.parents:
        raise LkError("the file must be inside the topic folder")
    if not p.is_file():
        raise LkError(f"no such file: {p}")
    return p


def check(slug: str, file: str | None = None) -> dict:
    p = doc_path(slug, file)
    ctx = _ctx_for(slug) if file in (None, "research.md") else Ctx(sources.by_id())
    meta: dict = {}
    render(p.read_text(encoding="utf-8"), ctx, meta)
    return {"ok": not ctx.errors, "errors": ctx.errors, "warnings": sorted(set(ctx.warnings)),
            "visuals": ctx.visuals, "words": meta["words"], "minutes": meta["minutes"]}


def page(slug: str, file: str | None = None) -> tuple[str, dict]:
    """Render a topic doc to a page in memory (the web app serves this; `build` writes it to disk)."""
    p = doc_path(slug, file)
    research = file in (None, "research.md")
    ctx = _ctx_for(slug) if research else Ctx(sources.by_id())
    st = topics.load_state(slug)
    meta = {"subtitle": f"{slug} · v{st['version']} · {st['depth']}"}
    html = render(p.read_text(encoding="utf-8"), ctx, meta)
    return html, {"source": str(p), "ok": not ctx.errors, "errors": ctx.errors,
                  "warnings": sorted(set(ctx.warnings)), "visuals": ctx.visuals, "words": meta["words"],
                  "minutes": meta["minutes"]}


def build(slug: str, file: str | None = None, out: str | None = None) -> dict:
    html, info = page(slug, file)
    p = Path(info.pop("source"))
    dest = Path(out).expanduser() if out else p.with_suffix(".html")
    write_text(dest, html)
    return {"html": str(dest), "url": dest.resolve().as_uri(), **info}


# ===================================================================== page assets

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<style>%%CSS%%</style>
</head>
<body>
<div class="progress"><div id="progress"></div></div>
<header class="bar">
  <button class="btn toc-btn" id="toc-btn" aria-expanded="false" aria-controls="toc">Contents</button>
  <div class="bar-title">%%TITLE_HTML%%</div>
  <div class="bar-right">
    <button class="btn" id="cite-btn" aria-pressed="true" title="Show or hide source chips">Sources</button>
    <button class="btn" id="theme-btn" title="Light / dark">Theme</button>
  </div>
</header>
<div class="layout">
  <nav id="toc" class="toc" aria-label="Contents"><ul>%%TOC%%</ul></nav>
  <main>
    <h1>%%TITLE_HTML%%</h1>
    <div class="subtitle">%%SUBTITLE%%</div>
%%BODY%%
    <footer class="foot">Rendered from Markdown by <code>lk read</code>. Edit the .md, not this page; it is rebuilt.</footer>
  </main>
</div>
<script>%%JS%%</script>
</body>
</html>
"""

CSS = r"""
:root {
  --bg:#f7f7f5; --surface:#fff; --surface-2:#f0efeb; --text:#1d1d1b; --muted:#6b6a66; --border:#e2e0da;
  --accent:#3b5bdb; --code-bg:#f3f2ee; --ok:#2b8a3e; --bad:#c92a2a; --radius:10px;
  --mono: ui-monospace,"Cascadia Code","JetBrains Mono",Consolas,monospace;
  --sans: system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --ph0:#3b5bdb; --ph0-bg:#e9edff; --ph1:#0c8599; --ph1-bg:#e3f6f8; --ph2:#7048e8; --ph2-bg:#f0ebff;
  --ph3:#d9480f; --ph3-bg:#fff0e6; --ph4:#2b8a3e; --ph4-bg:#e7f5ea; --ph5:#c2255c; --ph5-bg:#fdebf1;
  --c-note:#3b5bdb; --c-tip:#2b8a3e; --c-example:#0c8599; --c-warning:#d9480f; --c-misconception:#c92a2a;
  --c-interview:#7048e8; --c-tldr:#495057; --c-question:#b35c00;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#141413; --surface:#1d1d1b; --surface-2:#262624; --text:#ecebe6; --muted:#a3a29c; --border:#34342f;
  --accent:#91a7ff; --code-bg:#232321; --ok:#69db7c; --bad:#ff8787;
  --ph0:#91a7ff; --ph0-bg:#1f2547; --ph1:#66d9e8; --ph1-bg:#10303a; --ph2:#b197fc; --ph2-bg:#271f45;
  --ph3:#ffa94d; --ph3-bg:#3a2412; --ph4:#69db7c; --ph4-bg:#1b2e20; --ph5:#f783ac; --ph5-bg:#3a1825;
  --c-note:#91a7ff; --c-tip:#69db7c; --c-example:#66d9e8; --c-warning:#ffa94d; --c-misconception:#ff8787;
  --c-interview:#b197fc; --c-tldr:#adb5bd; --c-question:#ffc078;
} }
:root[data-theme="dark"] {
  --bg:#141413; --surface:#1d1d1b; --surface-2:#262624; --text:#ecebe6; --muted:#a3a29c; --border:#34342f;
  --accent:#91a7ff; --code-bg:#232321; --ok:#69db7c; --bad:#ff8787;
  --ph0:#91a7ff; --ph0-bg:#1f2547; --ph1:#66d9e8; --ph1-bg:#10303a; --ph2:#b197fc; --ph2-bg:#271f45;
  --ph3:#ffa94d; --ph3-bg:#3a2412; --ph4:#69db7c; --ph4-bg:#1b2e20; --ph5:#f783ac; --ph5-bg:#3a1825;
  --c-note:#91a7ff; --c-tip:#69db7c; --c-example:#66d9e8; --c-warning:#ffa94d; --c-misconception:#ff8787;
  --c-interview:#b197fc; --c-tldr:#adb5bd; --c-question:#ffc078;
}
* { box-sizing:border-box; }
html { scroll-padding-top:64px; }
html, body { margin:0; }
body { background:var(--bg); color:var(--text); font:17px/1.65 var(--sans); }
a { color:var(--accent); }
.progress { position:fixed; top:0; left:0; right:0; height:3px; z-index:5; }
#progress { height:100%; width:0; background:var(--accent); }
.bar { position:sticky; top:0; z-index:4; display:flex; align-items:center; gap:12px; padding:8px 16px;
  background:var(--surface); border-bottom:1px solid var(--border); }
.bar-title { flex:1; min-width:0; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar-right { display:flex; gap:8px; }
.btn { font:inherit; font-size:.85rem; color:var(--text); background:var(--surface-2); border:1px solid var(--border);
  border-radius:8px; padding:4px 10px; cursor:pointer; }
.btn[aria-pressed="false"] { opacity:.55; }
.toc-btn { display:none; }
.layout { display:grid; grid-template-columns:260px minmax(0,1fr); gap:32px; max-width:1180px; margin:0 auto; padding:0 16px; }
.toc { position:sticky; top:60px; align-self:start; max-height:calc(100vh - 72px); overflow:auto; padding:20px 0; font-size:.87rem; }
.toc ul { list-style:none; margin:0; padding:0; }
.toc ul ul { padding-left:12px; margin:2px 0 8px; border-left:1px solid var(--border); }
.toc a { display:flex; gap:6px; align-items:baseline; padding:3px 8px; border-radius:6px; color:var(--muted); text-decoration:none; }
.toc a:hover { background:var(--surface-2); color:var(--text); }
.toc a.active { color:var(--text); background:var(--surface-2); font-weight:600; }
.toc .rt { margin-left:auto; font-size:.75rem; opacity:.7; }
.dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--accent); margin-left:4px; }
main { min-width:0; max-width:820px; padding:28px 0 96px; }
h1 { font-size:1.9rem; line-height:1.25; margin:0 0 6px; }
.subtitle { color:var(--muted); font-size:.9rem; margin-bottom:24px; }
section { margin-top:40px; }
section.intro { margin-top:0; }
h2 { font-size:1.4rem; line-height:1.3; margin:0 0 14px; padding-top:8px; border-top:1px solid var(--border); padding-top:20px; }
h3 { font-size:1.15rem; margin:32px 0 8px; }
h4 { font-size:1rem; margin:24px 0 6px; }
h2 .rt { font-size:.75rem; font-weight:500; color:var(--muted); margin-left:10px; }
.hash { opacity:0; margin-left:8px; text-decoration:none; color:var(--muted); font-weight:400; }
h2:hover .hash, h3:hover .hash, h4:hover .hash { opacity:1; }
.badge { font-size:.68rem; font-weight:600; letter-spacing:.02em; text-transform:uppercase; vertical-align:middle;
  margin-left:10px; padding:2px 7px; border-radius:99px; border:1px solid var(--border); color:var(--muted); }
.badge.core { color:var(--accent); border-color:var(--accent); }
.concept-summary { margin:0 0 14px; padding:8px 12px; background:var(--surface-2); border-radius:8px; font-size:.95rem; }
.concept-summary span { font-size:.7rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); margin-right:8px; }
p { margin:0 0 14px; }
ul, ol { margin:0 0 14px; padding-left:24px; }
li { margin:4px 0; }
li > ul, li > ol { margin-bottom:4px; }
code { font-family:var(--mono); font-size:.86em; background:var(--code-bg); padding:1px 5px; border-radius:5px; }
pre { background:var(--code-bg); border:1px solid var(--border); border-radius:var(--radius); padding:12px 14px;
  overflow-x:auto; font-size:.85rem; line-height:1.5; margin:0 0 16px; }
pre code { background:none; padding:0; font-size:inherit; }
blockquote { margin:0 0 16px; padding:2px 16px; border-left:3px solid var(--border); color:var(--muted); }
blockquote.lead { border:0; padding:16px 18px; background:var(--surface); border-radius:var(--radius);
  box-shadow:0 0 0 1px var(--border); color:var(--text); font-size:1.05rem; }
blockquote.lead p:last-child { margin:0; }
hr { border:0; border-top:1px solid var(--border); margin:28px 0; }
.table-wrap { overflow-x:auto; margin:0 0 16px; border:1px solid var(--border); border-radius:var(--radius); }
table { border-collapse:collapse; width:100%; font-size:.92rem; background:var(--surface); }
th, td { text-align:left; vertical-align:top; padding:8px 12px; border-bottom:1px solid var(--border); }
th { background:var(--surface-2); font-weight:600; }
tr:last-child td { border-bottom:0; }
.cite { display:inline-block; position:relative; font-size:.66rem; font-weight:600; line-height:1; vertical-align:2px;
  padding:3px 5px; margin-left:3px; border-radius:5px; background:var(--surface-2); color:var(--muted);
  text-decoration:none; font-family:var(--mono); }
.cite + .cite { margin-left:2px; }
.cite:hover { color:var(--accent); }
.cite.missing { color:var(--bad); }
.cite:hover::after { content:attr(data-tip); position:absolute; left:0; top:calc(100% + 6px); z-index:9; width:max-content;
  max-width:300px; white-space:normal; font:500 .78rem/1.4 var(--sans); padding:6px 9px; border-radius:7px;
  background:var(--text); color:var(--bg); }
body.no-cites .cite, body.no-cites .cites { display:none; }
.callout { margin:0 0 18px; padding:12px 16px; border-radius:var(--radius); background:var(--surface);
  border:1px solid var(--border); border-left:4px solid var(--cc, var(--accent)); }
.callout > :last-child { margin-bottom:0; }
.callout-title { font-weight:600; margin-bottom:6px; }
.callout-kind { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--cc); margin-right:4px; }
.c-note{--cc:var(--c-note)} .c-tip{--cc:var(--c-tip)} .c-example{--cc:var(--c-example)} .c-warning{--cc:var(--c-warning)}
.c-misconception{--cc:var(--c-misconception)} .c-interview{--cc:var(--c-interview)} .c-tldr{--cc:var(--c-tldr)}
.c-question{--cc:var(--c-question)}
figure { margin:8px 0 24px; }
figcaption { font-weight:600; font-size:.95rem; margin-bottom:10px; }
.scroll-x { overflow-x:auto; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:6px; }
/* steps */
.lk-steps ol { list-style:none; padding:0; margin:0; position:relative; }
.lk-steps ol::before { content:""; position:absolute; left:17px; top:8px; bottom:8px; width:2px; background:var(--border); }
.step { position:relative; display:grid; grid-template-columns:36px minmax(0,1fr); gap:14px; margin:0 0 12px;
  transition:opacity .15s; }
.step-n { width:36px; height:36px; border-radius:50%; display:grid; place-items:center; font-weight:700; font-size:.95rem;
  background:var(--ph-bg, var(--surface-2)); color:var(--ph, var(--text)); border:2px solid var(--ph, var(--border)); z-index:1; }
.step-main { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:10px 14px;
  border-top:3px solid var(--ph, var(--border)); }
.step-head { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; margin-bottom:4px; }
.step-main p { margin:0 0 8px; font-size:.95rem; }
.step-main > :last-child { margin-bottom:0; }
.chip { font-size:.75rem; font-weight:600; padding:1px 8px; border-radius:99px; background:var(--ph-bg, var(--surface-2));
  color:var(--ph, var(--muted)); }
.step-ex { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; margin:6px 0 8px; font-size:.88rem; }
.step-ex span { font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
.step-ex code { white-space:pre-wrap; word-break:break-word; }
.cites { display:inline-block; }
/* sequence */
.lk-seq svg { display:block; margin:0 auto; font-family:var(--sans); }
.sq-life { stroke:var(--border); stroke-width:1.5; stroke-dasharray:3 4; }
.sq-actor { fill:var(--surface-2); stroke:var(--border); }
.sq-actor-t { fill:var(--text); font-size:13px; font-weight:600; }
.sq-arrow { stroke:var(--text); stroke-width:1.6; }
.sq-arrow.reply { stroke-dasharray:6 4; }
.sq-head { fill:var(--text); }
.sq-msg[data-phase] .sq-arrow { stroke:var(--ph-line, var(--text)); }
.sq-label { fill:var(--text); font-size:13px; }
.sq-sub { fill:var(--muted); font-size:12px; font-style:italic; }
.sq-note { fill:var(--surface-2); stroke:var(--border); }
.sq-note-t { fill:var(--text); font-size:12px; }
.band-bg { fill:var(--ph-bg); }
.band-edge { fill:var(--ph); }
.band-label { fill:var(--ph); font-size:12px; font-weight:700; letter-spacing:.03em; text-transform:uppercase; }
.sq-band, .sq-msg, .step { transition:opacity .15s; }
.focus-on .sq-band:not(.on), .focus-on .sq-msg:not(.on), .focus-on .step:not(.on) { opacity:.25; }
/* architecture */
.arch-panels { display:grid; gap:16px; }
.lk-arch.n2 .arch-panels { grid-template-columns:repeat(2,minmax(0,1fr)); }
.lk-arch.n3 .arch-panels { grid-template-columns:repeat(3,minmax(0,1fr)); }
.arch-panel { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:14px; }
.arch-title { font-weight:700; font-size:.92rem; margin-bottom:12px; text-align:center; }
.arch-flow { display:flex; flex-direction:column; align-items:center; }
.arch-box { width:100%; max-width:280px; border:1.5px solid var(--kc, var(--border)); background:var(--kb, var(--surface-2));
  border-radius:10px; padding:8px 12px; text-align:center; }
.arch-name { font-weight:700; color:var(--kc, var(--text)); }
.arch-sub { font-size:.8rem; color:var(--muted); }
.arch-box ul { list-style:none; padding:0; margin:6px 0 0; font-size:.85rem; }
.arch-box li { margin:3px 0; padding:2px 6px; background:var(--surface); border-radius:6px; }
.arch-arrow { position:relative; display:flex; justify-content:center; padding:6px 0 10px; min-height:40px; width:100%; }
.arch-arrow::before { content:""; position:absolute; top:4px; bottom:10px; left:50%; width:2px; background:var(--muted); }
.arch-arrow::after { content:""; position:absolute; bottom:4px; left:calc(50% - 5px); border:5px solid transparent;
  border-top:7px solid var(--muted); border-bottom:0; }
.arch-arrow span { position:relative; z-index:1; align-self:center; font-size:.78rem; color:var(--muted); background:var(--surface);
  padding:0 6px; }
.arch-arrow span:empty { display:none; }
.k-client{--kc:var(--ph0);--kb:var(--ph0-bg)} .k-edge{--kc:var(--ph5);--kb:var(--ph5-bg)} .k-web{--kc:var(--ph1);--kb:var(--ph1-bg)}
.k-app{--kc:var(--ph2);--kb:var(--ph2-bg)} .k-data{--kc:var(--ph4);--kb:var(--ph4-bg)} .k-cache{--kc:var(--ph3);--kb:var(--ph3-bg)}
.k-queue{--kc:var(--ph3);--kb:var(--ph3-bg)} .k-external{--kc:var(--muted);--kb:var(--surface)}
.arch-notes { list-style:none; padding:0; margin:12px 0 0; font-size:.86rem; }
.arch-notes li { padding-left:22px; position:relative; }
.arch-notes li::before { position:absolute; left:0; font-weight:700; }
.arch-notes .pro::before { content:"+"; color:var(--ok); } .arch-notes .con::before { content:"−"; color:var(--bad); }
.foot { margin-top:56px; font-size:.8rem; color:var(--muted); }
@media (max-width: 900px) {
  .layout { grid-template-columns:minmax(0,1fr); gap:0; }
  .toc-btn { display:inline-block; }
  .toc { display:none; position:fixed; top:50px; left:0; right:0; max-height:70vh; z-index:6; background:var(--surface);
    border-bottom:1px solid var(--border); padding:12px 16px; box-shadow:0 8px 24px rgba(0,0,0,.15); }
  .toc.open { display:block; }
  .lk-arch.n2 .arch-panels, .lk-arch.n3 .arch-panels { grid-template-columns:minmax(0,1fr); }
  body { font-size:16px; }
  h1 { font-size:1.55rem; }
}
@media print { .bar, .toc, .progress { display:none; } .layout { display:block; } }
"""

JS = r"""
(function () {
  var root = document.documentElement, body = document.body;
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  var theme = get('lk-reader-theme'); if (theme) root.setAttribute('data-theme', theme);
  document.getElementById('theme-btn').onclick = function () {
    var cur = root.getAttribute('data-theme') ||
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var next = cur === 'dark' ? 'light' : 'dark'; root.setAttribute('data-theme', next); set('lk-reader-theme', next);
  };
  var citeBtn = document.getElementById('cite-btn');
  function cites(on) { body.classList.toggle('no-cites', !on); citeBtn.setAttribute('aria-pressed', on ? 'true' : 'false'); }
  cites(get('lk-reader-cites') !== 'off');
  citeBtn.onclick = function () { var on = body.classList.contains('no-cites'); cites(on); set('lk-reader-cites', on ? 'on' : 'off'); };
  var toc = document.getElementById('toc'), tocBtn = document.getElementById('toc-btn');
  tocBtn.onclick = function () { var o = toc.classList.toggle('open'); tocBtn.setAttribute('aria-expanded', o); };
  toc.addEventListener('click', function (e) { if (e.target.closest('a')) { toc.classList.remove('open'); tocBtn.setAttribute('aria-expanded', false); } });
  var bar = document.getElementById('progress');
  var links = {}; toc.querySelectorAll('a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
  var heads = Array.prototype.slice.call(document.querySelectorAll('main h2[id], main h3[id]'));
  function onScroll() {
    var h = document.documentElement, max = h.scrollHeight - h.clientHeight;
    bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + '%';
    var cur = null;
    for (var i = 0; i < heads.length; i++) { if (heads[i].getBoundingClientRect().top < 120) cur = heads[i].id; else break; }
    Object.keys(links).forEach(function (k) { links[k].classList.toggle('active', k === cur); });
  }
  addEventListener('scroll', onScroll, { passive: true }); onScroll();
  // Hover a step (or a phase band) to light up the same phase in every diagram.
  function focus(phase) {
    var any = !!phase;
    body.classList.toggle('focus-on', any);
    document.querySelectorAll('[data-phase]').forEach(function (el) { el.classList.toggle('on', any && el.getAttribute('data-phase') === phase); });
  }
  document.querySelectorAll('.step[data-phase], .sq-band[data-phase]').forEach(function (el) {
    el.addEventListener('mouseenter', function () { focus(el.getAttribute('data-phase')); });
    el.addEventListener('mouseleave', function () { focus(null); });
  });
})();
"""
