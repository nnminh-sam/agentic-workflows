"""Findings and how they combine into a single hook decision."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import paths

SEVERITIES = ("off", "warn", "ask", "deny")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str  # one of SEVERITIES except "off"
    message: str

    def render(self) -> str:
        return f"[{self.rule_id}] {self.message}"


@dataclass
class Verdict:
    findings: list[Finding] = field(default_factory=list)

    def add(self, rule_id: str, severity: str, message: str) -> None:
        if severity != "off":
            self.findings.append(Finding(rule_id, severity, message))

    def extend(self, other: "Verdict") -> None:
        self.findings.extend(other.findings)

    @property
    def decision(self) -> str:
        """Highest severity among findings: 'deny' > 'ask' > 'warn' > 'allow'."""
        top = max((_RANK[f.severity] for f in self.findings), default=0)
        return {0: "allow", 1: "warn", 2: "ask", 3: "deny"}[top]

    def of(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def reason(self) -> str:
        blocking = [f for f in self.findings if f.severity in ("deny", "ask")]
        lines = [f.render() for f in blocking]
        warns = self.of("warn")
        if warns:
            lines.append("Warnings: " + "; ".join(f.render() for f in warns))
        lines.append(f"Rules: {paths.display(paths.RULES_HTML)}")
        return "git-guard: " + "\n".join(lines)
