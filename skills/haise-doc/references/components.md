# Components — copy-paste snippets

All classes below are defined in the shell (`assets/template.html`). Do not add
a `<style>` block to a document; if a one-off tweak is unavoidable use an
inline `style` attribute that only references CSS variables
(`var(--ok)`, `var(--c2)`…), never hard-coded colours.

Colour variables (theme-aware): `--ink --muted --line --panel --panel-2 --bg --code`,
status `--accent/--accent-soft --ok/--ok-soft --bad/--bad-soft --warn/--warn-soft --info/--info-soft`,
categorical series `--c1 … --c5` (blue, orange, green, violet, teal),
phase sets `--p1/--p1-soft … --p6/--p6-soft` (blue, teal, violet, orange, green, pink).

### Look (shell v3)

Sections are not boxed: each `h2` gets a thin rule above it and the page reads
as one calm column (17 px text, `max-width` 860 px). Visual weight comes from
the components below — white cards on a warm-grey page, coloured edges, small
uppercase labels. Keep it that way: one idea per block, short paragraphs, a
figure or card wherever a structure is explained.

### Phase colours — one colour per stage, the same everywhere

When a doc walks through stages (request hops, pipeline steps, layers), give
each stage one of `.p1 … .p6` and reuse it in the step card, the chips, the
callouts and the SVG (`var(--p3)` stroke / `var(--p3-soft)` fill). Readers
then match a step to its diagram by colour alone. Say the mapping once in a
figure caption.

### Phase focus — hover one stage, light it up everywhere

Give every element that belongs to a stage the same `data-phase="<name>"`
(short lowercase names: `dns`, `tcp`, `build`, `deploy` …): the step card, the
SVG group(s) of that stage in **every** diagram, and the chips of a phase key.
Hovering any of them dims everything that has a *different* `data-phase`
(elements without the attribute stay as they are). Click/tap on a step card or
chip pins the focus (works on touch); click again or Esc releases it. Clicks
inside diagrams still open the zoom viewer.

```html
<!-- phase key under the section's first callout: one chip per stage -->
<div class="phase-key">Hover or tap a stage:
  <span class="chip p1" data-phase="dns">DNS</span><span class="chip p2" data-phase="tcp">TCP</span> …</div>

<ol class="steps">
  <li class="p1" data-phase="dns"><strong>DNS: name → IP</strong> …</li>
  <li class="p2" data-phase="tcp"><strong>TCP: open a line</strong> …</li>
</ol>

<!-- in SVG: wrap ALL marks of one stage (box/arrow + its labels) in one <g> -->
<g data-phase="dns">
  <rect … fill="var(--p1-soft)" stroke="var(--p1)"/><text …>1 · DNS</text>
</g>
<g data-phase="tcp">
  <line … stroke="var(--p2)" marker-end="url(#arr-seq-tcp)"/><text …>SYN · SYN-ACK · ACK</text>
</g>
```

Rules: use it only when ≥ 3 stages recur across ≥ 2 places (steps + a diagram,
or two diagrams); one phase name per stage, identical everywhere; do not put
`data-phase` on whole sections, whole figures or the `<svg>` itself (that would
dim everything); leave actors, lifelines, axes and legends without it.

## Lead block (verdict + key points) — use when the doc opens with an answer

```html
<section id="answer">
  <h2>Answer &amp; key findings</h2>
  <div class="verdict">
    <span class="label">Short answer</span>
    <p>Pallet shipments (type 801) never reach <strong>inspected</strong>, because the transport-complete IF only flips the box status. <a class="cite" href="#src-1">S1</a></p>
  </div>
  <ol class="findings">
    <li><strong>Only the box status changes.</strong> <code>pallet_pass_st_codes_id</code> → 810; <code>ins_qty</code> is untouched. <a class="cite" href="#src-1">S1</a> <span class="tag sure">VERIFIED</span></li>
    <li><strong>Orders stay stuck.</strong> The fix-results batch only picks 82/93 orders. <a class="cite" href="#src-4">S4</a> <span class="tag infer">INFERRED</span></li>
  </ol>
</section>
```

## Headline numbers

```html
<div class="stats">
  <div class="stat bad"><span class="v">0 / 40</span><span class="k">cases counted as inspected</span></div>
  <div class="stat ok"><span class="v">40 / 40</span><span class="k">after the fix</span></div>
  <div class="stat"><span class="v">7</span><span class="k">shipment work types</span></div>
</div>
```

## Callouts, tags, severity

