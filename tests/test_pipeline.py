"""Minimal integration test for the multi-agent pipeline wiring.

No Azure credentials or live infrastructure required: all specialist agents
are replaced with lightweight mock implementations that return pre-canned
JSON responses.  The test verifies that:

  1. ``wrap_as_tool`` produces a correctly named ``FunctionTool``.
  2. Invoking the tool calls the underlying agent and returns its text.
  3. When the underlying agent raises, the tool returns a structured JSON
     error object instead of propagating the exception.
  4. ``create_orchestrator`` wires all five specialist tools into the
     orchestrator's tool list.
  5. The ``_make_deploy_tool`` factory creates a fresh deploy agent per call
     and respects the ``max_parallel`` semaphore.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment stubs -- prevent real credential / config lookups during import
# ---------------------------------------------------------------------------
os.environ.setdefault("AZURE_AI_PROJECT_ENDPOINT", "https://mock.endpoint")
os.environ.setdefault("MODEL_DEPLOYMENT_NAME", "mock-model")


# ---------------------------------------------------------------------------
# Minimal in-process chat client that returns pre-canned text
# ---------------------------------------------------------------------------

from agent_framework import Agent, BaseChatClient, ChatResponse, Message
from agent_framework._middleware import FunctionInvocationContext
from agent_framework._tools import FunctionTool


class _MockChatClient(BaseChatClient):
    """Synchronous mock that always returns a fixed text response."""

    def __init__(self, response_text: str) -> None:
        super().__init__()
        self._response_text = response_text

    async def _inner_get_response(  # type: ignore[override]
        self, *, messages: Any, stream: bool, options: Any = None, **kwargs: Any
    ) -> ChatResponse:
        return ChatResponse(
            messages=[Message(role="assistant", contents=[self._response_text])],
            response_id="mock-response",
        )


class _FailingChatClient(BaseChatClient):
    """Mock that always raises to simulate specialist failure."""

    async def _inner_get_response(  # type: ignore[override]
        self, *, messages: Any, stream: bool, options: Any = None, **kwargs: Any
    ) -> ChatResponse:
        raise RuntimeError("specialist boom")


def _make_agent(response_text: str, name: str = "mock") -> Agent:
    return Agent(
        client=_MockChatClient(response_text),
        instructions="mock instructions",
        name=name,
    )


def _make_failing_agent(name: str = "mock") -> Agent:
    return Agent(
        client=_FailingChatClient(),
        instructions="mock instructions",
        name=name,
    )


# ---------------------------------------------------------------------------
# Helper: run a FunctionTool directly
# ---------------------------------------------------------------------------


async def _call_tool(tool: FunctionTool, task: str) -> str:
    ctx = FunctionInvocationContext(function=tool, arguments={}, kwargs={})
    return await tool.invoke(  # type: ignore[return-value]
        arguments={"task": task},
        context=ctx,
        skip_parsing=True,
    )


# ============================================================================
# Tests
# ============================================================================


class TestWrapAsTool:
    """Unit tests for ``agents.base.wrap_as_tool``."""

    def test_tool_name_and_description(self) -> None:
        from agents.base import wrap_as_tool

        agent = _make_agent("{}", "discovery")
        tool = wrap_as_tool(agent, "invoke_discovery", "Runs discovery")
        assert tool.name == "invoke_discovery"
        assert tool.description == "Runs discovery"

    def test_tool_is_function_tool(self) -> None:
        from agents.base import wrap_as_tool

        agent = _make_agent("{}", "discovery")
        tool = wrap_as_tool(agent, "invoke_discovery", "Runs discovery")
        assert isinstance(tool, FunctionTool)

    def test_invocation_returns_agent_text(self) -> None:
        from agents.base import wrap_as_tool

        payload = json.dumps({"examples": ["default"]})
        agent = _make_agent(payload, "discovery")
        tool = wrap_as_tool(agent, "invoke_discovery", "Runs discovery")

        result = asyncio.run(_call_tool(tool, "scan module"))
        assert result == payload

    def test_error_returns_json_object(self) -> None:
        from agents.base import wrap_as_tool

        agent = _make_failing_agent("discovery")
        tool = wrap_as_tool(agent, "invoke_discovery", "Runs discovery")

        result = asyncio.run(_call_tool(tool, "scan module"))
        data = json.loads(result)
        assert data["status"] == "error"
        assert data["agent"] == "invoke_discovery"
        assert "specialist boom" in data["error"]

    def test_custom_arg_name(self) -> None:
        from agents.base import wrap_as_tool
        from agent_framework._middleware import FunctionInvocationContext

        payload = "custom payload"
        agent = _make_agent(payload, "tester")
        tool = wrap_as_tool(agent, "my_tool", "A tool", arg_name="prompt")

        async def _call_with_prompt(t: FunctionTool, text: str) -> str:
            ctx = FunctionInvocationContext(function=t, arguments={}, kwargs={})
            return await t.invoke(  # type: ignore[return-value]
                arguments={"prompt": text},
                context=ctx,
                skip_parsing=True,
            )

        result = asyncio.run(_call_with_prompt(tool, "hello"))
        assert result == payload


class TestDeployTool:
    """Tests for the concurrent deploy tool factory."""

    def test_deploy_tool_is_function_tool(self) -> None:
        from agents.orchestrator import _make_deploy_tool

        with patch("agents.orchestrator.create_deploy_agent") as mock_factory:
            mock_factory.return_value = _make_agent('{"status": "success"}', "deploy")
            tool = _make_deploy_tool(max_parallel=2)
        assert isinstance(tool, FunctionTool)
        assert tool.name == "invoke_deploy"

    def test_deploy_creates_fresh_agent_per_call(self) -> None:
        """Each invocation of invoke_deploy must create a new agent instance."""
        from agents.orchestrator import _make_deploy_tool

        agents_created: list[Agent] = []

        def _factory() -> Agent:
            a = _make_agent('{"status": "success"}', "deploy")
            agents_created.append(a)
            return a

        # The patch must remain active while the tool is invoked, not just
        # while it is constructed, because the factory call happens inside
        # the async _invoke_deploy closure.
        with patch("agents.orchestrator.create_deploy_agent", side_effect=_factory):
            tool = _make_deploy_tool(max_parallel=3)

            async def _run_two() -> None:
                await asyncio.gather(
                    _call_tool(tool, "deploy example_a"),
                    _call_tool(tool, "deploy example_b"),
                )

            asyncio.run(_run_two())

        assert len(agents_created) == 2, "Expected one fresh agent per call"

    def test_deploy_error_returns_json(self) -> None:
        from agents.orchestrator import _make_deploy_tool

        with patch("agents.orchestrator.create_deploy_agent") as mock_factory:
            mock_factory.return_value = _make_failing_agent("deploy")
            tool = _make_deploy_tool(max_parallel=1)

        result = asyncio.run(_call_tool(tool, "deploy example_fail"))
        data = json.loads(result)
        assert data["status"] == "error"
        assert data["agent"] == "invoke_deploy"

    def test_max_parallel_limits_concurrency(self) -> None:
        """With max_parallel=1 calls are serialised, not run concurrently."""
        from agents.orchestrator import _make_deploy_tool

        active: list[int] = []
        peak: list[int] = []

        async def _slow_agent_run(*args: Any, **kwargs: Any) -> Any:
            active.append(1)
            peak.append(len(active))
            await asyncio.sleep(0)  # yield
            active.pop()
            resp = MagicMock()
            resp.text = '{"status":"ok"}'
            return resp

        with patch("agents.orchestrator.create_deploy_agent") as mock_factory:
            mock_agent = MagicMock()
            mock_agent.run = _slow_agent_run
            mock_factory.return_value = mock_agent
            tool = _make_deploy_tool(max_parallel=1)

            async def _run_three() -> None:
                await asyncio.gather(
                    _call_tool(tool, "a"),
                    _call_tool(tool, "b"),
                    _call_tool(tool, "c"),
                )

            asyncio.run(_run_three())

        assert max(peak) == 1, f"Expected peak concurrency 1, got {max(peak)}"


class TestOrchestratorWiring:
    """Verify create_orchestrator wires all five specialist tools."""

    _SPECIALIST_TOOL_NAMES = {
        "invoke_discovery",
        "invoke_deploy",
        "invoke_analysis",
        "invoke_reviewer",
        "invoke_reporter",
    }

    def _mock_create_specialist(self, name: str, instructions: str, tools: list) -> Agent:
        """Return a mock Agent so no Azure credentials are needed."""
        return _make_agent("{}", name)

    def test_orchestrator_has_all_five_tools(self) -> None:
        orchestrator_tools_seen: list[list[Any]] = []

        def _mock_create_specialist(name: str, instructions: str, tools: list) -> Agent:
            if name == "orchestrator":
                orchestrator_tools_seen.append(tools)
            return _make_agent("{}", name)

        with (
            patch("agents.base.FoundryChatClient"),
            patch("agents.base.DefaultAzureCredential"),
            patch(
                "agents.orchestrator.create_specialist",
                side_effect=_mock_create_specialist,
            ),
        ):
            from agents.orchestrator import create_orchestrator

            create_orchestrator(mode="local")

        assert orchestrator_tools_seen, "create_specialist was never called for orchestrator"
        tool_names = {t.name for t in orchestrator_tools_seen[0] if hasattr(t, "name")}
        assert self._SPECIALIST_TOOL_NAMES <= tool_names, (
            f"Missing tools: {self._SPECIALIST_TOOL_NAMES - tool_names}"
        )

    def test_orchestrator_specialists_dict_present(self) -> None:
        with (
            patch("agents.base.FoundryChatClient"),
            patch("agents.base.DefaultAzureCredential"),
            patch(
                "agents.orchestrator.create_specialist",
                side_effect=self._mock_create_specialist,
            ),
        ):
            from agents.orchestrator import create_orchestrator

            pipeline = create_orchestrator(mode="local")

        assert "orchestrator" in pipeline
        assert "specialists" in pipeline
        expected_specialists = {"discovery", "analysis", "reviewer", "reporter"}
        assert expected_specialists <= set(pipeline["specialists"].keys())
