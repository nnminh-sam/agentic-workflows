#!/usr/bin/env python3
"""kaneki-design-system: build an interactive design-system gallery (one offline HTML file)
from two hand-written sources that sit beside it:

  <stem>.ds.json          system meta, tokens, contrast pairs, icons, components (with live
                          example markup), screens, principles   -> see references/data-format.md
  <stem>.components.css   the system's component CSS, written only against the tokens

Usage:
  python ds.py init  --out <file.html> --name "<System>" [--prefix ds-] [--lang en|vi] [--force]
  python ds.py check <file.html>                    validate only (exit 1 on errors)
  python ds.py build <file.html> [--strict]         validate, then write <file.html> (and
                                                    <stem>.tokens.css) ; --strict fails on warnings
  python ds.py tokens <file.html>                   print the generated tokens.css

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as htmllib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
SHELL = ASSETS / "shell.html"
SHELL_VERSION = "1.0.0"

THEMED_FAMILIES = ("color", "shadow")
PX_FAMILIES = ("spacing", "radius")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
ICON_RE = re.compile(r"\{\{icon:([a-z0-9-]+)(?:\|([^}]*))?\}\}")
BARE_SELECTOR_RE = re.compile(r"^(html|body|\*|a|button|input|select|textarea|h[1-6]|p|ul|ol|li|table|img|svg|label|div|span|main|header|nav|section)\b", re.I)


# ----------------------------------------------------------------------------- paths
def sources_for(out: Path, data: str | None = None, css: str | None = None) -> tuple[Path, Path]:
    stem = out.with_suffix("")
    return (Path(data) if data else Path(str(stem) + ".ds.json"), Path(css) if css else Path(str(stem) + ".components.css"))


# ----------------------------------------------------------------------------- colour maths
def parse_color(v: str):
    """-> (r, g, b, a) floats 0..1 or None for values we cannot measure (oklch, color-mix, var...)."""
    v = v.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return r, g, b, a
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        r, g, b = (float(m.group(i)) / 255 for i in (1, 2, 3))
        a = m.group(4)
        a = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return r, g, b, a
    if v in ("white", "#fff"):
        return 1.0, 1.0, 1.0, 1.0
    if v == "black":
        return 0.0, 0.0, 0.0, 1.0
    return None


def lum(c) -> float:
    f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def contrast(fg, bg) -> float | None:
    if fg is None or bg is None or bg[3] < 1:
        return None
    if fg[3] < 1:  # composite fg over bg
        a = fg[3]
        fg = tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)
    l1, l2 = sorted((lum(fg), lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# ----------------------------------------------------------------------------- css helpers
def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def css_blocks(css: str):
    """Innermost rule blocks as (selector, body). Good enough for flat or @media-nested CSS."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", strip_comments(css)):
        yield m.group(1).strip(), m.group(2)


def split_selectors(sel: str) -> list[str]:
    """Split a selector list on top-level commas only (not inside :is(), :where(), :not())."""
    parts, depth, cur = [], 0, ""
    for ch in sel:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return [x.strip() for x in parts + [cur] if x.strip()]


def css_classes(css: str) -> set[str]:
    out = set()
    for sel, _ in css_blocks(css):
        if sel.startswith("@"):
            continue
        sel = re.sub(r"url\([^)]*\)", "", sel)
        out.update(re.findall(r"\.(-?[A-Za-z_][\w-]*)", sel))
    return out


def px_of(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*(px|rem|em)?\s*", str(v))
    if not m:
        return None
    n = float(m.group(1))
    return n * 16 if m.group(2) in ("rem", "em") else n


def norm_value(fam: str, v) -> str:
    if isinstance(v, (int, float)) and fam in PX_FAMILIES + ("size",):
        return f"{v:g}px"
    if isinstance(v, (int, float)) and fam == "motion":
        return f"{v:g}ms"
    return str(v)


def css_value(v: str) -> str:
    """'{name}' alias -> var(--name); embedded {name} refs inside a value too."""
    return re.sub(r"\{([A-Za-z0-9_-]+)\}", r"var(--\1)", v)


# ----------------------------------------------------------------------------- validation + model
class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, m):
        self.errors.append(m)

    def warn(self, m):
        self.warnings.append(m)


