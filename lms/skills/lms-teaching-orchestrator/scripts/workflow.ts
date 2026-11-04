// ============================================================================
// LMS Standalone Deterministic Agent Workflow Engine (Zero External Dependencies)
// Location: .agents/skills/lms-teaching-orchestrator/scripts/workflow.ts
// ============================================================================

/**
 * Dual Execution Mode Flag:
 * - "local_file": Default for local agent-first execution. Uses local file-based Markdown
 *                 knowledge directly on disk (.lms-state/knowledge/... and learning/<slug>/...) without RAG indexing.
 * - "cloud_rag":  Connects to the cloud system (Vercel MCP + GitHub + Neon pgvector RAG chunks).
 */
export type ExecutionMode = "local_file" | "cloud_rag";

export type WorkflowStage =
  | "IDLE"
  | "RESEARCHING"
  | "VALIDATING_RESEARCH"
  | "PAUSED_FOR_USER_RESEARCH_DECISION"
  | "AGGREGATING_SOURCES"
  | "PLACEMENT_QUIZ"
  | "DESIGNING_PATH"
  | "VALIDATING_PATH"
  | "PAUSED_FOR_USER_PATH_DECISION"
  | "TEACHING_ACTIVE"
  | "SUBJECT_COMPLETED";

export type PerspectiveType = "theory" | "practical" | "critique" | "reference" | "mixed";

export type SourceTrack = "byos" | "agent_research";
export type SourceFormat = "pdf" | "docx" | "xlsx" | "markdown" | "web_synthesis";

export type SkillIdentifier =
  | "lms-research-document"
  | "lms-validate-research"
  | "lms-aggregate-sources"
  | "lms-design-learning-path"
  | "lms-validate-learning-path"
  | "lms-teaching-orchestrator";

export interface TokenUsageInput {
  skill?: SkillIdentifier;
  taskName?: string;
  nodeId?: string;
  topicId?: string;
  model?: string;
  promptTokens?: number;
  completionTokens?: number;
  durationMs?: number;
  notes?: string;
}

export interface TokenUsageLogEntry {
  id: string;
  timestamp: string;
  subjectSlug: string;
  executionMode: ExecutionMode;
  stage: WorkflowStage;
  skill: SkillIdentifier;
  taskName: string;
  nodeId?: string;
  topicId?: string;
  model: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  isEstimated: boolean;
  durationMs: number;
  notes?: string;
}

export interface TokenBucketTotals {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  taskCount: number;
}

export interface TokenTelemetryState {
  logFilePath: string;
  maintenanceReportPath: string;
  eventLogPath: string;
  entries: TokenUsageLogEntry[];
  byNode: Record<string, TokenBucketTotals>;
  bySkill: Record<string, TokenBucketTotals>;
  byTopic: Record<string, TokenBucketTotals>;
  total: TokenBucketTotals;
}

export interface ArtifactManifest {
  artifactsRootDir: string;
  learningOutDir: string;
  courseIndexPath: string;
  capstoneBriefPath: string;
  dashboardPath: string;
  workflowMermaidPath: string;
  dagMermaidPath: string;
  pathValidationPath: string;
  researchDebatePaths: Record<string, string>;
}

export type ResearchStatus =
  | "drafting"
  | "in_review"
  | "revision_requested"
  | "validated"
  | "escalated_to_user"
  | "user_approved"
  | "rejected";

export interface ResearchDebateTurn {
  round: number;
  actor: "research_agent" | "validation_agent" | "user";
  action:
    | "submit_draft"
    | "defend_and_revise"
    | "accept"
    | "raise_issues"
    | "user_override_approve"
    | "user_reject";
  summary: string;
  defects?: string[];
  defenseArgument?: string;
  citations?: string[];
  timestamp: string;
}

export interface ResearchTopicState {
  id: string;
  title: string;
  requiredPerspectives: PerspectiveType[];
  coveredPerspectives: PerspectiveType[];
  currentDraftMarkdown: string;
  citations: string[];
  status: ResearchStatus;
  reviewRound: number; // 0..3
  maxRounds: number;   // default 3
  openDefects: string[];
  debateHistory: ResearchDebateTurn[];
}

export interface RagChunk {
  id: string;
  sourceId: string;
  githubMdPath: string;
  chunkIndex: number;
  heading: string;
  content: string;
  perspective: PerspectiveType;
}

export interface AggregatedSource {
  id: string;
  title: string;
  track: SourceTrack;
  sourceType: SourceFormat;
  originalUri: string;
  githubMdPath: string;
  localFilePath: string;     // Used in .lms-state/knowledge/
  organizedRefPath: string;  // Organized user-facing reference path in learning/<slug>/references/
  perspective: PerspectiveType;
  contentMarkdown: string;
  chunkIds: string[];        // Populated in "cloud_rag" mode; empty in "local_file" mode
}

export interface PlacementStepRecord {
  stepIndex: number;
  difficultyTested: number;
  quizId: string;
  passed: boolean;
  lowBefore: number;
  highBefore: number;
  lowAfter: number;
  highAfter: number;
}

export interface PlacementSessionState {
  totalDifficultyTiers: number; // 1..N
  low: number;
  high: number;
  currentMid: number | null;
  entryDifficultyLevel: number;
  status: "not_started" | "in_progress" | "completed";
  history: PlacementStepRecord[];
}

export type NodeStatus =
  | "locked"
  | "unlocked"
  | "in_progress"
  | "completed"
  | "mastered_by_placement";

export interface SpacedRepetitionState {
  nodeId: string;
  reps: number;
  lapses: number;
  intervalDays: number;
  easinessFactor: number;
  lastReviewedAt: string;
  nextReviewAt: string;
  needsRefresher: boolean;
}

export interface NodeQaEntry {
  id: string;
  question: string;
  answerMarkdown: string;
  timestamp: string;
}

export interface LearningNode {
  id: string;
  moduleNumber: number;           // 1-based ordered module number (01, 02, ...)
  title: string;
  summary: string;
  difficultyLevel: number;        // 1..N
  prerequisites: string[];        // Node IDs
  sourceIds: string[];            // Must reference valid AggregatedSource IDs
  githubMdPath: string;
  localFilePath: string;
  studyGuidePath: string;         // User-facing organized Markdown file: learning/<slug>/modules/01-<id>.md
  materialPublished: boolean;     // True once the agent writes the full study guide for the learner to read
  studyMaterialMarkdown?: string; // Full Markdown lesson content written by the agent
  qaLog: NodeQaEntry[];           // Interactive Q&A answered by the agent and appended to the Markdown file
  projectMilestone?: string;
  status: NodeStatus;
}

export interface CapstoneProject {
  title: string;
  description: string;
  milestones: string[];
}

export interface PathValidationResult {
  valid: boolean;
  round: number;
  structuralErrors: string[];
  pedagogicalCritiques: string[];
  status: "pending" | "accepted" | "revision_requested" | "escalated_to_user";
}

export type QuizPurpose = "placement" | "mastery_gate" | "spaced_repetition";
export type QuizModality = "mcq_4" | "open_ended" | "code_challenge" | "project_task";

export interface QuizOption {
  label: "A" | "B" | "C" | "D";
  text: string;
}

export interface QuizItem {
  id: string;
  nodeId?: string;
  purpose: QuizPurpose;
  modality: QuizModality;
  difficultyLevel: number;
  questionPrompt: string;
  options?: [QuizOption, QuizOption, QuizOption, QuizOption]; // Strictly 4 options for mcq_4
  correctOption?: "A" | "B" | "C" | "D";
  starterCode?: string;
  rubricCriteria?: string[];
  passingScoreThreshold: number; // Default 0.7 (or 1.0 for mcq_4)
  completed: boolean;
  passed?: boolean;
  score?: number;
  feedback?: string;
}

export interface LmsWorkflowState {
  subjectSlug: string;
  subjectTitle: string;
  executionMode: ExecutionMode; // "local_file" (default local agent workflow) | "cloud_rag"
  localKnowledgeRoot: string;   // Base directory for internal knowledge (e.g. "lms/raft-consensus-go")
  learnerStyle: {
    mode: "first_principles" | "code_first" | "visual" | "analogy_heavy";
    pace: "intensive" | "moderate" | "relaxed";
  };
  stage: WorkflowStage;
  maxReviewRounds: number; // Strictly 3 rounds by default
  researchTopics: Record<string, ResearchTopicState>;
  sources: Record<string, AggregatedSource>;
  ragChunks: RagChunk[];   // Populated only when executionMode === "cloud_rag"
  placement: PlacementSessionState;
  capstoneProject?: CapstoneProject;
  nodes: Record<string, LearningNode>;
  pathValidation: PathValidationResult;
  quizzes: Record<string, QuizItem>;
  spacedRepetition: Record<string, SpacedRepetitionState>;
  tokenTelemetry: TokenTelemetryState;
  artifacts: ArtifactManifest;
  updatedAt: string;
}

// ============================================================================
// 2. Token Telemetry & Estimation Helpers
// ============================================================================

