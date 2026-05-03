"""avm-contributor-agent — entry point.

An AVM module developer assistant built with the Microsoft Agent Framework.
Takes GitHub issues from upstream AVM repos, implements fixes on a fork,
dispatches CI to kewalaka/avm-contributions via repository_dispatch, and
opens PRs with UPGRADE.md evidence.

Subcommands:
  dev    Run the Developer→Reviewer→CI pipeline on an upstream issue.
  chat   Interactive chat mode (single agent, local or Foundry-hosted). Default if no subcommand given.
  test   Legacy batch test-request mode.

Examples:
  python main.py dev --upstream-repo Azure/terraform-azurerm-avm-res-app-managedenvironment --issue 42
  python main.py dev --upstream-repo Azure/terraform-azurerm-avm-res-storage-storageaccount --issue 17 --fork-owner myorg
  python main.py chat
  python main.py test --request test-request.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

# Tool imports for single-agent (chat/test) modes
from tools.git_ops import (
    add_remote,
    clone_repo,
    create_branch,
    commit_files,
    git_switch_ref,
    push_branch,
    verify_branch_provenance,
)
from tools.analysis import (
    read_upgrade_doc,
    summarise_plan_json,
)
from tools.analysis_ci import (
    classify_plan_changes,
    generate_upgrade_md_section,
    parse_ci_summary,
)
from tools.dispatch_ci import (
    check_dispatch_token,
    dispatch_module_checks,
    dispatch_module_e2e,
    dispatch_upgrade_test,
)
from tools.fork_ops import (
    clone_fork,
    ensure_fork,
    get_fork_info,
    sync_fork_default_branch,
)
from tools.github_ops import (
    add_issue_comment,
    create_pull_request,
    download_workflow_artifacts,
    flip_pr_ready,
    get_latest_release,
    get_workflow_run_status,
    search_github_issues,
    update_pr_body_section,
)
from tools.module_discovery import (
    discover_module_structure,
    ingest_local_module,
    list_module_examples,
    read_module_skill,
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

SYSTEM_INSTRUCTIONS = """\
You are the avm-contributor-agent — an AVM module developer assistant.

You take GitHub issues from upstream AVM repositories, implement fixes on a fork,
dispatch CI to kewalaka/avm-contributions via repository_dispatch, and open PRs
with UPGRADE.md evidence. You work in a maker/checker 2-agent pipeline:
- Developer agent: writes code diffs
- Reviewer agent: pre-push diff gatekeeper (no tools, pure LLM diff evaluation)

