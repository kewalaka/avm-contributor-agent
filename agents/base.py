"""Base class and utilities for specialist agents."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

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


def create_specialist(
    name: str,
    instructions: str,
    tools: list,
) -> ChatAgent:
    """Create a specialist ChatAgent with focused instructions and tools."""
    agent = ChatAgent(
        chat_client=AzureAIAgentClient(
            project_endpoint=config.project_endpoint,
            model_deployment_name=config.model_deployment_name,
            credential=DefaultAzureCredential(),
        ),
        instructions=instructions,
        tools=tools,
    )
    logger.info("Created specialist agent: %s (%d tools)", name, len(tools))
    return agent