export function estimateTokensFromText(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

function addBucket(
  prev: TokenBucketTotals | undefined,
  promptTokens: number,
  completionTokens: number
): TokenBucketTotals {
  const base = prev ?? { promptTokens: 0, completionTokens: 0, totalTokens: 0, taskCount: 0 };
  return {
    promptTokens: base.promptTokens + promptTokens,
    completionTokens: base.completionTokens + completionTokens,
    totalTokens: base.totalTokens + promptTokens + completionTokens,
    taskCount: base.taskCount + 1,
  };
}

export function recordTokenUsage(
  state: LmsWorkflowState,
  params: {
    skill: SkillIdentifier;
    taskName: string;
    nodeId?: string;
    topicId?: string;
    defaultPromptText?: string;
    defaultCompletionText?: string;
    usage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = params.nowIso ?? state.updatedAt;
  const u = params.usage;
  const hasExplicit =
    typeof u?.promptTokens === "number" || typeof u?.completionTokens === "number";

  const promptTokens =
    u?.promptTokens ?? estimateTokensFromText(params.defaultPromptText ?? params.taskName);
  const completionTokens =
    u?.completionTokens ?? estimateTokensFromText(params.defaultCompletionText ?? "");
  const totalTokens = promptTokens + completionTokens;
  const skill = u?.skill ?? params.skill;
  const nodeId = u?.nodeId ?? params.nodeId;
  const topicId = u?.topicId ?? params.topicId;
  const taskName = u?.taskName ?? params.taskName;

  const entry: TokenUsageLogEntry = {
    id: `tok-${state.tokenTelemetry.entries.length + 1}`,
    timestamp: now,
    subjectSlug: state.subjectSlug,
    executionMode: state.executionMode,
    stage: state.stage,
    skill,
    taskName,
    nodeId,
    topicId,
    model: u?.model ?? "agent-default",
    promptTokens,
    completionTokens,
    totalTokens,
    isEstimated: !hasExplicit,
    durationMs: u?.durationMs ?? 0,
    notes: u?.notes,
  };

  const prevTel = state.tokenTelemetry;
  const nextBySkill = {
    ...prevTel.bySkill,
    [skill]: addBucket(prevTel.bySkill[skill], promptTokens, completionTokens),
  };

  const nextByNode = { ...prevTel.byNode };
  if (nodeId) {
    nextByNode[nodeId] = addBucket(prevTel.byNode[nodeId], promptTokens, completionTokens);
  }

  const nextByTopic = { ...prevTel.byTopic };
  if (topicId) {
    nextByTopic[topicId] = addBucket(prevTel.byTopic[topicId], promptTokens, completionTokens);
  }

  return {
    ...state,
    tokenTelemetry: {
      ...prevTel,
      entries: [...prevTel.entries, entry],
      bySkill: nextBySkill,
      byNode: nextByNode,
      byTopic: nextByTopic,
      total: addBucket(prevTel.total, promptTokens, completionTokens),
    },
  };
}

// ============================================================================
// 3. Initialization & Mode Configuration
// ============================================================================

export function createInitialWorkflowState(params: {
  subjectSlug: string;
  subjectTitle: string;
  executionMode?: ExecutionMode;
  localKnowledgeRoot?: string;
  learnerMode?: "first_principles" | "code_first" | "visual" | "analogy_heavy";
  learnerPace?: "intensive" | "moderate" | "relaxed";
  totalDifficultyTiers?: number;
  nowIso?: string;
}): LmsWorkflowState {
  const totalTiers = params.totalDifficultyTiers ?? 16;
  const now = params.nowIso ?? new Date().toISOString();
  const mode: ExecutionMode = params.executionMode ?? "local_file";
  const localRoot = params.localKnowledgeRoot ?? `lms/${params.subjectSlug}`;
  const artifactsRoot = `.lms-state/artifacts/${params.subjectSlug}`;
  const learningOutDir = `learning/${params.subjectSlug}`;

  return {
    subjectSlug: params.subjectSlug,
    subjectTitle: params.subjectTitle,
    executionMode: mode,
    localKnowledgeRoot: localRoot,
    learnerStyle: {
      mode: params.learnerMode ?? "code_first",
      pace: params.learnerPace ?? "moderate",
    },
    stage: "IDLE",
    maxReviewRounds: 3,
    researchTopics: {},
    sources: {},
    ragChunks: [],
    placement: {
      totalDifficultyTiers: totalTiers,
      low: 1,
      high: totalTiers,
      currentMid: null,
      entryDifficultyLevel: 1,
      status: "not_started",
      history: [],
    },
    nodes: {},
    pathValidation: {
      valid: false,
      round: 0,
      structuralErrors: [],
      pedagogicalCritiques: [],
      status: "pending",
    },
    quizzes: {},
    spacedRepetition: {},
    tokenTelemetry: {
      logFilePath: `.lms-state/logs/${params.subjectSlug}-token-usage.jsonl`,
      maintenanceReportPath: `.lms-state/logs/${params.subjectSlug}-maintenance.md`,
      eventLogPath: `.lms-state/logs/workflow-events.log`,
      entries: [],
      byNode: {},
      bySkill: {},
      byTopic: {},
      total: { promptTokens: 0, completionTokens: 0, totalTokens: 0, taskCount: 0 },
    },
    artifacts: {
      artifactsRootDir: artifactsRoot,
      learningOutDir,
      courseIndexPath: `${learningOutDir}/README.md`,
      capstoneBriefPath: `${learningOutDir}/capstone/PROJECT_BRIEF.md`,
      dashboardPath: `${artifactsRoot}/dashboard.txt`,
      workflowMermaidPath: `${artifactsRoot}/workflow-graph.mmd`,
      dagMermaidPath: `${artifactsRoot}/learning-path-dag.mmd`,
      pathValidationPath: `${artifactsRoot}/path-validation.json`,
      researchDebatePaths: {},
    },
    updatedAt: now,
  };
}

// ============================================================================
// 4. Skill #1 & #2: Research Document & 3-Round Validation Loop
// ============================================================================

export interface SubmitResearchDraftInput {
  topicId: string;
  title: string;
  requiredPerspectives: PerspectiveType[];
  coveredPerspectives: PerspectiveType[];
  draftMarkdown: string;
  citations: string[];
  defenseArgument?: string;
  tokenUsage?: TokenUsageInput;
  nowIso?: string;
}

function validateSubmitResearchInput(input: SubmitResearchDraftInput): void {
  if (!input.topicId || typeof input.topicId !== "string") {
    throw new Error("topicId is required.");
  }
  if (!input.title || typeof input.title !== "string") {
    throw new Error("title is required.");
  }
  if (!Array.isArray(input.requiredPerspectives) || input.requiredPerspectives.length === 0) {
    throw new Error("requiredPerspectives must be a non-empty array.");
  }
  if (!Array.isArray(input.coveredPerspectives) || input.coveredPerspectives.length === 0) {
    throw new Error("coveredPerspectives must be a non-empty array.");
  }
  if (!input.draftMarkdown || input.draftMarkdown.length < 20) {
    throw new Error("draftMarkdown must be at least 20 characters.");
  }
  if (!Array.isArray(input.citations) || input.citations.length === 0) {
    throw new Error("citations must contain at least one citation URI.");
  }
}

export function submitResearchDraft(
  state: LmsWorkflowState,
  input: SubmitResearchDraftInput
): LmsWorkflowState {
  validateSubmitResearchInput(input);
  const now = input.nowIso ?? new Date().toISOString();
  const existing = state.researchTopics[input.topicId];

  if (existing && existing.status === "escalated_to_user") {
    throw new Error(
      `Research topic "${input.topicId}" reached the ${existing.maxRounds}-round review limit and is ESCALATED_TO_USER. The research agent must stop and wait for user decision.`
    );
  }
  if (existing && (existing.status === "validated" || existing.status === "user_approved")) {
    throw new Error(`Research topic "${input.topicId}" is already validated.`);
  }

  const isRevision = Boolean(existing);
  const roundNum = existing ? existing.reviewRound + 1 : 1;
  const nextTurn: ResearchDebateTurn = {
    round: roundNum,
    actor: "research_agent",
    action: isRevision ? "defend_and_revise" : "submit_draft",
    summary: isRevision
      ? `Submitted revised draft${input.defenseArgument ? " with defense argument" : ""}.`
      : "Submitted initial multi-perspective research draft.",
    defenseArgument: input.defenseArgument,
    citations: input.citations,
    timestamp: now,
  };

  const updatedTopic: ResearchTopicState = {
    id: input.topicId,
    title: input.title,
    requiredPerspectives: input.requiredPerspectives,
    coveredPerspectives: input.coveredPerspectives,
    currentDraftMarkdown: input.draftMarkdown,
    citations: input.citations,
    status: "in_review",
    reviewRound: existing ? existing.reviewRound : 0,
    maxRounds: state.maxReviewRounds,
    openDefects: existing ? existing.openDefects : [],
    debateHistory: [...(existing?.debateHistory ?? []), nextTurn],
  };

  const debatePath = `${state.artifacts.artifactsRootDir}/research/${input.topicId}-debate.md`;

  const nextState: LmsWorkflowState = {
    ...state,
    stage: "VALIDATING_RESEARCH",
    researchTopics: {
      ...state.researchTopics,
      [input.topicId]: updatedTopic,
    },
    artifacts: {
      ...state.artifacts,
      researchDebatePaths: {
        ...state.artifacts.researchDebatePaths,
        [input.topicId]: debatePath,
      },
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-research-document",
    taskName: isRevision ? `research-revise-round-${roundNum}` : "research-initial-draft",
    topicId: input.topicId,
    defaultPromptText: `${input.title} ${existing?.openDefects.join(" ") ?? ""}`,
    defaultCompletionText: `${input.draftMarkdown} ${input.defenseArgument ?? ""}`,
    usage: input.tokenUsage,
    nowIso: now,
  });
}

export interface ValidateResearchInput {
  topicId: string;
  decision: "accept" | "raise_issues";
  critiqueSummary: string;
  defects?: string[];
  acceptedDefense?: boolean;
  tokenUsage?: TokenUsageInput;
  nowIso?: string;
}

export function validateResearchDraft(
  state: LmsWorkflowState,
  input: ValidateResearchInput
): LmsWorkflowState {
  const now = input.nowIso ?? new Date().toISOString();
  const topic = state.researchTopics[input.topicId];
  if (!topic) {
    throw new Error(`Research topic "${input.topicId}" not found.`);
  }
  if (topic.status !== "in_review") {
    throw new Error(
      `Research topic "${input.topicId}" is in status "${topic.status}", expected "in_review".`
    );
  }

  const nextRound = topic.reviewRound + 1;

  const missingPerspectives = topic.requiredPerspectives.filter(
    (p) => !topic.coveredPerspectives.includes(p)
  );
  const autoDefects: string[] = [];
  if (missingPerspectives.length > 0) {
    autoDefects.push(`Missing required perspectives: ${missingPerspectives.join(", ")}`);
  }
  if (topic.citations.length === 0) {
    autoDefects.push("No source citations provided.");
  }

  const combinedDefects = [...autoDefects, ...(input.defects ?? [])];
  const passed = input.decision === "accept" && autoDefects.length === 0;

  const turn: ResearchDebateTurn = {
    round: nextRound,
    actor: "validation_agent",
    action: passed ? "accept" : "raise_issues",
    summary: input.critiqueSummary,
    defects: passed ? [] : combinedDefects,
    timestamp: now,
  };

  let nextStatus: ResearchStatus;
  let nextWorkflowStage: WorkflowStage = state.stage;

  if (passed) {
    nextStatus = "validated";
    nextWorkflowStage = "RESEARCHING";
  } else if (nextRound >= topic.maxRounds) {
    nextStatus = "escalated_to_user";
    nextWorkflowStage = "PAUSED_FOR_USER_RESEARCH_DECISION";
  } else {
    nextStatus = "revision_requested";
    nextWorkflowStage = "RESEARCHING";
  }

  const updatedTopic: ResearchTopicState = {
    ...topic,
    status: nextStatus,
    reviewRound: nextRound,
    openDefects: passed ? [] : combinedDefects,
    debateHistory: [...topic.debateHistory, turn],
  };

  const nextState: LmsWorkflowState = {
    ...state,
    stage: nextWorkflowStage,
    researchTopics: {
      ...state.researchTopics,
      [input.topicId]: updatedTopic,
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-validate-research",
    taskName: `validate-research-round-${nextRound}`,
    topicId: input.topicId,
    defaultPromptText: topic.currentDraftMarkdown,
    defaultCompletionText: `${input.critiqueSummary} ${combinedDefects.join(" ")}`,
    usage: input.tokenUsage,
    nowIso: now,
  });
}

export function resolveResearchEscalation(
  state: LmsWorkflowState,
  params: {
    topicId: string;
    userDecision: "approve_override" | "reject";
    userNotes: string;
    editedMarkdown?: string;
    tokenUsage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = params.nowIso ?? new Date().toISOString();
  const topic = state.researchTopics[params.topicId];
  if (!topic || topic.status !== "escalated_to_user") {
    throw new Error(`Topic "${params.topicId}" is not currently escalated_to_user.`);
  }

  const turn: ResearchDebateTurn = {
    round: topic.reviewRound,
    actor: "user",
    action: params.userDecision === "approve_override" ? "user_override_approve" : "user_reject",
    summary: params.userNotes,
    timestamp: now,
  };

  const updatedTopic: ResearchTopicState = {
    ...topic,
    currentDraftMarkdown: params.editedMarkdown ?? topic.currentDraftMarkdown,
    status: params.userDecision === "approve_override" ? "user_approved" : "rejected",
    openDefects: [],
    debateHistory: [...topic.debateHistory, turn],
  };

  const nextState: LmsWorkflowState = {
    ...state,
    stage: "RESEARCHING",
    researchTopics: {
      ...state.researchTopics,
      [params.topicId]: updatedTopic,
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-validate-research",
    taskName: "user-escalation-resolution",
    topicId: params.topicId,
    defaultPromptText: params.userNotes,
    defaultCompletionText: params.userDecision,
    usage: params.tokenUsage,
    nowIso: now,
  });
}

// ============================================================================
// 5. Skill #3: Aggregate into Sources (Dual Mode: local_file vs cloud_rag)
// ============================================================================

export function chunkMarkdownByHeadings(params: {
  sourceId: string;
  githubMdPath: string;
  markdown: string;
  perspective: PerspectiveType;
}): RagChunk[] {
  const lines = params.markdown.split(/\r?\n/);
  const chunks: RagChunk[] = [];
  let currentHeading = "Introduction";
  let currentBuffer: string[] = [];

  const flush = () => {
    const text = currentBuffer.join("\n").trim();
    if (text.length > 0) {
      const idx = chunks.length;
      chunks.push({
        id: `${params.sourceId}#chunk-${idx + 1}`,
        sourceId: params.sourceId,
        githubMdPath: params.githubMdPath,
        chunkIndex: idx,
        heading: currentHeading,
        content: text,
        perspective: params.perspective,
      });
    }
    currentBuffer = [];
  };

  for (const line of lines) {
    const match = /^(#{1,3})\s+(.+)$/.exec(line.trim());
    if (match) {
      flush();
      currentHeading = match[2].trim();
      currentBuffer.push(line);
    } else {
      currentBuffer.push(line);
    }
  }
  flush();

  return chunks;
}

export function ingestUserSource(
  state: LmsWorkflowState,
  input: {
    sourceId: string;
    title: string;
    sourceType: "pdf" | "docx" | "xlsx" | "markdown";
    originalUri: string;
    normalizedMarkdown: string;
    perspective?: PerspectiveType;
    tokenUsage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = input.nowIso ?? new Date().toISOString();
  const perspective = input.perspective ?? "reference";
  const githubMdPath = `lms/${state.subjectSlug}/sources/user/${input.sourceId}.md`;
  const localFilePath = `${state.localKnowledgeRoot}/sources/user/${input.sourceId}.md`;
  const organizedRefPath = `learning/${state.subjectSlug}/references/user/${input.sourceId}.md`;

  const isRagMode = state.executionMode === "cloud_rag";
  const newChunks = isRagMode
    ? chunkMarkdownByHeadings({
        sourceId: input.sourceId,
        githubMdPath,
        markdown: input.normalizedMarkdown,
        perspective,
      })
    : [];

  const source: AggregatedSource = {
    id: input.sourceId,
    title: input.title,
    track: "byos",
    sourceType: input.sourceType,
    originalUri: input.originalUri,
    githubMdPath,
    localFilePath,
    organizedRefPath,
    perspective,
    contentMarkdown: input.normalizedMarkdown,
    chunkIds: newChunks.map((c) => c.id),
  };

  const filteredChunks = state.ragChunks.filter((c) => c.sourceId !== input.sourceId);

  const nextState: LmsWorkflowState = {
    ...state,
    stage: "AGGREGATING_SOURCES",
    sources: {
      ...state.sources,
      [input.sourceId]: source,
    },
    ragChunks: isRagMode ? [...filteredChunks, ...newChunks] : [],
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-aggregate-sources",
    taskName: `ingest-byos-${input.sourceId}`,
    defaultPromptText: input.normalizedMarkdown,
    defaultCompletionText: isRagMode
      ? `Indexed ${newChunks.length} RAG chunks`
      : `Saved local file ${localFilePath}`,
    usage: input.tokenUsage,
    nowIso: now,
  });
}

export function aggregateValidatedResearchIntoSources(
  state: LmsWorkflowState,
  nowIso?: string,
  tokenUsage?: TokenUsageInput
): LmsWorkflowState {
  const now = nowIso ?? new Date().toISOString();
  const topics = Object.values(state.researchTopics);

  const unresolved = topics.filter(
    (t) =>
      t.status === "drafting" ||
      t.status === "in_review" ||
      t.status === "revision_requested" ||
      t.status === "escalated_to_user"
  );
  if (unresolved.length > 0) {
    throw new Error(
      `Cannot aggregate research while topics are unresolved: ${unresolved
        .map((u) => `${u.id} (${u.status})`)
        .join(", ")}`
    );
  }

  const approvedTopics = topics.filter(
    (t) => t.status === "validated" || t.status === "user_approved"
  );

  const isRagMode = state.executionMode === "cloud_rag";
  const nextSources = { ...state.sources };
  let nextChunks = isRagMode ? [...state.ragChunks] : [];

  for (const topic of approvedTopics) {
    const sourceId = `res-${topic.id}`;
    const githubMdPath = `lms/${state.subjectSlug}/sources/research/${topic.id}.md`;
    const localFilePath = `${state.localKnowledgeRoot}/sources/research/${topic.id}.md`;
    const organizedRefPath = `learning/${state.subjectSlug}/references/research/${topic.id}.md`;
    const perspective: PerspectiveType =
      topic.coveredPerspectives.length === 1 ? topic.coveredPerspectives[0] : "mixed";

    const chunks = isRagMode
      ? chunkMarkdownByHeadings({
          sourceId,
          githubMdPath,
          markdown: topic.currentDraftMarkdown,
          perspective,
        })
      : [];

    nextSources[sourceId] = {
      id: sourceId,
      title: topic.title,
      track: "agent_research",
      sourceType: "web_synthesis",
      originalUri: topic.citations[0] ?? "web-research",
      githubMdPath,
      localFilePath,
      organizedRefPath,
      perspective,
      contentMarkdown: topic.currentDraftMarkdown,
      chunkIds: chunks.map((c) => c.id),
    };

    if (isRagMode) {
      nextChunks = [...nextChunks.filter((c) => c.sourceId !== sourceId), ...chunks];
    }
  }

  if (Object.keys(nextSources).length === 0) {
    throw new Error("No validated research or BYOS sources available to aggregate.");
  }

  const nextState: LmsWorkflowState = {
    ...state,
    stage: "AGGREGATING_SOURCES",
    sources: nextSources,
    ragChunks: nextChunks,
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-aggregate-sources",
    taskName: `aggregate-sources-${state.executionMode}`,
    defaultPromptText: approvedTopics.map((t) => t.currentDraftMarkdown).join("\n"),
    defaultCompletionText: `Aggregated ${approvedTopics.length} topics in ${state.executionMode} mode`,
    usage: tokenUsage,
    nowIso: now,
  });
}

export function retrieveKnowledgeForNode(
  state: LmsWorkflowState,
  nodeId: string,
  readFileFn?: (localFilePath: string) => string | null
): {
  mode: ExecutionMode;
  nodeStudyGuidePath: string;
  materialPublished: boolean;
  ragChunks: RagChunk[];
  localFiles: Array<{
    sourceId: string;
    localFilePath: string;
    organizedRefPath: string;
    contentMarkdown: string;
  }>;
} {
  const node = state.nodes[nodeId];
  if (!node) {
    throw new Error(`Node "${nodeId}" not found.`);
  }

  if (state.executionMode === "cloud_rag") {
    const chunks = state.ragChunks.filter((c) => node.sourceIds.includes(c.sourceId));
    return {
      mode: "cloud_rag",
      nodeStudyGuidePath: node.studyGuidePath,
      materialPublished: node.materialPublished,
      ragChunks: chunks,
      localFiles: [],
    };
  }

  const localFiles = node.sourceIds
    .map((sid) => state.sources[sid])
    .filter((s): s is AggregatedSource => Boolean(s))
    .map((src) => {
      const diskContent = readFileFn ? readFileFn(src.localFilePath) : null;
      return {
        sourceId: src.id,
        localFilePath: src.localFilePath,
        organizedRefPath:
          src.organizedRefPath ?? `learning/${state.subjectSlug}/references/user/${src.id}.md`,
        contentMarkdown: diskContent ?? src.contentMarkdown,
      };
    });

  return {
    mode: "local_file",
    nodeStudyGuidePath: node.studyGuidePath,
    materialPublished: node.materialPublished,
    ragChunks: [],
    localFiles,
  };
}

// ============================================================================
// 6. Skill #4: Binary Search Placement & Learning Path Design
// ============================================================================

export function startPlacementQuiz(
  state: LmsWorkflowState,
  totalDifficultyTiers?: number,
  nowIso?: string,
  tokenUsage?: TokenUsageInput
): LmsWorkflowState {
  const now = nowIso ?? new Date().toISOString();
  const N = totalDifficultyTiers ?? state.placement.totalDifficultyTiers;
  if (N < 1) throw new Error("totalDifficultyTiers must be >= 1");

  const low = 1;
  const high = N;
  const currentMid = Math.floor((low + high) / 2);

  const nextState: LmsWorkflowState = {
    ...state,
    stage: "PLACEMENT_QUIZ",
    placement: {
      totalDifficultyTiers: N,
      low,
      high,
      currentMid,
      entryDifficultyLevel: 1,
      status: "in_progress",
      history: [],
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-design-learning-path",
    taskName: "placement-quiz-start",
    defaultPromptText: `Start binary search placement 1..${N}`,
    defaultCompletionText: `Initial mid=${currentMid}`,
    usage: tokenUsage,
    nowIso: now,
  });
}

export function submitPlacementQuizStep(
  state: LmsWorkflowState,
  params: {
    quizId: string;
    answeredCorrectly: boolean;
    tokenUsage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = params.nowIso ?? new Date().toISOString();
  const p = state.placement;
  if (p.status !== "in_progress" || p.currentMid === null) {
    throw new Error("Placement session is not currently in_progress.");
  }

  const mid = p.currentMid;
  let nextLow = p.low;
  let nextHigh = p.high;
  let nextEntry = p.entryDifficultyLevel;

  if (params.answeredCorrectly) {
    nextEntry = mid + 1;
    nextLow = mid + 1;
  } else {
    nextHigh = mid - 1;
  }

  const stepRecord: PlacementStepRecord = {
    stepIndex: p.history.length + 1,
    difficultyTested: mid,
    quizId: params.quizId,
    passed: params.answeredCorrectly,
    lowBefore: p.low,
    highBefore: p.high,
    lowAfter: nextLow,
    highAfter: nextHigh,
  };

  const isDone = nextLow > nextHigh;
  const resolvedEntry = Math.min(nextEntry, p.totalDifficultyTiers);
  const nextMid = isDone ? null : Math.floor((nextLow + nextHigh) / 2);

  const nextState: LmsWorkflowState = {
    ...state,
    stage: isDone ? "DESIGNING_PATH" : "PLACEMENT_QUIZ",
    placement: {
      ...p,
      low: nextLow,
      high: nextHigh,
      currentMid: nextMid,
      entryDifficultyLevel: resolvedEntry,
      status: isDone ? "completed" : "in_progress",
      history: [...p.history, stepRecord],
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-design-learning-path",
    taskName: `placement-step-${stepRecord.stepIndex}-tier-${mid}`,
    defaultPromptText: `Placement quiz ${params.quizId} at tier ${mid}`,
    defaultCompletionText: `Result=${params.answeredCorrectly ? "pass" : "fail"}, nextMid=${nextMid ?? "done"}`,
    usage: params.tokenUsage,
    nowIso: now,
  });
}

export interface DraftLearningPathInput {
  capstoneProject?: CapstoneProject;
  nodes: Array<{
    id: string;
    title: string;
    summary?: string;
    description?: string;
    difficultyLevel: number;
    prerequisites: string[];
    sourceIds: string[];
    projectMilestone?: string;
  }>;
  tokenUsage?: TokenUsageInput;
  nowIso?: string;
}

/**
 * Skill #4: Drafts the node-based DAG learning path and capstone project.
 * Automatically assigns ordered module filenames (`learning/<slug>/modules/01-<id>.md`)
 * and marks nodes below `placement.entryDifficultyLevel` as `mastered_by_placement`.
 */
export function designLearningPath(
  state: LmsWorkflowState,
  input: DraftLearningPathInput
): LmsWorkflowState {
  const now = input.nowIso ?? new Date().toISOString();
  const entryLevel = state.placement.entryDifficultyLevel;
  const nextNodes: Record<string, LearningNode> = {};

  const sortedInputNodes = [...input.nodes].sort(
    (a, b) => a.difficultyLevel - b.difficultyLevel
  );

  sortedInputNodes.forEach((n, index) => {
    const prevNode = state.nodes[n.id];
    const moduleNum = index + 1;
    const paddedNum = String(moduleNum).padStart(2, "0");
    const status: NodeStatus =
      n.difficultyLevel < entryLevel ? "mastered_by_placement" : "locked";
    const resolvedSummary = n.summary ?? n.description ?? prevNode?.summary ?? n.title;

    nextNodes[n.id] = {
      id: n.id,
      moduleNumber: moduleNum,
      title: n.title,
      summary: resolvedSummary,
      difficultyLevel: n.difficultyLevel,
      prerequisites: n.prerequisites,
      sourceIds: n.sourceIds,
      githubMdPath: `lms/${state.subjectSlug}/nodes/${n.id}.md`,
      localFilePath: `${state.localKnowledgeRoot}/nodes/${n.id}.md`,
      studyGuidePath: `learning/${state.subjectSlug}/modules/${paddedNum}-${n.id}.md`,
      materialPublished:
        status === "mastered_by_placement" ? true : (prevNode?.materialPublished ?? false),
      studyMaterialMarkdown: prevNode?.studyMaterialMarkdown,
      qaLog: prevNode?.qaLog ?? [],
      projectMilestone: n.projectMilestone,
      status,
    };
  });

  const nextState: LmsWorkflowState = {
    ...state,
    stage: "VALIDATING_PATH",
    capstoneProject: input.capstoneProject ?? state.capstoneProject,
    nodes: nextNodes,
    pathValidation: {
      ...state.pathValidation,
      valid: false,
      structuralErrors: [],
      pedagogicalCritiques: [],
      status: "pending",
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-design-learning-path",
    taskName: "design-learning-path-dag",
    defaultPromptText: JSON.stringify(Object.keys(state.sources)),
    defaultCompletionText: JSON.stringify(input.nodes),
    usage: input.tokenUsage,
    nowIso: now,
  });
}

// ============================================================================
// 7. Skill #5: Learning Path Validation (Deterministic DAG + Agent Review)
// ============================================================================

export function detectDagCycles(nodes: Record<string, LearningNode>): string[] {
  const color: Record<string, "white" | "gray" | "black"> = {};
  for (const id of Object.keys(nodes)) color[id] = "white";

  const cycles: string[] = [];

  const dfs = (id: string, stack: string[]) => {
    color[id] = "gray";
    const node = nodes[id];
    if (node) {
      for (const prereqId of node.prerequisites) {
        if (!nodes[prereqId]) continue;
        if (color[prereqId] === "gray") {
          cycles.push(`Cycle detected involving: ${[...stack, id, prereqId].join(" -> ")}`);
        } else if (color[prereqId] === "white") {
          dfs(prereqId, [...stack, id]);
        }
      }
    }
    color[id] = "black";
  };

  for (const id of Object.keys(nodes)) {
    if (color[id] === "white") {
      dfs(id, []);
    }
  }

  return cycles;
}

export function validateLearningPath(
  state: LmsWorkflowState,
  agentReview?: {
    decision: "accept" | "raise_issues";
    pedagogicalCritiques?: string[];
    tokenUsage?: TokenUsageInput;
  },
  nowIso?: string
): LmsWorkflowState {
  const now = nowIso ?? new Date().toISOString();
  const nodeList = Object.values(state.nodes);
  const structuralErrors: string[] = [];

  if (nodeList.length === 0) {
    structuralErrors.push("Learning path contains zero nodes.");
  }

  for (const node of nodeList) {
    for (const prereqId of node.prerequisites) {
      const prereq = state.nodes[prereqId];
      if (!prereq) {
        structuralErrors.push(
          `Node "${node.id}" references non-existent prerequisite "${prereqId}".`
        );
      } else if (prereq.difficultyLevel > node.difficultyLevel) {
        structuralErrors.push(
          `Difficulty inversion: Prerequisite "${prereq.id}" (tier ${prereq.difficultyLevel}) is harder than dependent node "${node.id}" (tier ${node.difficultyLevel}).`
        );
      }
    }

    if (node.sourceIds.length === 0) {
      structuralErrors.push(`Node "${node.id}" has no linked knowledge sources.`);
    } else {
      for (const sid of node.sourceIds) {
        if (!state.sources[sid]) {
          structuralErrors.push(
            `Node "${node.id}" references unknown sourceId "${sid}".`
          );
        }
      }
    }
  }

  const cycles = detectDagCycles(state.nodes);
  structuralErrors.push(...cycles);

  if (state.capstoneProject && state.capstoneProject.milestones.length > 0) {
    const nodesWithMilestones = nodeList.filter((n) => Boolean(n.projectMilestone));
    if (nodesWithMilestones.length === 0) {
      structuralErrors.push(
        `Capstone project "${state.capstoneProject.title}" is defined, but no learning path nodes link to a projectMilestone.`
      );
    }
  }

  const nextRound = state.pathValidation.round + 1;
  const critiques = agentReview?.pedagogicalCritiques ?? [];
  const agentAccepted = !agentReview || agentReview.decision === "accept";
  const isValid = structuralErrors.length === 0 && agentAccepted;

  let nextState: LmsWorkflowState;

  if (isValid) {
    const unlockedNodes: Record<string, LearningNode> = {};
    for (const [id, node] of Object.entries(state.nodes)) {
      if (node.status === "locked") {
        const prereqsMet = node.prerequisites.every(
          (pid) => state.nodes[pid]?.status === "mastered_by_placement"
        );
        unlockedNodes[id] = {
          ...node,
          status: prereqsMet ? "unlocked" : "locked",
        };
      } else {
        unlockedNodes[id] = node;
      }
    }

    nextState = {
      ...state,
      stage: "TEACHING_ACTIVE",
      nodes: unlockedNodes,
      pathValidation: {
        valid: true,
        round: nextRound,
        structuralErrors: [],
        pedagogicalCritiques: [],
        status: "accepted",
      },
      updatedAt: now,
    };
  } else {
    const escalated = nextRound >= state.maxReviewRounds;
    nextState = {
      ...state,
      stage: escalated ? "PAUSED_FOR_USER_PATH_DECISION" : "DESIGNING_PATH",
      pathValidation: {
        valid: false,
        round: nextRound,
        structuralErrors,
        pedagogicalCritiques: critiques,
        status: escalated ? "escalated_to_user" : "revision_requested",
      },
      updatedAt: now,
    };
  }

  return recordTokenUsage(nextState, {
    skill: "lms-validate-learning-path",
    taskName: `validate-learning-path-round-${nextRound}`,
    defaultPromptText: JSON.stringify(state.nodes),
    defaultCompletionText: JSON.stringify(nextState.pathValidation),
    usage: agentReview?.tokenUsage,
    nowIso: now,
  });
}

// ============================================================================
// 8. Skill #6: Teaching Orchestrator (Markdown Material Publishing, Q&A & Quizzes)
// ============================================================================

/**
 * Skill #6: Publishes the organized Markdown learning material for a node so the
 * learner reads from the Markdown file (`learning/<slug>/modules/<NN>-<nodeId>.md`)
 * instead of being lectured in the CLI/chat.
 */
export function publishNodeLearningMaterial(
  state: LmsWorkflowState,
  params: {
    nodeId: string;
    studyMaterialMarkdown: string;
    tokenUsage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = params.nowIso ?? new Date().toISOString();
  const node = state.nodes[params.nodeId];
  if (!node) {
    throw new Error(`Node "${params.nodeId}" not found.`);
  }
  if (!params.studyMaterialMarkdown || params.studyMaterialMarkdown.trim().length < 40) {
    throw new Error(
      `studyMaterialMarkdown for node "${params.nodeId}" must be a comprehensive Markdown study guide (at least 40 characters).`
    );
  }

  const updatedNode: LearningNode = {
    ...node,
    materialPublished: true,
    studyMaterialMarkdown: params.studyMaterialMarkdown.trim(),
    status: node.status === "unlocked" ? "in_progress" : node.status,
  };

  const nextState: LmsWorkflowState = {
    ...state,
    nodes: {
      ...state.nodes,
      [params.nodeId]: updatedNode,
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-teaching-orchestrator",
    taskName: `publish-material-${params.nodeId}`,
    nodeId: params.nodeId,
    defaultPromptText: `Synthesize organized Markdown study guide for ${node.title}`,
    defaultCompletionText: params.studyMaterialMarkdown,
    usage: params.tokenUsage,
    nowIso: now,
  });
}

/**
 * Skill #6: Records a learner's question and the agent's answer for a specific node,
 * appending it to the node's organized Markdown file so all Q&A stays with the reading material.
 */
export function recordNodeQaInteraction(
  state: LmsWorkflowState,
  params: {
    nodeId: string;
    question: string;
    answerMarkdown: string;
    tokenUsage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = params.nowIso ?? new Date().toISOString();
  const node = state.nodes[params.nodeId];
  if (!node) {
    throw new Error(`Node "${params.nodeId}" not found.`);
  }
  if (!params.question || !params.answerMarkdown) {
    throw new Error("Both question and answerMarkdown are required.");
  }

  const qaEntry: NodeQaEntry = {
    id: `qa-${(node.qaLog?.length ?? 0) + 1}`,
    question: params.question.trim(),
    answerMarkdown: params.answerMarkdown.trim(),
    timestamp: now,
  };

  const updatedNode: LearningNode = {
    ...node,
    qaLog: [...(node.qaLog ?? []), qaEntry],
  };

  const nextState: LmsWorkflowState = {
    ...state,
    nodes: {
      ...state.nodes,
      [params.nodeId]: updatedNode,
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-teaching-orchestrator",
    taskName: `qa-${params.nodeId}-${qaEntry.id}`,
    nodeId: params.nodeId,
    defaultPromptText: params.question,
    defaultCompletionText: params.answerMarkdown,
    usage: params.tokenUsage,
    nowIso: now,
  });
}

/**
 * Creates a quiz for a node (Mastery Gate or Spaced Repetition) or Placement.
 * Enforces that:
 * 1. mcq_4 quizzes have strictly 4 choices (A, B, C, D).
 * 2. For `mastery_gate` quizzes, the node's Markdown learning material MUST already
 *    be published (`node.materialPublished === true`) so the learner reads from the
 *    organized Markdown file before taking the quiz!
 */
export function registerQuiz(
  state: LmsWorkflowState,
  quiz: Omit<QuizItem, "completed" | "passed" | "score" | "feedback"> & {
    tokenUsage?: TokenUsageInput;
  },
  nowIso?: string
): LmsWorkflowState {
  const now = nowIso ?? new Date().toISOString();

  if (quiz.modality === "mcq_4") {
    if (!quiz.options || quiz.options.length !== 4 || !quiz.correctOption) {
      throw new Error("mcq_4 quiz requires exactly 4 options (A, B, C, D) and a correctOption.");
    }
  }

  if (quiz.purpose === "mastery_gate" && quiz.nodeId) {
    const targetNode = state.nodes[quiz.nodeId];
    if (!targetNode) {
      throw new Error(`Node "${quiz.nodeId}" not found.`);
    }
    if (!targetNode.materialPublished) {
      throw new Error(
        `Cannot register a Mastery Gate quiz for node "${targetNode.id}" before its learning material is written to "${targetNode.studyGuidePath}". Run "material-publish ${state.subjectSlug} ..." first so the learner reads from the organized Markdown file.`
      );
    }
  }

  const { tokenUsage, ...quizFields } = quiz;
  const item: QuizItem = {
    ...quizFields,
    completed: false,
  };

  const nextState: LmsWorkflowState = {
    ...state,
    quizzes: {
      ...state.quizzes,
      [quiz.id]: item,
    },
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-teaching-orchestrator",
    taskName: `register-quiz-${quiz.id}`,
    nodeId: quiz.nodeId,
    defaultPromptText: `Generate ${quiz.purpose} quiz (${quiz.modality}) for node ${quiz.nodeId ?? "placement"}`,
    defaultCompletionText: JSON.stringify(item),
    usage: tokenUsage,
    nowIso: now,
  });
}

export function computeNextSpacedRepetition(
  prev: SpacedRepetitionState | undefined,
  nodeId: string,
  qualityGrade: 0 | 1 | 2 | 3 | 4 | 5,
  nowIso: string
): SpacedRepetitionState {
  const nowMs = new Date(nowIso).getTime();
  const prevReps = prev?.reps ?? 0;
  const prevLapses = prev?.lapses ?? 0;
  const prevInterval = prev?.intervalDays ?? 0;
  const prevEf = prev?.easinessFactor ?? 2.5;

  if (qualityGrade < 3) {
    const nextReviewMs = nowMs + 1 * 24 * 60 * 60 * 1000;
    return {
      nodeId,
      reps: 0,
      lapses: prevLapses + 1,
      intervalDays: 1,
      easinessFactor: Math.max(1.3, Number((prevEf - 0.2).toFixed(2))),
      lastReviewedAt: nowIso,
      nextReviewAt: new Date(nextReviewMs).toISOString(),
      needsRefresher: true,
    };
  }

  const nextReps = prevReps + 1;
  let nextInterval: number;
  if (nextReps === 1) {
    nextInterval = 1;
  } else if (nextReps === 2) {
    nextInterval = 3;
  } else {
    nextInterval = Math.round(prevInterval * prevEf);
  }

  const efDelta = 0.1 - (5 - qualityGrade) * (0.08 + (5 - qualityGrade) * 0.02);
  const nextEf = Math.max(1.3, Number((prevEf + efDelta).toFixed(2)));
  const nextReviewMs = nowMs + nextInterval * 24 * 60 * 60 * 1000;

  return {
    nodeId,
    reps: nextReps,
    lapses: prevLapses,
    intervalDays: nextInterval,
    easinessFactor: nextEf,
    lastReviewedAt: nowIso,
    nextReviewAt: new Date(nextReviewMs).toISOString(),
    needsRefresher: false,
  };
}

export function submitNodeQuizAttempt(
  state: LmsWorkflowState,
  params: {
    quizId: string;
    selectedOption?: "A" | "B" | "C" | "D"; // For mcq_4
    evaluatedScore?: number;               // 0.0 .. 1.0 for open_ended / code_challenge / project_task
    feedback?: string;
    tokenUsage?: TokenUsageInput;
    nowIso?: string;
  }
): LmsWorkflowState {
  const now = params.nowIso ?? new Date().toISOString();
  const quiz = state.quizzes[params.quizId];
  if (!quiz) {
    throw new Error(`Quiz "${params.quizId}" not found.`);
  }
  if (!quiz.nodeId) {
    throw new Error(`Quiz "${params.quizId}" is not attached to a learning node.`);
  }
  const node = state.nodes[quiz.nodeId];
  if (!node) {
    throw new Error(`Node "${quiz.nodeId}" not found.`);
  }

  let score = 0;
  if (quiz.modality === "mcq_4") {
    score = params.selectedOption === quiz.correctOption ? 1.0 : 0.0;
  } else {
    score = params.evaluatedScore ?? 0.0;
  }

  const passed = score >= quiz.passingScoreThreshold;
  const qualityGrade: 0 | 1 | 2 | 3 | 4 | 5 = passed
    ? score >= 0.95
      ? 5
      : score >= 0.85
      ? 4
      : 3
    : score >= 0.5
    ? 2
    : 1;

  const updatedQuiz: QuizItem = {
    ...quiz,
    completed: true,
    passed,
    score,
    feedback: params.feedback ?? (passed ? "Passed mastery check." : "Needs additional review."),
  };

  const nextNodes = { ...state.nodes };
  const nextSrs = { ...state.spacedRepetition };

  if (quiz.purpose === "mastery_gate") {
    if (node.status === "locked") {
      throw new Error(`Cannot take Mastery Gate quiz for locked node "${node.id}".`);
    }

    if (passed) {
      nextNodes[node.id] = {
        ...node,
        status: "completed",
      };

      for (const [candidateId, candidate] of Object.entries(nextNodes)) {
        if (candidate.status === "locked") {
          const allPrereqsDone = candidate.prerequisites.every((pid) => {
            const st = nextNodes[pid]?.status;
            return st === "completed" || st === "mastered_by_placement";
          });
          if (allPrereqsDone) {
            nextNodes[candidateId] = {
              ...candidate,
              status: "unlocked",
            };
          }
        }
      }

      nextSrs[node.id] = computeNextSpacedRepetition(
        nextSrs[node.id],
        node.id,
        qualityGrade,
        now
      );
    } else {
      nextNodes[node.id] = {
        ...node,
        status: "in_progress",
      };
    }
  } else if (quiz.purpose === "spaced_repetition") {
    nextSrs[node.id] = computeNextSpacedRepetition(
      nextSrs[node.id],
      node.id,
      qualityGrade,
      now
    );
  }

  const allCompleted =
    Object.values(nextNodes).length > 0 &&
    Object.values(nextNodes).every(
      (n) => n.status === "completed" || n.status === "mastered_by_placement"
    );

  const nextState: LmsWorkflowState = {
    ...state,
    stage: allCompleted ? "SUBJECT_COMPLETED" : state.stage,
    nodes: nextNodes,
    quizzes: {
      ...state.quizzes,
      [quiz.id]: updatedQuiz,
    },
    spacedRepetition: nextSrs,
    updatedAt: now,
  };

  return recordTokenUsage(nextState, {
    skill: "lms-teaching-orchestrator",
    taskName: `submit-quiz-${quiz.id}`,
    nodeId: quiz.nodeId,
    defaultPromptText: `${quiz.questionPrompt} | answer=${params.selectedOption ?? score}`,
    defaultCompletionText: updatedQuiz.feedback ?? "",
    usage: params.tokenUsage,
    nowIso: now,
  });
}

export function getDueSpacedRepetitionNodes(
  state: LmsWorkflowState,
  asOfIso: string
): SpacedRepetitionState[] {
  const asOfMs = new Date(asOfIso).getTime();
  return Object.values(state.spacedRepetition).filter((srs) => {
    const dueMs = new Date(srs.nextReviewAt).getTime();
    return srs.needsRefresher || dueMs <= asOfMs;
  });
}

// ============================================================================
// 9. Organized Learning Material Generators (Course Index, Modules, Capstone)
// ============================================================================

/**
 * Generates the organized Master Course Index (`learning/<slug>/README.md`)
 * so the learner has a single organized hub linking to all module Markdown files,
 * placement results, capstone milestones, and reference materials.
 */
export function generateCourseIndexMarkdown(state: LmsWorkflowState): string {
  const nodes = Object.values(state.nodes).sort(
    (a, b) => a.difficultyLevel - b.difficultyLevel
  );
  const sources = Object.values(state.sources);

  const lines: string[] = [
    `# ${state.subjectTitle} — Personalized Learning Workspace`,
    ``,
    `> **Subject Slug**: \`${state.subjectSlug}\`  `,
    `> **Learner Profile**: Style = \`${state.learnerStyle.mode}\`, Pace = \`${state.learnerStyle.pace}\`  `,
    `> **Diagnostic Placement**: Entry Difficulty = **Tier ${state.placement.entryDifficultyLevel}** / ${state.placement.totalDifficultyTiers} (${state.placement.status})  `,
    `> **Workflow Stage**: \`${state.stage}\``,
    ``,
    `## How to Use This Learning Workspace`,
    `1. **Read from the Module Markdown Files**: All study material is organized in the [\`modules/\`](./modules/) directory below. Open the currently **Unlocked / In-Progress** module file to study at your own pace.`,
    `2. **Ask Questions Through the Agent**: Whenever you want deeper intuition, examples, or clarification on anything you read, ask the Agent in chat. Your Q&A is also saved directly at the bottom of each module's Markdown file.`,
    `3. **Pass Mastery Gate Quizzes Through the Agent**: Once you finish reading a module, tell the Agent you are ready for the **Mastery Gate Quiz**. Passing unlocks and publishes the next module's study guide.`,
    ``,
    `---`,
    ``,
    `## 1. Learning Path Modules (DAG)`,
    ``,
    `| # | Module Study Guide | Difficulty | Status | Material Status | Prerequisites | Capstone Milestone |`,
    `| :- | :--- | :---: | :--- | :--- | :--- | :--- |`,
  ];

  if (nodes.length === 0) {
    lines.push(`| - | *(No modules drafted yet)* | - | - | - | - | - |`);
  } else {
    nodes.forEach((n, idx) => {
      const modNum = String(n.moduleNumber ?? idx + 1).padStart(2, "0");
      const relPath = `./modules/${modNum}-${n.id}.md`;
      const statusBadge =
        n.status === "mastered_by_placement"
          ? "⚡ Mastered (Placement)"
          : n.status === "completed"
          ? "✅ Completed"
          : n.status === "in_progress"
          ? "📖 In Progress (Reading)"
          : n.status === "unlocked"
          ? "🔓 Unlocked (Current)"
          : "🔒 Locked";
      const matBadge =
        n.status === "mastered_by_placement"
          ? "Reference Ready"
          : n.materialPublished
          ? "📘 Study Guide Published"
          : "⏳ Pending Agent Publish";
      const prereqs = n.prerequisites.length > 0 ? n.prerequisites.map((p) => `\`${p}\``).join(", ") : "None";
      lines.push(
        `| **${modNum}** | [**${n.title}**](${relPath}) | Tier ${n.difficultyLevel} | ${statusBadge} | ${matBadge} | ${prereqs} | ${n.projectMilestone ?? "-"} |`
      );
    });
  }

  if (state.capstoneProject) {
    lines.push(
      ``,
      `---`,
      ``,
      `## 2. Capstone Project: ${state.capstoneProject.title}`,
      ``,
      `${state.capstoneProject.description}`,
      ``,
      `- **Full Specification**: [\`capstone/PROJECT_BRIEF.md\`](./capstone/PROJECT_BRIEF.md)`,
      `- **Milestones**:`
    );
    for (const m of state.capstoneProject.milestones) {
      lines.push(`  - [ ] ${m}`);
    }
  }

  lines.push(
    ``,
    `---`,
    ``,
    `## 3. Curated Reference Sources`,
    ``
  );

  if (sources.length === 0) {
    lines.push(`*(No reference sources ingested yet)*`);
  } else {
    for (const s of sources) {
      const subFolder = s.track === "byos" ? "user" : "research";
      const relRef = `./references/${subFolder}/${s.id.replace(/^res-/, "")}.md`;
      lines.push(`- [**${s.title}**](${relRef}) (\`${s.track}\` / \`${s.perspective}\`)`);
    }
  }

  lines.push(``);
  return lines.join("\n");
}

/**
 * Generates the organized Markdown file for an individual learning node
 * (`learning/<slug>/modules/<NN>-<nodeId>.md`).
 */
export function generateNodeModuleMarkdown(state: LmsWorkflowState, node: LearningNode): string {
  const modNum = String(node.moduleNumber ?? 1).padStart(2, "0");
  const statusLabel =
    node.status === "mastered_by_placement"
      ? "⚡ Mastered via Binary Search Placement"
      : node.status === "completed"
      ? "✅ Completed (Mastery Gate Passed)"
      : node.status === "in_progress"
      ? "📖 Active Module (Ready to Read)"
      : node.status === "unlocked"
      ? "🔓 Unlocked"
      : "🔒 Locked (Complete Prerequisites First)";

  const headerLines: string[] = [
    `# Module ${modNum}: ${node.title}`,
    ``,
    `> **Subject**: [${state.subjectTitle}](../README.md)  `,
    `> **Node ID**: \`${node.id}\` | **Difficulty**: Tier ${node.difficultyLevel} / ${state.placement.totalDifficultyTiers}  `,
    `> **Status**: ${statusLabel}  `,
    `> **Prerequisites**: ${node.prerequisites.length > 0 ? node.prerequisites.map((p) => `\`${p}\``).join(", ") : "None (Entry Module)"}  `,
    `> **Capstone Milestone**: ${node.projectMilestone ?? "N/A"}`,
    ``,
    `---`,
    ``,
  ];

  const bodyLines: string[] = [];

  if (node.studyMaterialMarkdown && node.studyMaterialMarkdown.trim().length > 0) {
    bodyLines.push(node.studyMaterialMarkdown.trim());
  } else if (node.status === "mastered_by_placement") {
    const linkedSourceText = node.sourceIds
      .map((sid) => state.sources[sid]?.contentMarkdown)
      .filter(Boolean)
      .join("\n\n---\n\n");

    bodyLines.push(
      `## Placement Mastery Summary`,
      `You demonstrated mastery of **${node.title}** (Tier ${node.difficultyLevel}) during the Binary Search Diagnostic Placement Quiz. This module is marked as mastered so you can skip ahead to your active boundary.`,
      ``,
      `### Module Overview`,
      node.summary || node.title,
      ``,
      linkedSourceText ? `## Quick Reference Notes\n\n${linkedSourceText}` : ``
    );
  } else {
    bodyLines.push(
      `## Module Overview`,
      node.summary || node.title,
      ``,
      `> **Note for Agent**: Publish the full, tailored study guide for this module using:`,
      `> \`npx tsx .agents/skills/lms-teaching-orchestrator/scripts/workflow-cli.ts material-publish ${state.subjectSlug} '{"nodeId":"${node.id}","studyMaterialMarkdown":"..."}'\``
    );
  }

  const qaLines: string[] = [];
  if (node.qaLog && node.qaLog.length > 0) {
    qaLines.push(
      ``,
      `---`,
      ``,
      `## Learner Q&A & Clarifications (Answered by Agent)`,
      ``
    );
    for (const qa of node.qaLog) {
      qaLines.push(
        `### Q (${qa.id}): ${qa.question}`,
        `${qa.answerMarkdown}`,
        ``
      );
    }
  }

  const footerLines: string[] = [
    ``,
    `---`,
    ``,
    `## Next Steps (Interactive via Agent)`,
    `- **Have a question about this material?** Ask the Agent in chat—your question and the explanation will be answered interactively and appended above.`,
    `- **Ready to test your mastery?** Tell the Agent: *"I finished reading \`${node.id}\` and I'm ready for the Mastery Gate Quiz."*`,
    ``,
  ];

  return [...headerLines, ...bodyLines, ...qaLines, ...footerLines].join("\n");
}

export function generateCapstoneBriefMarkdown(state: LmsWorkflowState): string {
  const cap = state.capstoneProject;
  const nodes = Object.values(state.nodes).sort(
    (a, b) => a.difficultyLevel - b.difficultyLevel
  );

  if (!cap) {
    return `# Capstone Project Brief: ${state.subjectTitle}\n\n*(No capstone project defined yet)*\n`;
  }

  const lines: string[] = [
    `# Capstone Project: ${cap.title}`,
    ``,
    `> **Subject**: [${state.subjectTitle}](../README.md) (\`${state.subjectSlug}\`)`,
    ``,
    `## Project Overview`,
    cap.description,
    ``,
    `## Milestones & Linked Learning Modules`,
    ``,
    `| Milestone | Linked Modules | Status |`,
    `| :--- | :--- | :--- |`,
  ];

  for (const m of cap.milestones) {
    const linked = nodes.filter((n) => n.projectMilestone === m);
    const modLinks =
      linked.length > 0
        ? linked
            .map((n) => {
              const num = String(n.moduleNumber ?? 1).padStart(2, "0");
              return `[${n.title}](../modules/${num}-${n.id}.md)`;
            })
            .join(", ")
        : "*(Unassigned)*";
    const allDone =
      linked.length > 0 &&
      linked.every((n) => n.status === "completed" || n.status === "mastered_by_placement");
    lines.push(`| **${m}** | ${modLinks} | ${allDone ? "✅ Complete" : "⏳ In Progress"} |`);
  }

  lines.push(``);
  return lines.join("\n");
}

// ============================================================================
// 10. Visualization, Artifacts & Token Maintenance Report Generators
// ============================================================================

export function generateResearchDebateArtifact(topic: ResearchTopicState): string {
  const lines: string[] = [
    `# Research Peer-Review Debate Artifact: ${topic.title} (\`${topic.id}\`)`,
    ``,
    `- **Status**: \`${topic.status}\``,
    `- **Review Round**: ${topic.reviewRound} / ${topic.maxRounds}`,
    `- **Required Perspectives**: ${topic.requiredPerspectives.map((p) => `\`${p}\``).join(", ")}`,
    `- **Covered Perspectives**: ${topic.coveredPerspectives.map((p) => `\`${p}\``).join(", ")}`,
    `- **Citations**: ${topic.citations.join(", ")}`,
    ``,
    `## 1. Peer-Review Debate Transcript`,
    ``,
  ];

  for (const turn of topic.debateHistory) {
    lines.push(
      `### Round ${turn.round} — \`${turn.actor}\` (\`${turn.action}\`) at ${turn.timestamp}`,
      `- **Summary**: ${turn.summary}`
    );
    if (turn.defenseArgument) {
      lines.push(`- **Defense Argument**: ${turn.defenseArgument}`);
    }
    if (turn.defects && turn.defects.length > 0) {
      lines.push(`- **Defects Raised**:`);
      for (const d of turn.defects) {
        lines.push(`  - ${d}`);
      }
    }
    lines.push(``);
  }

  lines.push(
    `## 2. Current Research Draft Markdown`,
    ``,
    topic.currentDraftMarkdown,
    ``
  );

  return lines.join("\n");
}

