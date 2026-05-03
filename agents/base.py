"""Base class and utilities for specialist agents."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from agent_framework import ChatAgent
from agent_framework.azure import AzureAIAgentClient
from azure.identity import DefaultAzureCredential

from config import config

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Structured result from a specialist agent invocation."""

    agent_name: str
    status: str = "success"
    data: dict = field(default_factory=dict)
    error: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {"agent": self.agent_name, "status": self.status, "data": self.data, "error": self.error},
            indent=2,
        )


def _get_mcp_tools() -> list:
    """Return MCPTool declarations when Foundry-hosted mode and MCP connections are configured.

    In local mode (FOUNDRY_HOSTED=false), returns an empty list — the agents fall back
    to local @ai_function tool wrappers (gh CLI).  In Foundry-hosted mode, MCPTool
    objects are returned so the specialist agents can use GitHub/Azure/EVA MCP servers
    alongside their Python tool functions.
    """
    if not config.foundry_hosted or not config.has_mcp:
        return []
    try:
        from runtime.foundry import build_mcp_tools
        return build_mcp_tools()
    except ImportError:
        logger.warning("runtime.foundry not available; MCP tools will not be wired")
        return []


def create_specialist(
    name: str,
    instructions: str,
    tools: list,
    mcp_tools: list | None = None,
) -> ChatAgent:
    """Create a specialist ChatAgent with focused instructions and tools.

    In Foundry-hosted mode (``FOUNDRY_HOSTED=true``) with MCP connections
    configured, MCPTool declarations are automatically appended to the tool
    list so the agent can reach GitHub, Azure, and EVA/AzAPI MCP servers
    alongside its local @ai_function tools.

    Pass ``mcp_tools=[]`` explicitly to suppress automatic MCP injection.

    Args:
        name: Identifier used in log messages (e.g. 'developer', 'reviewer').
        instructions: System instructions for this specialist.
        tools: List of @ai_function decorated callables for local tool execution.
        mcp_tools: Optional explicit MCP tool declarations.  If None, auto-injected
            from config when Foundry-hosted mode is active and MCP is configured.
    """
    if mcp_tools is None:
        mcp_tools = _get_mcp_tools()

    local_tool_count = len(tools)
    mcp_tool_count = len(mcp_tools)
    all_tools = list(tools) + mcp_tools
    agent = ChatAgent(
        chat_client=AzureAIAgentClient(
            project_endpoint=config.project_endpoint,
            model_deployment_name=config.model_deployment_name,
            credential=DefaultAzureCredential(),
        ),
        instructions=instructions,
        tools=all_tools,
    )
    logger.info(
        "Created specialist agent: %s (%d local tools, %d MCP tools)",
        name,
        local_tool_count,
        mcp_tool_count,
    )
    return agent
