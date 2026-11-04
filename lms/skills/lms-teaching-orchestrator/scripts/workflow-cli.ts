#!/usr/bin/env node
// ============================================================================
// LMS Standalone Skill CLI & Learning Material Organizer
// Location: .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.ts
// ============================================================================
import fs from "node:fs";
import path from "node:path";
import {
  type ExecutionMode,
  type LmsWorkflowState,
  createInitialWorkflowState,
  submitResearchDraft,
  validateResearchDraft,
  resolveResearchEscalation,
  ingestUserSource,
  aggregateValidatedResearchIntoSources,
  retrieveKnowledgeForNode,
  startPlacementQuiz,
  submitPlacementQuizStep,
  designLearningPath,
  validateLearningPath,
  publishNodeLearningMaterial,
  recordNodeQaInteraction,
  registerQuiz,
  submitNodeQuizAttempt,
  recordTokenUsage,
  generateCourseIndexMarkdown,
  generateNodeModuleMarkdown,
  generateCapstoneBriefMarkdown,
  generateResearchDebateArtifact,
  generateTokenMaintenanceMarkdownReport,
  generateWorkflowMermaid,
  generateLearningPathDagMermaid,
  renderAsciiDashboard,
} from "./workflow.ts";

export interface SkillEnvironmentConfig {
  skillSuiteVersion: string;
  installedAt: string;
  defaultExecutionMode: ExecutionMode;
  rootDir: string;
  directories: {
    stateDir: string;
    logsDir: string;
    artifactsDir: string;
    knowledgeDir: string;
    learningDir: string;
  };
}

export function getStateRootDir(baseCwd: string = process.cwd()): string {
  return path.resolve(baseCwd, ".lms-state");
}

/**
 * Installation Step for the LMS Skill Suite:
 * Sets up the self-contained `.lms-state/` workspace AND the user-facing `learning/` directory:
 * - `.lms-state/.gitignore` (so skill logs/artifacts never dirty host git status)
 * - `.lms-state/config.json`
 * - `.lms-state/state/`
 * - `.lms-state/logs/` (token telemetry JSONL, maintenance reports, workflow events log)
 * - `.lms-state/artifacts/` (Mermaid graphs, research debates, validation reports, dashboards)
 * - `.lms-state/knowledge/` (internal knowledge mirror)
 * - `learning/` (organized user-facing Markdown course books, modules, capstone & references)
 */
export function installSkillEnvironment(
  options?: {
    baseCwd?: string;
    defaultMode?: ExecutionMode;
    nowIso?: string;
  }
): SkillEnvironmentConfig {
  const baseCwd = options?.baseCwd ?? process.cwd();
  const now = options?.nowIso ?? new Date().toISOString();
  const defaultMode: ExecutionMode = options?.defaultMode ?? "local_file";

  const rootDir = getStateRootDir(baseCwd);
  const stateDir = path.join(rootDir, "state");
  const logsDir = path.join(rootDir, "logs");
  const artifactsDir = path.join(rootDir, "artifacts");
  const knowledgeDir = path.join(rootDir, "knowledge");
  const learningDir = path.resolve(baseCwd, "learning");

  for (const dir of [rootDir, stateDir, logsDir, artifactsDir, knowledgeDir, learningDir]) {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  }

  // Create self-contained .gitignore inside .lms-state so host repo git status remains clean
  const gitignorePath = path.join(rootDir, ".gitignore");
  fs.writeFileSync(gitignorePath, "*\n", "utf-8");

  // Initialize workflow-events.log if not present
  const eventLogPath = path.join(logsDir, "workflow-events.log");
  const installEventLine = `[${now}] [INSTALL] LMS 6-Skill environment initialized (defaultMode=${defaultMode}, root=${rootDir}, learningDir=${learningDir})\n`;
  fs.appendFileSync(eventLogPath, installEventLine, "utf-8");

  const config: SkillEnvironmentConfig = {
    skillSuiteVersion: "1.1.0",
    installedAt: now,
    defaultExecutionMode: defaultMode,
    rootDir,
    directories: {
      stateDir,
      logsDir,
      artifactsDir,
      knowledgeDir,
      learningDir,
    },
  };

  const configPath = path.join(rootDir, "config.json");
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2), "utf-8");

  return config;
}

export function ensureSkillEnvironmentInstalled(baseCwd: string = process.cwd()): SkillEnvironmentConfig {
  const configPath = path.join(getStateRootDir(baseCwd), "config.json");
  if (!fs.existsSync(configPath)) {
    return installSkillEnvironment({ baseCwd });
  }
  const parsed = JSON.parse(fs.readFileSync(configPath, "utf-8")) as SkillEnvironmentConfig;
  if (!parsed.directories.learningDir) {
    parsed.directories.learningDir = path.resolve(baseCwd, "learning");
  }
  return parsed;
}

export function getStateFilePath(slug: string, baseCwd: string = process.cwd()): string {
  const env = ensureSkillEnvironmentInstalled(baseCwd);
  return path.join(env.directories.stateDir, `${slug}.json`);
}

