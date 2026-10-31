---
name: lms-design-learning-path
description: >-
  Skill #4 for LMS Curriculum & Learning Path Design. Use this skill to run the
  O(log N) Binary Search Diagnostic Placement Quiz to find the learner's starting
  difficulty level, draft the node-based DAG learning path with a meaningful
  Capstone Project, and log token usage & DAG artifacts.
---

# LMS Skill #4: Design Learning Path (`lms-design-learning-path`)

## Role & Responsibility
Grounded in the aggregated sources from Skill #3 (either via direct Markdown files in `local_file` mode or RAG chunks in `cloud_rag` mode), you:
1. Map the subject into a **linearly increasing difficulty spectrum** ($1 \dots N$).
2. Run the **Binary Search Diagnostic Placement Quiz** ($\mathcal{O}(\log N)$ steps) with the user to pinpoint their exact `entryDifficultyLevel`.
3. Draft the **Directed Acyclic Graph (DAG)** of atomic learning nodes integrated with an overarching **Capstone Project**.
4. Log `tokenUsage` and emit the Mermaid DAG artifact (`.lms-state/artifacts/<subject-slug>/learning-path-dag.mmd`).

## Prerequisite: Skill Installation Step
Ensure the local skill environment is installed before running commands:
```bash
bash .agents/skills/lms-teaching-orchestrator/scripts/install.sh
```

## Deterministic Workflow Steps

### Step 1: Start the Binary Search Placement Session
Initialize binary search over difficulty tiers $1 \dots N$ (e.g., $N = 16$):

```bash
node .agents/skills/lms-design-learning-path/scripts/run.mjs placement-start <subject-slug> 16
```

### Step 2: Administer Midpoint Diagnostic Questions ($\mathcal{O}(\log N)$ Loop)
Check `currentMid` in the dashboard, ask the learner a diagnostic question at difficulty tier `currentMid`, and record whether they answered correctly along with `tokenUsage`:

```bash
node .agents/skills/lms-design-learning-path/scripts/run.mjs placement-step <subject-slug> '{
  "quizId": "place-tier-8",
  "answeredCorrectly": true,
  "tokenUsage": {
    "promptTokens": 220,
    "completionTokens": 80,
    "model": "gemini-2.5-flash"
  }
}'
```

Repeat until `placement.status` becomes `COMPLETED` (when `low > high`), which resolves `entryDifficultyLevel`.

### Step 3: Draft the Node-Based Learning Path & Capstone Project
Design the DAG of knowledge nodes. Nodes with `difficultyLevel < entryDifficultyLevel` are automatically marked `mastered_by_placement`:

```bash
node .agents/skills/lms-design-learning-path/scripts/run.mjs path-design <subject-slug> '{
  "capstoneProject": {
    "title": "Fault-Tolerant Raft KV Store in Go",
    "description": "Build a replicated key-value store surviving node failures.",
    "milestones": ["M1: RPC Harness", "M2: Leader Election", "M3: Log Replication"]
  },
  "nodes": [
    {
      "id": "node-01-rpc-basics",
      "title": "Go Concurrency & RPC Framing",
      "summary": "RPC message framing and mutex discipline.",
      "difficultyLevel": 5,
      "prerequisites": [],
      "sourceIds": ["user-go-rpc-notes"],
      "projectMilestone": "M1: RPC Harness"
    },
    {
      "id": "node-02-leader-election",
      "title": "Leader Election & Split Vote Recovery",
      "summary": "Implement RequestVote RPC and Pre-Vote.",
      "difficultyLevel": 9,
      "prerequisites": ["node-01-rpc-basics"],
      "sourceIds": ["res-raft-leader-election"],
      "projectMilestone": "M2: Leader Election"
    }
  ],
  "tokenUsage": {
    "promptTokens": 1400,
    "completionTokens": 560,
    "model": "gemini-2.5-pro"
  }
}'
```
