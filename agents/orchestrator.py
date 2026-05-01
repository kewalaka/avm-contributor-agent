"""Orchestrator agent -- top-level hub that delegates to specialist agents.

The orchestrator owns the test plan and coordinates the pipeline:
  Discovery -> Deploy (concurrent per example) -> Analysis -> Review -> Report

It keeps its own context small by delegating all heavy work to specialists
and receiving only structured JSON summaries back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents.base import create_specialist, wrap_as_tool
from agents.discovery import create_discovery_agent
from agents.deploy import create_deploy_agent
from agents.analysis import create_analysis_agent
from agents.reviewer import create_reviewer_agent
from agents.reporter import create_reporter_agent

logger = logging.getLogger(__name__)

ORCHESTRATOR_INSTRUCTIONS = """\
You are the Orchestrator Agent for the Infrastructure Testing pipeline.

You coordinate specialist agents to test Terraform modules. Your context
stays small -- you delegate heavy work and receive structured results.

## Input

You receive a TestRequest (structured or as a user message) containing:
- module_source: what to test
- base_ref / head_ref: version comparison (upgrade testing when head_ref set)
- examples / skip_examples: which examples to test (empty = ALL)
- github_repo / github_pr / github_issue: where to post results
- run_id: tracking identifier

## Pipeline

1. **Discovery**: Call `invoke_discovery` with the module path or registry
   source. It returns a ModuleMap (structure, examples, skills, UPGRADE.md).

2. **Example Filtering**: Apply the TestRequest's examples/skip_examples
   against the discovered examples. Default is ALL examples.

3. **Deploy/Upgrade** (per example, up to max_parallel in parallel):

   **If head_ref is set (upgrade test)**:
   Call `invoke_deploy` for each example with run_upgrade_test instructions.

   **If head_ref is NOT set (simple deploy)**:
   Call `invoke_deploy` for each example for a standard deploy test.

   You may call `invoke_deploy` multiple times concurrently (one per example).
   Each call returns a DeployResult or UpgradeTestResult JSON.

4. **Analysis**: Call `invoke_analysis` with all deploy results + ModuleMap.
   For upgrade tests, the analysis agent cross-references upgrade plan diffs
   against UPGRADE.md to identify undocumented breaking changes.
   Returns a list of AnalysisFinding JSON objects.

5. **Review**: Call `invoke_reviewer` with the findings.
   Returns a list of ReviewedFinding JSON objects (confirmed/rejected).

6. **Report**: Call `invoke_reporter` with the validated findings plus
   github_repo/pr/issue from the TestRequest.
   Returns a report path, filed issues, and upgrade suggestions.

## Your responsibilities
- Parse the TestRequest to determine scope and reporting targets.
- Determine the mode: upgrade test (head_ref set) vs simple deploy.
- Create the test plan based on discovery results.
- Apply example filtering (default: test all discovered examples).
- Coordinate the pipeline sequence.
- Summarise final results for the user.

## What you do NOT do
- You don't run terraform commands directly.
- You don't read raw terraform output.
- You don't interact with GitHub directly.
- You delegate ALL specialist work to the appropriate agent.

