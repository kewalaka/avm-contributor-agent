"""Configuration model for the tf-module-developer-agent."""

import os
import subprocess
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

    # CI dispatch — fine-grained PAT scoped to kewalaka/avm-contributions
    # Permissions: Actions:RW, Contents:R, Metadata:R
    agent_dispatch_token: str = field(
        default_factory=lambda: os.environ.get("AGENT_DISPATCH_TOKEN", "")
    )

    # MCP connections (Foundry-hosted mode)
    github_mcp_connection_id: str = field(
        default_factory=lambda: os.environ.get("GITHUB_MCP_CONNECTION_ID", "")
    )
    azure_mcp_connection_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_MCP_CONNECTION_ID", "")
    )
    eva_mcp_server_url: str = field(
        default_factory=lambda: os.environ.get("EVA_MCP_SERVER_URL", "")
    )

    # Runtime mode
    foundry_hosted: bool = field(
        default_factory=lambda: os.environ.get("FOUNDRY_HOSTED", "false").lower()
        == "true"
    )
    multi_agent: bool = field(
        default_factory=lambda: os.environ.get("MULTI_AGENT", "false").lower()
        == "true"
    )

    def validate(self) -> list[str]:
        """Return a list of missing-but-required settings."""
        issues: list[str] = []
        if not self.project_endpoint:
            issues.append("AZURE_AI_PROJECT_ENDPOINT is not set")
        return issues

    def validate_dev_mode(self) -> list[str]:
        """Validate prerequisites for developer-agent (issue-driven / existing-repo) mode."""
        issues: list[str] = []
        if not self.project_endpoint:
            issues.append("AZURE_AI_PROJECT_ENDPOINT is not set")
        if not self.agent_dispatch_token:
            issues.append(
                "AGENT_DISPATCH_TOKEN is not set — create a fine-grained PAT "
                "(kewalaka/avm-contributions, Actions:RW, Contents:R, Metadata:R)"
            )
        result = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True
        )
        if result.returncode != 0:
            issues.append(
                "gh CLI is not authenticated — run 'gh auth login' first"
            )
        return issues

    @property
    def has_mcp(self) -> bool:
        """Check if any MCP connections are configured."""
        return bool(
            self.github_mcp_connection_id
            or self.azure_mcp_connection_id
            or self.eva_mcp_server_url
        )


# Singleton used by tools
config = AgentConfig()

# Singleton used by tools
config = AgentConfig()