/**
 * Normalizes legacy state objects loaded from disk so new fields (`moduleNumber`,
 * `studyGuidePath`, `materialPublished`, `qaLog`, `learningOutDir`, etc.) are always populated.
 */
function normalizeLoadedState(raw: LmsWorkflowState): LmsWorkflowState {
  const learningOutDir = raw.artifacts?.learningOutDir ?? `learning/${raw.subjectSlug}`;
  const artifacts = {
    ...raw.artifacts,
    learningOutDir,
    courseIndexPath: raw.artifacts?.courseIndexPath ?? `${learningOutDir}/README.md`,
    capstoneBriefPath: raw.artifacts?.capstoneBriefPath ?? `${learningOutDir}/capstone/PROJECT_BRIEF.md`,
  };

  const sortedNodes = Object.values(raw.nodes ?? {}).sort(
    (a, b) => a.difficultyLevel - b.difficultyLevel
  );
  const normalizedNodes: LmsWorkflowState["nodes"] = {};
  sortedNodes.forEach((n, idx) => {
    const modNum = n.moduleNumber ?? idx + 1;
    const padded = String(modNum).padStart(2, "0");
    normalizedNodes[n.id] = {
      ...n,
      moduleNumber: modNum,
      summary: n.summary ?? n.title,
      studyGuidePath: n.studyGuidePath ?? `${learningOutDir}/modules/${padded}-${n.id}.md`,
      materialPublished:
        typeof n.materialPublished === "boolean"
          ? n.materialPublished
          : n.status === "mastered_by_placement" || Boolean(n.studyMaterialMarkdown),
      qaLog: n.qaLog ?? [],
    };
  });

  const normalizedSources: LmsWorkflowState["sources"] = {};
  for (const [sid, s] of Object.entries(raw.sources ?? {})) {
    const sub = s.track === "byos" ? "user" : "research";
    normalizedSources[sid] = {
      ...s,
      organizedRefPath:
        s.organizedRefPath ??
        `${learningOutDir}/references/${sub}/${s.id.replace(/^res-/, "")}.md`,
    };
  }

  return {
    ...raw,
    artifacts,
    nodes: normalizedNodes,
    sources: normalizedSources,
  };
}

export function loadState(slug: string, baseCwd: string = process.cwd()): LmsWorkflowState {
  const file = getStateFilePath(slug, baseCwd);
  const legacyFile = path.join(getStateRootDir(baseCwd), `${slug}.json`);
  if (fs.existsSync(file)) {
    return normalizeLoadedState(JSON.parse(fs.readFileSync(file, "utf-8")) as LmsWorkflowState);
  }
  if (fs.existsSync(legacyFile)) {
    return normalizeLoadedState(JSON.parse(fs.readFileSync(legacyFile, "utf-8")) as LmsWorkflowState);
  }
  throw new Error(
    `State file not found for subject "${slug}" at ${file}. Run "install" and "init <slug> <title>" first.`
  );
}

/**
 * Organizes all user-facing learning materials under `learning/<subject-slug>/`:
 * - `learning/<slug>/README.md` (Master Course Index & Progress Tracker)
 * - `learning/<slug>/capstone/PROJECT_BRIEF.md` (Capstone Specification & Milestones)
 * - `learning/<slug>/modules/<NN>-<nodeId>.md` (Per-Node Markdown Study Guides + Learner Q&A Log)
 * - `learning/<slug>/references/{user,research}/*.md` (Curated Reference Sources)
 */
export function organizeLearningMaterialsOnDisk(
  state: LmsWorkflowState,
  baseCwd: string = process.cwd()
): void {
  const courseDir = path.resolve(baseCwd, state.artifacts.learningOutDir);
  const modulesDir = path.join(courseDir, "modules");
  const capstoneDir = path.join(courseDir, "capstone");
  const refsUserDir = path.join(courseDir, "references", "user");
  const refsResearchDir = path.join(courseDir, "references", "research");

  for (const dir of [courseDir, modulesDir, capstoneDir, refsUserDir, refsResearchDir]) {
    fs.mkdirSync(dir, { recursive: true });
  }

  // 1. Write Master Course Index (`learning/<slug>/README.md`)
  const indexPath = path.resolve(baseCwd, state.artifacts.courseIndexPath);
  fs.writeFileSync(indexPath, generateCourseIndexMarkdown(state), "utf-8");

  // 2. Write Capstone Project Brief (`learning/<slug>/capstone/PROJECT_BRIEF.md`)
  const capstonePath = path.resolve(baseCwd, state.artifacts.capstoneBriefPath);
  fs.writeFileSync(capstonePath, generateCapstoneBriefMarkdown(state), "utf-8");

  // 3. Write Reference Sources (`learning/<slug>/references/...`)
  for (const src of Object.values(state.sources)) {
    const subFolder = src.track === "byos" ? "user" : "research";
    const cleanId = src.id.replace(/^res-/, "");
    const relRefPath =
      src.organizedRefPath ?? `learning/${state.subjectSlug}/references/${subFolder}/${cleanId}.md`;
    const absRefPath = path.resolve(baseCwd, relRefPath);
    fs.mkdirSync(path.dirname(absRefPath), { recursive: true });
    fs.writeFileSync(absRefPath, src.contentMarkdown, "utf-8");
  }

  // 4. Write Organized Module Study Guides (`learning/<slug>/modules/<NN>-<nodeId>.md`)
  for (const node of Object.values(state.nodes)) {
    const absStudyGuidePath = path.resolve(baseCwd, node.studyGuidePath);
    fs.mkdirSync(path.dirname(absStudyGuidePath), { recursive: true });
    const fullModuleMarkdown = generateNodeModuleMarkdown(state, node);
    fs.writeFileSync(absStudyGuidePath, fullModuleMarkdown, "utf-8");
  }
}

