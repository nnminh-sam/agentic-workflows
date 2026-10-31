---
name: lms-validate-learning-path
description: >-
  Skill #5 for LMS Learning Path Validation. Use this skill to deterministically
  validate a drafted learning path DAG for acyclicity, monotonic difficulty progression,
  source grounding (local_file or cloud_rag), and capstone project continuity while
  logging validation token usage and organizing the learning/<subject-slug>/ workspace.
---

# LMS Skill #5: Learning Path Validation (`lms-validate-learning-path`)

## Role & Responsibility
Before the learner starts studying, you verify the structural and pedagogical integrity of the learning path drafted by Skill #4 (`lms-design-learning-path`), write the validation artifact to `.lms-state/artifacts/<subject-slug>/path-validation.json`, and organize the `learning/<subject-slug>/` directory (`README.md`, `capstone/PROJECT_BRIEF.md`, `modules/`, `references/`).

## Prerequisite: Skill Installation Step
Ensure the local skill environment is installed before running commands:
```bash
bash .agents/skills/lms-teaching-orchestrator/scripts/install.sh
```

## Deterministic Validation Rules Checked by the Script
1. **Non-Empty Graph**: At least one learning node exists.
2. **Prerequisite Existence**: Every node ID in `prerequisites` exists in the graph.
3. **DAG Acyclicity**: 3-color DFS cycle detection guarantees zero circular dependencies.
4. **Difficulty Monotonicity**: For every edge $Node_A \to Node_B$, $Node_A.\text{difficultyLevel} \le Node_B.\text{difficultyLevel}$.
5. **Source Grounding**: Every node links to at least one valid `sourceId` in `state.sources` (backed by local Markdown files in `local_file` mode or RAG chunks in `cloud_rag` mode).
6. **Capstone Milestone Coverage**: If a `capstoneProject` is defined, nodes map to project milestones.

## Deterministic Workflow Steps

### Step 1: Run Deterministic + Pedagogical Path Validation
```bash
node .agents/skills/lms-validate-learning-path/scripts/run.mjs path-validate <subject-slug> '{
  "decision": "accept",
  "pedagogicalCritiques": [],
  "tokenUsage": {
    "promptTokens": 640,
    "completionTokens": 120,
    "model": "gemini-2.5-flash"
  }
}'
```

### Step 2: Handle Validation Outcome & Transition to Organized Markdown Study Guides
- **If Valid (`status = ACCEPTED`)**:
  - The engine automatically transitions the workflow to `TEACHING_ACTIVE`, unlocks the entry node(s) at `entryDifficultyLevel`, and creates the organized `learning/<subject-slug>/` workspace (`README.md`, `capstone/PROJECT_BRIEF.md`, `modules/<NN>-<node-id>.md`, `references/`).
  - **IMMEDIATE NEXT ACTION (Skill #6)**: Do **NOT** lecture in chat! Immediately publish the comprehensive Markdown study guide for the unlocked entry node using `material-publish` into `learning/<subject-slug>/modules/<NN>-<node-id>.md`, and direct the learner to open and read that Markdown file.
- **If Invalid (`status = REVISION_REQUESTED`)**:
  - Call Skill #4 (`lms-design-learning-path`) to fix the reported `structuralErrors` or `pedagogicalCritiques` (up to 3 rounds before escalating to the user).
