"""tf-module-developer-agent — entry point.

An AVM module developer assistant built with the Microsoft Agent Framework.
Takes GitHub issues from upstream AVM repos, implements fixes on a fork,
dispatches CI to kewalaka/avm-contributions via repository_dispatch, and
opens PRs with UPGRADE.md evidence.

Invocation modes:
  Interactive (default):  python main.py
  Batch (JSON request):  python main.py --request test-request.json
  Batch (CLI shorthand): python main.py --module Azure/terraform-azurerm-avm-res-... --head-ref feat/azapi
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

from config import config

# Import all tool functions so the framework discovers them
from tools.git_ops import (
    add_remote,
    clone_registry_module,
    clone_repo,
    git_switch_ref,
)
from tools.analysis import (
    read_upgrade_doc,
    summarise_plan_json,
)
from tools.module_discovery import (
    discover_module_structure,
    ingest_local_module,
    list_module_examples,
    read_module_skill,
)
from tools.github_ops import (
    add_issue_comment,
    create_github_issue,
    create_pull_request,
    download_workflow_artifacts,
    get_latest_release,
    get_workflow_run_status,
    search_github_issues,
)
from tools.reporting import (
    generate_issue_body,
    generate_test_report,
    generate_upgrade_doc_suggestion,
)
from tools.tracking import (
    query_findings,
    query_module_health,
    query_test_history,
    store_test_run,
)
from tools.analysis_ci import (
    classify_plan_changes,
    generate_upgrade_md_section,
    parse_ci_summary,
)

SYSTEM_INSTRUCTIONS = """\
You are the tf-module-developer-agent — an AVM module developer assistant.

You take GitHub issues from upstream AVM repositories, implement fixes on a fork,
dispatch CI to kewalaka/avm-contributions via repository_dispatch, and open PRs
with UPGRADE.md evidence. You work in a maker/checker 2-agent pipeline:
- Developer agent: writes code diffs
- Reviewer agent: pre-push diff gatekeeper

Phase 4 will wire the full CLI entry points. For now, the pipeline is being constructed.
"""

ALL_TOOLS = [
    # Git
    clone_repo,
    clone_registry_module,
    git_switch_ref,
    add_remote,
    # Module Discovery
    ingest_local_module,
    discover_module_structure,
    read_module_skill,
    list_module_examples,
    # Analysis
    read_upgrade_doc,
    summarise_plan_json,
    # CI Analysis
    parse_ci_summary,
    classify_plan_changes,
    generate_upgrade_md_section,
    # GitHub Operations
    create_github_issue,
    create_pull_request,
    add_issue_comment,
    search_github_issues,
    get_latest_release,
    download_workflow_artifacts,
    get_workflow_run_status,
    # Reporting
    generate_test_report,
    generate_issue_body,
    generate_upgrade_doc_suggestion,
    # Tracking
    store_test_run,
    query_findings,
    query_module_health,
    query_test_history,
]


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Infrastructure Testing Agent — test Terraform modules",
    )
    parser.add_argument(
        "--request",
        metavar="FILE",
        help="Path to a test-request.json file (batch mode)",
    )
    parser.add_argument(
        "--module",
        metavar="SOURCE",
        help="Module source: registry path, GitHub URL, or local path",
    )
    parser.add_argument("--base-ref", default="main", help="Base ref (default: main)")
    parser.add_argument("--head-ref", default="", help="Head ref for upgrade testing")
    parser.add_argument(
        "--examples", default="", help="Comma-separated example names to test"
    )
    parser.add_argument(
        "--skip-examples", default="", help="Comma-separated examples to skip"
    )
    parser.add_argument("--github-repo", default="", help="GitHub repo for reporting")
    parser.add_argument(
        "--github-pr", type=int, default=0, help="PR number for reporting"
    )
    parser.add_argument("--no-cleanup", action="store_true", help="Keep test resources")
    parser.add_argument("--subscription-id", default="", help="Azure subscription ID")
    parser.add_argument("--location", default="", help="Azure region")
    parser.add_argument(
        "--max-parallel", type=int, default=3, help="Max parallel deploys"
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="Timeout in minutes"
    )
    return parser


def main() -> None:
    """Select runtime and start the agent."""
    parser = build_cli_parser()
    args = parser.parse_args()

    issues = config.validate()
    if issues:
        for issue in issues:
            logger.warning("Config: %s", issue)

    # Build TestRequest from CLI args if provided
    test_request = None
    if args.request:
        from request import TestRequest

        logger.info("Loading test request from %s", args.request)
        test_request = TestRequest.from_json_file(args.request)
        logger.info("Test request loaded: run_id=%s", test_request.run_id)
    elif args.module:
        from request import TestRequest

        logger.info("Building test request from CLI args")
        test_request = TestRequest.from_cli_args(
            module=args.module,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            examples=args.examples,
            skip=args.skip_examples,
            github_repo=args.github_repo,
            github_pr=args.github_pr,
            no_cleanup=args.no_cleanup,
            subscription_id=args.subscription_id,
            location=args.location,
            max_parallel=args.max_parallel,
            timeout=args.timeout,
        )
        logger.info("Test request built: run_id=%s", test_request.run_id)

    # Evaluate security policy when a request is provided
    if test_request is not None:
        from policy import evaluate_request

        policy_result = evaluate_request(test_request)
        if not policy_result.approved:
            logger.error("Policy check failed: %s", policy_result.reason)
            sys.exit(1)
        if policy_result.requires_confirmation:
            if not test_request.interactive:
                logger.error(
                    "Policy requires confirmation but running in non-interactive (batch) mode: %s",
                    policy_result.reason,
                )
                sys.exit(1)
            logger.warning("Policy: %s", policy_result.reason)

    # Format the initial message for batch mode
    initial_message = None
    if test_request is not None:
        initial_message = test_request.to_agent_message()
        logger.info(
            "Batch mode: agent will process TestRequest (run_id=%s)",
            test_request.run_id,
        )

    if config.multi_agent:
        logger.info("Multi-agent mode requested — not yet implemented (Phase 3)")
        # TODO Phase 3: create_orchestrator()
        sys.exit(1)

    elif config.foundry_hosted:
        logger.info("Starting in Foundry-hosted mode (MCP enabled: %s)", config.has_mcp)
        from runtime.foundry import create_agent, run

        client, agent_def = create_agent(SYSTEM_INSTRUCTIONS)
        run(client, agent_def)
    else:
        logger.info("Starting in local mode")
        from runtime.local import run as local_run, create_agent

        agent = create_agent(SYSTEM_INSTRUCTIONS, ALL_TOOLS)
        local_run(agent, initial_message=initial_message)


if __name__ == "__main__":
    main()
