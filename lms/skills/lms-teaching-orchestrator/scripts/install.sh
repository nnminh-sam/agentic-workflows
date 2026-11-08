#!/usr/bin/env bash
set -euo pipefail

# LMS 6-Skill Suite Local Environment Installer
# Sets up .lms-state/{logs,artifacts,state,knowledge} and learning/ independently of host source code.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
node "${SCRIPT_DIR}/workflow-cli.mjs" install "$@"
