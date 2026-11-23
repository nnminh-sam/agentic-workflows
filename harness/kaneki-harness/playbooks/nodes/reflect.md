# reflect (learning, last node)

**Goal:** a cited account of what went wrong on this ticket and, only where it would have prevented it, proposals
the user may approve later. You change nothing: no rule, profile, prompt, graph, gate or code (decision D10).
Ship never waits for this node.

**When it runs** (profile `learning.reflect`): `on-signal` (default) dispatches you only when the signals file has
new signals; with none the driver closes reflect itself (`no-signal` gate note). `always` dispatches you every time;
`off` removes the node. Every PR review round restarts it, and only signals new since the last reflection count.

Read the signals file in your inputs (the same JSON as `sdlc reflect-signals --ticket <id>`): loop-backs, attempts
per node, invalidations with their cause, lane changes, host switches, tier escalations, failing unit verdicts with
their class, non-G0 gate firings, HIGH questions the user answered, accepted PR findings and the node triage
reopened, citation warnings. Each item has an `id`, a `ref` (`decisions.jsonl#<line>` or `<file>:<line>`) and `new`.
Open the referenced artifacts in the ticket folder to find the cause; do not re-review the code.

Write in the ticket folder only:

1. `reflection.md`
   - `## Signals`: each new signal you looked at, one line, with its `ref`.
   - `## Causes`: per signal, the earliest point it could have been caught (which node, which input was missing
     or misread), OBSERVED with a ref or ASSUMED. "Noise" (flaky tool, user changed their mind) is a valid cause.
   - `## Proposals`: the ids you propose and why, or `None` with the reason.
2. `proposals.json`: `{"schemaVersion": 1, "ticket": "<id>", "proposals": [...]}`; each proposal follows
   `contracts/proposal.schema.json`:

   | Field | Content |
   | --- | --- |
   | `id` | lowercase slug, unique in the file (`code-review-null-input`) |
   | `type` | `lesson` (guidance for the scoped nodes' packets), `rule` (a rulepack rule), `profile` (a profile setting), `harness` (for the harness maintainers; never applied by the harness) |
   | `title`, `body` | what to do differently, in prose |
   | `evidence` | at least one `{ticket, ref}` from this ticket; `ref` is `decisions.jsonl#<line>` or `path:line` |
   | `scope` | `nodes` (graph node ids, at least one), `stacks` (empty = all), `paths` (repository-relative globs, empty = all) |
   | `metric`, `baseline` | what it should improve and its value on this ticket |
   | `status` | `proposed` |

   An empty `proposals` list is a good outcome. Propose only what is general: a one-off mistake is a cause, not a lesson.

The gate refuses a proposals file that does not match the schema, an unknown `type`, unknown nodes or stacks,
absolute or `..` scope paths, evidence that does not resolve, and prose (`title`, `body`, `metric`, `baseline`)
that contains code blocks, commands in backticks, tool names, URLs, addresses, IPs, network shares, absolute paths,
or paths outside `scope.paths` (files in the ticket folder are allowed as evidence). These checks are heuristic:
write plain prose and keep paths inside the scope you declare.

Boundary **controller**: the controller closes it. Approving proposals is a separate step for the user
(`sdlc learn review`, or `sdlc learn apply|reject --actor user`): an applied `lesson` reaches the packets of its scope
nodes, a `rule` joins the learned rulepack, a `profile` proposal becomes a suggestion file only, and a `harness`
proposal is never applied (it goes to the harness owner).
