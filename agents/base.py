"""Base class and utilities for specialist agents."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent
from agent_framework._middleware import FunctionInvocationContext
from agent_framework._tools import FunctionTool
from agent_framework.foundry import FoundryChatClient
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
) -> Agent:
    """Create a specialist Agent with focused instructions and tools."""
    agent = Agent(
        client=FoundryChatClient(
            project_endpoint=config.project_endpoint,
            model=config.model_deployment_name,
            credential=DefaultAzureCredential(),
        ),
        name=name,
        instructions=instructions,
        tools=tools,
    )
    logger.info("Created specialist agent: %s (%d tools)", name, len(tools))
    return agent


def wrap_as_tool(
    agent: Agent,
    name: str,
    description: str,
    arg_name: str = "task",
    arg_description: str | None = None,
) -> FunctionTool:
    """Wrap a specialist Agent as a FunctionTool for orchestrator invocation.

    Adds structured error handling (returns a JSON error object on failure so
    the orchestrator can decide whether to retry or abort) and INFO-level
    telemetry logging (agent_name, input_len, output_len, duration_ms) on
    every call.

    Args:
        agent: The specialist Agent to wrap.
        name: Tool name exposed to the orchestrator LLM.
        description: Tool description exposed to the orchestrator LLM.
        arg_name: Name of the single string argument (default: ``"task"``).
        arg_description: Human-readable description for the argument.

    Returns:
        A :class:`FunctionTool` that can be placed in the orchestrator's
        ``tools`` list.
    """
    _arg_description = arg_description or f"Task for {name}"
    _input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            arg_name: {
                "type": "string",
                "description": _arg_description,
            }
        },
        "required": [arg_name],
        "additionalProperties": False,
    }

    async def _invoke(ctx: FunctionInvocationContext, **kwargs: Any) -> str:
        prompt = str(kwargs.get(arg_name, ""))
        input_len = len(prompt)
        start = time.monotonic()
        try:
            response = await agent.run(
                prompt,
                function_invocation_kwargs=dict(ctx.kwargs),
            )
            result = response.text or ""
            duration_ms = (time.monotonic() - start) * 1000
            output_len = len(result)
            logger.info(
                "agent=%s input_len=%d output_len=%d duration_ms=%.1f",
                name,
                input_len,
                output_len,
                duration_ms,
            )
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "agent=%s input_len=%d duration_ms=%.1f error=%s",
                name,
                input_len,
                duration_ms,
                exc,
            )
            return json.dumps(
                {
                    "agent": name,
                    "status": "error",
                    "error": str(exc),
                }
            )

    return FunctionTool(
        name=name,
        description=description,
        func=_invoke,
        input_model=_input_schema,
    )