/**
 * Persists workflow state AND writes:
 * 1. State JSON (`.lms-state/state/<slug>.json` & `.lms-state/<slug>.json`)
 * 2. Logs (`.lms-state/logs/`):
 *    - `<slug>-token-usage.jsonl`
 *    - `<slug>-maintenance.md`
 *    - `workflow-events.log`
 * 3. Artifacts (`.lms-state/artifacts/<slug>/`):
 *    - `dashboard.txt`
 *    - `workflow-graph.mmd`
 *    - `learning-path-dag.mmd`
 *    - `path-validation.json`
 *    - `research/<topicId>-debate.md`
 * 4. Organized User-Facing Learning Materials (`learning/<slug>/`):
 *    - `README.md`, `capstone/PROJECT_BRIEF.md`, `modules/<NN>-<nodeId>.md`, `references/...`
 * 5. Internal Knowledge Mirror (`.lms-state/knowledge/lms/<slug>/...`)
 */
export function saveState( rawState: LmsWorkflowState, baseCwd: string = process.cwd()): void {
  const env = ensureSkillEnvironmentInstalled(baseCwd);
  const state = normalizeLoadedState(rawState);

  // 1. State files
  const stateFile = getStateFilePath(state.subjectSlug, baseCwd);
  const rootAliasFile = path.join(env.rootDir, `${state.subjectSlug}.json`);
  const serialized = JSON.stringify(state, null, 2);
  fs.writeFileSync(stateFile, serialized, "utf-8");
  fs.writeFileSync(rootAliasFile, serialized, "utf-8");

  // 2. Logs (.lms-state/logs/)
  const jsonlPath = path.resolve(baseCwd, state.tokenTelemetry.logFilePath);
  fs.mkdirSync(path.dirname(jsonlPath), { recursive: true });
  const jsonlContent =
    state.tokenTelemetry.entries.map((e) => JSON.stringify(e)).join("\n") +
    (state.tokenTelemetry.entries.length > 0 ? "\n" : "");
  fs.writeFileSync(jsonlPath, jsonlContent, "utf-8");

  const reportPath = path.resolve(baseCwd, state.tokenTelemetry.maintenanceReportPath);
  fs.writeFileSync(reportPath, generateTokenMaintenanceMarkdownReport(state), "utf-8");

  const latestEntry = state.tokenTelemetry.entries[state.tokenTelemetry.entries.length - 1];
  const eventLogPath = path.resolve(baseCwd, state.tokenTelemetry.eventLogPath);
  const eventSummary = latestEntry
    ? `[${state.updatedAt}] [${state.subjectSlug}] [mode=${state.executionMode}] [stage=${state.stage}] skill=${latestEntry.skill} task=${latestEntry.taskName} tokens=${latestEntry.totalTokens}\n`
    : `[${state.updatedAt}] [${state.subjectSlug}] [mode=${state.executionMode}] [stage=${state.stage}] state saved\n`;
  fs.appendFileSync(eventLogPath, eventSummary, "utf-8");

  // 3. Artifacts (.lms-state/artifacts/<slug>/)
  const subjectArtifactsDir = path.resolve(baseCwd, state.artifacts.artifactsRootDir);
  const researchArtifactsDir = path.join(subjectArtifactsDir, "research");
  fs.mkdirSync(researchArtifactsDir, { recursive: true });

  fs.writeFileSync(
    path.resolve(baseCwd, state.artifacts.dashboardPath),
    renderAsciiDashboard(state) + "\n",
    "utf-8"
  );
  fs.writeFileSync(
    path.resolve(baseCwd, state.artifacts.workflowMermaidPath),
    generateWorkflowMermaid(state) + "\n",
    "utf-8"
  );
  fs.writeFileSync(
    path.resolve(baseCwd, state.artifacts.dagMermaidPath),
    generateLearningPathDagMermaid(state) + "\n",
    "utf-8"
  );
  fs.writeFileSync(
    path.resolve(baseCwd, state.artifacts.pathValidationPath),
    JSON.stringify(state.pathValidation, null, 2) + "\n",
    "utf-8"
  );

  for (const topic of Object.values(state.researchTopics)) {
    const relDebatePath =
      state.artifacts.researchDebatePaths[topic.id] ??
      `${state.artifacts.artifactsRootDir}/research/${topic.id}-debate.md`;
    const absDebatePath = path.resolve(baseCwd, relDebatePath);
    fs.mkdirSync(path.dirname(absDebatePath), { recursive: true });
    fs.writeFileSync(absDebatePath, generateResearchDebateArtifact(topic), "utf-8");
  }

  // 4. Organize User-Facing Learning Material (`learning/<slug>/`)
  organizeLearningMaterialsOnDisk(state, baseCwd);

  // 5. In "local_file" mode, keep `.lms-state/knowledge/lms/<slug>/` mirror updated
  if (state.executionMode === "local_file") {
    for (const src of Object.values(state.sources)) {
      const absLocalPath = path.join(env.directories.knowledgeDir, src.localFilePath);
      fs.mkdirSync(path.dirname(absLocalPath), { recursive: true });
      fs.writeFileSync(absLocalPath, src.contentMarkdown, "utf-8");
    }
    for (const node of Object.values(state.nodes)) {
      const absNodePath = path.join(env.directories.knowledgeDir, node.localFilePath);
      fs.mkdirSync(path.dirname(absNodePath), { recursive: true });
      fs.writeFileSync(absNodePath, generateNodeModuleMarkdown(state, node), "utf-8");
    }
  }
}

