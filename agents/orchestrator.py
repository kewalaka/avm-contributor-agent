"""Orchestrator agent -- top-level hub that delegates to specialist agents.

The orchestrator owns the test plan and coordinates the pipeline:
  Discovery -> Deploy (concurrent per example) -> Analysis -> Review -> Report

It keeps its own context small by delegating all heavy work to specialists
and receiving only structured JSON summaries back.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import create_specialist
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

## Pipeline

1. **Discovery**: Ask the Discovery Agent to scan the module.
   Input: module path or registry source.
   Output: ModuleMap (structure, examples, skills, UPGRADE.md).

2. **Deploy** (concurrent per example): Ask Deploy Agent(s) to test each
   example. Each returns a DeployResult JSON.
   You can dispatch multiple deploy tasks concurrently.

3. **Analysis**: Pass all DeployResults + ModuleMap to the Analysis Agent.
   It cross-references results with UPGRADE.md and skills.
   Output: list of AnalysisFinding objects.

4. **Review**: Pass findings to the Reviewer Agent for cross-checking.
   Output: list of ReviewedFinding objects (confirmed/rejected).

5. **Report**: Pass validated findings to the Reporter Agent for delivery.
   Output: report path, filed issues, upgrade suggestions.

## Your responsibilities
- Create the test plan based on discovery results.
- Decide which examples to test (default: all; user can override).
- Coordinate the pipeline sequence.
- Summarise final results for the user.

## What you do NOT do
- You don't run terraform commands directly.
- You don't read raw terraform output.
- You don't interact with GitHub directly.
- You delegate ALL specialist work to the appropriate agent.
"""


def create_orchestrator(mode: str = "local") -> dict[str, Any]:
    """Create the orchestrator and all specialist agents.

    Args:
        mode: "local" for ChatAgent-based agents, "foundry" for future A2A.

    Returns:
        Dict with orchestrator agent and specialist agents.
    """
    specialists = {
        "discovery": create_discovery_agent(),
        "deploy": create_deploy_agent(),
        "analysis": create_analysis_agent(),
        "reviewer": create_reviewer_agent(),
        "reporter": create_reporter_agent(),
    }

    # The orchestrator doesn't need tools of its own -- it delegates.
    # But it needs a way to invoke specialists. In Phase 2, this uses
    # the agent-as-tools pattern. For now, we expose the specialists
    # and let the runtime wire them together.
    orchestrator = create_specialist(
        "orchestrator",
        ORCHESTRATOR_INSTRUCTIONS,
        tools=[],  # Orchestrator delegates, no direct tools
    )

    logger.info(
        "Multi-agent pipeline ready: orchestrator + %d specialists",
        len(specialists),
    )

    return {
        "orchestrator": orchestrator,
        "specialists": specialists,
    }
