#!/usr/bin/env python3
"""
Build a notebook into a public static site.

    build.py NOTEBOOK [OUT]        # OUT defaults to NOTEBOOK/dist

Copies the chapters and assets/ into OUT. If NOTEBOOK/learning-path.json exists (written by
/learning-path:diagnose) it also renders OUT/path.html and adds assets/path.js to every page:
a "Your path" link in the sidebar, a "path · step N" badge on each question on the path, and
deep links (chapter.html#<qid>) that land on the question.

Never copied: answers/, .notes/, examples/, *.zip, serve.py, assets/check.py — that is the
learner's private work, and the published site has no server to save to (notebook.js falls
back to localStorage on its own).
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog import pages, questions, text  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
e = html.escape


def slug(s):
    # same rule app.js uses to give headings their ids
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", s.strip().lower()))[:60]


def section_ids(src):
    out = {}
    for attrs, inner in re.findall(r"<h2([^>]*)>(.*?)</h2>", src, re.S):
        m = re.search(r'id="([^"]+)"', attrs)
        out[text(inner).lower()] = m.group(1) if m else slug(text(inner))
    return out


def render_path(lp, chapters):
    """chapters: {file: {"title", "src", "q": {qid: text}}}. Returns (html, path_js_data)."""
    problems, on_ch, on_q = [], {}, {}
    steps_html = []
    for i, st in enumerate(lp.get("steps") or [], 1):
        f = st.get("chapter")
        if f not in chapters:
            problems.append(f"step {i}: unknown chapter {f!r}")
            continue
        ch = chapters[f]
        on_ch.setdefault(f, []).append(i)
        secs = section_ids(ch["src"])
        read = []
        for s in st.get("sections") or []:
            sid = secs.get(s.lower())
            if sid is None:
                problems.append(f"step {i}: no section {s!r} in {f}")
                continue
            read.append(f'<a href="{f}#{sid}">{e(s)}</a>')
        qs = []
        for qid in st.get("questions") or []:
            if qid not in ch["q"]:
                problems.append(f"step {i}: no question {qid!r} in {f}")
                continue
            on_q[qid] = i
            qs.append(f'<li><a href="{f}#{qid}"><code>{qid}</code></a> {e(ch["q"][qid])}</li>')
        parts = [f'<div class="practice" id="step-{i}">',
                 f'<span class="t">Step {i} <span class="time">&middot; {e(st.get("time", ""))}</span></span>',
                 f'<h3>{e(st.get("title") or ch["title"])}</h3>',
                 f'<p><b>Why:</b> {e(st.get("why", ""))}</p>',
                 f'<p><b>Chapter:</b> <a href="{f}">{e(ch["title"])}</a></p>']
        if read:
            parts.append("<p><b>Read:</b> " + " &middot; ".join(read) + "</p>")
        if qs:
            parts.append("<p><b>Answer:</b></p><ul>" + "".join(qs) + "</ul>")
        if st.get("practices"):
            parts.append("<p><b>Do:</b> " + " &middot; ".join(e(p) for p in st["practices"]) + "</p>")
        if st.get("done_when"):
            parts.append(f'<p style="margin-bottom:0"><b>Done when:</b> {e(st["done_when"])}</p>')
        steps_html.append("\n".join(parts) + "\n</div>")
    if problems:
        sys.exit("learning-path.json does not match the notebook:\n  " + "\n  ".join(problems))

    ev = "".join(f"<li>{e(x)}</li>" for x in lp.get("evidence") or [])
    skip = "".join(f'<tr><td><a href="{s["chapter"]}">{e(chapters.get(s["chapter"], {}).get("title", s["chapter"]))}</a></td>'
                   f'<td>{e(s.get("why", ""))}</td></tr>' for s in lp.get("skip") or [])
    body = f"""<h1>{e(lp.get("title", "Your learning path"))}</h1>