def load(out: Path, data_p: Path, css_p: Path, rep: Report):
    if not data_p.exists():
        rep.err(f"missing data file {data_p} (run `ds.py init`)")
        return None, ""
    try:
        data = json.loads(data_p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        rep.err(f"{data_p.name}: invalid JSON: {e}")
        return None, ""
    css = css_p.read_text(encoding="utf-8") if css_p.exists() else ""
    if not css_p.exists():
        rep.err(f"missing CSS file {css_p}")
    return data, css


def themed_value(t: dict, theme: str, first: str):
    v = t.get("value")
    if isinstance(v, dict):
        return v.get(theme, v.get(first))
    return v


def validate_and_model(data: dict, css: str, rep: Report) -> dict:
    sysm = dict(data.get("system") or {})
    if not sysm.get("name"):
        rep.err("system.name is required")
    sysm.setdefault("id", re.sub(r"[^a-z0-9]+", "-", str(sysm.get("name", "ds")).lower()).strip("-") or "ds")
    sysm.setdefault("lang", "en")
    if sysm["lang"] not in ("en", "vi"):
        rep.err(f"system.lang must be en or vi (got {sysm['lang']!r})")
    prefix = sysm.get("prefix") or ""
    tok = data.get("tokens") or {}
    themes = tok.get("themes") or [{"id": "light", "name": "Light", "scheme": "light"}]
    for th in themes:
        th.setdefault("name", th.get("id", "").title())
        th.setdefault("scheme", "dark" if "dark" in th.get("id", "") else "light")
        if th["scheme"] not in ("light", "dark"):
            rep.err(f"theme {th.get('id')}: scheme must be light or dark")
    tids = [t["id"] for t in themes]
    first = tids[0]

    # ---- token registry
    defined: dict[str, str] = {}  # name -> family
    families: dict[str, list] = {}
    for fam, lst in tok.items():
        if fam in ("themes", "notes", "other"):
            continue
        if not isinstance(lst, list):
            rep.err(f"tokens.{fam} must be a list")
            continue
        if fam == "type":
            continue
        families[fam] = lst
    for grp in tok.get("other") or []:
        if not grp.get("title") or not isinstance(grp.get("tokens"), list):
            rep.err("tokens.other[] entries need a title and a tokens list")
            continue
        families[f"other:{grp['title']}"] = grp["tokens"]
    for fam, lst in families.items():
        for t in lst:
            n = t.get("name", "")
            if not TOKEN_RE.match(n):
                rep.err(f"tokens.{fam}: bad token name {n!r} (letters, digits, dashes; no leading --)")
                continue
            if n in defined:
                rep.err(f"token --{n} defined twice ({defined[n]} and {fam})")
            defined[n] = fam
            if t.get("value") in (None, ""):
                rep.err(f"token --{n} has no value")
            if not t.get("usage"):
                rep.warn(f"token --{n} has no usage note")
            if fam in THEMED_FAMILIES and isinstance(t.get("value"), dict):
                for th in tids:
                    if th not in t["value"]:
                        rep.warn(f"token --{n} has no value for theme {th!r} (falls back to {first!r})")

    def resolve(name: str, theme: str, depth=0):
        if depth > 10 or name not in defined:
            return None
        fam = defined[name]
        t = next(x for x in families[fam] if x.get("name") == name)
        v = themed_value(t, theme, first) if fam in THEMED_FAMILIES else t.get("value")
        v = str(v)
        m = re.fullmatch(r"\{([A-Za-z0-9_-]+)\}", v.strip())
        return resolve(m.group(1), theme, depth + 1) if m else v

    # alias targets exist
    for fam, lst in families.items():
        for t in lst:
            vals = t.get("value")
            for v in (vals.values() if isinstance(vals, dict) else [vals]):
                for ref in re.findall(r"\{([A-Za-z0-9_-]+)\}", str(v)):
                    if ref not in defined:
                        rep.err(f"token --{t.get('name')} refers to unknown token {{{ref}}}")

    # ---- tokens.css
    def themed_lines(theme):
        lines = []
        for fam in THEMED_FAMILIES:
            for t in families.get(fam, []):
                if "name" in t and t.get("value") not in (None, ""):
                    lines.append(f"  --{t['name']}: {css_value(str(themed_value(t, theme, first)))};")
        return lines

    out = [f"/* {sysm.get('name', '')} tokens: generated by kaneki-design-system from the .ds.json source; do not edit by hand */", ""]
    out += [f':root, [data-theme="{first}"] {{', *themed_lines(first), f"  color-scheme: {themes[0]['scheme']};", "}", ""]
    for th in themes[1:]:
        out += [f'[data-theme="{th["id"]}"] {{', *themed_lines(th["id"]), f"  color-scheme: {th['scheme']};", "}", ""]
    dark = [th for th in themes[1:] if th["scheme"] == "dark"]
    if themes[0]["scheme"] == "light" and dark:
        body = themed_lines(dark[0]["id"])
        out += ["@media (prefers-color-scheme: dark) {", "  :root:not([data-theme]) {", *("  " + l for l in body), "    color-scheme: dark;", "  }", "}", ""]
    root = []
    for fam, lst in families.items():
        if fam in THEMED_FAMILIES:
            continue
        for t in lst:
            if "name" in t and t.get("value") not in (None, ""):
                root.append(f"  --{t['name']}: {css_value(norm_value(fam, t['value']))};")
    if root:
        out += [":root {", *root, "}", ""]
    tokens_css = "\n".join(out)

    # ---- the system css
    if "</style" in css.lower():
        rep.err("components.css contains '</style'")
    flat = strip_comments(css)
    if re.search(r"@import|url\(\s*['\"]?(https?:)?//", flat, re.I):
        rep.err("components.css loads an external resource (@import or remote url()); the gallery must work offline")
    local_vars = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", flat))
    used_vars = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", flat))
    runtime = {"--" + str(v).lstrip("-") for v in (sysm.get("runtimeVars") or [])}
    for v in sorted(used_vars - local_vars - runtime - {"--" + n for n in defined}):
        rep.err(f"components.css uses {v} which is not a token (add it to tokens, define it locally, or list it in system.runtimeVars if the app sets it inline)")
    for v in sorted(runtime - used_vars):
        rep.warn(f"system.runtimeVars lists {v} but components.css never uses it")
    for sel, body in css_blocks(css):
        if sel.startswith("@"):
            continue
        for part in split_selectors(sel):
            if BARE_SELECTOR_RE.match(part) and "." not in part.split()[0]:
                rep.warn(f"components.css: selector `{part}` styles bare elements; it leaks into the gallery chrome. Scope it under a class with the system prefix ({prefix or 'none set'})")
                break
        hexes = re.findall(r"(?<![\w-])(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\))", re.sub(r"var\([^)]*\)", "", body))
        if hexes and not sel.startswith(":root"):
            rep.warn(f"components.css: `{sel[:60]}` hard-codes colour {hexes[0]} (use a token)")
    defined_classes = css_classes(css)

    # ---- tokens used per class (direct var() refs in rules that mention the class)
    class_vars: dict[str, set] = {}
    for sel, body in css_blocks(css):
        vs = {v[2:] for v in re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", body)}
        for cls in set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", sel)):
            class_vars.setdefault(cls, set()).update(vs)

    # ---- icons
    icons = data.get("icons") or {}
    iset = icons.get("set") or {}
    icls = icons.get("className") or (prefix + "icon")
    attrs = icons.get("attrs") or {"fill": "none", "stroke": "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round"}
    vb = icons.get("viewBox") or "0 0 24 24"
    for n in iset:
        if not NAME_RE.match(n):
            rep.err(f"icon name {n!r} must be lowercase with dashes")
    sprite = ""
    if iset:
        at = " ".join(f'{k}="{htmllib.escape(str(v))}"' for k, v in attrs.items())
        sprite = '<svg xmlns="http://www.w3.org/2000/svg"><defs>' + "".join(
            f'<symbol id="i-{n}" viewBox="{vb}" {at}>{p}</symbol>' for n, p in iset.items()) + "</defs></svg>"
        if icls.split()[0] not in defined_classes:
            rep.warn(f"icons: class .{icls} is not styled in components.css (icons will render at the browser default size)")

    def expand(html: str, where: str) -> str:
        def sub(m):
            n, extra = m.group(1), (m.group(2) or "").strip()
            if n not in iset:
                rep.err(f"{where}: unknown icon {{{{icon:{n}}}}}")
            return f'<svg class="{icls.replace("{name}", n)}{" " + extra if extra else ""}" aria-hidden="true"><use href="#i-{n}"/></svg>'
        return ICON_RE.sub(sub, html)

    def check_markup(html: str, where: str):
        low = html.lower()
        if "<script" in low:
            rep.err(f"{where}: <script> is not allowed in example markup (show states with state classes)")
        if re.search(r"\son[a-z]+\s*=", low):
            rep.warn(f"{where}: inline event handler in example markup")
        if re.search(r"(src|href)\s*=\s*['\"]\s*(https?:)?//", low):
            rep.err(f"{where}: example markup loads a remote resource; the gallery must work offline")
        if "{{" in html:
            rep.err(f"{where}: leftover template token {{{{…}}}}")
        if prefix:
            used = set()
            for cl in re.findall(r'class\s*=\s*"([^"]*)"', html):
                used.update(c for c in cl.split() if c.startswith(prefix))
            for c in sorted(used - defined_classes):
                if c not in icls.split() and not c.startswith(icls.split()[0] + "-"):
                    rep.warn(f"{where}: class .{c} is used but not defined in components.css")
        for ref in re.findall(r'href="#i-([a-z0-9-]+)"', html):
            if ref not in iset:
                rep.err(f"{where}: icon #i-{ref} is not in icons.set")

    # ---- components
    comps_out = []
    ids = set()
    for i, c in enumerate(data.get("components") or []):
        where = f"component {c.get('id') or i}"
        for k in ("id", "group", "name", "desc"):
            if not c.get(k):
                rep.err(f"{where}: missing {k}")
        if c.get("id") in ids:
            rep.err(f"{where}: duplicate id")
        ids.add(c.get("id"))
        if c.get("id") and not NAME_RE.match(c["id"]):
            rep.err(f"{where}: id must be lowercase with dashes")
        classes = [k if isinstance(k, dict) else {"name": k} for k in (c.get("classes") or [])]
        if not classes:
            rep.err(f"{where}: classes is empty")
        for k in classes:
            if k.get("name") not in defined_classes:
                rep.warn(f"{where}: listed class .{k.get('name')} is not defined in components.css")
        exs = []
        for j, ex in enumerate(c.get("examples") or []):
            w = f"{where} example {j + 1}"
            if not ex.get("title") or not ex.get("html"):
                rep.err(f"{w}: needs title and html")
                continue
            h = expand(ex["html"], w)
            check_markup(h, w)
            exs.append({**ex, "html": h})
        if not exs:
            rep.err(f"{where}: needs at least one example")
            continue
        if not c.get("dos") and not c.get("donts"):
            rep.warn(f"{where}: no dos/donts")
        used = set()
        for k in classes:
            used |= class_vars.get(k["name"], set())
        comps_out.append({**c, "classes": classes, "examples": exs, "tokensUsed": sorted(u for u in used if u in defined)})

    if not comps_out:
        rep.err("no components")

    screens_out = []
    for i, s in enumerate(data.get("screens") or []):
        where = f"screen {s.get('id') or i}"
        for k in ("id", "name", "html"):
            if not s.get(k):
                rep.err(f"{where}: missing {k}")
        if not s.get("html"):
            continue
        if s.get("id") in ids:
            rep.err(f"{where}: id clashes with a component id")
        h = expand(s["html"], where)
        check_markup(h, where)
        for u in s.get("uses") or []:
            if u not in ids:
                rep.warn(f"{where}: uses unknown component {u!r}")
        screens_out.append({**s, "html": h})

    # ---- contrast
    contrast_out = []
    for p in data.get("contrast") or []:
        fg, bg, mn = p.get("fg"), p.get("bg"), float(p.get("min", 4.5))
        for n in (fg, bg):
            if n not in defined:
                rep.err(f"contrast: unknown token {n!r}")
        if fg not in defined or bg not in defined:
            continue
        for th in themes:
            if p.get("themes") and th["id"] not in p["themes"]:
                continue
            fv, bv = resolve(fg, th["id"]), resolve(bg, th["id"])
            r = contrast(parse_color(fv or ""), parse_color(bv or ""))
            res = "unchecked" if r is None else ("pass" if r + 1e-9 >= mn else "fail")
            if res == "fail" and p.get("knownGap"):
                rep.warn(f"contrast: {fg} on {bg} in {th['id']} is {r:.2f}:1, below {mn:g}:1 (known gap, shown as failing)")
            elif res == "fail":
                rep.err(f"contrast: {fg} on {bg} in {th['id']} is {r:.2f}:1, below {mn:g}:1 (mark the pair knownGap:true to document it instead)")
            elif res == "unchecked":
                rep.warn(f"contrast: {fg} on {bg} in {th['id']} could not be measured ({fv} / {bv}); use hex or rgb()")
            contrast_out.append({"fg": fg, "bg": bg, "min": f"{mn:g}", "note": p.get("note", ""), "theme": th["id"], "themeName": th["name"],
                                 "fgValue": fv, "bgValue": bv, "ratio": round(r, 2) if r else None, "result": res})

    # ---- token model for the page
    tm: dict = {"themes": themes, "notes": tok.get("notes") or {}}
    tm["color"] = [{**t, "alias": (re.fullmatch(r"\{([A-Za-z0-9_-]+)\}", t["value"].strip()).group(1) if isinstance(t.get("value"), str) and re.fullmatch(r"\{([A-Za-z0-9_-]+)\}", t["value"].strip()) else None),
                    "resolved": {th: (resolve(t["name"], th) or "") for th in tids}} for t in tok.get("color") or [] if t.get("name")]
    tm["shadow"] = [t for t in tok.get("shadow") or [] if t.get("name")]
    fonts = [t for t in tok.get("fonts") or [] if t.get("name")]
    tm["fonts"] = [{**t, "css": resolve(t["name"], first) or ""} for t in fonts]
    tstyles = []
    for s in tok.get("type") or []:
        fam = str(s.get("family", "inherit"))
        m = re.fullmatch(r"\{([A-Za-z0-9_-]+)\}", fam.strip())
        if m and m.group(1) not in defined:
            rep.err(f"type {s.get('name')}: unknown font token {fam}")
        for k in ("name", "size", "lineHeight", "weight"):
            if s.get(k) in (None, ""):
                rep.err(f"type style {s.get('name', '?')}: missing {k}")
        tstyles.append({**s, "size": norm_value("size", s.get("size", "")), "lineHeight": str(s.get("lineHeight", "")), "weight": str(s.get("weight", "")),
                        "familyCss": css_value(fam), "familyLabel": (m.group(1) if m else fam)})
    tm["type"] = tstyles
    for fam in ("spacing", "radius", "motion"):
        lst = []
        for t in tok.get(fam) or []:
            if not t.get("name"):
                continue
            v = norm_value(fam, t.get("value"))
            e = {**t, "value": v, "px": px_of(v)}
            if fam == "motion":
                e["kind"] = "duration" if re.fullmatch(r"[\d.]+m?s", v) else ("easing" if re.search(r"cubic-bezier|ease|linear|steps", v) else None)
            lst.append(e)
        tm[fam] = lst
    known = {"themes", "notes", "color", "shadow", "fonts", "type", "spacing", "radius", "motion", "other"}
    other = [{"title": g["title"], "tokens": [{**t, "value": norm_value("size", t.get("value"))} for t in g.get("tokens", [])]} for g in tok.get("other") or [] if g.get("title")]
    for fam in tok:
        if fam not in known and isinstance(tok[fam], list):
            other.append({"title": fam.capitalize(), "tokens": [{**t, "value": norm_value(fam, t.get("value"))} for t in tok[fam]]})
    tm["other"] = other

    # ---- stage vars
    st = sysm.get("stage") or {}
    stage_css = []
    for key, var in (("background", "--dsg-stage-bg"), ("color", "--dsg-stage-fg"), ("font", "--dsg-stage-font"), ("surface", "--dsg-stage-surface")):
        n = st.get(key)
        if n:
            if n not in defined:
                rep.err(f"system.stage.{key}: unknown token {n!r}")
            stage_css.append(f"  {var}: var(--{n});")
    stage_css_text = (".dsg-stage, .dsg-tile__view {\n" + "\n".join(stage_css) + "\n}") if stage_css else ""
    if st.get("rootClass") and st["rootClass"] not in defined_classes:
        rep.warn(f"system.stage.rootClass .{st['rootClass']} is not used in components.css")
    for th in themes:
        if th.get("className") and th["className"] not in defined_classes:
            rep.warn(f"theme {th['id']}: className .{th['className']} is not used in components.css")
    if not st.get("background"):
        rep.warn("system.stage.background not set: stages use a neutral grey, not the system's own page colour")

    for k in ("principles",):
        for p in data.get(k) or []:
            if not p.get("title"):
                rep.err("principles[]: each needs a title")

    tokens_json = json.dumps({"system": {k: sysm.get(k) for k in ("name", "version") if sysm.get(k)}, "tokens": tok, "contrast": data.get("contrast") or []}, ensure_ascii=False, indent=2)
    stem = sysm["id"]
    page = {
        "system": sysm, "principles": data.get("principles") or [], "tokens": tm, "components": comps_out, "screens": screens_out,
        "contrast": contrast_out, "icons": {"className": icls, "names": list(iset)},
        "exports": [{"file": f"{stem}.tokens.css", "text": tokens_css}, {"file": f"{stem}.components.css", "text": css}, {"file": f"{stem}.tokens.json", "text": tokens_json}],
    }
    return {"page": page, "tokens_css": tokens_css, "stage_css": stage_css_text, "sprite": sprite, "css": css}


# ----------------------------------------------------------------------------- commands
def print_report(rep: Report) -> None:
    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    print(f"{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")


def render(model: dict) -> str:
    shell = SHELL.read_text(encoding="utf-8")
    page = model["page"]
    data_json = json.dumps(page, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    fills = {
        "{{SHELL_VERSION}}": SHELL_VERSION,
        "{{LANG}}": page["system"].get("lang", "en"),
        "{{TITLE}}": htmllib.escape(f"{page['system'].get('name', 'Design system')} · Design system"),
        "/*{{TOKENS_CSS}}*/": model["tokens_css"],
        "/*{{COMPONENTS_CSS}}*/": model["css"],
        "/*{{STAGE_CSS}}*/": model["stage_css"],
        "{{ICON_SPRITE}}": model["sprite"],
        "{{DATA_JSON}}": data_json,
    }
    pat = re.compile("|".join(re.escape(k) for k in fills))
    return pat.sub(lambda m: fills[m.group(0)], shell)  # single pass: content can't re-trigger tokens


def cmd_check(a, write: bool) -> int:
    out = Path(a.file).resolve()
    if out.suffix.lower() != ".html":
        print(f"ERROR output must be a .html file (got {out})")
        return 2
    data_p, css_p = sources_for(out, a.data, a.css)
    rep = Report()
    data, css = load(out, data_p, css_p, rep)
    model = validate_and_model(data, css, rep) if data is not None else None
    print_report(rep)
    if rep.errors or (getattr(a, "strict", False) and rep.warnings):
        print("NOT BUILT" if write else "FAILED")
        return 1
    if write and model:
        out.write_text(render(model), encoding="utf-8", newline="\n")
        tcss = Path(str(out.with_suffix("")) + ".tokens.css")
        tcss.write_text(model["tokens_css"], encoding="utf-8", newline="\n")
        p = model["page"]
        print(f"OK built {out}")
        print(f"   {len(p['components'])} components, {len(p['screens'])} screens, {len(p['tokens']['color'])} colour tokens x {len(p['tokens']['themes'])} themes, "
              f"{len(p['icons']['names'])} icons, {len(p['contrast'])} contrast checks; tokens also written to {tcss.name}")
    return 0


def cmd_tokens(a) -> int:
    out = Path(a.file).resolve()
    data_p, css_p = sources_for(out, a.data, a.css)
    rep = Report()
    data, css = load(out, data_p, css_p, rep)
    if data is None:
        print_report(rep)
        return 1
    print(validate_and_model(data, css, rep)["tokens_css"])
    return 0


def cmd_init(a) -> int:
    out = Path(a.out).resolve()
    if out.suffix.lower() != ".html":
        print(f"ERROR --out must be a .html file path (got {a.out!r})")
        return 2
    data_p, css_p = sources_for(out)
    for p in (data_p, css_p):
        if p.exists() and not a.force:
            print(f"ERROR {p} already exists (use --force to overwrite)")
            return 2
    prefix = a.prefix
    if not re.fullmatch(r"[a-z][a-z0-9]*-", prefix):
        print("ERROR --prefix must look like 'ds-' (lowercase, ends with a dash)")
        return 2
    sid = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-") or "ds"
    subs = {"__NAME__": a.name, "__ID__": sid, "__LANG__": a.lang, "__DATE__": dt.date.today().isoformat(), "ds-": prefix}
    for src, dst in ((ASSETS / "starter.ds.json", data_p), (ASSETS / "starter.components.css", css_p)):
        t = src.read_text(encoding="utf-8")
        for k, v in subs.items():
            t = t.replace(k, v)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(t, encoding="utf-8", newline="\n")
        print(f"OK wrote {dst}")
    print(f"Next: replace the starter content with the real system, then `ds.py build \"{out}\"`.")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("init")
    pi.add_argument("--out", required=True)
    pi.add_argument("--name", required=True)
    pi.add_argument("--prefix", default="ds-")
    pi.add_argument("--lang", default="en", choices=["en", "vi"])
    pi.add_argument("--force", action="store_true")
    for name in ("check", "build", "tokens"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--data")
        p.add_argument("--css")
        if name == "build":
            p.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    if a.cmd == "init":
        return cmd_init(a)
    if a.cmd == "tokens":
        return cmd_tokens(a)
    return cmd_check(a, write=(a.cmd == "build"))


if __name__ == "__main__":
    sys.exit(main())
