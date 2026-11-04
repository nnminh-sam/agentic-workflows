#!/usr/bin/env python3
"""Scaffold a haise doc from the shared template.

Fills the language, title, date, date label and legend footer. The outline is
not fixed: content tokens ({{EYEBROW}}, {{SUBTITLE}}, {{SECTIONS}}) are left in
place on purpose, and check_doc.py fails until every one of them is replaced
with real content.

Usage:
  python new_doc.py --out <file.html> --title "<title>" [--lang en|vi] [--date YYYY-MM-DD] [--force]
"""
import argparse
import datetime as dt
import html
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "template.html"

LABELS = {
    "en": {
        "L_DATE": "Updated",
        "FOOTER": ('Legend: <span class="tag sure">VERIFIED</span> seen directly in the cited source · '
                   '<span class="tag infer">INFERRED</span> reasoned from evidence, not confirmed end-to-end · '
                   '<span class="tag open">OPEN</span> unresolved, needs an answer. '
                   'Translated keywords are <strong>bold</strong> with the original term in (parentheses). '
                   'Press <code>/</code> to search; the ⟳ button reloads the latest version of this file.'),
    },
    "vi": {
        "L_DATE": "Cập nhật",
        "FOOTER": ('Ký hiệu: <span class="tag sure">CHẮC CHẮN</span> đã thấy trực tiếp trong nguồn được dẫn · '
                   '<span class="tag infer">SUY RA</span> suy luận từ bằng chứng, chưa kiểm chứng trọn vẹn · '
                   '<span class="tag open">OPEN</span> chưa có lời giải, cần trả lời. '
                   'Thuật ngữ đã dịch được <strong>in đậm</strong>, kèm thuật ngữ gốc trong (ngoặc). '
                   'Nhấn <code>/</code> để tìm kiếm; nút ⟳ tải lại bản mới nhất của file.'),
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--lang", default="en", choices=sorted(LABELS))
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    a = ap.parse_args()

    if not a.out.strip() or not a.out.lower().endswith(".html"):
        print(f"ERROR: --out must be a .html file path (got {a.out!r})", file=sys.stderr)
        return 2
    out = pathlib.Path(a.out)
    if out.exists() and not a.force:
        print(f"ERROR: {out} already exists (use --force to overwrite)", file=sys.stderr)
        return 2

    text = TEMPLATE.read_text(encoding="utf-8")
    fills = dict(LABELS[a.lang])
    fills.update({"LANG": a.lang, "TITLE": html.escape(a.title), "DATE": a.date})
    for k, v in fills.items():
        text = text.replace("{{" + k + "}}", v)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"OK scaffolded {out} (lang={a.lang})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