White box with a coloured left edge. Start with a `.kind` label (small
uppercase, in the callout colour) and optionally a `.ct` title line.

```html
<div class="callout bad"><span class="kind">Bug</span><strong>In one sentence:</strong> …</div>
<div class="callout ok">…confirmed…</div>
<div class="callout warn"><span class="kind">Watch out</span>…risk…</div>
<div class="callout info">…note…</div>
<div class="callout tip"><span class="kind">Tip</span>…</div>             <!-- green -->
<div class="callout interview"><span class="kind">Interview</span>…</div> <!-- violet -->
<div class="callout example"><p class="ct"><span class="kind">Example</span>What the handshakes cost</p><p>…</p></div> <!-- teal -->
<div class="callout myth"><span class="kind">Common mix-up</span>…</div> <!-- red -->
<div class="callout tldr"><span class="kind">In short</span>…</div>      <!-- grey -->
<div class="callout p3"><span class="kind">TLS</span>…</div>             <!-- any phase colour -->
<span class="tag sure">VERIFIED</span> <span class="tag infer">INFERRED</span> <span class="tag open">OPEN</span>
<span class="sev high">High</span> <span class="sev med">Medium</span> <span class="sev low">Low</span>
```

## Keyword with original

```html
<strong class="kw">transport-complete report</strong> <span class="orig" lang="ja">(搬送完了報告)</span>
<code>引当済</code> (allocated)            <!-- verbatim status value: code, not bold -->
```

## Cards / grids / steps / deep dive

```html
<div class="grid2">            <!-- or grid3 -->
  <div class="card"><h4>Customer system</h4>Sends orders…</div>
  <div class="card bad"><h4>Current code</h4>…</div>   <!-- coloured top edge: .ok .bad .warn .info .p1…p6 -->
</div>

<!-- step cards: numbered circle on a timeline + white card with a coloured top edge.
     Colour per step: .p1 … .p6 (phase), or .ok / .bad / .warn (status). -->
<ol class="steps">
  <li class="p1"><strong>DNS: name → IP</strong><span class="chip p1">skipped when cached</span>
    <p>The browser asks a recursive resolver …</p>
    <div class="ex"><span class="k">Example</span><code>www.example.com → 192.0.2.44</code></div></li>
  <li class="bad"><strong>Inspection</strong> — the step that is skipped</li>
  <li class="ok"><strong>Fix results</strong> — …</li>
</ol>

<span class="chip">1 round trip</span>  <span class="chip p4">cost</span>   <!-- small pill; phase class colours it -->

<details class="more">
  <summary>Full call chain (12 hops)</summary>
  <div class="body">…long detail; search auto-expands it on a hit…</div>
</details>
```

## Tables

```html
<div class="tbl">
  <p class="cap">Status codes of <code>sh_details</code> <a class="cite" href="#src-3">S3</a></p>
  <table>
    <tr><th>Code</th><th>Meaning</th><th class="num">Count</th></tr>
    <tr><td><code>109</code></td><td><strong class="kw">allocated</strong> <span class="orig" lang="ja">(引当済)</span></td><td class="num">1,204</td></tr>
    <tr class="hl"><td><code>110</code></td><td>inspected</td><td class="num">0</td></tr>
  </table>
</div>
```
✓/✗ matrices: `<td class="good">✓</td>` / `<td class="badv">✗ reason</td>`.

## Figures — SVG rules

- `<figure class="fig">` → `<svg viewBox="0 0 W H" role="img" aria-label="…">` → `<figcaption>` (what to notice + cite).
- Width 900 viewBox units is the default canvas; text ≥ 12 units; `text-anchor="middle"` for box labels.
- Fills/strokes only via `var(--…)`. Box: `fill="var(--panel)" stroke="var(--line)"`; highlighted: `fill="var(--bad-soft)" stroke="var(--bad)"`.
- Arrow-marker ids must be **unique per document** (`arr-ctx`, `arr-seq`…), because all SVGs share one id space.
- Original-language labels: `<tspan class="orig">(出荷ヘッダ)</tspan>`.

### Entity / flow diagram