/**
 * Runs a complete deterministic end-to-end simulation of all 6 skills in either
 * "local_file" (default local workflow) or "cloud_rag" mode, including publishing
 * the node's Markdown study guide and recording a learner Q&A before taking the Mastery Gate quiz.
 */
export function runDeterministicSimulation(
  executionMode: ExecutionMode = "local_file",
  baseCwd: string = process.cwd()
): {
  config: SkillEnvironmentConfig;
  state: LmsWorkflowState;
  dashboard: string;
  tokenReport: string;
  workflowMermaid: string;
  dagMermaid: string;
} {
  const t0 = "2026-10-31T08:00:00.000Z";
  const config = installSkillEnvironment({ baseCwd, defaultMode: executionMode, nowIso: t0 });

  // 0. Init Subject with explicit executionMode flag ("local_file" | "cloud_rag")
  let state = createInitialWorkflowState({
    subjectSlug: "raft-consensus-go",
    subjectTitle: "Distributed Consensus & Raft in Go",
    executionMode,
    learnerMode: "code_first",
    learnerPace: "intensive",
    totalDifficultyTiers: 16,
    nowIso: t0,
  });

  // Skill #1: Research Sub-Agent submits Round 1 draft
  state = submitResearchDraft(state, {
    topicId: "raft-leader-election",
    title: "Raft Leader Election & Split Vote Mechanics",
    requiredPerspectives: ["theory", "practical", "critique"],
    coveredPerspectives: ["theory", "practical"],
    draftMarkdown:
      "# Raft Leader Election\nRaft uses randomized election timeouts...\n## Practical Implementation\nIn Go, use time.Timer with jitter...",
    citations: ["https://raft.github.io/raft.pdf"],
    tokenUsage: {
      promptTokens: 850,
      completionTokens: 620,
      model: "gemini-2.5-pro",
      durationMs: 1420,
    },
    nowIso: t0,
  });

  // Skill #2: Validation Sub-Agent raises issues in Round 1
  state = validateResearchDraft(state, {
    topicId: "raft-leader-election",
    decision: "raise_issues",
    critiqueSummary: "Missing critique/edge-case perspective on network partitions and Pre-Vote extension.",
    defects: ["Add Pre-Vote disruption analysis under asymmetric network partitions."],
    tokenUsage: {
      promptTokens: 920,
      completionTokens: 210,
      model: "gemini-2.5-flash",
      durationMs: 680,
    },
    nowIso: t0,
  });

  // Skill #1: Research Sub-Agent revises and defends in Round 2
  state = submitResearchDraft(state, {
    topicId: "raft-leader-election",
    title: "Raft Leader Election & Split Vote Mechanics",
    requiredPerspectives: ["theory", "practical", "critique"],
    coveredPerspectives: ["theory", "practical", "critique"],
    draftMarkdown:
      "# Raft Leader Election\nRaft uses randomized election timeouts (150-300ms).\n## Practical Implementation\nIn Go, reset election timer on valid AppendEntries RPC.\n## Critique & Pre-Vote Edge Cases\nPartitioned nodes increment terms and disrupt cluster upon rejoin unless Pre-Vote (Section 9.6) is implemented.",
    citations: [
      "https://raft.github.io/raft.pdf",
      "https://decentralizedthoughts.github.io/2020-12-12-raft-liveness-full-omission/",
    ],
    defenseArgument: "Added Pre-Vote disruption analysis and cited Ongaro dissertation Section 9.6.",
    tokenUsage: {
      promptTokens: 1150,
      completionTokens: 780,
      model: "gemini-2.5-pro",
      durationMs: 1650,
    },
    nowIso: t0,
  });

  // Skill #2: Validation Sub-Agent accepts in Round 2
  state = validateResearchDraft(state, {
    topicId: "raft-leader-election",
    decision: "accept",
    critiqueSummary: "All 3 perspectives verified with primary sources.",
    acceptedDefense: true,
    tokenUsage: {
      promptTokens: 1280,
      completionTokens: 150,
      model: "gemini-2.5-flash",
      durationMs: 540,
    },
    nowIso: t0,
  });

  // Skill #3: Ingest Track 1 (BYOS) + Aggregate Track 2 (Validated Research)
  state = ingestUserSource(state, {
    sourceId: "user-go-rpc-notes",
    title: "User Notes on Go net/rpc & Context Cancellation",
    sourceType: "markdown",
    originalUri: "local://notes/go-rpc.md",
    normalizedMarkdown:
      "# Go RPC Basics\nUsing net/rpc with context deadlines.\n## Concurrency\nMutex locking discipline in state machines.",
    perspective: "practical",
    tokenUsage: {
      promptTokens: 310,
      completionTokens: 90,
      model: "gemini-2.5-flash",
    },
    nowIso: t0,
  });

  state = aggregateValidatedResearchIntoSources(state, t0, {
    promptTokens: 420,
    completionTokens: 110,
    model: "gemini-2.5-flash",
  });

  // Skill #4a: Binary Search Placement Quiz across Tiers 1..16
  state = startPlacementQuiz(state, 16, t0);
  state = submitPlacementQuizStep(state, {
    quizId: "place-tier-8",
    answeredCorrectly: true,
    tokenUsage: { promptTokens: 220, completionTokens: 80, model: "gemini-2.5-flash" },
    nowIso: t0,
  });
  state = submitPlacementQuizStep(state, {
    quizId: "place-tier-12",
    answeredCorrectly: false,
    tokenUsage: { promptTokens: 240, completionTokens: 85, model: "gemini-2.5-flash" },
    nowIso: t0,
  });
  state = submitPlacementQuizStep(state, {
    quizId: "place-tier-10",
    answeredCorrectly: false,
    tokenUsage: { promptTokens: 230, completionTokens: 75, model: "gemini-2.5-flash" },
    nowIso: t0,
  });
  state = submitPlacementQuizStep(state, {
    quizId: "place-tier-9",
    answeredCorrectly: false,
    tokenUsage: { promptTokens: 225, completionTokens: 70, model: "gemini-2.5-flash" },
    nowIso: t0,
  });

  // Skill #4b: Design Learning Path DAG (with Capstone Project)
  state = designLearningPath(state, {
    capstoneProject: {
      title: "Fault-Tolerant Raft KV Store in Go",
      description: "Build a linearizable, replicated key-value store surviving minority node failures.",
      milestones: [
        "M1: RPC Harness & State Structs",
        "M2: Leader Election & Pre-Vote",
        "M3: Log Replication & Commit Index",
      ],
    },
    nodes: [
      {
        id: "node-01-rpc-basics",
        title: "Go Concurrency & RPC Framing",
        summary: "Foundational RPC message framing and mutex discipline.",
        difficultyLevel: 5,
        prerequisites: [],
        sourceIds: ["user-go-rpc-notes"],
        projectMilestone: "M1: RPC Harness & State Structs",
      },
      {
        id: "node-02-leader-election",
        title: "Leader Election & Split Vote Recovery",
        summary: "Implement RequestVote RPC, randomized election timers, and Pre-Vote.",
        difficultyLevel: 9,
        prerequisites: ["node-01-rpc-basics"],
        sourceIds: ["res-raft-leader-election"],
        projectMilestone: "M2: Leader Election & Pre-Vote",
      },
      {
        id: "node-03-log-replication",
        title: "Log Replication & Safety Invariants",
        summary: "Implement AppendEntries consistency check and commitIndex advancement.",
        difficultyLevel: 13,
        prerequisites: ["node-02-leader-election"],
        sourceIds: ["res-raft-leader-election", "user-go-rpc-notes"],
        projectMilestone: "M3: Log Replication & Commit Index",
      },
    ],
    tokenUsage: {
      promptTokens: 1400,
      completionTokens: 560,
      model: "gemini-2.5-pro",
    },
    nowIso: t0,
  });

  // Skill #5: Validate Learning Path (DAG acyclicity, difficulty monotonicity, source grounding)
  state = validateLearningPath(
    state,
    {
      decision: "accept",
      tokenUsage: { promptTokens: 640, completionTokens: 120, model: "gemini-2.5-flash" },
    },
    t0
  );

  // Skill #6 Step A: Agent publishes organized Markdown learning material for unlocked node-02
  state = publishNodeLearningMaterial(state, {
    nodeId: "node-02-leader-election",
    studyMaterialMarkdown: [
      "## 1. Learning Objectives",
      "- Understand how randomized election timeouts ($150\\text{–}300\\text{ms}$) prevent split votes.",
      "- Implement `RequestVote` RPC and term monotonicity checks in Go.",
      "- Prevent partitioned nodes with inflated terms from disrupting a healthy leader via **Pre-Vote**.",
      "",
      "## 2. Core Theory & State Transitions",
      "Every Raft peer is in one of three states: `Follower`, `Candidate`, or `Leader`. Time is divided into strictly increasing `currentTerm` epochs.",
      "",
      "## 3. Go Implementation Walkthrough",
      "```go",
      "func (rf *Raft) startElection() {",
      "    rf.mu.Lock()",
      "    rf.state = Candidate",
      "    rf.currentTerm++",
      "    rf.votedFor = rf.me",
      "    rf.mu.Unlock()",
      "}",
      "```",
      "",
      "## 4. Edge Cases: Asymmetric Network Partitions & Pre-Vote",
      "Without Pre-Vote (Ongaro Dissertation §9.6), an isolated follower repeatedly times out and increments its `currentTerm`. When the partition heals, its higher term forces the active leader to step down.",
    ].join("\n"),
    tokenUsage: {
      promptTokens: 680,
      completionTokens: 520,
      model: "gemini-2.5-pro",
    },
    nowIso: t0,
  });

  // Skill #6 Step B: Learner reads the Markdown file and asks a question via the Agent
  state = recordNodeQaInteraction(state, {
    nodeId: "node-02-leader-election",
    question: "Why does Pre-Vote check log up-to-dateness before incrementing currentTerm?",
    answerMarkdown:
      "Because a partitioned node cannot append new committed entries; checking whether a majority would grant a vote *before* incrementing `currentTerm` prevents a stale node from forcing a leader step-down.",
    tokenUsage: {
      promptTokens: 210,
      completionTokens: 140,
      model: "gemini-2.5-flash",
    },
    nowIso: t0,
  });

  // Skill #6 Step C: Teaching Orchestrator administers Mastery Gate Quiz on node-02-leader-election
  state = registerQuiz(
    state,
    {
      id: "quiz-mastery-node-02",
      nodeId: "node-02-leader-election",
      purpose: "mastery_gate",
      modality: "mcq_4",
      difficultyLevel: 9,
      questionPrompt:
        "What prevents a partitioned follower with a higher term from disrupting a stable leader upon rejoining?",
      options: [
        { label: "A", text: "Increasing the heartbeat frequency to 1ms" },
        {
          label: "B",
          text: "The Pre-Vote phase where a candidate checks if its log is up-to-date before incrementing term",
        },
        { label: "C", text: "Always accepting RequestVote regardless of log length" },
        { label: "D", text: "Using TCP instead of UDP" },
      ],
      correctOption: "B",
      passingScoreThreshold: 1.0,
      tokenUsage: {
        promptTokens: 520,
        completionTokens: 240,
        model: "gemini-2.5-flash",
      },
    },
    t0
  );

  // Learner passes Mastery Gate for node-02 -> Completes node-02, unlocks node-03, and schedules SRS!
  state = submitNodeQuizAttempt(state, {
    quizId: "quiz-mastery-node-02",
    selectedOption: "B",
    tokenUsage: {
      promptTokens: 310,
      completionTokens: 95,
      model: "gemini-2.5-flash",
    },
    nowIso: t0,
  });

  saveState(state, baseCwd);

  return {
    config,
    state,
    dashboard: renderAsciiDashboard(state),
    tokenReport: generateTokenMaintenanceMarkdownReport(state),
    workflowMermaid: generateWorkflowMermaid(state),
    dagMermaid: generateLearningPathDagMermaid(state),
  };
}

