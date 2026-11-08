---
name: lms-aggregate-sources
description: >-
  Skill #3 for LMS Source Aggregation. Supports Mode #1 (local_file: saving & reading
  local Markdown files directly without RAG) and Mode #2 (cloud_rag: chunking & indexing
  sources for cloud RAG), while logging token usage and updating workflow artifacts.
---

# LMS Skill #3: Aggregate into Sources (`lms-aggregate-sources`)

## Role & Responsibility
You normalize and aggregate both knowledge tracks into structured Markdown sources (`lms/<subject-slug>/sources/...`):
- **Track 1 (Bring Your Own Sources - BYOS)**: User-supplied `.pdf`, `.docx`, `.xlsx`/sheets, or `.md` files.
- **Track 2 (Validated Agent Research)**: Research topics that have achieved `validated` or `user_approved` status.

## Prerequisite: Skill Installation Step
Ensure the local skill environment (`.lms-state/{logs,artifacts,state,knowledge}`) is installed before running commands:
```bash
bash .agents/skills/lms-teaching-orchestrator/scripts/install.sh
```

## Dual Execution Modes (`local_file` vs `cloud_rag`)
- **Mode #1 (`executionMode === "local_file"`, Default Local Workflow)**:
  - **No RAG indexing is performed** (`state.ragChunks` remains empty).
  - Writes clean `.md` files directly to disk under `.lms-state/knowledge/lms/<subject-slug>/sources/...` so downstream skills read directly from local files.
- **Mode #2 (`executionMode === "cloud_rag"`)**:
  - Splits Markdown by `#`, `##`, `###` headings into deterministic `RagChunk` records (`state.ragChunks`) ready for cloud `pgvector` indexing.

## Deterministic Workflow Steps

### Step 1: Ingest User-Provided Sources (Track 1 - BYOS)
Parse the user's document or spreadsheet into clean Markdown and register it along with `tokenUsage`:

```bash
node .agents/skills/lms-aggregate-sources/scripts/run.mjs source-ingest <subject-slug> '{
  "sourceId": "user-go-rpc-notes",
  "title": "User Notes on Go net/rpc",
  "sourceType": "markdown",
  "originalUri": "local://notes/go-rpc.md",
  "normalizedMarkdown": "# Go RPC Basics\nContent...\n## Concurrency\nMutex discipline...",
  "perspective": "practical",
  "tokenUsage": {
    "promptTokens": 310,
    "completionTokens": 90,
    "model": "gemini-2.5-flash"
  }
}'
```

### Step 2: Aggregate Validated Research Documents (Track 2)
Once all active research topics are `validated` (or `user_approved`), aggregate them into canonical sources (local `.md` files in `local_file` mode, or RAG chunks in `cloud_rag` mode):

```bash
node .agents/skills/lms-aggregate-sources/scripts/run.mjs source-aggregate <subject-slug>
```