```html
<figure class="fig">
<svg viewBox="0 0 900 220" role="img" aria-label="Order side vs physical side">
  <defs><marker id="arr-ent" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>
  <g font-size="13">
    <rect x="30" y="40" width="220" height="64" rx="10" fill="var(--panel)" stroke="var(--line)"/>
    <text x="140" y="66" text-anchor="middle" font-weight="600" fill="var(--ink)">Shipment header <tspan class="orig" font-weight="400" fill="var(--muted)">(出荷ヘッダ)</tspan></text>
    <text x="140" y="88" text-anchor="middle" font-size="12" fill="var(--muted)">one customer order</text>

    <rect x="640" y="40" width="230" height="64" rx="10" fill="var(--bad-soft)" stroke="var(--bad)"/>
    <text x="755" y="66" text-anchor="middle" font-weight="600" fill="var(--ink)">Box</text>
    <text x="755" y="88" text-anchor="middle" font-size="12" fill="var(--bad)">status updated only here</text>

    <line x1="250" y1="72" x2="638" y2="72" stroke="var(--muted)" stroke-width="2" marker-end="url(#arr-ent)"/>
    <text x="444" y="64" text-anchor="middle" font-size="12" fill="var(--muted)">1 : n</text>
  </g>
</svg>
<figcaption>The order side never hears about the pallet event. <a class="cite" href="#src-2">S2</a></figcaption>
</figure>
```

### Sequence diagram

```html
<figure class="fig">
<svg viewBox="0 0 900 260" role="img" aria-label="WCS to W3 sequence">
  <defs><marker id="arr-seq" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
    <path d="M0,0 L10,5 L0,10 z" fill="var(--ink)"/></marker></defs>
  <g font-size="13" fill="var(--ink)">
    <!-- lanes: header box + dashed lifeline -->
    <rect x="70" y="16" width="140" height="34" rx="8" fill="var(--panel)" stroke="var(--line)"/><text x="140" y="38" text-anchor="middle" font-weight="600">WCS</text>
    <line x1="140" y1="50" x2="140" y2="250" stroke="var(--line)" stroke-dasharray="4 4"/>
    <rect x="380" y="16" width="140" height="34" rx="8" fill="var(--panel)" stroke="var(--line)"/><text x="450" y="38" text-anchor="middle" font-weight="600">W3</text>
    <line x1="450" y1="50" x2="450" y2="250" stroke="var(--line)" stroke-dasharray="4 4"/>
    <rect x="690" y="16" width="140" height="34" rx="8" fill="var(--panel)" stroke="var(--line)"/><text x="760" y="38" text-anchor="middle" font-weight="600">DB</text>
    <line x1="760" y1="50" x2="760" y2="250" stroke="var(--line)" stroke-dasharray="4 4"/>
    <!-- messages -->
    <line x1="140" y1="90" x2="446" y2="90" stroke="var(--ink)" stroke-width="1.6" marker-end="url(#arr-seq)"/>
    <text x="295" y="82" text-anchor="middle" font-size="12">① CSV TRANS-REP</text>
    <line x1="450" y1="140" x2="756" y2="140" stroke="var(--ok)" stroke-width="1.6" marker-end="url(#arr-seq)"/>
    <text x="605" y="132" text-anchor="middle" font-size="12" fill="var(--ok)">② box → 810</text>
    <line x1="450" y1="190" x2="756" y2="190" stroke="var(--bad)" stroke-width="1.6" stroke-dasharray="6 4" marker-end="url(#arr-seq)"/>
    <text x="605" y="182" text-anchor="middle" font-size="12" fill="var(--bad)">③ ins_qty — missing</text>
  </g>
</svg>
<figcaption>Step ③ is what the spec adds. <a class="cite" href="#src-1">S1</a><a class="cite" href="#src-5">S5</a></figcaption>
</figure>
```

### Bar chart (horizontal, labelled values)

Compute bar widths yourself: `width = value / max * plotWidth`. Label values
directly; no gridlines needed when every bar is labelled.

```html
<figure class="fig">
<svg viewBox="0 0 900 190" role="img" aria-label="Orders stuck per status">
  <g font-size="13" fill="var(--ink)">
    <!-- plot area x=220..820 (600 wide); max value 1200 -->
    <text x="210" y="44" text-anchor="end">Allocated (80)</text>
    <rect x="220" y="28" width="600" height="24" rx="4" fill="var(--c1)"/>
    <text x="826" y="45" font-weight="600">1,200</text>

    <text x="210" y="92" text-anchor="end">Partly inspected (81)</text>
    <rect x="220" y="76" width="175" height="24" rx="4" fill="var(--c2)"/>
    <text x="401" y="93" font-weight="600">350</text>

    <text x="210" y="140" text-anchor="end">Inspected (82)</text>
    <rect x="220" y="124" width="2" height="24" fill="var(--c3)"/>
    <text x="228" y="141" font-weight="600">0</text>
  </g>
</svg>
<div class="legend"><span><i style="background:var(--c1)"></i>status 80</span><span><i style="background:var(--c2)"></i>status 81</span></div>
<figcaption>No pallet order ever reaches 82. Query run on staging dump 2026-09-24. <a class="cite" href="#src-6">S6</a></figcaption>
</figure>
```

