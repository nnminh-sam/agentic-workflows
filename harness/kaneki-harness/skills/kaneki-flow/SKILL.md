---
name: kaneki-flow
description: "Run one ticket (bug or feature) through a gated SDLC lifecycle — analysis, approved plan, coding, independent review, test cases, real-surface evidence, final review with receipts, delivery drafts, and the after-PR review-fix loop — on any project described by an sdlc.profile.json."
when_to_use: "Invoke to work a ticket from intake to deliverable-ready, to run a single node of it, or to handle an external PR review round. The project profile supplies tracker, stacks, surfaces, rules, testcase format, evidence runners and delivery."
argument-hint: "<ticket-id> [--lite|--full] [--fix-bug|--new-feature] [--node <name>] [--family <id>] [--repo <id>] [--auto] [--profile <path>]"
user-invocable: true
hooks:
  PreToolUse:
    - matcher: "Bash|PowerShell"
      hooks:
        - type: command
          # CLAUDE_SKILL_DIR is not exported to hook processes: find the installed guard from the project dir
          # upwards, then in the user's ~/.claude. Missing guard = commands that run git refused (fail closed), other
          # commands pass. The guard itself is self-contained: it needs no harness path.
          command: 'g=".claude/skills/kaneki-flow/scripts/guard-git"; d=$(printf "%s" "${CLAUDE_PROJECT_DIR}" | tr "\\\\" "/"); while [ -n "$d" ] && [ ! -f "$d/$g" ]; do p="${d%/*}"; [ "$p" = "$d" ] && break; d="$p"; done; if [ -f "$d/$g" ]; then exec sh "$d/$g"; fi; if [ -f "$HOME/$g" ]; then exec sh "$HOME/$g"; fi; if grep -Eq "(^|[^[:alnum:]_.-])git(\\.exe)?([[:space:]]|$)" 2>/dev/null; then echo "Blocked by kaneki-flow: guard not installed (run sdlc install)." >&2; exit 2; fi; exit 0'
---

# Kaneki Flow

The controller text lives in one place: [`playbooks/flow.md`](../../playbooks/flow.md) in the harness home.
`sdlc install --host <host>` renders this skill for each agent as this frontmatter + the host binding
(`hosts/<host>/preamble.md`) + `playbooks/flow.md`, so an installed skill is complete on its own.

If you are reading this file straight from the harness repository, read `playbooks/flow.md` and your host's
`hosts/<host>/preamble.md`, and follow them. Do not add controller instructions here; edit `playbooks/flow.md`.
