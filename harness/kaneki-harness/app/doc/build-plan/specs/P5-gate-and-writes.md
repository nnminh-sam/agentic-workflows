# P5 · Gate approval and other write actions (M2)

**Milestone:** M2. **Output:** `parts/P5-gate-and-writes.plan.md`

## Problem
The gate dialog is the core screen (D3). The user must decide a gate with its evidence beside it, see the exact
command before it runs (D2), and the result must land in `decisions.jsonl` with `actor: user`. M2 done-when:
"Approve/reject a real gate; `decisions.jsonl` shows `actor: user`".

## Plan must cover
1. **Exact CLI semantics** (read `cli.py` `gate`, `invalidate`, `lane` parsers and handlers): statuses
   (approved / needs-changes / rejected / stopped), `--reopen NODE` rules (which nodes are valid targets, what goes
   stale), `--note`, `--actor`, `invalidate --reason` (required), `lane --set full --reason`. Document error cases
   (wrong node, not at gate, lock held, integrity failure) and their JSON output. Note the mock's known bug
   (reject preview ran `gate` then `invalidate` without `--reason`).
2. **When is a gate "waiting on the user"**: how to detect it from state/decisions/graph so the top bar count and
   toasts are right. Which nodes have user gates.
3. **Gate dialog flow**: open from rail/palette/toast → evidence panel (reviewer verdict, NG list, artifact
   excerpts; source files for each) → choice → reopen target picker (only valid targets) → note (optional; must
   accept Vietnamese Telex input) → **command preview** (exact argv, copyable) → confirm → run → result/error →
   refresh. "Which nodes go stale" preview: computed by the harness (propose a read-only dry-run if none exists)
   or by the Host from the graph — recommend one.
4. **Other write actions** in M2: invalidate node, lane full. Same preview/confirm pattern; shared
   `CommandPreview` component.
5. **Toasts** (gate needs you, reviewer FAIL, node reopened): triggered from Host events; dedupe; click-through.
6. **Safety**: no write without confirm; double-submit protection; what if the ticket changed while the dialog
   was open (re-validate before run).
7. **Testing**: a throwaway test ticket (created by the user or by a test script in a temp copy of the workspace,
   never in the real tickets) to approve/reject for real; fixture tests for the dialog states.

## Not in scope
Running nodes or sessions (P6), new ticket/onboarding (P8).
