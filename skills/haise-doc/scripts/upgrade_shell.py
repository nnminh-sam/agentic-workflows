#!/usr/bin/env python3
"""Move an existing haise doc onto the current shell (assets/template.html).

Replaces only the shell blocks (#haise-shell-css, #haise-shell-js), the
data-haise-version attribute and the topbar/sidebar markup between <body> and
<main>. The document content between the DOCUMENT CONTENT markers, the title
and the language are kept as they are.

Use it only when the user asks for an existing doc to get a newer shell
feature; docs otherwise keep the shell they were built with.

Usage: python upgrade_shell.py <doc.html>
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "template.html"


def block(text, kind, ident):
    return re.search(rf'<{kind} id="{ident}">.*?</{kind}>', text, re.S)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    if 'data-haise="doc"' not in text:
        print("ERROR: not a haise doc (no data-haise marker)", file=sys.stderr)
        return 1

    for kind, ident in (("style", "haise-shell-css"), ("script", "haise-shell-js")):
        mine, ref = block(text, kind, ident), block(tpl, kind, ident)
        if not mine or not ref:
            print(f"ERROR: shell block #{ident} missing", file=sys.stderr)
            return 1
        text = text[:mine.start()] + ref.group(0) + text[mine.end():]

    old = re.search(r'data-haise-version="([^"]*)"', text)
    new = re.search(r'data-haise-version="([^"]*)"', tpl).group(1)
    text = re.sub(r'data-haise-version="[^"]*"', f'data-haise-version="{new}"', text, count=1)

    # chrome between <body> and <main> (topbar, sidebar); keep this doc's title text
    title = re.search(r'<div class="doc-title">(.*?)</div>', text, re.S)
    chrome_re = re.compile(r"<body>.*?<main ", re.S)
    chrome = chrome_re.search(tpl).group(0)
    if title:
        chrome = chrome.replace("{{TITLE}}", title.group(1))
    if "{{" not in chrome:
        text = chrome_re.sub(lambda _: chrome, text, count=1)

    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"OK {path.name}: shell {old.group(1) if old else '?'} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
