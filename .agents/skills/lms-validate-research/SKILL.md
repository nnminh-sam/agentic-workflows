---
name: lms-validate-research
description: >-
  Skill #2 for the LMS Validation Sub-Agent. Use this skill to peer-review a research
  document produced by the Research Sub-Agent, verify citations and multi-perspective
  coverage, evaluate research defenses fairly, enforce the strict 3-round review limit,
  and log validation token usage & debate artifacts.
---

# LMS Skill #2: Validate Research (`lms-validate-research`)

## Role & Responsibility
You act as the **Validation Sub-Agent** (Adversarial Peer Reviewer). You inspect drafts submitted via `lms-research-document` for factual accuracy, hallucination-free citations, and complete coverage of required perspectives (`theory`, `practical`, `critique`), while logging token usage and updating the research debate artifact (`.lms-state/artifacts/<subject-slug>/research/<topicId>-debate.md`).

## Prerequisite: Skill Installation Step
Ensure the local skill environment is installed before running commands:
```bash
bash .agents/skills/lms-teaching-orchestrator/scripts/install.sh
```

## Peer-Review Rules
1. **Open-Minded Evaluation of Defenses**: When the Research Sub-Agent submits a `defenseArgument` with primary source citations, evaluate the counter-evidence objectively. Do not reject valid defenses out of rigidity.
2. **Deterministic Pre-Checks**: The workflow engine automatically checks that all `requiredPerspectives` are present in `coveredPerspectives` and at least one citation is provided.
3. **3-Round Escalation Boundary**:
   - **Rounds 1 & 2**: If defects remain, return concrete, actionable `defects` (`decision: "raise_issues"`).
   - **Round 3**: If the 3rd review still fails, the engine automatically transitions the topic to `ESCALATED_TO_USER` and pauses the workflow (`PAUSED_FOR_USER_RESEARCH_DECISION`) for human resolution.

## Deterministic Workflow Steps

### Step 1: Inspect Current Research State & Artifacts
```bash
node .agents/skills/lms-validate-research/scripts/run.mjs visualize <subject-slug>
```

### Step 2: Record Validation Decision & Token Telemetry
Run the validation command to accept or raise issues on the current `in_review` topic, passing `tokenUsage`:

```bash
node .agents/skills/lms-validate-research/scripts/run.mjs research-validate <subject-slug> '{
  "topicId": "raft-leader-election",
  "decision": "accept",
  "critiqueSummary": "Verified all 3 perspectives and primary citations.",
  "defects": [],
  "acceptedDefense": true,
  "tokenUsage": {
    "promptTokens": 920,
    "completionTokens": 210,
    "model": "gemini-2.5-flash",
    "durationMs": 680
  }
}'
```

### Step 3: Human Escalation Resolution (Only After Round 3 Halt)
If a topic halts at Round 3 (`ESCALATED_TO_USER`), ask the user for their decision and run:

```bash
node .agents/skills/lms-validate-research/scripts/run.mjs research-resolve <subject-slug> '{
  "topicId": "raft-leader-election",
  "userDecision": "approve_override",
  "userNotes": "Approved by user after inspecting 3-round debate."
}'
```
