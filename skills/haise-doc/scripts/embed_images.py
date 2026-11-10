#!/usr/bin/env python3
"""Embed local images into a haise doc as data URIs, at full resolution.

Write image tags in the doc with a placeholder source:
    <img alt="Dashboard" src="@@shots/dashboard.png@@">
then run this script. Every @@path@@ (resolved against --base, default: the
doc's folder) is replaced by a data: URI of the file's bytes, untouched (no
resize, no recompression), so the shell's zoom viewer can show it sharp.

Images narrower than --min-width (default 1200 px) are reported: the zoom
viewer enlarges them, but they will look soft. Fetch a larger original.

Usage:
  python embed_images.py <doc.html> [--base <dir>] [--min-width 1200]
Exit 0 = all placeholders embedded, 1 = missing files, 2 = bad usage.
"""
import argparse
import base64
import pathlib
import re
import struct
import sys

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml", ".avif": "image/avif"}
TOKEN = re.compile(r"@@([^@\s\"'<>]+)@@")


def image_size(data: bytes):
    """(width, height) for PNG / JPEG / WebP / GIF bytes, else None. Stdlib only."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return (w & 0x3FFF, h & 0x3FFF)
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return (w, h)
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


def data_uri_size(uri: str):
    """Decode just enough of a base64 data URI to read the image size."""
    m = re.match(r"data:image/[\w.+-]+;base64,", uri)
    if not m:
        return None
    head = uri[m.end():m.end() + 87384]  # ~64 KB decoded: enough for JPEG headers with EXIF
    head = head[:len(head) // 4 * 4]
    try:
        return image_size(base64.b64decode(head))
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--base", help="folder the @@paths@@ are relative to (default: the doc's folder)")
    ap.add_argument("--min-width", type=int, default=1200)
    a = ap.parse_args()

    doc = pathlib.Path(a.doc)
    base = pathlib.Path(a.base) if a.base else doc.parent
    text = doc.read_text(encoding="utf-8")
    missing, done, small = [], 0, []

    def rep(m):
        nonlocal done
        p = (base / m.group(1)).resolve()
        mime = MIME.get(p.suffix.lower())
        if not p.is_file() or not mime:
            missing.append(m.group(1))
            return m.group(0)
        raw = p.read_bytes()
        size = image_size(raw)
        if size and size[0] < a.min_width:
            small.append(f"{m.group(1)} is {size[0]}x{size[1]} px (< {a.min_width} px wide: soft when zoomed)")
        done += 1
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"

    text = TOKEN.sub(rep, text)
    doc.write_text(text, encoding="utf-8", newline="\n")
    for s in small:
        print("WARN   " + s)
    for m in missing:
        print("ERROR  not found or unsupported type: " + m)
    print(f"embedded {done} image(s); {doc.name} is now {doc.stat().st_size / 1048576:.1f} MB")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