Always follow AVM conventions: snake_case variables, id+resource outputs,
no hardcoded locations, no provider version pins unless required.
"""

ALL_TOOLS = [
    # Git
    clone_repo,
    create_branch,
    commit_files,
    git_switch_ref,
    add_remote,
    push_branch,
    verify_branch_provenance,
    # Fork management
    ensure_fork,
    sync_fork_default_branch,
    clone_fork,
    get_fork_info,
    # CI dispatch
    dispatch_module_checks,
    dispatch_module_e2e,
    dispatch_upgrade_test,
    check_dispatch_token,
    # Module discovery
    ingest_local_module,
    discover_module_structure,
    read_module_skill,
    list_module_examples,
    # Analysis
    read_upgrade_doc,
    summarise_plan_json,
    # CI analysis
    parse_ci_summary,
    classify_plan_changes,
    generate_upgrade_md_section,
    # GitHub operations
    create_pull_request,
    add_issue_comment,
    search_github_issues,
    get_latest_release,
    download_workflow_artifacts,
    get_workflow_run_status,
    update_pr_body_section,
    flip_pr_ready,
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


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def build_cli_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="avm-contributor-agent — AVM module developer assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # --- dev subcommand (default) ---
    dev_p = subparsers.add_parser(
        "dev",
        help="Run the Developer→Reviewer→CI pipeline on an upstream issue",
    )
    dev_p.add_argument(
        "--upstream-repo",
        required=True,
        metavar="OWNER/REPO",
        help="Upstream AVM module repo (e.g. Azure/terraform-azurerm-avm-res-...)",
    )
    dev_p.add_argument(
        "--issue",
        type=int,
        default=None,
        metavar="NUMBER",
        help="Issue number in the upstream repo (issue-driven mode)",
    )
    dev_p.add_argument(
        "--fork-owner",
        default="",
        metavar="OWNER",
        help="GitHub user/org that owns the fork (default: authenticated gh user)",
    )
    dev_p.add_argument(
        "--base-ref",
        default="main",
        help="Base branch to fork from and PR against (default: main)",
    )
    dev_p.add_argument(
        "--existing-repo",
        default="",
        metavar="PATH",
        help="Local path for existing-repo mode (skips fork/clone)",
    )
    dev_p.add_argument(
        "--pr",
        type=int,
        default=None,
        metavar="NUMBER",
        help="PR number in the upstream repo (existing-pr mode: clones fork branch and continues)",
    )
    dev_p.add_argument(
        "--resume",
        default="",
        metavar="RUN_ID",
        help="Resume a prior pipeline run by run_id",
    )
    dev_p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max Developer→Reviewer→CI retry attempts (default: 3)",
    )

    # --- chat subcommand ---
    subparsers.add_parser(
        "chat",
        help="Interactive chat mode (single agent, Foundry-hosted or local)",
    )

    # --- test subcommand (legacy) ---
    test_p = subparsers.add_parser(
        "test",
        help="Legacy batch test-request mode",
    )
    test_p.add_argument(
        "--request",
        metavar="FILE",
        help="Path to a test-request.json file",
    )
    test_p.add_argument(
        "--module",
        metavar="SOURCE",
        help="Module source: registry path, GitHub URL, or local path",
    )
    test_p.add_argument("--base-ref", default="main")
    test_p.add_argument("--head-ref", default="")
    test_p.add_argument("--examples", default="")
    test_p.add_argument("--skip-examples", default="")
    test_p.add_argument("--github-repo", default="")
    test_p.add_argument("--github-pr", type=int, default=0)
    test_p.add_argument("--no-cleanup", action="store_true")
    test_p.add_argument("--subscription-id", default="")
    test_p.add_argument("--location", default="")
    test_p.add_argument("--max-parallel", type=int, default=3)
    test_p.add_argument("--timeout", type=int, default=120)

    return parser


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def run_dev(args: argparse.Namespace) -> None:
    """Handle the `dev` subcommand."""
    dev_issues = config.validate_dev_mode()
    if dev_issues:
        for issue in dev_issues:
            logger.error("Config: %s", issue)
        sys.exit(1)

    from request import DevRequest

    dev_request = DevRequest(
        upstream_repo=args.upstream_repo,
        issue_number=args.issue,
        fork_owner=args.fork_owner,
        base_ref=args.base_ref,
        local_path=args.existing_repo,
        pr_number=args.pr,
        max_ci_retries=args.max_retries,
    )

    try:
        dev_request.validate()
    except ValueError as exc:
        logger.error("Invalid dev request: %s", exc)
        sys.exit(1)

    logger.info(
        "Starting developer pipeline: mode=%s upstream=%s issue=%s run_id=%s",
        dev_request.mode,
        dev_request.upstream_repo,
        dev_request.issue_number,
        dev_request.run_id,
    )

    from agents.orchestrator import run_developer_pipeline

    result = asyncio.run(run_developer_pipeline(dev_request))

    print(json.dumps(result, indent=2))

    if result.get("outcome") == "success":
        logger.info("Pipeline succeeded — PR: %s", result.get("pr_url", ""))
    elif result.get("outcome") == "escalated":
        logger.warning("Pipeline escalated: %s", result.get("escalation_reason", ""))
        sys.exit(2)
    else:
        logger.error("Pipeline failed: %s", result.get("errors", result))
        sys.exit(1)


def run_chat(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Handle the `chat` subcommand."""
    issues = config.validate()
    if issues:
        for issue in issues:
            logger.warning("Config: %s", issue)

    if config.foundry_hosted:
        logger.info("Starting in Foundry-hosted mode (MCP enabled: %s)", config.has_mcp)
        from runtime.foundry import create_agent, run

        client, agent_def = create_agent(SYSTEM_INSTRUCTIONS)
        run(client, agent_def)
    else:
        logger.info("Starting in local interactive mode")
        from runtime.local import run as local_run, create_agent

        agent = create_agent(SYSTEM_INSTRUCTIONS, ALL_TOOLS)
        local_run(agent, initial_message=None)


def run_test(args: argparse.Namespace) -> None:
    """Handle the legacy `test` subcommand."""
    issues = config.validate()
    if issues:
        for issue in issues:
            logger.warning("Config: %s", issue)

    from request import TestRequest

    test_request = None
    if args.request:
        logger.info("Loading test request from %s", args.request)
        test_request = TestRequest.from_json_file(args.request)
        logger.info("Test request loaded: run_id=%s", test_request.run_id)
    elif args.module:
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

    if test_request is not None:
        from policy import evaluate_request

        policy_result = evaluate_request(test_request)
        if not policy_result.approved:
            logger.error("Policy check failed: %s", policy_result.reason)
            sys.exit(1)
        if policy_result.requires_confirmation:
            if not test_request.interactive:
                logger.error(
                    "Policy requires confirmation but running non-interactively: %s",
                    policy_result.reason,
                )
                sys.exit(1)
            logger.warning("Policy: %s", policy_result.reason)

    initial_message = None
    if test_request is not None:
        initial_message = test_request.to_agent_message()

    logger.info("Starting in local mode")
    from runtime.local import run as local_run, create_agent

    agent = create_agent(SYSTEM_INSTRUCTIONS, ALL_TOOLS)
    local_run(agent, initial_message=initial_message)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse subcommand and dispatch to handler."""
    parser = build_cli_parser()
    args = parser.parse_args()

    if args.subcommand == "dev":
        run_dev(args)
    elif args.subcommand == "test":
        run_test(args)
    else:
        # Default: chat (covers both explicit `chat` and bare `python main.py`)
        run_chat(args)


if __name__ == "__main__":
    main()
