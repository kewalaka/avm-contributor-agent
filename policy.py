"""Security policy enforcement for the testing agent.

Evaluates whether a TestRequest is safe to execute based on:
  - Module source allowlist (trusted orgs, registries, explicit repos)
  - Local path restrictions (disabled in non-interactive mode)
  - Cost/scale guardrails
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from request import TestRequest

logger = logging.getLogger(__name__)

# GitHub org/repo pattern: "org/repo" or "https://github.com/org/repo..."
_GITHUB_PATTERN = re.compile(
    r"(?:https?://github\.com/)?([^/]+)/([^/\s.]+)", re.IGNORECASE
)

# Terraform registry pattern: "namespace/name/provider"
_REGISTRY_PATTERN = re.compile(r"^([^/]+)/([^/]+)/([^/]+)$")


@dataclass
class ModuleAllowlist:
    """Controls which module sources are auto-approved for testing.

    Modules not on the list require explicit human approval before any
    terraform apply runs.
    """

    # GitHub orgs whose modules are auto-approved
    trusted_orgs: list[str] = field(
        default_factory=lambda: _default_trusted_orgs()
    )

    # Terraform registry namespaces that are auto-approved
    trusted_registry: list[str] = field(
        default_factory=lambda: _default_trusted_registry()
    )

    # Explicitly allowed repos (full "org/repo" or registry "ns/name/provider")
    allowed_repos: list[str] = field(default_factory=list)

    def is_trusted(self, module_source: str) -> bool:
        """Check if a module source is on the allowlist.

        Returns True if the source matches a trusted org, registry namespace,
        or explicit repo entry.
        """
        source = module_source.strip()

        # Explicit repo match
        if source in self.allowed_repos:
            return True

        # Check GitHub org
        gh_match = _GITHUB_PATTERN.match(source)
        if gh_match:
            org = gh_match.group(1)
            repo = f"{org}/{gh_match.group(2)}"
            if org.lower() in (o.lower() for o in self.trusted_orgs):
                return True
            if repo in self.allowed_repos:
                return True

        # Check registry namespace
        reg_match = _REGISTRY_PATTERN.match(source)
        if reg_match:
            namespace = reg_match.group(1)
            full = f"{namespace}/{reg_match.group(2)}/{reg_match.group(3)}"
            if any(namespace.lower().startswith(t.lower().rstrip("/"))
                   for t in self.trusted_registry):
                return True
            if full in self.allowed_repos:
                return True

        # Local paths: never auto-trusted (require interactive approval)
        if _is_local_path(source):
            return False

        return False


def _default_trusted_orgs() -> list[str]:
    """Load trusted orgs from env or use defaults."""
    env_val = os.environ.get("TRUSTED_ORGS", "")
    if env_val:
        return [o.strip() for o in env_val.split(",") if o.strip()]
    return ["Azure", "kewalaka"]


def _default_trusted_registry() -> list[str]:
    """Load trusted registry namespaces from env or use defaults."""
    env_val = os.environ.get("TRUSTED_REGISTRY", "")
    if env_val:
        return [n.strip() for n in env_val.split(",") if n.strip()]
    return ["Azure/"]


@dataclass
class PolicyResult:
    """Outcome of a policy evaluation."""

    approved: bool
    reason: str
    requires_confirmation: bool = False


def _is_local_path(source: str) -> bool:
    """Determine if a module source looks like a local filesystem path."""
    # Absolute paths
    if source.startswith(("/", "~")):
        return True
    # Explicit relative paths
    if source.startswith(("./", "../")):
        return True
    # Windows absolute paths (C:\...)
    if len(source) >= 3 and source[1] == ":" and source[2] in ("/", "\\"):
        return True
    # Backslash paths (Windows)
    if "\\" in source:
        return True
    # Not a local path -- org/repo or registry patterns contain / but aren't local
    return False


def evaluate_request(
    request: TestRequest,
    allowlist: ModuleAllowlist | None = None,
) -> PolicyResult:
    """Evaluate a TestRequest against security policy.

    Returns a PolicyResult indicating whether the request is approved,
    needs human confirmation, or is blocked.
    """
    if allowlist is None:
        allowlist = ModuleAllowlist()

    source = request.module_source

    # Block empty source
    if not source:
        return PolicyResult(
            approved=False,
            reason="module_source is required",
        )

    is_local = _is_local_path(source)

    # Local paths in non-interactive mode are blocked
    if is_local and not request.interactive:
        return PolicyResult(
            approved=False,
            reason=(
                "Local module paths are not allowed in non-interactive (CI) mode. "
                "Use a git URL or registry source instead."
            ),
        )

    # Local paths in interactive mode: require confirmation
    if is_local:
        return PolicyResult(
            approved=True,
            requires_confirmation=True,
            reason=f"Local path '{source}' requires confirmation before deploy.",
        )

    # Check allowlist
    if allowlist.is_trusted(source):
        logger.info("Module source '%s' is on the allowlist", source)
        return PolicyResult(approved=True, reason="Module is on the allowlist.")

    # Unknown source: require confirmation in interactive, block in CI
    if request.interactive:
        return PolicyResult(
            approved=True,
            requires_confirmation=True,
            reason=(
                f"Module source '{source}' is not on the allowlist. "
                "Human approval required before deploying."
            ),
        )

    return PolicyResult(
        approved=False,
        reason=(
            f"Module source '{source}' is not on the allowlist and "
            "non-interactive mode does not allow unapproved sources. "
            "Add it to TRUSTED_ORGS, TRUSTED_REGISTRY, or the explicit allowlist."
        ),
    )