For time series draw a `<polyline points="…" fill="none" stroke="var(--c1)" stroke-width="2">`
over a light axis (`stroke="var(--line)"`), with x labels every few points and the
last value labelled at the end of the line. For chart-type choice and palette
discipline you may also consult the `dataviz` skill.

### Before → after cards

```html
<figure class="fig">
  <div class="grid2">
    <div class="card bad"><h4>Before</h4><code>ins_qty = 0 / 40</code><br>header 80 · 91</div>
    <div class="card ok"><h4>After</h4><code>ins_qty = 40 / 40</code><br>header 82 · 93</div>
  </div>
  <figcaption>Same pallet, same report, with spec step Ⅱ+Ⅲ. <a class="cite" href="#src-1">S1</a></figcaption>
</figure>
```

## Images (screenshots, photos) — embedded, high resolution, zoomable

Every `<img>` and every diagram `<svg>` inside `figure.fig` is clickable: the shell
opens it in a full-screen viewer (wheel / pinch zoom, drag to pan, `+` `−` `0` fit,
`1` actual size, ←/→ between figures, Esc to close). Diagrams stay sharp at any
zoom because they are vectors; raster images are only as sharp as the file you embed.

- **Resolution:** embed the largest original you can get, at least **1200 px wide**
  (1600–2400 px for UI screenshots). Never downscale for the page: the page shrinks
  it, the viewer shows the full pixels. `check_doc.py` warns below 1200 px.
- **Format:** keep the source format (PNG for UI with text, JPEG/WebP for photos).
  A whole doc should stay under ~15 MB.
- **How:** write a placeholder, then run `scripts/embed_images.py <doc> [--base <dir>]`,
  which inlines each file as a `data:` URI unchanged:

```html
<figure class="fig">
  <div class="grid2">
    <img alt="Copilot dashboard (iPad)" src="@@shots/copilot-dashboard.png@@">
    <img alt="Copilot cash flow (iPad)" src="@@shots/copilot-cashflow.png@@">
  </div>
  <figcaption>What to notice … <a class="cite" href="#src-3">S3</a></figcaption>
</figure>
```

- Always set a meaningful `alt` — the viewer shows it as the title with the caption.
- Optional inline style may only use sizes and `var(--…)` colours, e.g.
  `style="border:1px solid var(--line);border-radius:8px"`; `max-width:100%` is already in the shell.
- To opt one element out of zooming (a tiny icon, a decorative mark), add `data-nozoom` to it or a parent.

## Source entry

```html
<section id="sources">
  <h2>Sources</h2>
  <ol class="sources">
    <li id="src-1">
      <div class="src-head"><span class="src-id">S1</span><span class="src-type">Spec</span>
        <span class="src-title">IF transport-complete import (SLC) design, V06</span></div>
      <div class="src-loc"><code>from_customer/docs/kai/20_成果物/…/PU08010205_IF_搬送完了報告取込（SLC）_設計書_V06.xlsx</code> · sheet <code>処理詳細</code> · rows 41–58 · updated 2026-10-08</div>
      <blockquote class="src-quote">Ⅱ．出荷実績の更新　sh_results.ins_qty に個口明細.qty_in_box を加算する
        <span class="tr">Ⅱ. Update shipment results: add box-detail qty_in_box to sh_results.ins_qty.</span></blockquote>
      <div class="src-note">Latest version in the local spec folder; V05 moved to <code>old/</code>.</div>
    </li>
    <li id="src-2">
      <div class="src-head"><span class="src-id">S2</span><span class="src-type">Code</span>
        <span class="src-title">KaiTransportCompleteReportService::updateSmallLotGroups</span></div>
      <div class="src-loc"><code>w3package_v2/app/Services/Alternatives/Kai/KaiTransportCompleteReportService.php:653-671</code> · branch <code>kai/develop/base</code> @ <code>c0c6271</code></div>
      <blockquote class="src-quote"><code>$group-&gt;pallet_pass_st_codes_id = ShSmallLotGroup::PALLET_PASS_DONE; // 810</code></blockquote>
    </li>
  </ol>
</section>
```
Only when the doc cites anything; it is always the last section. Source types to use in `.src-type`: Code · Spec · Ticket · DB · Command · Log · Test · Web · Decision · Doc.
