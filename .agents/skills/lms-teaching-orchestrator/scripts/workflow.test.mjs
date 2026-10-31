import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  createInitialWorkflowState,
  submitResearchDraft,
  validateResearchDraft,
  resolveResearchEscalation,
  ingestUserSource,
  retrieveKnowledgeForNode,
  startPlacementQuiz,
  submitPlacementQuizStep,
  designLearningPath,
  validateLearningPath,
  publishNodeLearningMaterial,
  recordNodeQaInteraction,
  registerQuiz,
  getDueSpacedRepetitionNodes,
  generateTokenMaintenanceMarkdownReport
} from "./workflow.mjs";
import {
  installSkillEnvironment,
  runDeterministicSimulation
} from "./workflow-cli.mjs";
describe("LMS 6-Skill Standalone Agent Workflow Engine", () => {
  const fixedNow = "2026-10-31T08:00:00.000Z";
  it("Skill Installation Step: sets up .lms-state/ and learning/ directories for logs, artifacts, state, knowledge, config.json, and .gitignore", () => {
    const cfg = installSkillEnvironment({ defaultMode: "local_file", nowIso: fixedNow });
    assert.equal(cfg.defaultExecutionMode, "local_file");
    assert.ok(fs.existsSync(cfg.directories.logsDir));
    assert.ok(fs.existsSync(cfg.directories.artifactsDir));
    assert.ok(fs.existsSync(cfg.directories.stateDir));
    assert.ok(fs.existsSync(cfg.directories.knowledgeDir));
    assert.ok(fs.existsSync(cfg.directories.learningDir));
    assert.ok(fs.existsSync(path.join(cfg.rootDir, "config.json")));
    assert.ok(fs.existsSync(path.join(cfg.rootDir, ".gitignore")));
    assert.ok(fs.existsSync(path.join(cfg.directories.logsDir, "workflow-events.log")));
  });
  it("Skills #1 & #2: enforces the 3-round research peer-review loop, allows defense, and halts for user decision after Round 3", () => {
    let state = createInitialWorkflowState({
      subjectSlug: "zero-knowledge-proofs",
      subjectTitle: "Zero-Knowledge Proofs (zk-SNARKs)",
      nowIso: fixedNow
    });
    state = submitResearchDraft(state, {
      topicId: "zk-polynomial-commitments",
      title: "KZG Polynomial Commitments",
      requiredPerspectives: ["theory", "practical", "critique"],
      coveredPerspectives: ["theory", "practical", "critique"],
      draftMarkdown: "# KZG Commitments\nTrusted setup ceremony...",
      citations: ["https://iacr.org/archive/asiacrypt2010/6477178/6477178.pdf"],
      tokenUsage: { promptTokens: 500, completionTokens: 400, model: "gemini-2.5-pro" },
      nowIso: fixedNow
    });
    assert.equal(state.stage, "VALIDATING_RESEARCH");
    state = validateResearchDraft(state, {
      topicId: "zk-polynomial-commitments",
      decision: "raise_issues",
      critiqueSummary: "Missing pairing verification equation.",
      defects: ["Include bilinear pairing equation e(C - [y], [1]) = e(pi, [tau - z])."],
      tokenUsage: { promptTokens: 600, completionTokens: 150, model: "gemini-2.5-flash" },
      nowIso: fixedNow
    });
    assert.equal(state.researchTopics["zk-polynomial-commitments"].status, "revision_requested");
    assert.equal(state.researchTopics["zk-polynomial-commitments"].reviewRound, 1);
    state = submitResearchDraft(state, {
      topicId: "zk-polynomial-commitments",
      title: "KZG Polynomial Commitments",
      requiredPerspectives: ["theory", "practical", "critique"],
      coveredPerspectives: ["theory", "practical", "critique"],
      draftMarkdown: "# KZG Commitments\nAdded pairing equation...",
      citations: ["https://iacr.org/archive/asiacrypt2010/6477178/6477178.pdf"],
      defenseArgument: "Added bilinear pairing equation in Section 2.",
      nowIso: fixedNow
    });
    state = validateResearchDraft(state, {
      topicId: "zk-polynomial-commitments",
      decision: "raise_issues",
      critiqueSummary: "Batch opening optimization still unclear.",
      defects: ["Clarify random linear combination for batch KZG openings."],
      nowIso: fixedNow
    });
    assert.equal(state.researchTopics["zk-polynomial-commitments"].status, "revision_requested");
    assert.equal(state.researchTopics["zk-polynomial-commitments"].reviewRound, 2);
    state = submitResearchDraft(state, {
      topicId: "zk-polynomial-commitments",
      title: "KZG Polynomial Commitments",
      requiredPerspectives: ["theory", "practical", "critique"],
      coveredPerspectives: ["theory", "practical", "critique"],
      draftMarkdown: "# KZG Commitments\nAdded batch openings...",
      citations: ["https://iacr.org/archive/asiacrypt2010/6477178/6477178.pdf"],
      defenseArgument: "Defended batch opening bound.",
      nowIso: fixedNow
    });
    state = validateResearchDraft(state, {
      topicId: "zk-polynomial-commitments",
      decision: "raise_issues",
      critiqueSummary: "Disagree on toxic waste mitigation comparison.",
      defects: ["Compare with FRI transparent setup."],
      nowIso: fixedNow
    });
    assert.equal(state.researchTopics["zk-polynomial-commitments"].status, "escalated_to_user");
    assert.equal(state.researchTopics["zk-polynomial-commitments"].reviewRound, 3);
    assert.equal(state.stage, "PAUSED_FOR_USER_RESEARCH_DECISION");
    assert.throws(() => {
      submitResearchDraft(state, {
        topicId: "zk-polynomial-commitments",
        title: "KZG Polynomial Commitments",
        requiredPerspectives: ["theory", "practical", "critique"],
        coveredPerspectives: ["theory", "practical", "critique"],
        draftMarkdown: "# 4th attempt should be blocked",
        citations: ["https://iacr.org"]
      });
    }, /ESCALATED_TO_USER/);
    state = resolveResearchEscalation(state, {
      topicId: "zk-polynomial-commitments",
      userDecision: "approve_override",
      userNotes: "FRI comparison will be covered in a separate STARKs topic. Approving KZG doc.",
      nowIso: fixedNow
    });
    assert.equal(state.researchTopics["zk-polynomial-commitments"].status, "user_approved");
    assert.equal(state.stage, "RESEARCHING");
    assert.equal(state.tokenTelemetry.byTopic["zk-polynomial-commitments"].taskCount, 7);
  });
  it("Dual Execution Modes: Mode #1 (local_file default) skips RAG and reads directly from files vs Mode #2 (cloud_rag) indexes RAG chunks", () => {
    let localState = createInitialWorkflowState({
      subjectSlug: "options-local",
      subjectTitle: "Options Pricing (Local File Mode)",
      nowIso: fixedNow
    });
    assert.equal(localState.executionMode, "local_file");
    localState = ingestUserSource(localState, {
      sourceId: "byos-greeks",
      title: "Greeks Formula Sheet",
      sourceType: "xlsx",
      originalUri: "local://sheets/greeks.xlsx",
      normalizedMarkdown: "# Delta & Gamma\nDelta measures first derivative.\n## Vega\nSensitivity to implied volatility.",
      nowIso: fixedNow
    });
    assert.equal(localState.ragChunks.length, 0);
    assert.equal(localState.sources["byos-greeks"].chunkIds.length, 0);
    assert.equal(
      localState.sources["byos-greeks"].localFilePath,
      "lms/options-local/sources/user/byos-greeks.md"
    );
    localState = designLearningPath(localState, {
      nodes: [
        {
          id: "node-greeks",
          title: "Understanding Option Greeks",
          summary: "Delta, Gamma, Vega",
          difficultyLevel: 3,
          prerequisites: [],
          sourceIds: ["byos-greeks"]
        }
      ],
      nowIso: fixedNow
    });
    const retrieved = retrieveKnowledgeForNode(
      localState,
      "node-greeks",
      (filePath) => `Read from disk: ${filePath}`
    );
    assert.equal(retrieved.mode, "local_file");
    assert.equal(retrieved.ragChunks.length, 0);
    assert.equal(retrieved.localFiles.length, 1);
    assert.equal(
      retrieved.localFiles[0].contentMarkdown,
      "Read from disk: lms/options-local/sources/user/byos-greeks.md"
    );
    let cloudState = createInitialWorkflowState({
      subjectSlug: "options-cloud",
      subjectTitle: "Options Pricing (Cloud RAG)",
      executionMode: "cloud_rag",
      nowIso: fixedNow
    });
    cloudState = ingestUserSource(cloudState, {
      sourceId: "byos-greeks",
      title: "Greeks Formula Sheet",
      sourceType: "xlsx",
      originalUri: "local://sheets/greeks.xlsx",
      normalizedMarkdown: "# Delta & Gamma\nDelta measures first derivative.\n## Vega\nSensitivity to implied volatility.",
      nowIso: fixedNow
    });
    assert.equal(cloudState.executionMode, "cloud_rag");
    assert.equal(cloudState.ragChunks.length, 2);
  });
  it("Skill #4a: binary search placement converges in O(log N) steps to the exact entry difficulty level", () => {
    let state = createInitialWorkflowState({
      subjectSlug: "rust-async",
      subjectTitle: "Async Rust & Pin/Future Internals",
      totalDifficultyTiers: 16,
      nowIso: fixedNow
    });
    state = startPlacementQuiz(state, 16, fixedNow);
    assert.equal(state.placement.currentMid, 8);
    state = submitPlacementQuizStep(state, { quizId: "q1", answeredCorrectly: true, nowIso: fixedNow });
    assert.equal(state.placement.currentMid, 12);
    state = submitPlacementQuizStep(state, { quizId: "q2", answeredCorrectly: true, nowIso: fixedNow });
    assert.equal(state.placement.currentMid, 14);
    state = submitPlacementQuizStep(state, { quizId: "q3", answeredCorrectly: false, nowIso: fixedNow });
    assert.equal(state.placement.currentMid, 13);
    state = submitPlacementQuizStep(state, { quizId: "q4", answeredCorrectly: true, nowIso: fixedNow });
    assert.equal(state.placement.status, "completed");
    assert.equal(state.placement.entryDifficultyLevel, 14);
    assert.equal(state.placement.history.length, 4);
    assert.equal(state.stage, "DESIGNING_PATH");
  });
  it("Skill #5: detects DAG cycles, difficulty inversions, and missing source grounding", () => {
    let state = createInitialWorkflowState({
      subjectSlug: "compiler-design",
      subjectTitle: "SSA Compiler Design",
      nowIso: fixedNow
    });
    state = ingestUserSource(state, {
      sourceId: "src-ssa",
      title: "SSA Book Notes",
      sourceType: "pdf",
      originalUri: "local://ssa.pdf",
      normalizedMarkdown: "# Dominance Frontiers\nPhi nodes placement.",
      nowIso: fixedNow
    });
    state = designLearningPath(state, {
      nodes: [
        {
          id: "node-a",
          title: "Dominator Trees",
          summary: "Compute Lengauer-Tarjan dominators.",
          difficultyLevel: 10,
          prerequisites: ["node-b"],
          sourceIds: ["src-ssa"]
        },
        {
          id: "node-b",
          title: "CFG Basics",
          summary: "Basic blocks and edges.",
          difficultyLevel: 12,
          prerequisites: ["node-a"],
          sourceIds: ["non-existent-source"]
        }
      ],
      nowIso: fixedNow
    });
    state = validateLearningPath(state, void 0, fixedNow);
    assert.equal(state.pathValidation.valid, false);
    assert.equal(state.pathValidation.status, "revision_requested");
    assert.ok(state.pathValidation.structuralErrors.some((e) => e.includes("Cycle detected")));
    assert.ok(state.pathValidation.structuralErrors.some((e) => e.includes("Difficulty inversion")));
    assert.ok(state.pathValidation.structuralErrors.some((e) => e.includes("unknown sourceId")));
  });
  it("Skill #6: deterministically blocks registering a Mastery Gate quiz until the node's Markdown study guide is published", () => {
    let state = createInitialWorkflowState({
      subjectSlug: "sql-gate-test",
      subjectTitle: "SQL Mastery Gate Enforcement",
      nowIso: fixedNow
    });
    state = ingestUserSource(state, {
      sourceId: "src-sql",
      title: "SQL Joins",
      sourceType: "markdown",
      originalUri: "local://joins.md",
      normalizedMarkdown: "# INNER JOIN\nCorrelates rows via ON predicate.",
      nowIso: fixedNow
    });
    state = designLearningPath(state, {
      nodes: [
        {
          id: "node-01-joins",
          title: "Multi-Table INNER JOIN",
          summary: "Joining relations with explicit predicates.",
          difficultyLevel: 5,
          prerequisites: [],
          sourceIds: ["src-sql"]
        }
      ],
      nowIso: fixedNow
    });
    state = validateLearningPath(state, { decision: "accept" }, fixedNow);
    assert.equal(state.nodes["node-01-joins"].status, "unlocked");
    assert.equal(state.nodes["node-01-joins"].materialPublished, false);
    assert.throws(() => {
      registerQuiz(state, {
        id: "quiz-joins-1",
        nodeId: "node-01-joins",
        purpose: "mastery_gate",
        modality: "mcq_4",
        difficultyLevel: 5,
        questionPrompt: "What does INNER JOIN return?",
        options: [
          { label: "A", text: "Only matching rows" },
          { label: "B", text: "All left rows" },
          { label: "C", text: "All right rows" },
          { label: "D", text: "Cartesian product" }
        ],
        correctOption: "A",
        passingScoreThreshold: 1
      });
    }, /material-publish/);
    state = publishNodeLearningMaterial(state, {
      nodeId: "node-01-joins",
      studyMaterialMarkdown: "## Deep Dive: INNER JOIN\nAn `INNER JOIN` evaluates the `ON` predicate for every candidate pair and emits only rows where the predicate evaluates to `TRUE`.",
      nowIso: fixedNow
    });
    assert.equal(state.nodes["node-01-joins"].materialPublished, true);
    assert.equal(state.nodes["node-01-joins"].status, "in_progress");
    state = recordNodeQaInteraction(state, {
      nodeId: "node-01-joins",
      question: "What happens if the ON column contains NULL?",
      answerMarkdown: "In SQL three-valued logic, `NULL = NULL` evaluates to `UNKNOWN`, so the row is excluded by `INNER JOIN`.",
      nowIso: fixedNow
    });
    assert.equal(state.nodes["node-01-joins"].qaLog.length, 1);
  });
  it("Skill #6, Organized Learning Materials, Logs & Artifacts: runs simulation, organizes learning/<slug>/ files, and writes logs & artifacts", () => {
    const simLocal = runDeterministicSimulation("local_file");
    assert.equal(simLocal.state.executionMode, "local_file");
    assert.equal(simLocal.state.ragChunks.length, 0);
    const courseIndexMd = path.resolve(process.cwd(), "learning/raft-consensus-go/README.md");
    const capstoneBriefMd = path.resolve(
      process.cwd(),
      "learning/raft-consensus-go/capstone/PROJECT_BRIEF.md"
    );
    const mod01Md = path.resolve(
      process.cwd(),
      "learning/raft-consensus-go/modules/01-node-01-rpc-basics.md"
    );
    const mod02Md = path.resolve(
      process.cwd(),
      "learning/raft-consensus-go/modules/02-node-02-leader-election.md"
    );
    const mod03Md = path.resolve(
      process.cwd(),
      "learning/raft-consensus-go/modules/03-node-03-log-replication.md"
    );
    assert.ok(fs.existsSync(courseIndexMd));
    assert.ok(fs.existsSync(capstoneBriefMd));
    assert.ok(fs.existsSync(mod01Md));
    assert.ok(fs.existsSync(mod02Md));
    assert.ok(fs.existsSync(mod03Md));
    const mod02Content = fs.readFileSync(mod02Md, "utf-8");
    assert.ok(mod02Content.includes("## 1. Learning Objectives"));
    assert.ok(mod02Content.includes("## Learner Q&A & Clarifications"));
    assert.ok(mod02Content.includes("Why does Pre-Vote check log up-to-dateness"));
    const simCloud = runDeterministicSimulation("cloud_rag");
    const { state, dashboard, tokenReport, workflowMermaid, dagMermaid } = simCloud;
    assert.equal(state.executionMode, "cloud_rag");
    assert.equal(state.ragChunks.length, 5);
    assert.equal(state.nodes["node-01-rpc-basics"].status, "mastered_by_placement");
    assert.equal(state.nodes["node-02-leader-election"].status, "completed");
    assert.equal(state.nodes["node-03-log-replication"].status, "unlocked");
    const srs02 = state.spacedRepetition["node-02-leader-election"];
    assert.ok(srs02);
    assert.equal(srs02.intervalDays, 1);
    const dueTwoDaysLater = getDueSpacedRepetitionNodes(state, "2026-11-02T08:00:00.000Z");
    assert.equal(dueTwoDaysLater.length, 1);
    assert.ok(state.tokenTelemetry.total.totalTokens > 0);
    assert.equal(state.tokenTelemetry.byNode["node-02-leader-election"].taskCount, 4);
    const reportText = generateTokenMaintenanceMarkdownReport(state);
    assert.ok(reportText.includes("Token Usage by Skill"));
    assert.ok(reportText.includes("Token Usage by Learning Node"));
    assert.ok(tokenReport.includes("node-02-leader-election"));
    assert.ok(dashboard.includes("[LOGS, ARTIFACTS & TOKEN TELEMETRY]"));
    assert.ok(workflowMermaid.includes("flowchart TD"));
    assert.ok(dagMermaid.includes("tokens"));
  });
});
