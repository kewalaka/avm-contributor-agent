"""Infrastructure Testing Agent — entry point.

A hosted agent built with the Microsoft Agent Framework that tests
Terraform modules by deploying, planning, and analysing diffs.
Dispatches to local or Foundry-hosted runtime based on configuration.

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
from tools.terraform import (
    check_idempotency,
    create_workspace,
    delete_workspace,
    list_workspace_files,
    read_workspace_file,
    run_avm_cli,
    terraform_apply,
    terraform_destroy,
    terraform_init,
    terraform_init_upgrade,
    terraform_output,
    terraform_plan,
    terraform_plan_json,
    terraform_show,
    terraform_test,
    write_workspace_file,
)
from tools.azure import (
    check_resource_group_exists,
    check_role_assignments,
    create_resource_group,
    delete_resource_group,
    get_current_identity,
)
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
from tools.upgrade_test import run_upgrade_test

SYSTEM_INSTRUCTIONS = """\
You are an Infrastructure Testing Agent with expertise in Terraform, \
Azure Verified Modules (AVM), and Azure infrastructure.

Your primary capabilities:
1. **Module Upgrade Testing** — The core workflow. Deploy the old version \
   of a module (base_ref), switch to the new version (head_ref), and \
   capture what terraform plan shows as the upgrade diff. Then \
   cross-reference the diff against UPGRADE.md to identify undocumented \
   breaking changes.
2. **Simple Deploy Testing** — Deploy a module version and verify it works \
   (plan, apply, idempotency check, destroy).
3. **Resource Lifecycle** — Create test resource groups, deploy, and clean up.
4. **Module Discovery** — Scan a module to understand its structure, examples, \
   tests, skills, and available tooling.
5. **Reporting** — Generate structured test reports and file GitHub issues \
   for findings.

## Upgrade Testing Workflow (when head_ref is set)

This is the most important workflow. For each example in the module:

1. **Clone the module** with enough depth to switch refs: \
   Use `clone_repo` or `clone_registry_module`.
2. **Run `run_upgrade_test`** for each example. This deterministic tool:
   a. Checks out base_ref (old version)
   b. Runs terraform init + apply to deploy the old version
   c. Checks idempotency at base_ref
   d. Checks out head_ref (new version) — TF_DATA_DIR is externalized \
      so ref switching does not contaminate provider state
   e. Runs terraform init -upgrade + terraform plan (captures the diff)
   f. Runs terraform destroy in a finally block (always cleans up)
3. **Analyse the upgrade diff**: Compare what terraform plan shows \
   against UPGRADE.md. Resource replacements (delete+create) are likely \
   breaking changes. Updates may indicate behavioral shifts.
4. **Report**: Generate a test report with findings and file GitHub \
   issues for undocumented breaking changes.

Use `git_switch_ref` if you need to manually inspect refs, but for \
the actual test lifecycle always use `run_upgrade_test`.

## Simple Deploy Workflow (no head_ref)

For each example:
1. Clone the module at the specified ref.
2. Run terraform init + plan + apply.
3. Check idempotency (terraform plan after apply should be empty).
4. Destroy resources.
5. Report results.

## Module Discovery Workflow
- When given a module to test, first call `ingest_local_module` (for local \
  paths) or `clone_registry_module` (for registry modules) to load it.
- Then call `discover_module_structure` to understand what's available: \
  examples, tests, skill files, UPGRADE.md, AVM CLI, provider requirements.
- Read `.agents/skills/.../example-test.md` (if present) using \
  `read_module_skill` and follow its workflow for deploy testing.
- Use the MUT's `./avm` CLI when available via `run_avm_cli` for \
  pre-commit checks and test runners.

## Knowledge Sources (DO NOT embed — query at runtime)
- AzAPI patterns -> read MUT's `.agents/skills/.../AzAPI.md`
- ARM schemas -> execute MUT's `azure-schema` CLI
- Provider schemas -> execute `tfpluginschema`
- AVM conventions -> read MUT's skill files via `read_module_skill`
- Breaking changes -> read `UPGRADE.md` via `read_upgrade_doc`

## Testing Scope
- Always start by calling `create_workspace` to get an isolated working area.
- Use `ingest_local_module` for local modules, `clone_registry_module` for \
  registry modules, and `clone_repo` for GitHub-hosted modules.
- When a TestRequest is provided, test ALL examples unless the request \
  specifies a subset via `examples` or `skip_examples`.
- In interactive mode without a TestRequest, ask which examples to test \
  or default to all discovered examples.

## Feedback Loop
- After testing, use `generate_test_report` to produce a structured report.
- Use `store_test_run` to persist results in the tracking database.
- Use `search_github_issues` before filing to avoid duplicates.
- Use `generate_issue_body` to format findings, then `create_github_issue` \
  to file bugs discovered during testing.
- Use `generate_upgrade_doc_suggestion` when observed changes don't match \
  UPGRADE.md documentation.

## Output Style
- Be concise and structured in your reports.
- Use tables or bullet lists for change summaries.
- Clearly flag breaking changes, replacements, and idempotency failures.
- If cross-referencing UPGRADE.md, note which documented changes match \
  observed changes and which are missing.

## Safety
- Never deploy to production resource groups.
- Always verify the resource group name starts with the test prefix.
- Do not store secrets in workspace files.
- Confirm destructive operations before proceeding if the user explicitly \
  asked for interactive mode.
- Before deploying any module, check the module allowlist (policy.py). If \
  the module is not trusted, ask the user for explicit approval. In \
  non-interactive (CI) mode, reject untrusted modules.
- When a TestRequest includes a cost estimate threshold, show the estimate \
  and ask to proceed before deploying.
"""

ALL_TOOLS = [
    # Workspace
    create_workspace,
    delete_workspace,
    list_workspace_files,
    read_workspace_file,
    write_workspace_file,
    # Terraform
    terraform_init,
    terraform_init_upgrade,
    terraform_plan,
    terraform_plan_json,
    terraform_apply,
    terraform_destroy,
    terraform_show,
    terraform_output,
    terraform_test,
    check_idempotency,
    run_avm_cli,
    # Azure
    create_resource_group,
    delete_resource_group,
    check_resource_group_exists,
    get_current_identity,
    check_role_assignments,
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
    # Upgrade Test
    run_upgrade_test,
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
        logger.info("Starting in multi-agent mode")
        from agents.orchestrator import create_orchestrator

        pipeline = create_orchestrator(
            mode="foundry" if config.foundry_hosted else "local"
        )
        orchestrator = pipeline["orchestrator"]
        specialists = pipeline["specialists"]
        logger.info(
            "Pipeline ready: orchestrator + %s",
            ", ".join(specialists.keys()),
        )

        from runtime.local import run

        run(orchestrator, initial_message=initial_message)

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
