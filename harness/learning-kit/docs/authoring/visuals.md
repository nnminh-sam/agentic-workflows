# Visuals and readable writing (research.md, reteach docs)

The learner reads these docs as an HTML page built by `LK read <slug>` (`lk/reader.py`). The Markdown stays
the source of truth: you write Markdown plus a few JSON blocks, and the page is rendered from them. Never
write HTML; it is escaped and shows up as text.

**Why this matters.** Long, dense paragraphs are tiring to read, so people skim them and lose the details.
A process becomes something you can picture when it is drawn as steps or a diagram. Each fact should appear
once, in the form that suits it best: a process as steps, an interaction as a sequence, a structure as
boxes, a comparison as a table.

## Writing rules

- **One idea per paragraph, ≤ 4 sentences.** `LK read --check` warns above 110 words. Treat the warning
  as a bug: split the paragraph, or turn it into a list, a table or steps.
- **Show a process; don't narrate it.** If text says "(1) … (2) … (3) …" or "first … then … then …", it
  should be an `lk-steps` block, or an `lk-sequence` if two or more parties exchange messages.
- **A concrete example for every concept**: real values (`192.0.2.44`, `GET /profile`, `56 ms`), not
  "some IP" or "a request". Put a worked example in a `> [!example]` callout.
- **Comparisons are tables** (X vs Y, pick-when / cost).
- **Lead with the point.** Under a concept heading, the page already shows the concept's `summary` from
  concepts.json as "In short", so don't repeat it. Go straight to the explanation.
- Keep citations on every claim `[S0001]`; the page turns them into small chips the reader can hide.
- Budget: about one visual per core concept, plus one for the main mechanism. Don't decorate: a visual
  that repeats the paragraph next to it adds reading, not understanding.

## Callouts

```markdown
> [!example] Optional title
> Body, any Markdown.
```

| Kind | Use for |
|---|---|
| `example` | A worked example with real values |
| `tip` | A rule of thumb ("if X, use Y") |
| `warning` | A limit or trap ("what this does NOT fix") |
| `misconception` | A common wrong belief and the correction; the title is the wrong belief in quotes |
| `interview` | How this comes up in an interview, and what a strong answer says |
| `tldr` | A short summary at the top of a long section |
| `question` | A self-check question (no answer given) |
| `note` | Anything else worth setting apart |

## Visual blocks

Fenced code blocks whose language is `lk-steps`, `lk-sequence` or `lk-arch`, with a JSON object inside.
Strings inside are plain text (step `body` allows Markdown). Run `LK read <slug> --check` after writing.
It reports JSON and schema errors with the field path, and `read` cannot be entered until it passes.

**Phases tie visuals together.** `phase` is any short key (`dns`, `tcp`, `db`). The same key gets the same
colour in every block on the page, and hovering a step highlights that phase in the diagrams. Use the same
keys for the same stage of a process everywhere in the doc.

### lk-steps: an ordered process (2–12 steps)

```lk-steps
{
  "title": "Loading https://www.example.com/profile",
  "steps": [
    {"title": "DNS: name → IP", "phase": "dns", "cost": "skipped when cached",
     "body": "The browser asks a **recursive resolver**…",
     "example": "www.example.com → 192.0.2.44", "cite": ["S0019"]},
    {"title": "TCP: open a connection", "phase": "tcp", "cost": "1 round trip",
     "body": "SYN → SYN-ACK → ACK.", "cite": ["S0014"]}
  ]
}
```

Fields per step: `title` (required), `body` (Markdown, 1–3 sentences), `example` (monospace; `\n` for
lines), `cost` (a short chip: time, round trips, complexity), `phase`, `cite` (source ids).

### lk-sequence: who talks to whom, in order (2–7 actors, ≤ 30 messages)

```lk-sequence
{
  "title": "First visit to an HTTPS page",
  "actors": [{"id": "b", "label": "Browser"}, {"id": "w", "label": "Web server"}, {"id": "db", "label": "Database"}],
  "phases": {"tcp": "TCP", "http": "HTTP", "db": "DB"},
  "messages": [
    {"from": "b", "to": "w", "label": "SYN / SYN-ACK / ACK", "note": "1 round trip", "phase": "tcp", "both": true},
    {"from": "b", "to": "w", "label": "GET /profile", "phase": "http"},
    {"over": "w", "label": "render template", "phase": "http"},
    {"from": "w", "to": "db", "label": "SELECT …", "phase": "db"},
    {"from": "db", "to": "w", "label": "rows", "phase": "db", "reply": true}
  ]
}
```

- `reply: true` draws a dashed return arrow; `both: true` a two-way exchange (a handshake);
  `{"over": actor}` a note box on one lifeline; `note` small italic text under the arrow.
- `phases` maps keys to band labels. Consecutive messages with the same phase share one coloured band.
- Labels wrap automatically, but keep them short (≤ 40 characters). Order the actors left to right in the
  order the request travels.

### lk-arch: a structure, or before → after (1–3 panels)

```lk-arch
{
  "title": "Single server → two tiers",
  "panels": [
    {"title": "Before", "flow": [
       {"box": "Users", "sub": "browser, mobile app", "kind": "client"},
       {"arrow": "HTTP"},
       {"box": "One server", "kind": "web", "items": ["web server", "app code", "database"]}],
     "pros": ["simplest to run"], "cons": ["box down = site down"]},
    {"title": "After", "flow": [ … ]}
  ]
}
```

- `flow` is a top-to-bottom chain: `{"box": name, "sub"?, "items"?, "kind"?}` and `{"arrow": label}`
  (the label may be empty) alternating.
- `kind` sets the colour: `client`, `edge` (DNS, CDN, load balancer), `web`, `app`, `data`, `cache`, `queue`,
  `external`, `plain`.
- Use 2 panels for before/after or option A/B, with `pros` / `cons` under each.

## Checklist before handing back

1. `LK read <slug> --check` → `ok: true`, and no warnings (long paragraphs, unknown citations, concepts
   without a `{#id}` heading, unknown callouts).
2. Every process in the doc is a steps or sequence block, not a numbered paragraph.
3. Every concept has a concrete example.
