#!/usr/bin/env python3
"""
Print a compact map of a notebook so the diagnose skill never has to read 1 MB of HTML.

    catalog.py NOTEBOOK                      # chapters, sections, practices, projects, question counts
    catalog.py NOTEBOOK --chapter 08-data.html   # ...plus every question in that chapter (id, tier, text)
    catalog.py NOTEBOOK --q dt-04 nr-02      # full question + "Check yourself" text, for grading a probe
    catalog.py NOTEBOOK --answers [FILE]     # latest grade + fuzzy line per answered question
                                             # (FILE defaults to NOTEBOOK/answers/state.json; a
                                             #  "Backup (.json)" export from the index page also works)

A notebook is any folder whose assets/app.js has a PAGES list (node-path, java-path, frontend-path).
"""
import html
import json
import os
import re
import sys


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def pages(root):
    js = open(os.path.join(root, "assets", "app.js"), encoding="utf-8").read()
    # title is "..." in node-path/java-path, '...' in frontend-path
    return [{"file": f, "num": n, "title": t1 or t2, "group": g} for f, n, t1, t2, g in
            re.findall(r"file:\s*'([^']+)',\s*num:\s*'([^']+)',\s*title:\s*(?:\"([^\"]+)\"|'([^']+)'),"
                       r"\s*group:\s*'([^']+)'", js)]


Q_RE = re.compile(r'<div class="q" data-q="([^"]+)" data-tier="([^"]+)">(.*?)'
                  r'<details class="q-check"><summary>[^<]*</summary><div class="inner">(.*?)</div></details>', re.S)


def questions(src):
    for qid, tier, head, check in Q_RE.findall(src):
        m = re.search(r'<p class="q-text">(.*?)</p>', head, re.S)
        yield qid, tier, text(m.group(1) if m else head), text(check)


def blocks(src, cls):
    return [(text(t), text(h)) for t, h in
            re.findall(r'<div class="%s">\s*<span class="t">(.*?)</span>\s*<h3>(.*?)</h3>' % cls, src, re.S)]


def outline(root, chapter=None):
    for p in pages(root):
        path = os.path.join(root, p["file"])
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        qs = list(questions(src))
        tiers = {t: sum(1 for q in qs if q[1] == t) for t in ("recall", "predict", "judge")}
        print(f"\n## {p['file']}  [{p['group']}] {text(p['title'])}  "
              f"— {len(qs)} q (r{tiers['recall']} p{tiers['predict']} j{tiers['judge']})")
        skip = {"Practices", "Questions", "The gate", "The smells"}
        secs = [text(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", src, re.S)]
        print("  sections: " + " | ".join(s for s in secs if s not in skip and not s.startswith("Project")))
        for t, h in blocks(src, "practice") + blocks(src, "project"):
            print(f"  - {t}: {h}")
        if chapter == p["file"]:
            for qid, tier, q, _ in qs:
                print(f"  {qid:8} {tier:7} {q[:160]}")


def dump(root, qids):
    want = set(qids)
    for p in pages(root):
        path = os.path.join(root, p["file"])
        if not os.path.exists(path):
            continue
        for qid, tier, q, check in questions(open(path, encoding="utf-8").read()):
            if qid in want:
                print(f"### {qid} ({tier}, {p['file']})\nQ: {q}\nCHECK: {check}\n")
                want.discard(qid)
    for qid in want:
        print(f"### {qid}: not found")


def answers(root, file=None):
    file = file or os.path.join(root, "answers", "state.json")
    if not os.path.exists(file):
        print(f"no answers at {file} (answers may only be in browser localStorage — "
              "ask the learner for a 'Backup (.json)' export)")
        return
    data = json.load(open(file, encoding="utf-8"))
    ans = data.get("answers") or {}
    if not ans:
        print("answers file is empty")
        return
    for qid in sorted(ans):
        a = ans[qid][-1]
        print(f"{qid:8} {a.get('grade', '?')}  x{len(ans[qid])}  {a.get('at', '')[:10]}  "
              f"fuzzy: {(a.get('fuzzy') or '-')[:120]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    root = os.path.abspath(args.pop(0))
    if "--q" in args:
        dump(root, args[args.index("--q") + 1:])
    elif "--answers" in args:
        rest = args[args.index("--answers") + 1:]
        answers(root, rest[0] if rest else None)
    else:
        outline(root, args[args.index("--chapter") + 1] if "--chapter" in args else None)
