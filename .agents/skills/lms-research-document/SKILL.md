---
name: lms-research-document
description: >-
  Skill #1 for the LMS Research Sub-Agent. Use this skill when researching a learning
  subject across multiple perspectives (theory, practical, critique), synthesizing
  findings into a structured Markdown research draft, defending/revising research
  during the 3-round peer-review validation loop, and logging token usage & debate artifacts.
---

# LMS Skill #1: Research Document (`lms-research-document`)

## Role & Responsibility
You act as the **Research Sub-Agent** spawned by the Orchestrator. Your mandate is to gather multi-perspective knowledge on a learning topic, write a well-cited Markdown research document, respond to peer-review critiques from the Validation Sub-Agent (`lms-validate-research`), and record token telemetry & research debate artifacts in `.lms-state/`.

## Prerequisite: Skill Installation Step
Ensure the local skill environment (`.lms-state/logs/`, `.lms-state/artifacts/`, `.lms-state/state/`, `.lms-state/knowledge/`) is installed before running commands:
```bash
bash .agents/skills/lms-teaching-orchestrator/scripts/install.sh
```

## Dual Execution Modes (`local_file` vs `cloud_rag`)
- **Mode #1 (`local_file`, Default)**: Your Markdown document will be saved directly as a local `.md` file on disk (`.lms-state/knowledge/lms/<slug>/sources/research/<topicId>.md`) and read directly without RAG.
- **Mode #2 (`cloud_rag`)**: Your Markdown headings (`#`, `##`, `###`) will be split into RAG chunks and indexed into the cloud vector store by Skill #3.

## Multi-Perspective Requirements
Every research document MUST cover the required perspectives for the topic:
1. **`theory`**: Formal definitions, mathematical/algorithmic foundations, core invariants.
2. **`practical`**: Real-world implementation patterns, code idioms, configuration, and tooling.
3. **`critique`**: Edge cases, failure modes, performance bottlenecks, trade-offs, and common pitfalls.

## Deterministic Workflow Steps

### Step 1: Submit Draft or Defense with Token Telemetry
Submit your initial draft—or your revised draft + `defenseArgument` if responding to validation defects—using this skill's standalone script. This automatically writes both the token audit log (`.lms-state/logs/<subject-slug>-token-usage.jsonl`) and the peer-review debate artifact (`.lms-state/artifacts/<subject-slug>/research/<topicId>-debate.md`):

```bash
node .agents/skills/lms-research-document/scripts/run.mjs research-submit <subject-slug> '{
  "topicId": "raft-leader-election",
  "title": "Raft Leader Election & Split Vote Mechanics",
  "requiredPerspectives": ["theory", "practical", "critique"],
  "coveredPerspectives": ["theory", "practical", "critique"],
  "draftMarkdown": "# Theory\n...\n## Practical\n...\n## Critique & Edge Cases\n...",
  "citations": ["https://raft.github.io/raft.pdf"],
  "defenseArgument": "Optional defense explaining why a claim is valid with primary citations.",
  "tokenUsage": {
    "promptTokens": 850,
    "completionTokens": 620,
    "model": "gemini-2.5-pro",
    "durationMs": 1420
  }
}'
```

### Step 2: Respect the 3-Round Peer-Review Boundary
- If the Validation Sub-Agent raises issues in **Round 1 or Round 2**, either fix the defects in `draftMarkdown` or provide a rigorous `defenseArgument` backed by primary citations.
- If the topic reaches **Round 3** and is still rejected (`status = ESCALATED_TO_USER`), the script deterministically blocks a 4th submission. **Stop immediately** and wait for the human user's decision.