export function generateTokenMaintenanceMarkdownReport(state: LmsWorkflowState): string {
  const tel = state.tokenTelemetry;
  const lines: string[] = [
    `# Token Usage & Workflow Maintenance Report: ${state.subjectTitle} (\`${state.subjectSlug}\`)`,
    ``,
    `- **Execution Mode**: \`${state.executionMode}\` (${
      state.executionMode === "local_file"
        ? "Mode #1 (Local Default): Local File-Based Knowledge System (No RAG)"
        : "Mode #2: Cloud System + RAG Chunks"
    })`,
    `- **Active Stage**: \`${state.stage}\``,
    `- **Organized Learning Dir**: \`${state.artifacts.learningOutDir}\` (Index: \`${state.artifacts.courseIndexPath}\`)`,
    `- **Total Tasks Executed**: ${tel.total.taskCount}`,
    `- **Total Tokens**: **${tel.total.totalTokens.toLocaleString()}** (Prompt: ${tel.total.promptTokens.toLocaleString()} | Completion: ${tel.total.completionTokens.toLocaleString()})`,
    `- **JSONL Audit Log**: \`${tel.logFilePath}\``,
    `- **Artifacts Directory**: \`${state.artifacts.artifactsRootDir}\``,
    ``,
    `## 1. Token Usage by Skill`,
    ``,
    `| Skill | Tasks | Prompt Tokens | Completion Tokens | Total Tokens |`,
    `| :--- | ---: | ---: | ---: | ---: |`,
  ];

  for (const [skill, bucket] of Object.entries(tel.bySkill)) {
    lines.push(
      `| \`${skill}\` | ${bucket.taskCount} | ${bucket.promptTokens} | ${bucket.completionTokens} | **${bucket.totalTokens}** |`
    );
  }

  lines.push(
    ``,
    `## 2. Token Usage by Learning Node`,
    ``,
    `| Node ID | Node Title | Study Guide File | Tasks | Prompt Tokens | Completion Tokens | Total Tokens |`,
    `| :--- | :--- | :--- | ---: | ---: | ---: | ---: |`
  );

  const nodeEntries = Object.entries(tel.byNode);
  if (nodeEntries.length === 0) {
    lines.push(`| *(none)* | *(no node-specific tasks yet)* | - | 0 | 0 | 0 | 0 |`);
  } else {
    for (const [nodeId, bucket] of nodeEntries) {
      const n = state.nodes[nodeId];
      const title = n?.title ?? nodeId;
      const guide = n?.studyGuidePath ?? "-";
      lines.push(
        `| \`${nodeId}\` | ${title} | \`${guide}\` | ${bucket.taskCount} | ${bucket.promptTokens} | ${bucket.completionTokens} | **${bucket.totalTokens}** |`
      );
    }
  }

  lines.push(
    ``,
    `## 3. Token Usage by Research Topic`,
    ``,
    `| Topic ID | Status | Rounds | Tasks | Total Tokens |`,
    `| :--- | :--- | ---: | ---: | ---: |`
  );

  const topicEntries = Object.entries(tel.byTopic);
  if (topicEntries.length === 0) {
    lines.push(`| *(none)* | - | 0 | 0 | 0 |`);
  } else {
    for (const [topicId, bucket] of topicEntries) {
      const t = state.researchTopics[topicId];
      lines.push(
        `| \`${topicId}\` | ${t?.status ?? "unknown"} | ${t?.reviewRound ?? 0}/${t?.maxRounds ?? 3} | ${bucket.taskCount} | **${bucket.totalTokens}** |`
      );
    }
  }

  lines.push(
    ``,
    `## 4. Chronological Task Telemetry Log`,
    ``,
    `| # | Timestamp | Skill | Task | Node / Topic | Model | Prompt | Completion | Total | Source |`,
    `| :- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- |`
  );

  for (const e of tel.entries) {
    const target = e.nodeId ? `node:${e.nodeId}` : e.topicId ? `topic:${e.topicId}` : "-";
    lines.push(
      `| ${e.id} | ${e.timestamp} | \`${e.skill}\` | \`${e.taskName}\` | \`${target}\` | ${e.model} | ${e.promptTokens} | ${e.completionTokens} | **${e.totalTokens}** | ${e.isEstimated ? "est" : "exact"} |`
    );
  }

  return lines.join("\n") + "\n";
}

