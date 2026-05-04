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


def run(agent: ChatAgent, initial_message: str | None = None) -> None:
    """Start the agent server using the agent-framework server adapter.

    Args:
        agent: The ChatAgent instance to run.
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
            response = await agent.get_response(initial_message)
            print(response)

        asyncio.run(_run_batch())
    else:
        # Interactive REPL mode: read from stdin and send to agent
        import asyncio
        import logging

        logger = logging.getLogger(__name__)
        logger.info("Starting local interactive mode — type your message and press Enter (Ctrl-C to exit)")

        async def _repl() -> None:
            while True:
                try:
                    user_input = input("\nYou: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user_input:
                    continue
                response = await agent.get_response(user_input)
                print(f"\nAgent: {response}")

        asyncio.run(_repl())
