#!/usr/bin/env node
// Skill #1 CLI Entrypoint (lms-research-document)
// Delegates to the standalone LMS workflow CLI in lms-teaching-orchestrator/scripts/workflow-cli.ts
import { runCli } from "../../lms-teaching-orchestrator/scripts/workflow-cli.ts";

runCli(process.argv);
