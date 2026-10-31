#!/usr/bin/env node
// Skill #2 CLI Entrypoint (lms-validate-research)
// Delegates to the standalone LMS workflow CLI in lms-teaching-orchestrator/scripts/workflow-cli.ts
import { runCli } from "../../lms-teaching-orchestrator/scripts/workflow-cli.ts";

runCli(process.argv);
