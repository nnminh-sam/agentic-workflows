"""Generate tokens.css and tokens.js from tokens.json (the single source of every design value).

Usage: python build_tokens.py [--check | --contrast]
  --check     exit 1 if tokens.css is out of date instead of writing it (for CI / pre-commit).
  --contrast  print the WCAG 2 ratio of every pair listed under "contrast" in tokens.json, per theme;
              exit 1 if any pair is below its minimum.

Stdlib only. Output layout:
  :root, [data-theme="light"]          colour + shadow tokens of the first theme
  [data-theme="dark"]                  the dark values (explicit choice)
  @media (prefers-color-scheme: dark)  the dark values again when no theme is chosen (follow the OS)
  :root                                spacing, radius, size and font-family tokens
  .type-<style>                        one class per type style
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "tokens.json"
OUT = HERE / "tokens.css"
OUT_JS = HERE / "tokens.js"  # the same tokens as window.KanekiTokens, for pages opened from file:// (no fetch)
JS_HEAD = "/* generated from tokens.json by build_tokens.py; do not edit by hand */\nwindow.KanekiTokens = "


def themed(families: list[dict], theme: str, first: str) -> list[str]:
    lines = []
    for fam in families:
        for t in fam:
            v = t["value"]
            val = v if isinstance(v, str) else v.get(theme, v.get(first))
            if isinstance(val, str) and val.startswith("{") and val.endswith("}"):
                val = f"var(--{val[1:-1]})"
            lines.append(f"  --{t['name']}: {val};")
    return lines


def px(v) -> str:
    return f"{v}px" if isinstance(v, (int, float)) else str(v)


def build(tok: dict) -> str:
    themes = [t["id"] for t in tok["color"]["themes"]]
    first = themes[0]
    themed_fams = [tok["color"]["tokens"], tok.get("shadow", {}).get("tokens", [])]
    out = [f"/* {tok['name']} — generated from tokens.json by build_tokens.py; do not edit by hand */", ""]
    out += [f':root, [data-theme="{first}"] {{', *themed(themed_fams, first, first), "}", ""]
    for th in themes[1:]:
        body = themed(themed_fams, th, first)
        scheme = ["  color-scheme: dark;"] if th == "dark" else []
        out += [f'[data-theme="{th}"] {{', *body, *scheme, "}", ""]
        if th == "dark":
            out += ["@media (prefers-color-scheme: dark) {", f'  :root:not([data-theme="{first}"]) {{',
                    *("  " + l for l in body + scheme), "  }", "}", ""]
    out.append(":root {")
    for key in tok:
        fam = tok[key]
        if key in ("name", "version", "meta", "color", "type", "shadow", "contrast") or not isinstance(fam, dict):
            continue
        out += [f"  --{t['name']}: {px(t['value'])};" for t in fam.get("tokens", [])]
    out += [f"  --font-{k}: {v};" for k, v in tok["type"]["families"].items()]
    out += ["}", ""]
    for group in tok["type"]["groups"]:
        for s in group["styles"]:
            fam = s.get("family", group["family"])
            decl = [f"font-family: var(--font-{fam})", f"font-size: {px(s['fontSize'])}",
                    f"line-height: {s['lineHeight']}", f"font-weight: {s['fontWeight']}"]
            if "letterSpacing" in s:
                decl.append(f"letter-spacing: {px(s['letterSpacing'])}")
            out.append(f".type-{s['name']} {{ " + "; ".join(decl) + "; }")
    return "\n".join(out) + "\n"


def _lum(hexv: str) -> float:
    h = hexv.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_report(tok: dict) -> list[str]:
    """WCAG 2 ratio of every pair in tok['contrast']['pairs'], per theme. Returns the failures."""
    colors = {t["name"]: t["value"] for t in tok["color"]["tokens"]}
    themes = [t["id"] for t in tok["color"]["themes"]]

    def resolve(name: str, theme: str) -> str:
        v = colors[name]
        v = v if isinstance(v, str) else v.get(theme, v.get(themes[0]))
        return resolve(v[1:-1], theme) if v.startswith("{") else v

    fails = []
    for fg, bg, minimum in tok.get("contrast", {}).get("pairs", []):
        for th in themes:
            a, b = _lum(resolve(fg, th)), _lum(resolve(bg, th))
            ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            line = f"{th:5} {fg:>13} on {bg:<13} {ratio:5.2f} (min {minimum})"
            print(("  ok  " if ratio >= minimum else "  FAIL ") + line)
            if ratio < minimum:
                fails.append(line)
    return fails


def main() -> int:
    tok = json.loads(SRC.read_text(encoding="utf-8"))
    if "--contrast" in sys.argv:
        fails = contrast_report(tok)
        print(f"{len(fails)} pair(s) below their minimum")
        return 1 if fails else 0
    outputs = {OUT: build(tok), OUT_JS: JS_HEAD + json.dumps(tok, ensure_ascii=False, indent=1) + ";\n"}
    if "--check" in sys.argv:
        stale = [p.name for p, text in outputs.items() if (p.read_text(encoding="utf-8") if p.exists() else "") != text]
        if stale:
            print(f"out of date: {', '.join(stale)}: run python build_tokens.py", file=sys.stderr)
            return 1
        print("tokens.css and tokens.js are up to date")
        return 0
    for p, text in outputs.items():
        p.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
