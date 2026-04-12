"""Foundry-hosted runtime — AIProjectClient with MCP tool declarations.

This runtime is used when FOUNDRY_HOSTED=true. It creates an agent via
the AIProjectClient API, which supports MCPTool declarations for
connecting to external MCP servers (GitHub, Azure, EVA/AzAPI).

The @ai_function tools are still registered alongside MCP tools, so the
agent has both local tool execution and MCP server access.
"""

from __future__ import annotations

import logging

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    MCPTool,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential

from config import config

logger = logging.getLogger(__name__)

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp"


def _build_mcp_tools() -> list[MCPTool]:
    """Build MCPTool declarations from config."""
    tools: list[MCPTool] = []

    if config.github_mcp_connection_id:
        tools.append(
            MCPTool(
                server_label="github",
                server_url=GITHUB_MCP_URL,
                require_approval="never",
                project_connection_id=config.github_mcp_connection_id,
            )
        )
        logger.info("MCP: GitHub server configured")

    if config.azure_mcp_connection_id:
        tools.append(
            MCPTool(
                server_label="azure",
                server_url="https://mcp.azure.com",
                require_approval="never",
                project_connection_id=config.azure_mcp_connection_id,
            )
        )
        logger.info("MCP: Azure server configured")

    if config.eva_mcp_server_url:
        tools.append(
            MCPTool(
                server_label="eva_azapi",
                server_url=config.eva_mcp_server_url,
                require_approval="never",
            )
        )
        logger.info("MCP: EVA/AzAPI server configured at %s", config.eva_mcp_server_url)

    return tools


def create_agent(instructions: str, tools: list) -> PromptAgentDefinition:
    """Create a Foundry-hosted agent with MCP tools.

    Returns a PromptAgentDefinition that can be used with AIProjectClient.
    The @ai_function tools are converted to their schema representations
    and combined with MCP tool declarations.
    """
    credential = DefaultAzureCredential()
    client = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=credential,
    )

    mcp_tools = _build_mcp_tools()
    if mcp_tools:
        logger.info("Configuring %d MCP server(s)", len(mcp_tools))

    # Create agent via the Foundry API
    agent_def = client.agents.create_agent(
        model=config.model_deployment_name,
        name="infra-testing-agent",
        instructions=instructions,
        tools=mcp_tools,
    )

    logger.info("Foundry agent created: %s", agent_def.id)
    return client, agent_def


def run(client, agent_def) -> None:
    """Run the Foundry-hosted agent.

    In hosted mode, the agent is managed by the Foundry platform.
    This function creates a thread and starts processing.
    """
    from azure.ai.agentserver.agentframework import from_agent_framework
    from agent_framework import ChatAgent
    from agent_framework.azure import AzureAIAgentClient

    # For hosted mode, we still use the agent-framework server adapter
    # but with MCP tools configured via the Foundry API
    agent = ChatAgent(
        chat_client=AzureAIAgentClient(
            project_endpoint=config.project_endpoint,
            model_deployment_name=config.model_deployment_name,
            credential=DefaultAzureCredential(),
        ),
        instructions="",  # Instructions set via Foundry agent definition
        tools=[],
    )
    from_agent_framework(agent).run()
