"""Configuration model for the Infrastructure Testing Agent."""

import os
from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    """Runtime configuration loaded from environment variables."""

    # Foundry
    project_endpoint: str = field(
        default_factory=lambda: os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    )
    model_deployment_name: str = field(
        default_factory=lambda: os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1")
    )

    # Azure target
    default_location: str = field(
        default_factory=lambda: os.environ.get("DEFAULT_LOCATION", "australiaeast")
    )
    default_subscription_id: str = field(
        default_factory=lambda: os.environ.get("DEFAULT_SUBSCRIPTION_ID", "")
    )
    test_rg_prefix: str = field(
        default_factory=lambda: os.environ.get("TEST_RG_PREFIX", "rg-avm-test-")
    )

    # Behaviour
    cleanup_on_complete: bool = field(
        default_factory=lambda: os.environ.get("CLEANUP_ON_COMPLETE", "true").lower()
        == "true"
    )

    def validate(self) -> list[str]:
        """Return a list of missing-but-required settings."""
        issues: list[str] = []
        if not self.project_endpoint:
            issues.append("AZURE_AI_PROJECT_ENDPOINT is not set")
        return issues


# Singleton used by tools
config = AgentConfig()
