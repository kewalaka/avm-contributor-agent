"""Local runtime — agent_framework ChatAgent with @ai_function tools.

This is the default runtime used during local development. It creates a
ChatAgent backed by AzureAIAgentClient and registers all tools as
@ai_function callables. No MCP servers are wired in this mode.
"""

from __future__ import annotations

from agent_framework import ChatAgent
from agent_framework.azure import AzureAIAgentClient
from azure.identity import DefaultAzureCredential

from config import config


def create_agent(instructions: str, tools: list) -> ChatAgent:
    """Create a ChatAgent for local development."""
    return ChatAgent(
        chat_client=AzureAIAgentClient(
            project_endpoint=config.project_endpoint,
            model_deployment_name=config.model_deployment_name,
            credential=DefaultAzureCredential(),
        ),
        instructions=instructions,
        tools=tools,
    )


def run(agent: ChatAgent) -> None:
    """Start the agent server using the agent-framework server adapter."""
    from azure.ai.agentserver.agentframework import from_agent_framework

    from_agent_framework(agent).run()