<p class="lede">{e(lp.get("diagnosis", ""))}</p>
<p class="q-note">{e(lp.get("learner", ""))} &middot; diagnosed {e(lp.get("created", ""))}</p>
{f'<div class="box info"><span class="box-title">What the interview showed</span><ul style="margin-bottom:0">{ev}</ul></div>' if ev else ''}
<h2>The path</h2>
{chr(10).join(steps_html)}
{f'<h2>Skip for now</h2><table><thead><tr><th>Chapter</th><th>Why</th></tr></thead><tbody>{skip}</tbody></table>' if skip else ''}
{f'<h2>When to re-diagnose</h2><p>{e(lp["revisit"])}</p>' if lp.get("revisit") else ''}"""
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(lp.get("title", "Your learning path"))}</title>
<link rel="stylesheet" href="assets/style.css">
<script src="assets/app.js"></script>
<script src="assets/notebook.js"></script>
<script src="assets/path.js"></script>
</head>
<body data-chapter="path.html">
<main class="content">
{body}
</main>
</body>
</html>
"""
    return page, {"chapters": on_ch, "questions": on_q}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(root, "dist")
    if out == root:
        sys.exit("OUT must not be the notebook folder itself")
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(root, "assets"), os.path.join(out, "assets"),
                    ignore=shutil.ignore_patterns("check.py", "__pycache__"))

    chapters = {}
    for p in pages(root):
        src_path = os.path.join(root, p["file"])
        if not os.path.exists(src_path):
            continue
        src = open(src_path, encoding="utf-8").read()
        chapters[p["file"]] = {"title": text(p["title"]), "src": src,
                               "q": {qid: q for qid, _, q, _ in questions(src)}}

    lp_file = os.path.join(root, "learning-path.json")
    lp = json.load(open(lp_file, encoding="utf-8")) if os.path.exists(lp_file) else None
    if lp:
        page, data = render_path(lp, chapters)
        open(os.path.join(out, "path.html"), "w", encoding="utf-8").write(page)
        js = open(os.path.join(HERE, "path.js"), encoding="utf-8").read()
        open(os.path.join(out, "assets", "path.js"), "w", encoding="utf-8").write(
            "var LEARNING_PATH = " + json.dumps(data) + ";\n" + js)
        with open(os.path.join(out, "assets", "style.css"), "a", encoding="utf-8") as f:
            f.write(open(os.path.join(HERE, "path.css"), encoding="utf-8").read())

    for f, ch in chapters.items():
        src = ch["src"]
        if lp:
            src = src.replace('<script src="assets/notebook.js"></script>',
                              '<script src="assets/notebook.js"></script>\n<script src="assets/path.js"></script>', 1)
            if f == "index.html":
                src = src.replace('<main class="content">', '<main class="content">\n<div class="box good path-cta">'
                                  '<span class="box-title">You have a learning path</span>'
                                  '<p style="margin-bottom:0"><a href="path.html">Start with your path &rarr;</a> '
                                  '&mdash; the chapters, sections and questions picked for you.</p></div>', 1)
        open(os.path.join(out, f), "w", encoding="utf-8").write(src)

    # validate the output with the notebook's own checker
    checker = os.path.join(root, "assets", "check.py")
    if os.path.exists(checker):
        shutil.copy(checker, os.path.join(out, "assets", "check.py"))
        r = subprocess.run([sys.executable, os.path.join(out, "assets", "check.py")], capture_output=True, text=True)
        os.remove(os.path.join(out, "assets", "check.py"))
        if r.returncode:
            sys.exit(r.stdout + r.stderr)

    n = sum(len(fs) for _, _, fs in os.walk(out))
    size = sum(os.path.getsize(os.path.join(d, x)) for d, _, fs in os.walk(out) for x in fs)
    print(f"built {out}  ({n} files, {size // 1024} KB){'  + path.html' if lp else '  (no learning-path.json)'}")


if __name__ == "__main__":
    main()
