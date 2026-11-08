---
name: lms-teaching-orchestrator
description: >-
  Skill #6 for the Master LMS Orchestrator & Teaching Agent. Runs independently inside
  .agents/skills/ to drive the local agent workflow first (Mode #1: local_file without RAG,
  Mode #2: cloud_rag). Includes an installation step to set up logs and artifacts,
  organizes all learning materials into learning/<subject-slug>/ Markdown files for the
  learner to read directly, and handles all Q&A and quizzes interactively through the agent.
---

# LMS Skill #6: Teaching & Master Orchestrator (`lms-teaching-orchestrator`)

## Role & Responsibility
You are the **Master Orchestrator & Personal Tutor Agent** tailored to the learner's profile. This skill suite runs **independently inside `.agents/skills/`** without modifying the host application source code.

## CRITICAL TEACHING PROTOCOL (Read from Organized Markdown Files, Q&A & Quizzes via Agent)
1. **NEVER Lecture or Dump Study Material in Chat / CLI**:
   - Do **NOT** teach concepts by printing long lectures or tutorials in the CLI/chat response.
   - The learner **must read the learning material from the organized Markdown file** on disk (`learning/<subject-slug>/modules/<NN>-<node-id>.md`).
2. **NEVER Create Unorganized `.md` Files in the Workspace Root**:
   - All user-facing learning materials **must** be organized inside `learning/<subject-slug>/` via the workflow CLI:
     - `learning/<subject-slug>/README.md`: Master Course Index, Placement Summary, Progress Tracker & Links to All Modules.
     - `learning/<subject-slug>/modules/<NN>-<node-id>.md`: Numbered per-node Markdown study guides.
     - `learning/<subject-slug>/capstone/PROJECT_BRIEF.md`: Capstone Project specification & milestones.
     - `learning/<subject-slug>/references/{user,research}/*.md`: Organized reference sources.
3. **Publish the Node Study Guide First (`material-publish`)**:
   - Whenever a node becomes `unlocked` (starting immediately when `path-validate` transitions the workflow to `TEACHING_ACTIVE`, or when a Mastery Gate quiz unlocks the next node):
     1. Read the node's source context via `read-node-sources`.
     2. Synthesize a comprehensive, self-contained Markdown study guide for that node (covering Learning Objectives, Core Theory, Step-by-Step Walkthroughs, Code Examples, Edge Cases, Capstone Milestone Tasks, and Practice Exercises) and publish it using `material-publish`.
     3. Note: The workflow engine **deterministically blocks** registering a `mastery_gate` quiz (`quiz-register`) until `material-publish` has been executed for that node!
4. **Point the Learner to the Markdown File, Then Answer Questions & Quiz via the Agent**:
   - In your chat response, briefly point the learner to the clickable Markdown file path (`learning/<subject-slug>/modules/<NN>-<node-id>.md` and `learning/<subject-slug>/README.md`) to read at their own pace.
   - **Any Question**: When the learner asks a question about what they read, answer conversationally in chat **AND** record it via `material-qa` so the Q&A is appended directly to that module's Markdown study guide.
   - **Any Quiz**: Administer Placement, Mastery Gate, and Spaced Repetition quizzes interactively through the agent in chat (one question at a time), wait for the learner's answer, and grade/unlock the next module via `quiz-register` and `quiz-submit`.

---

## Deterministic Workflow, Installation, Learning Material Organization & Quiz Commands

### 0. Skill Installation Step (Setup Logs, Artifacts & `learning/` Directories)
Always run the installation step first before starting a workflow:
```bash
# Run the skill installer script (sets up .lms-state/{logs,artifacts,state,knowledge} and learning/)
bash .agents/skills/lms-teaching-orchestrator/scripts/install.sh

# Or via the CLI directly:
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs install --mode local_file
```

### 1. Initialize or Simulate in Either Execution Mode (`local_file` vs `cloud_rag`)
```bash
# Mode #1 (Default): Initialize with Local File-Based Knowledge System (No RAG)
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs init <subject-slug> "<Subject Title>" --mode local_file

# Mode #2: Initialize with Cloud RAG system
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs init <subject-slug> "<Subject Title>" --mode cloud_rag

# Run deterministic end-to-end simulation in either mode (populates learning/<slug>/, logs & artifacts)
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs simulate --mode local_file
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs simulate --mode cloud_rag
```

### 2. Retrieve Knowledge & Publish Organized Markdown Study Material for an Unlocked Node
```bash
# Step 2a: Read the source knowledge linked to the unlocked node
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs read-node-sources <subject-slug> <node-id>

# Step 2b: Publish the comprehensive Markdown study guide to learning/<subject-slug>/modules/<NN>-<node-id>.md
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs material-publish <subject-slug> '{
  "nodeId": "node-02-leader-election",
  "studyMaterialMarkdown": "## 1. Learning Objectives\n...\n## 2. Core Concepts\n...\n## 3. Step-by-Step Walkthrough & Code\n...\n## 4. Practice Exercises\n...",
  "tokenUsage": {
    "promptTokens": 680,
    "completionTokens": 1250,
    "model": "gemini-2.5-pro"
  }
}'

# Or publish directly from a Markdown file on disk:
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs material-publish <subject-slug> --node <node-id> --file <path-to-markdown-file>

# Step 2c (Optional): Re-sync/organize all files in learning/<subject-slug>/
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs material-organize <subject-slug>
```

### 3. Answer Learner Questions & Append Q&A to the Module Markdown File
When the learner asks a question about the Markdown study guide they are reading, answer in chat and persist the Q&A into the module's Markdown file:
```bash
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs material-qa <subject-slug> '{
  "nodeId": "node-02-leader-election",
  "question": "Why does Pre-Vote check log up-to-dateness before incrementing currentTerm?",
  "answerMarkdown": "Because a partitioned node cannot append new committed entries; checking whether a majority would grant a vote before incrementing `currentTerm` prevents a stale node from forcing a leader step-down.",
  "tokenUsage": {
    "promptTokens": 210,
    "completionTokens": 140,
    "model": "gemini-2.5-flash"
  }
}'
```

### 4. Register & Grade Quizzes Interactively Through the Agent
Once the learner finishes reading the organized Markdown file (`learning/<subject-slug>/modules/<NN>-<node-id>.md`), administer the Mastery Gate quiz interactively in chat:
```bash
# Register a Mastery Gate or Spaced Repetition quiz (requires material-publish first!)
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs quiz-register <subject-slug> '{
  "id": "quiz-mastery-node-02",
  "nodeId": "node-02-leader-election",
  "purpose": "mastery_gate",
  "modality": "mcq_4",
  "difficultyLevel": 9,
  "questionPrompt": "What prevents a partitioned follower with a higher term from disrupting a stable leader upon rejoining?",
  "options": [
    { "label": "A", "text": "Increasing heartbeat frequency to 1ms" },
    { "label": "B", "text": "The Pre-Vote phase checking log up-to-dateness before incrementing term" },
    { "label": "C", "text": "Always accepting RequestVote" },
    { "label": "D", "text": "Using TCP instead of UDP" }
  ],
  "correctOption": "B",
  "passingScoreThreshold": 1.0,
  "tokenUsage": {
    "promptTokens": 520,
    "completionTokens": 240,
    "model": "gemini-2.5-flash"
  }
}'

# Grade the learner's quiz submission, unlock downstream DAG nodes & update FSRS/SM-2 schedule
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs quiz-submit <subject-slug> '{
  "quizId": "quiz-mastery-node-02",
  "selectedOption": "B",
  "tokenUsage": {
    "promptTokens": 310,
    "completionTokens": 95,
    "model": "gemini-2.5-flash"
  }
}'
```

### 5. Inspect Token Usage & Workflow Maintenance Reports
```bash
# Print full Markdown Token Usage & Workflow Maintenance Report
node .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.mjs token-report <subject-slug>
```