export function generateWorkflowMermaid(state: LmsWorkflowState): string {
  const stages: WorkflowStage[] = [
    "IDLE",
    "RESEARCHING",
    "VALIDATING_RESEARCH",
    "PAUSED_FOR_USER_RESEARCH_DECISION",
    "AGGREGATING_SOURCES",
    "PLACEMENT_QUIZ",
    "DESIGNING_PATH",
    "VALIDATING_PATH",
    "PAUSED_FOR_USER_PATH_DECISION",
    "TEACHING_ACTIVE",
    "SUBJECT_COMPLETED",
  ];

  const modeLabel =
    state.executionMode === "local_file"
      ? "#3 Aggregate into Local Markdown Files (No RAG)"
      : "#3 Aggregate into Sources + Cloud RAG Chunks";

  const lines: string[] = [
    "flowchart TD",
    `    IDLE["0. Idle / Subject Init (mode=${state.executionMode})"] --> RESEARCHING["#1 Research Document (Sub-Agent)"]`,
    '    RESEARCHING --> VALIDATING_RESEARCH["#2 Validate Research (Max 3 Rounds)"]',
    '    VALIDATING_RESEARCH -- "Issues Raised (Round < 3)" --> RESEARCHING',
    '    VALIDATING_RESEARCH -- "Failed Round 3 (Halt)" --> PAUSED_FOR_USER_RESEARCH_DECISION["User Decision Required (Research)"]',
    '    PAUSED_FOR_USER_RESEARCH_DECISION -- "User Override" --> AGGREGATING_SOURCES',
    `    VALIDATING_RESEARCH -- "Accepted" --> AGGREGATING_SOURCES["${modeLabel}"]`,
    '    AGGREGATING_SOURCES --> PLACEMENT_QUIZ["#4a Binary Search Placement Quiz"]',
    '    PLACEMENT_QUIZ --> DESIGNING_PATH["#4b Design Node-Based Learning Path"]',
    '    DESIGNING_PATH --> VALIDATING_PATH["#5 Learning Path Validation (DAG + Grounding)"]',
    '    VALIDATING_PATH -- "Invalid / Critique (Round < 3)" --> DESIGNING_PATH',
    '    VALIDATING_PATH -- "Failed Round 3" --> PAUSED_FOR_USER_PATH_DECISION["User Decision Required (Path)"]',
    '    VALIDATING_PATH -- "Valid & Unlocked" --> TEACHING_ACTIVE["#6 Publish Markdown Study Guides & Run Q&A/Quizzes"]',
    '    TEACHING_ACTIVE --> SUBJECT_COMPLETED["Subject & Capstone Completed"]',
  ];

  for (const st of stages) {
    if (st === state.stage) {
      lines.push(`    style ${st} fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:3px`);
    }
  }

  return lines.join("\n");
}

