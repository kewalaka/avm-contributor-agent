"""Infrastructure Testing Agent — entry point.

A hosted agent built with the Microsoft Agent Framework that tests
Terraform modules by deploying, planning, and analysing diffs.
Supports local development and Foundry-hosted modes.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=True)

from agent_framework import ChatAgent
from agent_framework.azure import AzureAIAgentClient
from azure.ai.agentserver.agentframework import from_agent_framework
from azure.identity import DefaultAzureCredential

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
    clone_registry_module,
    clone_repo,
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
    get_latest_release,
    search_github_issues,
)
from tools.reporting import (
    generate_issue_body,
    generate_test_report,
    generate_upgrade_doc_suggestion,
)

SYSTEM_INSTRUCTIONS = """\
You are an Infrastructure Testing Agent with expertise in Terraform, \
Azure Verified Modules (AVM), and Azure infrastructure.

Your primary capabilities:
1. **Module Upgrade Testing** — Deploy an existing module version, then plan \
   the upgrade to a new version, producing a structured diff report. \
   Cross-reference any UPGRADE.md documentation against actual changes.
2. **Idempotency Checking** — After applying a configuration, run a second \
   plan to verify no unexpected changes are detected.
3. **Resource Lifecycle** — Create test resource groups, deploy, and clean up.
4. **Module Discovery** — Scan a module to understand its structure, examples, \
   tests, skills, and available tooling.
5. **Reporting** — Generate structured test reports and file GitHub issues \
   for findings.

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
- AzAPI patterns → read MUT's `.agents/skills/.../AzAPI.md`
- ARM schemas → execute MUT's `azure-schema` CLI
- Provider schemas → execute `tfpluginschema`
- AVM conventions → read MUT's skill files via `read_module_skill`
- Breaking changes → read `UPGRADE.md` via `read_upgrade_doc`

## Testing Workflow
- Always start by calling `create_workspace` to get an isolated working area.
- Use `ingest_local_module` for local modules, `clone_registry_module` for \
  registry modules, and `clone_repo` for GitHub-hosted modules.
- Use the `default` example from a module unless the user specifies otherwise.
- For module upgrades: deploy the old version first with terraform apply, \
  then update the source and run `terraform_plan_json` to capture a \
  structured diff.
- Always call `check_idempotency` after `terraform_apply` to verify no \
  unexpected changes.
- Use `terraform_init_upgrade` when testing module version upgrades.
- Default behaviour is to destroy resources after testing \
  (CLEANUP_ON_COMPLETE=true). If the user asks to keep resources, skip destroy.
- When creating resource groups, use the naming convention: \
  {TEST_RG_PREFIX}{module-short-name}-{random-suffix}
- Tag all test resource groups with: purpose=infra-testing-agent, \
  managed-by=foundry-agent

## Feedback Loop
- After testing, use `generate_test_report` to produce a structured report.
- Use `search_github_issues` before filing to avoid duplicates.
- Use `generate_issue_body` to format findings, then `create_github_issue` \
  to file bugs discovered during testing.
- Use `generate_upgrade_doc_suggestion` when observed changes don't match \
  UPGRADE.md documentation.
- Use `add_issue_comment` to post results on existing tracking issues.

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
    # Reporting
    generate_test_report,
    generate_issue_body,
    generate_upgrade_doc_suggestion,
]

agent = ChatAgent(
    chat_client=AzureAIAgentClient(
        project_endpoint=config.project_endpoint,
        model_deployment_name=config.model_deployment_name,
        credential=DefaultAzureCredential(),
    ),
    instructions=SYSTEM_INSTRUCTIONS,
    tools=ALL_TOOLS,
)

if __name__ == "__main__":
    from_agent_framework(agent).run()
