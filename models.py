"""Shared data models for structured handoffs between agent phases.

These dataclasses define the contract for passing data between tools and
(eventually) between agents in the multi-agent topology.  All tool output
should use these models to avoid dumping raw terraform output into context.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class ModuleMap:
    """Structured representation of a Module Under Test (MUT)."""

    source_path: str
    source_type: Literal["local", "registry", "git"]
    examples: list[str] = field(default_factory=list)
    tests: dict[str, list[str]] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
    upgrade_md: str | None = None
    avm_cli: bool = False
    providers: dict[str, str] = field(default_factory=dict)
    devcontainer_image: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class PlanSummary:
    """Concise summary of a terraform plan."""

    creates: int = 0
    updates: int = 0
    deletes: int = 0
    replaces: int = 0
    no_ops: int = 0

    @property
    def total_changes(self) -> int:
        return self.creates + self.updates + self.deletes + self.replaces

    @property
    def is_empty(self) -> bool:
        return self.total_changes == 0


@dataclass
class IdempotencyResult:
    """Result of a post-apply idempotency check."""

    status: Literal["pass", "fail", "error"]
    unexpected_changes: int = 0
    details: list[dict] = field(default_factory=list)
    error_message: str = ""


@dataclass
class DeployResult:
    """Structured output from deploying a single example."""

    example: str
    status: Literal["success", "failure", "timeout", "skipped"]
    resources_created: int = 0
    apply_duration_seconds: float = 0.0
    idempotency: IdempotencyResult | None = None
    plan_summary: PlanSummary | None = None
    plan_json_path: str = ""
    errors: list[str] = field(default_factory=list)

    # Upgrade test fields (populated when head_ref is set)
    upgrade: dict | None = None  # UpgradeTestResult from run_upgrade_test

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class AnalysisFinding:
    """A single finding from the analysis phase."""

    category: Literal["breaking_change", "idempotency", "missing_doc", "suggestion"]
    severity: Literal["critical", "warning", "info"]
    description: str
    evidence: dict = field(default_factory=dict)
    upgrade_md_reference: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class ReviewedFinding:
    """A finding after cross-check by the reviewer."""

    finding: AnalysisFinding
    verdict: Literal["confirmed", "rejected", "needs_investigation"]
    reviewer_notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class TestReport:
    """Complete test report for a module test run."""

    module_source: str
    module_version: str = ""
    deploy_results: list[DeployResult] = field(default_factory=list)
    findings: list[AnalysisFinding] = field(default_factory=list)
    reviewed_findings: list[ReviewedFinding] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(r.status == "failure" for r in self.deploy_results)

    @property
    def has_breaking_changes(self) -> bool:
        return any(f.category == "breaking_change" for f in self.findings)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)
