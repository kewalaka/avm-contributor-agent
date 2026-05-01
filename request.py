"""TestRequest — the input contract for every test run.

A TestRequest describes WHAT to test, HOW to compare versions, WHICH
examples to include, and WHERE to report results.  It can be created from:
  - A JSON file (CLI batch mode: ``--request test-request.json``)
  - CLI shorthand flags (``--module ... --base-ref ... --head-ref ...``)
  - Chat conversation (agent parses user intent)
  - GitHub Action inputs (mapped to JSON)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TestRequest:
    """Structured input for a module test run.

    Revision semantics for ``base_ref`` / ``head_ref``:
      - Git sources: branch name, tag, or commit SHA (e.g. "main", "v0.1.1")
      - Registry sources: version string (e.g. "0.1.0", "0.2.0")
      - Local paths: can be left empty (test current state) or set to a
        branch/tag if the local repo supports ``git checkout``

    Example scoping:
      - ``examples=[]`` (default) → test ALL discovered examples
      - ``examples=["default", "simple_http"]`` → test only these
      - ``skip_examples=["kv_selfssl_waf_https"]`` → test all EXCEPT these
      - Both set → ``examples`` wins (skip_examples is ignored)
    """

    # What to test
    module_source: str = ""
    base_ref: str = "main"
    head_ref: str = ""
    head_module_source: str = ""

    # Scope
    examples: list[str] = field(default_factory=list)
    skip_examples: list[str] = field(default_factory=list)

    # Where to report
    github_repo: str = ""
    github_issue: int = 0
    github_pr: int = 0

    # Overrides (fall back to AgentConfig when empty)
    cleanup: bool = True
    subscription_id: str = ""
    location: str = ""

    # Execution control
    max_parallel: int = 3
    timeout_minutes: int = 120
    interactive: bool = True

    # Tracking
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # --- Constructors ---

    @classmethod
    def from_json_file(cls, path: str) -> TestRequest:
        """Load a TestRequest from a JSON file with validation."""
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object, got {type(data).__name__}")

        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(data.keys()) - known_fields
        if unknown:
            raise ValueError(f"Unknown fields in request: {', '.join(sorted(unknown))}")

        request = cls(**data)
        request.validate()
        return request

    @classmethod
    def from_cli_args(
        cls,
        module: str,
        base_ref: str = "main",
        head_ref: str = "",
        examples: str = "",
        skip: str = "",
        github_repo: str = "",
        github_pr: int = 0,
        no_cleanup: bool = False,
        subscription_id: str = "",
        location: str = "",
        max_parallel: int = 3,
        timeout: int = 120,
    ) -> TestRequest:
        """Create a TestRequest from CLI arguments."""
        request = cls(
            module_source=module,
            base_ref=base_ref,
            head_ref=head_ref,
            examples=[e.strip() for e in examples.split(",") if e.strip()],
            skip_examples=[e.strip() for e in skip.split(",") if e.strip()],
            github_repo=github_repo,
            github_pr=github_pr,
            cleanup=not no_cleanup,
            subscription_id=subscription_id,
            location=location,
            max_parallel=max_parallel,
            timeout_minutes=timeout,
            interactive=False,
        )
        request.validate()
        return request

    # --- Validation ---

    def validate(self) -> None:
        """Raise ValueError if the request is invalid."""
        errors: list[str] = []

        if not self.module_source:
            errors.append("module_source is required")

        if self.max_parallel < 1:
            errors.append("max_parallel must be >= 1")

        if self.timeout_minutes < 1:
            errors.append("timeout_minutes must be >= 1")

        if self.github_issue and self.github_pr:
            errors.append(
                "Set github_issue or github_pr, not both. "
                "Use github_issue for tracking issues, github_pr for PR comments."
            )

        if (self.github_issue or self.github_pr) and not self.github_repo:
            errors.append(
                "github_repo is required when github_issue or github_pr is set"
            )

        if errors:
            raise ValueError(
                "Invalid TestRequest:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    # --- Helpers ---

    def filter_examples(self, discovered: list[str]) -> list[str]:
        """Apply example/skip filters to a discovered example list.

        Returns the filtered list of example names to test.
        """
        if self.examples:
            # Explicit include list takes precedence
            missing = set(self.examples) - set(discovered)
            if missing:
                raise ValueError(
                    f"Requested examples not found in module: {', '.join(sorted(missing))}"
                )
            return [e for e in discovered if e in self.examples]

        if self.skip_examples:
            return [e for e in discovered if e not in self.skip_examples]

        # Default: test all
        return list(discovered)

    def to_agent_message(self) -> str:
        """Format this request as a structured message for the agent."""
        lines = [f"## Test Request (run_id: {self.run_id})", ""]
        lines.append(f"**Module**: {self.module_source}")

        if self.head_ref:
            lines.append(
                f"**Upgrade test**: {self.base_ref} → {self.head_ref}"
            )
        else:
            lines.append(f"**Ref**: {self.base_ref}")

        if self.head_module_source:
            lines.append(f"**Head module source**: {self.head_module_source}")

        if self.examples:
            lines.append(f"**Examples**: {', '.join(self.examples)}")
        elif self.skip_examples:
            lines.append(
                f"**Examples**: all (skip: {', '.join(self.skip_examples)})"
            )
        else:
            lines.append("**Examples**: all discovered examples")

        if self.github_repo:
            target = self.github_repo
            if self.github_pr:
                target += f" PR #{self.github_pr}"
            elif self.github_issue:
                target += f" issue #{self.github_issue}"
            lines.append(f"**Report to**: {target}")

        if self.subscription_id:
            lines.append(f"**Subscription**: {self.subscription_id}")
        if self.location:
            lines.append(f"**Location**: {self.location}")

        lines.append(f"**Cleanup**: {'yes' if self.cleanup else 'no'}")
        lines.append(f"**Max parallel**: {self.max_parallel}")
        lines.append(f"**Timeout**: {self.timeout_minutes} minutes")
        lines.append("")
        lines.append("Please proceed with the testing workflow.")

        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)
