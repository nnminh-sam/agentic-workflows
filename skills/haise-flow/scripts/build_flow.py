#!/usr/bin/env python3
"""haise-flow: build an archify-style flow / architecture page from a JSON spec.

usage:
  build_flow.py --spec <file.flow.json> --out <file.html> [--force] [--lang en|vi]
  build_flow.py --spec <file.flow.json> --check        # validate + layout lint only

The page shell (CSS, toolbar, theme/style switch, present mode, export, node
passport, search, guided views runtime) is assets/shell.html and is never edited
per page. This script fills only the title, the diagram SVG and the info cards.
Python 3.8+ standard library only. Exit codes: 0 ok (warnings allowed), 1 spec
errors, 2 usage / IO error.
"""
import argparse
import html
import json
import sys
import unicodedata
from pathlib import Path

SHELL_VERSION = "haise-flow 1.0.0 / archify-shell 2.17.0-dev.1"
SKILL_DIR = Path(__file__).resolve().parent.parent
SHELL = SKILL_DIR / "assets" / "shell.html"

KINDS = ("frontend", "backend", "security", "cloud", "external", "database", "messagebus")
VARIANTS = ("default", "emphasis", "dashed", "security")
DOTS = ("cyan", "amber", "emerald", "violet", "rose", "orange", "slate")
ROUTES = ("auto", "straight", "under", "over", "gutter", "direct", "side")

SIGILS = {
    "backend": '<path d="M6 3 3 8l3 5M10 3l3 5-3 5"/>',
    "security": '<path d="M8 2.2 13 4v3.5c0 3.1-1.8 5.4-5 6.5-3.2-1.1-5-3.4-5-6.5V4Z"/>\n            <path d="m5.8 8 1.5 1.5 3-3"/>',
    "frontend": '<rect x="2" y="3" width="12" height="10" rx="2"/>\n            <path d="M2 6.5h12"/>\n            <circle cx="4.1" cy="4.8" r=".7" class="sigil-fill"/>\n            <circle cx="6.3" cy="4.8" r=".7" class="sigil-fill"/>',
    "cloud": '<path d="M4.3 12.5h7.3a2.4 2.4 0 0 0 .2-4.8 4 4 0 0 0-7.5-1.3A3.1 3.1 0 0 0 4.3 12.5Z"/>',
    "external": '<path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5"/>\n            <path d="M12 9.5v4H2.5V4h4"/>',
    "database": '<ellipse cx="8" cy="4" rx="5" ry="1.8"/>\n            <path d="M3 4v8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V4"/>\n            <path d="M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8"/>',
    "messagebus": '<path d="M2 5.5h12M2 10.5h12"/>\n            <circle cx="5" cy="5.5" r="1.2" class="sigil-fill"/>\n            <circle cx="11" cy="10.5" r="1.2" class="sigil-fill"/>',
}
VARIANT_STYLE = {
    "default": ("a-default", "1.4", "arrowhead", ""),
    "emphasis": ("a-emphasis", "1.8", "arrowhead-emphasis", ""),
    "dashed": ("a-dashed", "1.4", "arrowhead-dashed", ' stroke-dasharray="5 4"'),
    "security": ("a-security", "1.6", "arrowhead-security", ""),
}
LEGEND_DEFAULT = {
    "en": {"frontend": "User / approval", "backend": "Worker / service", "security": "Review / policy",
           "cloud": "Handoff / external service", "external": "External system", "database": "Data store",
           "messagebus": "Queue / bus", "optional": "Only when condition applies",
           "emphasis": "Main path / gate passed", "dashed": "Loop / retry", "security": "Policy-controlled",
           "legend": "Legend"},
    "vi": {"frontend": "Người dùng / duyệt", "backend": "Worker / xử lý", "security": "Review / chính sách",
           "cloud": "Bàn giao / dịch vụ ngoài", "external": "Hệ thống ngoài", "database": "Lưu trữ dữ liệu",
           "messagebus": "Hàng đợi / bus", "optional": "Chỉ khi có điều kiện",
           "emphasis": "Đường chính / gate qua", "dashed": "Vòng lặp khi chưa đạt", "security": "Có kiểm soát chính sách",
           "legend": "Chú thích"},
}

esc = html.escape


def fmt(v):
    return ("%.1f" % v).rstrip("0").rstrip(".")


def text_units(s):
    """Width of a string in 'monospace cells' (the classic preset renders in a mono face)."""
    n = 0.0
    for ch in s:
        n += 2.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 1.0
    return n


CHAR = 0.6  # mono advance per font-size unit