export function generateLearningPathDagMermaid(state: LmsWorkflowState): string {
  const nodeList = Object.values(state.nodes);
  if (nodeList.length === 0) {
    return 'flowchart LR\n    empty["No learning path nodes drafted yet"]';
  }

  const lines: string[] = ["flowchart LR"];
  for (const node of nodeList) {
    const srs = state.spacedRepetition[node.id];
    const tok = state.tokenTelemetry.byNode[node.id];
    const badge =
      node.status === "mastered_by_placement"
        ? "⚡ Mastered (Placement)"
        : node.status === "completed"
        ? srs?.needsRefresher
          ? "⚠️ Completed (Refresher Due)"
          : "✅ Completed"
        : node.status === "unlocked"
        ? "🔓 Unlocked"
        : node.status === "in_progress"
        ? "📖 Reading Study Guide"
        : "🔒 Locked";

    const milestoneLabel = node.projectMilestone ? `<br/>🛠 ${node.projectMilestone}` : "";
    const tokenLabel = tok ? `<br/>🪙 ${tok.totalTokens} tokens` : "";
    lines.push(
      `    ${node.id}["[Tier ${node.difficultyLevel}] ${node.title}<br/>(${badge})${milestoneLabel}${tokenLabel}"]`
    );

    for (const prereq of node.prerequisites) {
      lines.push(`    ${prereq} --> ${node.id}`);
    }

    if (node.status === "completed" && !srs?.needsRefresher) {
      lines.push(`    style ${node.id} fill:#059669,stroke:#047857,color:#fff`);
    } else if (node.status === "mastered_by_placement") {
      lines.push(`    style ${node.id} fill:#2563eb,stroke:#1d4ed8,color:#fff`);
    } else if (node.status === "unlocked" || node.status === "in_progress") {
      lines.push(`    style ${node.id} fill:#d97706,stroke:#b45309,color:#fff`);
    } else if (srs?.needsRefresher) {
      lines.push(`    style ${node.id} fill:#dc2626,stroke:#b91c1c,color:#fff`);
    } else {
      lines.push(`    style ${node.id} fill:#374151,stroke:#1f2937,color:#9ca3af`);
    }
  }

  return lines.join("\n");
}

