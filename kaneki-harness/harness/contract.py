"""The harness's data contract with the desktop app (`sdlc contract`).

`CONTRACT_VERSION` is declared here and nowhere else. Every harness JSON the app consumes carries it, and the app's
Host compares it with its own at start-up: a mismatch shows a setup/repair state instead of guessing.

Versioning rule (kaneki-harness-app/doc/build-plan/parts/P1-data-contract.plan.md §4.4):
- additive (a new optional field, a new command, a new enum value consumers must tolerate): do NOT bump;
- breaking (remove or rename a field, change a type or a meaning): bump.
"""

from __future__ import annotations

from typing import Any

from . import __version__

CONTRACT_VERSION: int = 1

# Read-only commands that exist today and that the app may call. Append a command here when it ships
# (`cost`); adding one is additive and does not bump CONTRACT_VERSION.
APP_COMMANDS: tuple[str, ...] = ("contract", "tickets", "status-of", "status", "validate", "ticket-detail",
                                    "gate-request")


def contract_info() -> dict[str, Any]:
    """The `sdlc contract` payload."""
    return {"contractVersion": CONTRACT_VERSION, "harnessVersion": __version__, "commands": list(APP_COMMANDS)}