function parseModeFlag(args: string[], defaultMode: ExecutionMode = "local_file"): ExecutionMode {
  const idx = args.indexOf("--mode");
  if (idx !== -1 && args[idx + 1]) {
    const val = args[idx + 1];
    if (val === "local_file" || val === "cloud_rag") return val;
    throw new Error(`Invalid --mode "${val}". Expected "local_file" or "cloud_rag".`);
  }
  return defaultMode;
}

function parseFlagValue(args: string[], flag: string): string | undefined {
  const idx = args.indexOf(flag);
  if (idx !== -1 && args[idx + 1]) {
    return args[idx + 1];
  }
  return undefined;
}

export function runCli(argv: string[] = process.argv): void {
  const [, , command, ...args] = argv;

  if (!command || command === "help" || command === "--help") {
    console.log(`Usage: npx tsx .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.ts <command> [args]

Skill Installation & Environment Setup (Run First):
  install [--mode local_file|cloud_rag]   Setup .lms-state/ (logs, artifacts, state) & learning/ directories

Execution Modes (--mode):
  local_file                              Mode #1 (Default): Local File-Based Knowledge System (No RAG, reads from disk)
  cloud_rag                               Mode #2: Cloud System + RAG Chunks

Orchestrator, Visualization, Artifacts & Token Maintenance Commands:
  simulate [--mode local_file|cloud_rag]  Run deterministic end-to-end simulation of all 6 skills & write logs/artifacts
  init <slug> <title> [--mode <mode>]     Initialize a new subject workflow state in .lms-state/state/<slug>.json
  visualize <slug> [--mermaid]            Render ASCII dashboard (and Mermaid diagrams with --mermaid)
  token-report <slug>                     Print Markdown token usage & workflow maintenance report
  log-tokens <slug> '<jsonPayload>'       Explicitly log token usage for a task/node into JSONL & state
  read-node-sources <slug> <nodeId>       Retrieve knowledge for a node (local files in local_file vs RAG chunks in cloud_rag)
  status <slug>                           Print JSON summary of current workflow stage, logs, artifacts & learning files

Skill #1 (lms-research-document):
  research-submit <slug> '<jsonPayload>'  Submit initial or revised research draft (+ optional tokenUsage)

Skill #2 (lms-validate-research):
  research-validate <slug> '<jsonPayload>' Validate research draft (accept or raise_issues, max 3 rounds)
  research-resolve <slug> '<jsonPayload>'  Resolve 3-round escalation with user decision

Skill #3 (lms-aggregate-sources):
  source-ingest <slug> '<jsonPayload>'    Ingest Track 1 (BYOS) file (local file in local_file, RAG chunks in cloud_rag)
  source-aggregate <slug>                 Aggregate validated Track 2 research into sources

Skill #4 (lms-design-learning-path):
  placement-start <slug> [tiers]          Start O(log N) binary search diagnostic placement
  placement-step <slug> '<jsonPayload>'   Submit one binary search placement step ({quizId, answeredCorrectly})
  path-design <slug> '<jsonPayload>'      Draft DAG learning path nodes and capstone project

Skill #5 (lms-validate-learning-path):
  path-validate <slug> ['<jsonReview>']   Deterministically validate DAG cycles, monotonicity, source grounding & unlock entry nodes

Skill #6 (lms-teaching-orchestrator — Markdown Study Guides, Q&A & Quizzes):
  material-publish <slug> '<jsonPayload>' Publish a node's comprehensive Markdown study guide to learning/<slug>/modules/<NN>-<nodeId>.md
                                          (Also supports: material-publish <slug> --node <nodeId> --file <markdownFilePath>)
  material-qa <slug> '<jsonPayload>'      Record learner Q&A for a node and append it to the node's Markdown study guide
  material-organize <slug>                Reorganize/sync all Markdown files in learning/<slug>/ (README.md, modules/, capstone/, references/)
  quiz-register <slug> '<jsonPayload>'    Register a placement, mastery_gate (requires material-publish first!), or spaced_repetition quiz
  quiz-submit <slug> '<jsonPayload>'      Grade quiz attempt, unlock next DAG nodes, and update FSRS/SM-2 schedule
`);
    return;
  }

  if (command === "install") {
    const mode = parseModeFlag(args, "local_file");
    const cfg = installSkillEnvironment({ defaultMode: mode });
    console.log(
      JSON.stringify(
        {
          status: "installed",
          message:
            "LMS 6-Skill local environment initialized (logs, artifacts, state, knowledge, and organized learning/ directories ready).",
          config: cfg,
        },
        null,
        2
      )
    );
    return;
  }

  if (command === "simulate") {
    const mode = parseModeFlag(args, "local_file");
    const sim = runDeterministicSimulation(mode);
    console.log(sim.dashboard);
    console.log("\n--- WORKFLOW STATE MACHINE (MERMAID) ---\n```mermaid");
    console.log(sim.workflowMermaid);
    console.log("```\n\n--- LEARNING PATH DAG (MERMAID) ---\n```mermaid");
    console.log(sim.dagMermaid);
    console.log("```");
    return;
  }

  if (command === "init") {
    const env = ensureSkillEnvironmentInstalled();
    const slug = args[0] || "demo-subject";
    const title = args[1] || "Demo Subject";
    const mode = parseModeFlag(args, env.defaultExecutionMode);
    const state = createInitialWorkflowState({
      subjectSlug: slug,
      subjectTitle: title,
      executionMode: mode,
    });
    saveState(state);
    console.log(
      `Initialized workflow state at ${getStateFilePath(slug)} (executionMode=${state.executionMode}, learningDir=${state.artifacts.learningOutDir})`
    );
    return;
  }

  const slug = args[0];
  if (!slug) throw new Error("Missing <slug> argument.");
  let state = loadState(slug);

  switch (command) {
    case "visualize": {
      console.log(renderAsciiDashboard(state));
      if (args.includes("--mermaid")) {
        console.log("\n```mermaid\n" + generateWorkflowMermaid(state) + "\n```");
        console.log("\n```mermaid\n" + generateLearningPathDagMermaid(state) + "\n```");
      }
      return;
    }
    case "token-report": {
      console.log(generateTokenMaintenanceMarkdownReport(state));
      return;
    }
    case "log-tokens": {
      const payload = JSON.parse(args[1] || "{}");
      state = recordTokenUsage(state, {
        skill: payload.skill ?? "lms-teaching-orchestrator",
        taskName: payload.taskName ?? "custom-agent-task",
        nodeId: payload.nodeId,
        topicId: payload.topicId,
        usage: payload,
      });
      saveState(state);
      console.log(
        `Logged token usage (${payload.promptTokens ?? 0} prompt + ${payload.completionTokens ?? 0} completion) -> ${state.tokenTelemetry.logFilePath}`
      );
      return;
    }
    case "read-node-sources": {
      const nodeId = args[1];
      if (!nodeId) throw new Error("Missing <nodeId> argument.");
      const result = retrieveKnowledgeForNode(state, nodeId, (relPath) => {
        const abs = path.resolve(process.cwd(), ".lms-state", "knowledge", relPath);
        return fs.existsSync(abs) ? fs.readFileSync(abs, "utf-8") : null;
      });
      console.log(JSON.stringify(result, null, 2));
      return;
    }
    case "status": {
      console.log(
        JSON.stringify(
          {
            subjectSlug: state.subjectSlug,
            executionMode: state.executionMode,
            stage: state.stage,
            placementStatus: state.placement.status,
            entryDifficultyLevel: state.placement.entryDifficultyLevel,
            pathValidationStatus: state.pathValidation.status,
            learningWorkspace: {
              learningOutDir: state.artifacts.learningOutDir,
              courseIndexPath: state.artifacts.courseIndexPath,
              capstoneBriefPath: state.artifacts.capstoneBriefPath,
              modules: Object.values(state.nodes).map((n) => ({
                id: n.id,
                status: n.status,
                materialPublished: n.materialPublished,
                studyGuidePath: n.studyGuidePath,
              })),
            },
            totalTokens: state.tokenTelemetry.total.totalTokens,
            logs: {
              tokenJsonl: state.tokenTelemetry.logFilePath,
              maintenanceReport: state.tokenTelemetry.maintenanceReportPath,
              eventLog: state.tokenTelemetry.eventLogPath,
            },
            artifacts: state.artifacts,
            updatedAt: state.updatedAt,
          },
          null,
          2
        )
      );
      return;
    }
    case "research-submit": {
      const payload = JSON.parse(args[1] || "{}");
      state = submitResearchDraft(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "research-validate": {
      const payload = JSON.parse(args[1] || "{}");
      state = validateResearchDraft(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "research-resolve": {
      const payload = JSON.parse(args[1] || "{}");
      state = resolveResearchEscalation(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "source-ingest": {
      const payload = JSON.parse(args[1] || "{}");
      state = ingestUserSource(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "source-aggregate": {
      state = aggregateValidatedResearchIntoSources(state);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "placement-start": {
      const tiers = args[1] ? Number(args[1]) : undefined;
      state = startPlacementQuiz(state, tiers);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "placement-step": {
      const payload = JSON.parse(args[1] || "{}");
      state = submitPlacementQuizStep(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "path-design": {
      const payload = JSON.parse(args[1] || "{}");
      state = designLearningPath(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "path-validate": {
      const review = args[1] ? JSON.parse(args[1]) : undefined;
      state = validateLearningPath(state, review);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "material-publish": {
      const flagNode = parseFlagValue(args, "--node");
      const flagFile = parseFlagValue(args, "--file");
      let nodeId: string;
      let studyMaterialMarkdown: string;
      let tokenUsage: LmsWorkflowState["tokenTelemetry"]["entries"][number] | undefined;

      if (flagNode && flagFile) {
        nodeId = flagNode;
        const absFile = path.resolve(process.cwd(), flagFile);
        if (!fs.existsSync(absFile)) {
          throw new Error(`Markdown file not found at ${absFile}`);
        }
        studyMaterialMarkdown = fs.readFileSync(absFile, "utf-8");
      } else {
        const payload = JSON.parse(args[1] || "{}");
        nodeId = payload.nodeId;
        tokenUsage = payload.tokenUsage;
        if (payload.markdownFilePath) {
          const absFile = path.resolve(process.cwd(), payload.markdownFilePath);
          studyMaterialMarkdown = fs.readFileSync(absFile, "utf-8");
        } else {
          studyMaterialMarkdown = payload.studyMaterialMarkdown;
        }
      }

      state = publishNodeLearningMaterial(state, {
        nodeId,
        studyMaterialMarkdown,
        tokenUsage,
      });
      saveState(state);
      const publishedNode = state.nodes[nodeId];
      console.log(renderAsciiDashboard(state));
      console.log(
        `\n[ORGANIZED LEARNING MATERIAL PUBLISHED]\n- Module Study Guide : ${publishedNode.studyGuidePath}\n- Course Index       : ${state.artifacts.courseIndexPath}\n-> Direct the learner to read "${publishedNode.studyGuidePath}". Answer any questions and administer quizzes interactively through the agent.`
      );
      return;
    }
    case "material-qa": {
      const payload = JSON.parse(args[1] || "{}");
      state = recordNodeQaInteraction(state, payload);
      saveState(state);
      const targetNode = state.nodes[payload.nodeId];
      console.log(
        `Appended Q&A to organized study guide: ${targetNode.studyGuidePath} (total Q&A entries: ${targetNode.qaLog.length})`
      );
      return;
    }
    case "material-organize": {
      saveState(state);
      console.log(
        JSON.stringify(
          {
            status: "organized",
            learningOutDir: state.artifacts.learningOutDir,
            courseIndexPath: state.artifacts.courseIndexPath,
            capstoneBriefPath: state.artifacts.capstoneBriefPath,
            modules: Object.values(state.nodes).map((n) => ({
              id: n.id,
              title: n.title,
              status: n.status,
              materialPublished: n.materialPublished,
              studyGuidePath: n.studyGuidePath,
            })),
          },
          null,
          2
        )
      );
      return;
    }
    case "quiz-register": {
      const payload = JSON.parse(args[1] || "{}");
      state = registerQuiz(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    case "quiz-submit": {
      const payload = JSON.parse(args[1] || "{}");
      state = submitNodeQuizAttempt(state, payload);
      saveState(state);
      console.log(renderAsciiDashboard(state));
      return;
    }
    default:
      throw new Error(`Unknown command: ${command}`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runCli(process.argv);
}
