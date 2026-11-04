# haise-flow spec format and layout rules

A page is described by one JSON file (`<name>.flow.json`, kept next to the HTML).
`scripts/build_flow.py` turns it into the page; never hand-edit the generated HTML.

## 1. Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `title` | string | yes | Page `<h1>`, browser title and SVG title. A name, not a sentence: "Order API — Request Architecture", "WMS Ticket Workflow 1.22.4 — Steps và Gates". |
| `description` | string | no | One sentence for screen readers / the SVG `<desc>`. |
| `lang` | `en` \| `vi` | no (`en`) | Language of the default legend texts and the `lang` attribute. Content strings are written in that language by you. |
| `lanes` | array | yes | Horizontal bands, top to bottom (§2). |
| `nodes` | array | yes | Boxes placed on a lane / row / column grid (§3). |
| `edges` | array | no | Arrows between nodes (§4). |
| `legend` | array | no | Override legend texts: `{ "kind": "<kind>" \| "optional", "label": "…" }`. Only kinds that appear are shown. |
| `edge_legend` | array | no | Override edge-style legend texts: `{ "variant": "emphasis" \| "dashed" \| "security", "label": "…" }`. |
| `cards` | array | recommended | 2–4 info cards under the diagram (§5). |
| `layout` | object | no | `node_w` (124), `node_h` (56), `col_pitch` (180, ≥ node_w + 40), `row_pitch` (node_h + 22), `x0` (56). Change only when the defaults truly do not fit. |

## 2. Lanes

```json
{ "id": "analysis", "label": "Phân tích · feature (trên) · bug (dưới)", "context": "Phân tích", "rows": 2 }
```

- Rendered as `01 / <label>`, `02 / …` in order. One lane = one phase (workflow) or one layer (architecture).
- `rows` (default 1): a lane can hold parallel tracks, e.g. feature on row 0 and bug on row 1.
- `context`: the short lane name shown in each node's passport; defaults to the part of `label` before the first ` · `.
- 3–5 lanes read best. Lane label must fit the lane width (the build warns).

## 3. Nodes

```json
{ "id": "fix_plan", "lane": "analysis", "col": 2, "row": 1, "kind": "frontend",
  "label": "Fix plan", "sublabel": "Sửa nhỏ nhất · bạn duyệt",
  "detail": "fix-plan (trước @7: fix-plan-approval) · 1 worker Opus C3 · Gate A + 16 rule STACK · G3 · G0",
  "optional": false }
```

| Key | Meaning |
|---|---|
| `id` | Letters, digits, `-`, `_`. Stable: it becomes `#node-<id>` (deep links, passport). |
| `lane`, `col`, `row` | Grid slot. `col` from 0 (left). `row` may be fractional (`0.5`) to centre a node between two rows (e.g. a fan-out source). One node per slot. |
| `kind` | Semantic colour + icon (table below). |
| `label` | ≤ 16 characters (mono font, 11 px). The build shrinks it down to 8.5 px and then warns. |
| `sublabel` | ≤ 24 characters. Shrinks to 6 px, then warns. CJK counts double. |
| `detail` | Hover tooltip and passport text: put the exact names, owners, models, gates, sources here — not in the box. |
| `optional` | `true` → dashed border + the "only when condition applies" legend entry. Use for conditional steps (IF-only, HT-only, feature flags). |

| `kind` | Use for (workflow) | Use for (architecture) |
|---|---|---|
| `frontend` | a person decides / approves / reads | UI, client app |
| `backend` | a worker / automated step does the work | service, controller, job |
| `security` | review, gate, policy check | gateway, auth, policy engine |
| `cloud` | handoff to another tool / team | managed / 3rd-party service you hand work to |
| `external` | step owned by another system | external system / provider |
| `database` | stored artefact or ledger | database, storage |
| `messagebus` | queue of work items | queue, topic, event bus |

## 4. Edges

```json
{ "from": "coding_review", "to": "testcase", "label": "Gate BCJS · đạt · không IF",
  "variant": "emphasis", "route": "under", "label_seg": 0, "label_at": [x, y] }
```

| Key | Values | Meaning |
|---|---|---|
| `variant` | `default` · `emphasis` · `dashed` · `security` | `emphasis` = the main path / a gate that passes; `dashed` = loop, retry, async callback; `security` = policy-controlled hop. Keep emphasis for the one path a reader should follow. |
| `label` | string | Gate / condition / payload. Short: the horizontal gap between columns is 56 px (≈ 9 mono chars at 8 px). Longer labels go on long segments (lane-to-lane edges, under/over routes). |
| `route` | `auto` (default) · `straight` · `under` · `over` · `gutter` · `direct` · `side` | Force a routing shape when auto picks a poor one (see below). |
| `label_seg` | int | Put the label on segment N of the path (0 = first segment from the source). |
| `label_at` | `[x, y]` | Absolute label position (last resort). |

How auto-routing chooses (first route that crosses no other node wins):

- **Same lane and row, forward** → straight; if a node is in the way → `under` (below the row), then `over`.
- **Same lane and row, backward** (loop) → `over` (above the row), then `under`. Use `variant: dashed`.
- **Same lane, other row** → elbow in the column gap (`side`), or vertical then horizontal (`direct`).
- **To a later lane** → same column: straight down (except column 0); target in column 0: `gutter` (down to the gap above the target lane, left to the gutter, into the node's left side); otherwise `direct` (into the target's top).
- **To an earlier lane** → right-hand gutter into the target's right side (feedback / callback).
- Afterwards: edges sharing one side of a node get separate attachment points; loops above/below one row stack; two edges that would run on the same line are nudged apart. Anything left is a **warning** — fix it by moving a node to another column, not by ignoring it.

## 5. Cards

```json
{ "dot": "cyan", "title": "Phân tích · preparation → plan", "items": ["…", "…"] }
```

- `dot`: `cyan`, `amber`, `emerald`, `violet`, `rose`, `orange`, `slate`. Use one colour per lane, in lane order (cyan, amber, emerald, violet).
- One card per lane (or per concern), 2–5 items each. Items carry the facts the boxes cannot: exact node names, who does what, gates and what they check, counts, conditions. Start each item with the box name it explains: `"Coding review — 2 worker Opus, Gate B C J S (+I); chưa đạt → coding"`.

## 6. Layout recipes

- **Workflow / pipeline**: one lane per phase, 4–5 columns, left→right inside a lane, lane-to-lane hand-off via the gutter into column 0 of the next lane (as in `examples/wms-ticket-workflow.flow.json`). Optional steps get their own column with `optional: true`, plus an `under` edge that skips them.
- **Two variants of one flow** (bug / feature, sync / async): one lane with `rows: 2`, the shared start node at `row: 0.5`, both rows merging into column 0 of the next lane.
- **Layered architecture**: one lane per layer (client → API → domain → data), keep a component in the same column as what it mainly calls so the call drops straight down (`examples/order-api-architecture.flow.json`). Callbacks / async replies: `dashed` edges back up (right gutter).
- **Review / retry loops**: `dashed` edge from the checker back to the producer (routes `over`), label with the failing condition (`chưa đạt · lần 2 → G4`).
- Grouping many real steps into one box is fine when the box `detail` and the card list every real step by its exact name (see lane 04 of the WMS example).

## 7. Things the build checks

Errors (exit 1): missing title/lanes/labels, unknown lane / node ids, duplicate ids or slots, bad kind / variant / route / dot, `col_pitch` too small.
Warnings (exit 0, fix them): text too long for a box or lane, label wider than its segment, label over a node or another label, edge forced through a node, two edges on one line, no cards / more than 4 cards.