class Spec:
    def __init__(self, data, lang_override=None):
        self.errors, self.warnings = [], []
        self.d = data
        self.title = data.get("title") or ""
        self.description = data.get("description") or ""
        self.lang = lang_override or data.get("lang") or "en"
        if self.lang not in LEGEND_DEFAULT:
            self.errors.append(f"lang must be en or vi, got {self.lang!r}")
            self.lang = "en"
        if not self.title:
            self.errors.append("title is required")
        lay = data.get("layout") or {}
        self.NW = float(lay.get("node_w", 124))
        self.NH = float(lay.get("node_h", 56))
        self.PITCH = float(lay.get("col_pitch", 180))
        self.ROWP = float(lay.get("row_pitch", self.NH + 22))
        self.X0 = float(lay.get("x0", 56))
        self.GAP = self.PITCH - self.NW
        if self.GAP < 40:
            self.errors.append("layout.col_pitch must be at least node_w + 40")
        self.lanes = data.get("lanes") or []
        self.nodes = data.get("nodes") or []
        self.edges = data.get("edges") or []
        self.cards = data.get("cards") or []
        self._validate()

    # ---------------------------------------------------------------- validation
    def _validate(self):
        lane_ids = [l.get("id") for l in self.lanes]
        if not self.lanes:
            self.errors.append("at least one lane is required")
        if len(set(lane_ids)) != len(lane_ids):
            self.errors.append("lane ids must be unique")
        self.lane = {l["id"]: l for l in self.lanes if l.get("id")}
        for l in self.lanes:
            l.setdefault("rows", 1)
            if not l.get("label"):
                self.errors.append(f"lane {l.get('id')!r}: label is required")
        ids = [n.get("id") for n in self.nodes]
        if len(set(ids)) != len(ids):
            self.errors.append("node ids must be unique")
        self.node = {}
        slots = {}
        for n in self.nodes:
            nid = n.get("id")
            if not nid or not str(nid).replace("_", "").replace("-", "").isalnum():
                self.errors.append(f"node id {nid!r}: use letters, digits, - or _")
                continue
            if n.get("lane") not in self.lane:
                self.errors.append(f"node {nid}: unknown lane {n.get('lane')!r}")
                continue
            n.setdefault("row", 0)
            n.setdefault("kind", "backend")
            if n["kind"] not in KINDS:
                self.errors.append(f"node {nid}: kind must be one of {', '.join(KINDS)}")
            if not isinstance(n.get("col"), int) or n["col"] < 0:
                self.errors.append(f"node {nid}: col must be an integer >= 0")
                continue
            if not isinstance(n["row"], (int, float)) or not (0 <= n["row"] <= self.lane[n["lane"]]["rows"] - 1):
                self.errors.append(f"node {nid}: row {n['row']} outside lane {n['lane']} rows={self.lane[n['lane']]['rows']}")
                continue
            if not n.get("label"):
                self.errors.append(f"node {nid}: label is required")
            key = (n["lane"], n["row"], n["col"])
            if key in slots:
                self.errors.append(f"node {nid}: same lane/row/col as {slots[key]}")
            slots[key] = nid
            self.node[nid] = n
        for i, e in enumerate(self.edges):
            for end in ("from", "to"):
                if e.get(end) not in self.node:
                    self.errors.append(f"edge #{i}: unknown {end} node {e.get(end)!r}")
            e.setdefault("variant", "default")
            e.setdefault("route", "auto")
            e.setdefault("label", "")
            if e["variant"] not in VARIANTS:
                self.errors.append(f"edge #{i}: variant must be one of {', '.join(VARIANTS)}")
            if e["route"] not in ROUTES:
                self.errors.append(f"edge #{i}: route must be one of {', '.join(ROUTES)}")
        for c in self.cards:
            if c.get("dot", "slate") not in DOTS:
                self.errors.append(f"card {c.get('title')!r}: dot must be one of {', '.join(DOTS)}")
        if not self.cards:
            self.warnings.append("no cards: the page reads better with 2-4 cards explaining each lane")
        if len(self.cards) > 4:
            self.warnings.append("more than 4 cards: the card row wraps; consider merging")

    # ---------------------------------------------------------------- geometry
    def build_geometry(self):
        self.cols = max([n["col"] for n in self.node.values()] + [0]) + 1
        y = 52.0
        self.lane_y, self.lane_h = {}, {}
        for l in self.lanes:
            h = 50 + (l["rows"] - 1) * self.ROWP + self.NH + 28
            self.lane_y[l["id"]], self.lane_h[l["id"]] = y, h
            y += h + 20
        self.content_bottom = y - 20
        self.lane_x = 40.0
        self.lane_right = self.X0 + (self.cols - 1) * self.PITCH + self.NW + 16
        self.lane_w = self.lane_right - self.lane_x
        self.GX = 32.0
        self.RX = self.lane_right + 14
        self.view_w = self.RX + 26
        for n in self.node.values():
            x = self.X0 + n["col"] * self.PITCH
            yy = self.lane_y[n["lane"]] + 50 + n["row"] * self.ROWP
            n["_box"] = (x, yy, x + self.NW, yy + self.NH)
        self.lane_index = {l["id"]: i for i, l in enumerate(self.lanes)}

    def box(self, nid):
        return self.node[nid]["_box"]

    def anchor(self, nid, side, dy=0.0):
        x0, y0, x1, y1 = self.box(nid)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return {"right": (x1, cy + dy), "left": (x0, cy + dy), "top": (cx, y0), "bottom": (cx, y1)}[side]

    # ---------------------------------------------------------------- routing
    def collides(self, pts, skip):
        hits = []
        for nid, n in self.node.items():
            if nid in skip:
                continue
            x0, y0, x1, y1 = n["_box"]
            x0, y0, x1, y1 = x0 - 2, y0 - 2, x1 + 2, y1 + 2
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                if ax == bx:  # vertical
                    lo, hi = sorted((ay, by))
                    if x0 < ax < x1 and lo < y1 and hi > y0:
                        hits.append(nid)
                        break
                else:  # horizontal
                    lo, hi = sorted((ax, bx))
                    if y0 < ay < y1 and lo < x1 and hi > x0:
                        hits.append(nid)
                        break
        return hits

    def candidates(self, e, k):
        s, t = e["from"], e["to"]
        S, T = self.node[s], self.node[t]
        sx0, sy0, sx1, sy1 = S["_box"]
        tx0, ty0, tx1, ty1 = T["_box"]
        scx, tcx = (sx0 + sx1) / 2, (tx0 + tx1) / 2
        scy, tcy = (sy0 + sy1) / 2, (ty0 + ty1) / 2
        out = []
        si, ti = self.lane_index[S["lane"]], self.lane_index[T["lane"]]
        fan = e.get("_fan", 0.0)
        if si == ti and S["row"] == T["row"]:
            over_y = sy0 - 12 - e.get("_lift", 0)
            under_y = sy1 + 14 + e.get("_lift", 0)
            if T["col"] > S["col"]:
                out.append(("straight", [(sx1, scy), (tx0, tcy)]))
            elif T["col"] < S["col"]:
                pass
            out.append(("under", [(scx, sy1), (scx, under_y), (tcx, under_y), (tcx, ty1)]))
            out.append(("over", [(scx, sy0), (scx, over_y), (tcx, over_y), (tcx, ty0)]))
            if T["col"] < S["col"]:
                out = [o for o in out if o[0] != "straight"]
                out.sort(key=lambda o: 0 if o[0] == "over" else 1)
        elif si == ti:
            if T["col"] == S["col"]:
                if T["row"] > S["row"]:
                    out.append(("straight", [(scx, sy1), (tcx, ty0)]))
                else:
                    out.append(("straight", [(scx, sy0), (tcx, ty1)]))
            elif T["col"] > S["col"]:
                xa = sx1 + self.GAP / 2
                xb = tx0 - self.GAP / 2
                out.append(("side", [(sx1, scy + fan), (xa, scy + fan), (xa, tcy), (tx0, tcy)]))
                if xb != xa:
                    out.append(("side", [(sx1, scy + fan), (xb, scy + fan), (xb, tcy), (tx0, tcy)]))
                vy = sy1 if T["row"] > S["row"] else sy0
                out.append(("direct", [(scx, vy), (scx, tcy), (tx0, tcy)]))
            else:
                xa = sx0 - self.GAP / 2
                out.append(("side", [(sx0, scy + fan), (xa, scy + fan), (xa, tcy), (tx1, tcy)]))
        elif ti > si:
            gy = self.lane_y[T["lane"]] - 10 + e.get("_gap_off", 0.0)
            gutter = [(scx, sy1), (scx, gy), (self.GX, gy), (self.GX, tcy), (tx0, tcy)]
            direct = [(scx, sy1), (scx, gy), (tcx, gy), (tcx, ty0)]
            xr = sx1 + self.GAP / 2
            side_g = [(sx1, scy), (xr, scy), (xr, gy), (self.GX, gy), (self.GX, tcy), (tx0, tcy)]
            side_d = [(sx1, scy), (xr, scy), (xr, gy), (tcx, gy), (tcx, ty0)]
            if T["col"] == S["col"] and T["col"] != 0:  # col 0 would cut through the lane title
                out.append(("straight", [(scx, sy1), (tcx, ty0)]))
            if T["col"] == 0:
                out += [("gutter", gutter), ("direct", direct), ("side", side_g), ("side", side_d)]
            else:
                out += [("direct", direct), ("gutter", gutter), ("side", side_d), ("side", side_g)]
        else:  # back to an earlier lane: right-hand gutter
            rx = self.RX - e.get("_lift", 0)
            out.append(("side", [(sx1, scy), (rx, scy), (rx, tcy), (tx1, tcy)]))
        # collapse zero-length segments
        clean = []
        for name, pts in out:
            p2 = [pts[0]]
            for p in pts[1:]:
                if p != p2[-1]:
                    p2.append(p)
            clean.append((name, p2))
        return clean

    def route_all(self):
        # spread edges that share a lane gap or fan out from one node
        gap_count, fan_count, loop_count = {}, {}, {}
        for e in self.edges:
            S, T = self.node[e["from"]], self.node[e["to"]]
            si, ti = self.lane_index[S["lane"]], self.lane_index[T["lane"]]
            if ti > si:
                c = gap_count.get(T["lane"], 0)
                gap_count[T["lane"]] = c + 1
                e["_gap_off"] = [0, -6, 6, -12, 12][c % 5]
            elif si == ti and S["row"] != T["row"] and T["col"] != S["col"]:
                c = fan_count.get(e["from"], 0)
                fan_count[e["from"]] = c + 1
            elif ti < si:
                c = loop_count.get("back", 0)
                loop_count["back"] = c + 1
                e["_lift"] = 8 * c
        # vertical fan offsets for nodes with several side exits
        seen = {}
        for e in self.edges:
            n = fan_count.get(e["from"], 0)
            S, T = self.node[e["from"]], self.node[e["to"]]
            if False and n > 1:  # superseded by spread_ports()
                i = seen.get(e["from"], 0)
                seen[e["from"]] = i + 1
                e["_fan"] = (i - (n - 1) / 2) * 16
        for k, e in enumerate(self.edges):
            cands = self.candidates(e, k)
            want = e["route"]
            if want != "auto":
                pref = [c for c in cands if c[0] == want]
                if not pref:
                    self.warnings.append(f"edge {e['from']}->{e['to']}: route {want!r} not possible here, using auto")
                cands = pref + [c for c in cands if c[0] != want]
            chosen, hits = None, None
            for name, pts in cands:
                h = self.collides(pts, {e["from"], e["to"]})
                if not h:
                    chosen = (name, pts)
                    break
                if hits is None:
                    hits, first = h, (name, pts)
            if chosen is None:
                chosen = first
                self.warnings.append(f"edge {e['from']}->{e['to']}: every route crosses node(s) {', '.join(sorted(set(hits)))}; move a node or add a column/row")
            if want != "auto" and chosen[0] != want and any(c[0] == want for c in cands):
                blocked = self.collides(next(c[1] for c in cands if c[0] == want), {e["from"], e["to"]})
                self.warnings.append(f"edge {e['from']}->{e['to']}: route {want!r} crosses node(s) {', '.join(sorted(set(blocked)))}; used {chosen[0]!r} instead")
            e["_route"], e["_pts"] = chosen
        # loops sharing the band above the same row get stacked
        bands = {}
        for e in self.edges:
            if e["_route"] in ("over", "under"):
                S = self.node[e["from"]]
                key = (S["lane"], S["row"], e["_route"])
                xs = sorted((e["_pts"][0][0], e["_pts"][-1][0]))
                lane_es = bands.setdefault(key, [])
                lift = 0
                for (a, b, l) in lane_es:
                    if xs[0] < b and xs[1] > a:
                        lift = max(lift, l + 8)
                lane_es.append((xs[0], xs[1], lift))
                if lift:
                    e["_lift"] = lift
                    for name, pts in self.candidates(e, 0):
                        if name == e["_route"]:
                            e["_pts"] = pts
        self.spread_ports()
        for _ in range(4):
            if not self.lint_collinear(repair=True):
                break
        self.lint_collinear()

    def lint_collinear(self, repair=False):
        """Warn when two different edges run on (almost) the same line: readers then
        cannot tell which node connects to which."""
        segs = []
        for e in self.edges:
            pts = e["_pts"]
            for k, ((ax, ay), (bx, by)) in enumerate(zip(pts, pts[1:])):
                if ax == bx:
                    segs.append((e, "v", ax, min(ay, by), max(ay, by), k))
                elif ay == by:
                    segs.append((e, "h", ay, min(ax, bx), max(ax, bx), k))
        reported = set()
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                e1, o1, c1, a1, b1, _k1 = segs[i]
                e2, o2, c2, a2, b2, k2 = segs[j]
                if e1 is e2 or o1 != o2 or abs(c1 - c2) > 3:
                    continue
                if e1["from"] == e2["from"] or e1["to"] == e2["to"]:
                    continue  # shared trunk from/to one node reads as a merge on purpose
                if a1 < b2 + 24 and a2 < b1 + 24:
                    if repair:
                        # nudge e2's segment sideways; neighbours are perpendicular so they just stretch
                        pts = [list(p) for p in e2["_pts"]]
                        axis = 0 if o2 == "v" else 1
                        delta = 10.0 if c2 >= c1 else -10.0
                        pts[k2][axis] += delta
                        pts[k2 + 1][axis] += delta
                        e2["_pts"] = [tuple(p) for p in pts]
                        return True
                    key = tuple(sorted([(e1["from"], e1["to"]), (e2["from"], e2["to"])]))
                    if key not in reported:
                        reported.add(key)
                        self.warnings.append(f"edges {e1['from']}->{e1['to']} and {e2['from']}->{e2['to']} run on the same line; move a node to another column")
        return False

    def side_of(self, nid, p, q):
        """Which side of node nid the end point p sits on (q = the neighbouring point)."""
        x0, y0, x1, y1 = self.box(nid)
        if abs(p[1] - y1) < 0.5:
            return "bottom"
        if abs(p[1] - y0) < 0.5:
            return "top"
        if abs(p[0] - x0) < 0.5:
            return "left"
        return "right"

    def spread_ports(self):
        """Edges that leave / enter the same side of a node get separate attachment points,
        ordered by where their other end lies, so lines never merge at a node."""
        groups = {}
        for e in self.edges:
            pts = e["_pts"]
            if len(pts) < 2:
                continue
            groups.setdefault((e["from"], self.side_of(e["from"], pts[0], pts[1])), []).append((e, 0))
            groups.setdefault((e["to"], self.side_of(e["to"], pts[-1], pts[-2])), []).append((e, -1))
        for (nid, side), members in groups.items():
            if len(members) < 2:
                continue
            horiz_side = side in ("top", "bottom")
            step = 20.0 if horiz_side else 12.0

            def other_coord(m):
                e, end = m
                far = e["_pts"][-1] if end == 0 else e["_pts"][0]
                return far[0] if horiz_side else far[1]
            members.sort(key=other_coord)
            n = len(members)
            for i, (e, end) in enumerate(members):
                off = (i - (n - 1) / 2) * step
                pts = [list(p) for p in e["_pts"]]
                a, b = (0, 1) if end == 0 else (-1, -2)
                axis = 0 if horiz_side else 1
                # shift the end point and the neighbour that shares the first/last segment
                if not off:
                    continue
                shared = pts[a][axis] == pts[b][axis]
                if len(pts) == 2 and shared:
                    # straight edge: shift this end only and add an orthogonal jog at the middle
                    p0, p1 = pts
                    other = 1 - axis
                    mid = (p0[other] + p1[other]) / 2
                    if end == 0:
                        p0[axis] += off
                    else:
                        p1[axis] += off
                    j0, j1 = [0, 0], [0, 0]
                    j0[other], j0[axis] = mid, p0[axis]
                    j1[other], j1[axis] = mid, p1[axis]
                    pts = [p0, j0, j1, p1] if p0[axis] != p1[axis] else [p0, p1]
                else:
                    pts[a][axis] += off
                    if shared:
                        pts[b][axis] += off
                e["_pts"] = [tuple(p) for p in pts]

    # ---------------------------------------------------------------- labels / text fit
    def place_labels(self):
        rects = []
        for e in self.edges:
            lab = e.get("label") or ""
            if not lab:
                continue
            pts = e["_pts"]
            w = text_units(lab) * CHAR * 8 + 10
            if e.get("label_at"):
                lx, ly = e["label_at"]
                seg_len, horiz = None, True
            else:
                segs = list(range(len(pts) - 1))
                seglen = lambda j: abs(pts[j + 1][0] - pts[j][0]) + abs(pts[j + 1][1] - pts[j][1])
                if isinstance(e.get("label_seg"), int) and 0 <= e["label_seg"] < len(pts) - 1:
                    order = [e["label_seg"]]
                else:
                    order = sorted(segs, key=seglen, reverse=True)
                pick = None
                for i in order:
                    (ax, ay), (bx, by) = pts[i], pts[i + 1]
                    cx_, cy_ = (ax + bx) / 2, (ay + by) / 2
                    rr = (cx_ - w / 2, cy_ - 7, cx_ + w / 2, cy_ + 7)
                    free = all(not (rr[0] < o[2] and rr[2] > o[0] and rr[1] < o[3] and rr[3] > o[1]) for o, _ in rects) and                         all(not (rr[0] < n["_box"][2] and rr[2] > n["_box"][0] and rr[1] < n["_box"][3] and rr[3] > n["_box"][1]) for n in self.node.values())
                    fits = seglen(i) >= (w + 6 if ay == by else 18)
                    if free and fits:
                        pick = i
                        break
                    if pick is None and free:
                        pick = i
                i = pick if pick is not None else order[0]
                (ax, ay), (bx, by) = pts[i], pts[i + 1]
                lx, ly = (ax + bx) / 2, (ay + by) / 2
                horiz = ay == by
                seg_len = abs(bx - ax) if horiz else abs(by - ay)
                if horiz and seg_len < w + 6:
                    self.warnings.append(f"edge {e['from']}->{e['to']}: label {lab!r} (~{w:.0f}px) wider than its {seg_len:.0f}px segment; shorten it or set label_seg / label_at")
                if not horiz and seg_len < 18:
                    self.warnings.append(f"edge {e['from']}->{e['to']}: label {lab!r} sits on a {seg_len:.0f}px vertical; set label_seg / label_at")
            r = (lx - w / 2, ly - 7, lx + w / 2, ly + 7)
            for nid, n in self.node.items():
                x0, y0, x1, y1 = n["_box"]
                if r[0] < x1 and r[2] > x0 and r[1] < y1 and r[3] > y0:
                    self.warnings.append(f"edge {e['from']}->{e['to']}: label {lab!r} overlaps node {nid}")
            for (o, oe) in rects:
                if r[0] < o[2] and r[2] > o[0] and r[1] < o[3] and r[3] > o[1]:
                    self.warnings.append(f"edge labels overlap: {lab!r} and {oe!r}")
            rects.append((r, lab))
            e["_label"] = (lx, ly, w)

    def fit(self, s, max_px, size, min_size, what):
        u = text_units(s)
        if u == 0:
            return size
        need = u * CHAR * size
        if need <= max_px:
            return size
        f = max_px / (u * CHAR)
        if f < min_size:
            self.warnings.append(f"{what} {s!r} too long for the node (~{need:.0f}px at {size}px, room {max_px:.0f}px); shorten it")
            return min_size
        return round(f, 1)

    # ---------------------------------------------------------------- emit
    def svg(self):
        L = LEGEND_DEFAULT[self.lang]
        out = []
        legend_items = self._legend_items()
        # legend wraps at the lane width
        rows, cur, x = [], [], 20.0
        for it in legend_items:
            w = text_units(it["label"]) * CHAR * 7.5 + 44
            if cur and x + w > self.view_w - 20:
                rows.append(cur)
                cur, x = [], 20.0
            cur.append((x, w, it))
            x += w + 8
        if cur:
            rows.append(cur)
        legend_top = self.content_bottom + 28
        view_h = legend_top + 20 + 19 * len(rows) + 14
        out.append(f'<svg viewBox="0 0 {fmt(self.view_w)} {fmt(view_h)}" role="img" lang="{self.lang}" aria-labelledby="archify-diagram-title archify-diagram-description" data-preset="classic" data-quality-profile="showcase">')
        out.append(f'        <title id="archify-diagram-title">{esc(self.title)}</title>')
        out.append(f'        <desc id="archify-diagram-description">{esc(self.description or self.title)}</desc>')
        out.append('''        <!-- Definitions -->
        <defs>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-default" />
          </marker>
          <marker id="arrowhead-emphasis" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-emphasis" />
          </marker>
          <marker id="arrowhead-security" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-security" />
          </marker>
          <marker id="arrowhead-dashed" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-dashed" />
          </marker>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" class="c-grid" stroke-width="0.5"/>
          </pattern>
        </defs>

        <!-- Background Grid -->
        <rect width="100%" height="100%" fill="url(#grid)" />
''')
        out.append("        <!-- Swimlanes -->")
        for i, l in enumerate(self.lanes):
            y, h = self.lane_y[l["id"]], self.lane_h[l["id"]]
            label = f"{i + 1:02d} / {l['label']}"
            if text_units(label) * CHAR * 10 > self.lane_w - 28:
                self.warnings.append(f"lane {l['id']}: label too long for the lane width")
            out.append(f'        <rect data-graph-role="structural-frame" data-composition-frame-kind="lane" data-composition-frame-id="lane-{i}" x="{fmt(self.lane_x)}" y="{fmt(y)}" width="{fmt(self.lane_w)}" height="{fmt(h)}" rx="10" class="c-lane" stroke-width="1"/>')
            out.append(f'        <text x="54" y="{fmt(y + 20)}" class="t-dim" font-size="10" font-weight="600">{esc(label)}</text>\n')
        out.append("        <!-- Phase headers -->\n\n\n        <!-- Workflow groups -->\n\n")
        out.append("        <!-- Edge paths -->")
        for k, e in enumerate(self.edges):
            cls, sw, mk, dash = VARIANT_STYLE[e["variant"]]
            pts = e["_pts"]
            cp = ";".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)
            d = "M " + " L ".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)
            out.append(f'        <path data-edge-from="{esc(e["from"])}" data-edge-to="{esc(e["to"])}" data-edge-label="{esc(e["label"])}" data-edge-key="{k}" data-composition-points="{cp}" d="{d}" class="{cls}" stroke-width="{sw}"{dash} marker-end="url(#{mk})"/>')
        out.append("\n        <!-- Nodes -->")
        lane_name = {l["id"]: l.get("context") or l["label"].split(" · ")[0] for l in self.lanes}
        for n in self.nodes:
            if n.get("id") not in self.node:
                continue
            x0, y0, x1, y1 = n["_box"]
            cx = (x0 + x1) / 2
            kind = n["kind"]
            label, sub = n["label"], n.get("sublabel", "")
            ctx = lane_name[n["lane"]]
            tip = n.get("detail") or sub
            f_label = self.fit(label, self.NW - 22, 11, 8.5, f"node {n['id']} label")
            f_sub = self.fit(sub, self.NW - 10, 7, 6, f"node {n['id']} sublabel") if sub else 7
            dash = ' stroke-dasharray="4 3"' if n.get("optional") else ""
            out.append(f'        <g id="node-{esc(n["id"])}" data-node-id="{esc(n["id"])}" data-node-label="{esc(label)}" tabindex="0" role="button" aria-label="Focus {esc(label)}, {esc(sub)}, {esc(ctx)}" aria-pressed="false" data-node-kind="{kind}" data-node-sublabel="{esc(sub)}" data-node-context="{esc(ctx)}">')
            out.append(f'          <title>{esc(label)} · {esc(tip)}</title>')
            out.append(f'          <rect x="{fmt(x0)}" y="{fmt(y0)}" width="{fmt(self.NW)}" height="{fmt(self.NH)}" rx="6" class="c-mask"/>')
            out.append(f'          <rect x="{fmt(x0)}" y="{fmt(y0)}" width="{fmt(self.NW)}" height="{fmt(self.NH)}" rx="6" class="c-{kind}" stroke-width="1.5"{dash}/>')
            out.append(f'          <g aria-hidden="true" data-semantic-sigil="{kind}" class="semantic-sigil s-{kind}" transform="translate({fmt(x0 + 6)} {fmt(y0 + 6)}) scale(0.6875)">')
            out.append("            " + SIGILS[kind])
            out.append("          </g>")
            ly = y0 + (24 if sub else self.NH / 2 + 4)
            out.append(f'          <text data-node-label="" data-detail-anchor="" x="{fmt(cx)}" y="{fmt(ly)}" class="t-primary" font-size="{fmt(f_label)}" font-weight="600" text-anchor="middle">{esc(label)}</text>')
            if sub:
                out.append(f'          <text data-detail="context" x="{fmt(cx)}" y="{fmt(y0 + 41)}" class="t-muted" font-size="{fmt(f_sub)}" text-anchor="middle">{esc(sub)}</text>')
            out.append("        </g>\n")
        out.append("        <!-- Edge labels -->")
        for k, e in enumerate(self.edges):
            if "_label" not in e:
                continue
            lx, ly, w = e["_label"]
            tcls = "t-backend" if e["variant"] == "emphasis" else ("t-security" if e["variant"] == "security" else "t-muted")
            out.append(f'        <g data-detail="context" data-edge-from="{esc(e["from"])}" data-edge-to="{esc(e["to"])}" data-edge-label="{esc(e["label"])}" data-edge-key="{k}">')
            out.append(f'          <rect x="{fmt(lx - w / 2)}" y="{fmt(ly - 7)}" width="{fmt(w)}" height="14" rx="3" class="c-mask"/>')
            out.append(f'          <text x="{fmt(lx)}" y="{fmt(ly + 3)}" class="{tcls}" font-size="8" text-anchor="middle">{esc(e["label"])}</text>')
            out.append("        </g>")
        out.append("\n        <!-- Legend -->")
        out.append('        <g data-legend="" data-legend-bridge="">')
        out.append(f'          <text x="20" y="{fmt(legend_top)}" class="t-primary" font-size="12" font-weight="650">{esc(L["legend"])}</text>')
        for r, row in enumerate(rows):
            base = legend_top + 20 + 19 * r
            for x, w, it in row:
                if it["type"] == "kind":
                    dash = ' stroke-dasharray="3 2"' if it.get("optional") else ""
                    kind = it["kind"]
                    attrs = f'data-legend-semantic-kind="{kind}" data-legend-kind="{kind}" data-legend-label="{esc(it["label"])}" data-legend-x="{fmt(x)}" data-legend-baseline="{fmt(base)}" data-legend-width="{fmt(w)}"'
                    if it.get("optional"):
                        attrs = f'data-legend-label="{esc(it["label"])}"'
                    out.append(f'          <g {attrs}>')
                    out.append(f'            <rect x="{fmt(x)}" y="{fmt(base - 8)}" width="14" height="9" rx="2" class="c-{kind}" stroke-width="1"{dash}/>')
                    out.append(f'            <text x="{fmt(x + 22)}" y="{fmt(base)}" class="t-muted" font-size="7.5" font-weight="500">{esc(it["label"])}</text>')
                    out.append("          </g>")
                else:
                    cls, _, _, dash = VARIANT_STYLE[it["variant"]]
                    out.append(f'          <path d="M {fmt(x)} {fmt(base - 4)} L {fmt(x + 18)} {fmt(base - 4)}" class="{cls}" stroke-width="1.6"{dash}/>')
                    out.append(f'          <text x="{fmt(x + 24)}" y="{fmt(base)}" class="t-muted" font-size="7.5" font-weight="500">{esc(it["label"])}</text>')
        out.append("        </g>")
        out.append("      </svg>")
        return "\n".join(out)

    def _legend_items(self):
        L = LEGEND_DEFAULT[self.lang]
        custom = {i["kind"]: i["label"] for i in (self.d.get("legend") or []) if i.get("kind")}
        custom_v = {i["variant"]: i["label"] for i in (self.d.get("edge_legend") or []) if i.get("variant")}
        used = []
        for n in self.nodes:
            k = n.get("kind", "backend")
            if k in KINDS and k not in used:
                used.append(k)
        order = [k for k in KINDS if k in used]
        items = [{"type": "kind", "kind": k, "label": custom.get(k, L[k])} for k in order]
        if any(n.get("optional") for n in self.nodes):
            items.append({"type": "kind", "kind": "external", "optional": True, "label": custom.get("optional", L["optional"])})
        vs = [v for v in ("emphasis", "dashed", "security") if any(e["variant"] == v for e in self.edges)]
        items += [{"type": "edge", "variant": v, "label": custom_v.get(v, L[v])} for v in vs]
        return items

    def cards_html(self):
        out = ["    <!-- Info Cards -->", '    <div class="cards">']
        for c in self.cards:
            out.append('      <div class="card">')
            out.append('        <div class="card-header">')
            out.append(f'          <div class="card-dot {c.get("dot", "slate")}"></div>')
            out.append(f'          <h3>{esc(c.get("title", ""))}</h3>')
            out.append("        </div>")
            out.append("        <ul>")
            for it in c.get("items", []):
                out.append(f"          <li>&bull; {esc(it)}</li>")
            out.append("        </ul>")
            out.append("      </div>\n")
        out.append("    </div>")
        return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out")
    ap.add_argument("--lang", choices=["en", "vi"])
    ap.add_argument("--force", action="store_true", help="overwrite an existing --out")
    ap.add_argument("--check", action="store_true", help="validate and lint only, write nothing")
    a = ap.parse_args(argv)
    try:
        data = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        print(f"ERROR: cannot read spec: {ex}", file=sys.stderr)
        return 2
    sp = Spec(data, a.lang)
    if not sp.errors:
        sp.build_geometry()
        sp.route_all()
        sp.place_labels()
        svg = sp.svg()
    for e in sp.errors:
        print("ERROR:", e)
    if sp.errors:
        return 1
    if a.check:
        for w in sp.warnings:
            print("WARN:", w)
        print(f"OK: {len(sp.node)} nodes, {len(sp.edges)} edges, {len(sp.lanes)} lanes, {len(sp.cards)} cards, {len(sp.warnings)} warnings")
        return 0
    if not a.out or not a.out.lower().endswith(".html"):
        print("ERROR: --out <file.html> is required", file=sys.stderr)
        return 2
    out = Path(a.out).resolve()
    if out.exists() and not a.force:
        print(f"ERROR: {out} exists; pass --force to overwrite", file=sys.stderr)
        return 2
    shell = SHELL.read_text(encoding="utf-8")
    page = (shell.replace("{{LANG}}", sp.lang)
                 .replace("{{SHELL_VERSION}}", esc(SHELL_VERSION))
                 .replace("{{TITLE}}", esc(sp.title))
                 .replace("{{SVG}}", svg)
                 .replace("{{CARDS}}", sp.cards_html()))
    assert "{{" not in page.split("<script", 1)[0], "unfilled token in shell head"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8", newline="\n")
    for w in sp.warnings:
        print("WARN:", w)
    print(f"wrote {out} ({len(sp.node)} nodes, {len(sp.edges)} edges, {len(sp.warnings)} warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
