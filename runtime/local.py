"""Local runtime — agent_framework Agent with @tool-decorated functions.

This is the default runtime used during local development. It creates an
Agent backed by FoundryChatClient and registers all tools as decorated
callables. No MCP servers are wired in this mode.
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from config import config


def create_agent(instructions: str, tools: list) -> Agent:
    """Create an Agent for local development."""
    return Agent(
        client=FoundryChatClient(
            project_endpoint=config.project_endpoint,
            model=config.model_deployment_name,
            credential=DefaultAzureCredential(),
        ),
        instructions=instructions,
        tools=tools,
    )


def run(agent: Agent, initial_message: str | None = None) -> None:
    """Start the agent server using the agent-framework server adapter.

    Args:
        agent: The Agent instance to run.
        initial_message: If provided, the agent processes this message in
            batch mode instead of waiting for interactive input.
    """
    if initial_message is not None:
        # Batch mode: process the TestRequest and exit
        import asyncio
        import logging

        logger = logging.getLogger(__name__)
        logger.info("Running in batch mode")

        async def _run_batch() -> None:
            response = await agent.run(initial_message)
            print(response.text)

        asyncio.run(_run_batch())
    else:
        # Interactive mode: start the server
        from azure.ai.agentserver.agentframework import from_agent_framework

        from_agent_framework(agent).run()