export function renderAsciiDashboard(state: LmsWorkflowState): string {
  const topics = Object.values(state.researchTopics);
  const sources = Object.values(state.sources);
  const nodes = Object.values(state.nodes).sort(
    (a, b) => a.difficultyLevel - b.difficultyLevel
  );
  const tel = state.tokenTelemetry;

  const lines: string[] = [
    `================================================================================`,
    ` LMS ORCHESTRATOR WORKFLOW DASHBOARD: ${state.subjectTitle} (${state.subjectSlug})`,
    `================================================================================`,
    ` Execution Mode : ${state.executionMode.toUpperCase()} (${
      state.executionMode === "local_file"
        ? "Mode #1 (Local Default): Local File-Based Knowledge System (No RAG)"
        : "Mode #2: Cloud System + RAG Indexing"
    })`,
    ` Active Stage   : ${state.stage}`,
    ` Learner Profile: mode=${state.learnerStyle.mode}, pace=${state.learnerStyle.pace}`,
    ` Course Folder  : ${state.artifacts.learningOutDir}/ (Index: ${state.artifacts.courseIndexPath})`,
    `--------------------------------------------------------------------------------`,
    ` [1 & 2] RESEARCH & VALIDATION (${topics.length} topics, max ${state.maxReviewRounds} rounds)`,
  ];

  if (topics.length === 0) {
    lines.push(`   (no research topics registered)`);
  } else {
    for (const t of topics) {
      const topTok = tel.byTopic[t.id]?.totalTokens ?? 0;
      lines.push(
        `   - ${t.id}: "${t.title}" | status=${t.status.toUpperCase()} | round=${t.reviewRound}/${t.maxRounds} | perspectives=[${t.coveredPerspectives.join(",")}] | tokens=${topTok}`
      );
      if (t.openDefects.length > 0) {
        lines.push(`     Defects: ${t.openDefects.join("; ")}`);
      }
    }
  }

  lines.push(
    `--------------------------------------------------------------------------------`,
    ` [3] KNOWLEDGE SOURCES (${sources.length} sources | mode=${state.executionMode} | RAG chunks=${state.ragChunks.length})`
  );
  for (const s of sources) {
    const loc =
      state.executionMode === "cloud_rag"
        ? `${s.githubMdPath} (${s.chunkIds.length} RAG chunks)`
        : `${s.organizedRefPath ?? s.localFilePath} (direct local file read, no RAG)`;
    lines.push(`   - ${s.id} [${s.track}/${s.sourceType}] -> ${loc}`);
  }

  lines.push(
    `--------------------------------------------------------------------------------`,
    ` [4a] BINARY SEARCH PLACEMENT (Tiers 1..${state.placement.totalDifficultyTiers})`,
    `   Status: ${state.placement.status.toUpperCase()} | Entry Level: Tier ${state.placement.entryDifficultyLevel} | Bounds: [${state.placement.low}, ${state.placement.high}] | Mid: ${state.placement.currentMid ?? "N/A"}`
  );
  for (const h of state.placement.history) {
    lines.push(
      `   Step ${h.stepIndex}: Tested Tier ${h.difficultyTested} -> ${h.passed ? "PASS" : "FAIL"} (bounds [${h.lowBefore},${h.highBefore}] -> [${h.lowAfter},${h.highAfter}])`
    );
  }

  lines.push(
    `--------------------------------------------------------------------------------`,
    ` [4b, 5 & 6] ORGANIZED LEARNING MODULES & DAG (status=${state.pathValidation.status.toUpperCase()}, round=${state.pathValidation.round})`
  );
  if (state.capstoneProject) {
    lines.push(
      `   Capstone Project: "${state.capstoneProject.title}" (${state.artifacts.capstoneBriefPath})`
    );
  }
  if (state.pathValidation.structuralErrors.length > 0) {
    lines.push(`   Validation Errors: ${state.pathValidation.structuralErrors.join(" | ")}`);
  }
  for (const n of nodes) {
    const srs = state.spacedRepetition[n.id];
    const nodeTok = tel.byNode[n.id]?.totalTokens ?? 0;
    const matStatus = n.materialPublished ? "📘 PUBLISHED" : "⏳ MATERIAL_PENDING";
    const srsInfo = srs
      ? ` | SRS: interval=${srs.intervalDays}d, EF=${srs.easinessFactor}, reps=${srs.reps}, lapses=${srs.lapses}${srs.needsRefresher ? " [REFRESHER DUE]" : ""}`
      : "";
    lines.push(
      `   - [Tier ${n.difficultyLevel}] ${n.id}: "${n.title}" | status=${n.status.toUpperCase()} | ${matStatus} -> ${n.studyGuidePath} | tokens=${nodeTok}${srsInfo}`
    );
  }

  lines.push(
    `--------------------------------------------------------------------------------`,
    ` [LOGS, ARTIFACTS & TOKEN TELEMETRY]`,
    `   Total Tokens : ${tel.total.totalTokens} (Prompt: ${tel.total.promptTokens}, Completion: ${tel.total.completionTokens}, Tasks: ${tel.total.taskCount})`,
    `   JSONL Log    : ${tel.logFilePath}`,
    `   Maint Report : ${tel.maintenanceReportPath}`,
    `   Artifacts Dir: ${state.artifacts.artifactsRootDir}`
  );
  for (const [skillName, bucket] of Object.entries(tel.bySkill)) {
    lines.push(
      `   - ${skillName.padEnd(28)}: ${String(bucket.totalTokens).padStart(6)} tokens (${bucket.taskCount} tasks)`
    );
  }
  lines.push(`================================================================================`);

  return lines.join("\n");
}