## Security
- Check that the module source is approved before deploying.
- In non-interactive mode, reject untrusted module sources.
- Always confirm before deploying if the module is not on the allowlist.
"""


def _make_deploy_tool(max_parallel: int = 3) -> Any:
    """Build the ``invoke_deploy`` tool with a per-call fresh agent instance.

    Each invocation of the returned tool creates a new Deploy specialist so
    that concurrent example deployments do not share state.  An
    ``asyncio.Semaphore`` caps live concurrent calls at ``max_parallel``.
    """
    from agent_framework._middleware import FunctionInvocationContext
    from agent_framework._tools import FunctionTool
    import json
    import time
    import logging as _logging

    _logger = _logging.getLogger(__name__)
    semaphore = asyncio.Semaphore(max_parallel)

    _input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Deploy task description. Include example_name, workspace_path, "
                    "base_ref, and optionally head_ref and request_fields as JSON."
                ),
            }
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    async def _invoke_deploy(ctx: FunctionInvocationContext, **kwargs: Any) -> str:
        prompt = str(kwargs.get("task", ""))
        input_len = len(prompt)
        start = time.monotonic()

        async with semaphore:
            # Fresh agent per call for example-level isolation
            deploy_agent = create_deploy_agent()
            try:
                response = await deploy_agent.run(
                    prompt,
                    function_invocation_kwargs=dict(ctx.kwargs),
                )
                result = response.text or ""
                duration_ms = (time.monotonic() - start) * 1000
                output_len = len(result)
                _logger.info(
                    "agent=invoke_deploy input_len=%d output_len=%d duration_ms=%.1f",
                    input_len,
                    output_len,
                    duration_ms,
                )
                return result
            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                _logger.error(
                    "agent=invoke_deploy input_len=%d duration_ms=%.1f error=%s",
                    input_len,
                    duration_ms,
                    exc,
                )
                return json.dumps(
                    {
                        "agent": "invoke_deploy",
                        "status": "error",
                        "error": str(exc),
                    }
                )

    return FunctionTool(
        name="invoke_deploy",
        description=(
            "Deploy a single example and return a DeployResult or UpgradeTestResult JSON. "
            "Each call creates an isolated Deploy specialist. "
            "Calls may be made concurrently (one per example)."
        ),
        func=_invoke_deploy,
        input_model=_input_schema,
    )


def create_orchestrator(mode: str = "local", max_parallel: int = 3) -> dict[str, Any]:
    """Create the orchestrator and all specialist agents.

    Specialist agents are wrapped as tools and wired into the orchestrator so
    it can invoke them directly.  The Deploy specialist is created fresh for
    every invocation to support concurrent per-example deployments.

    Args:
        mode: "local" for Agent-based agents, "foundry" for future A2A.
        max_parallel: Maximum concurrent deploy agent calls (default 3).

    Returns:
        Dict with ``orchestrator`` Agent and ``specialists`` dict.
    """
    specialists = {
        "discovery": create_discovery_agent(),
        "analysis": create_analysis_agent(),
        "reviewer": create_reviewer_agent(),
        "reporter": create_reporter_agent(),
    }

    invoke_discovery = wrap_as_tool(
        specialists["discovery"],
        name="invoke_discovery",
        description=(
            "Scan a Terraform module and return a ModuleMap JSON. "
            "Input: module_source path or registry reference and base_ref."
        ),
    )

    # Deploy uses a fresh agent per call; built separately to cap concurrency.
    invoke_deploy = _make_deploy_tool(max_parallel=max_parallel)

    invoke_analysis = wrap_as_tool(
        specialists["analysis"],
        name="invoke_analysis",
        description=(
            "Analyse deploy results against module knowledge and return a JSON array "
            "of AnalysisFinding objects. Input: deploy_results JSON array and module_map JSON."
        ),
    )

    invoke_reviewer = wrap_as_tool(
        specialists["reviewer"],
        name="invoke_reviewer",
        description=(
            "Cross-check analysis findings and return a JSON array of ReviewedFinding objects "
            "(confirmed/rejected/needs_investigation). Input: findings JSON array."
        ),
    )

    invoke_reporter = wrap_as_tool(
        specialists["reporter"],
        name="invoke_reporter",
        description=(
            "Format validated findings and deliver reports via GitHub. "
            "Input: reviewed_findings JSON array plus github_repo, github_pr, github_issue."
        ),
    )

    orchestrator = create_specialist(
        "orchestrator",
        ORCHESTRATOR_INSTRUCTIONS,
        tools=[
            invoke_discovery,
            invoke_deploy,
            invoke_analysis,
            invoke_reviewer,
            invoke_reporter,
        ],
    )

    logger.info(
        "Multi-agent pipeline ready: orchestrator + %d specialists",
        len(specialists),
    )

    return {
        "orchestrator": orchestrator,
        "specialists": specialists,
    }
