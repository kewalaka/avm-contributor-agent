"""Infrastructure Testing Agent — entry point.

A hosted agent built with the Microsoft Agent Framework that tests
Terraform modules by deploying, planning, and analysing diffs.
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
    create_workspace,
    delete_workspace,
    list_workspace_files,
    read_workspace_file,
    terraform_apply,
    terraform_destroy,
    terraform_init,
    terraform_output,
    terraform_plan,
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

## Workflow Conventions
- Always start by calling `create_workspace` to get an isolated working area.
- Use `clone_registry_module` for official Terraform Registry modules and \
  `clone_repo` for forks or GitHub-hosted versions.
- Use the `default` example from a module unless the user specifies otherwise.
- For module upgrades: deploy the old version first with terraform apply, \
  then replace the module source with the new version and run terraform plan \
  to capture the diff.
- Always run a second `terraform plan` after `terraform apply` to check \
  idempotency. Report any non-empty plan as a potential issue.
- Default behaviour is to destroy resources after testing \
  (CLEANUP_ON_COMPLETE=true). If the user asks to keep resources, skip destroy.
- When creating resource groups, use the naming convention: \
  {TEST_RG_PREFIX}{module-short-name}-{random-suffix}
- Tag all test resource groups with: purpose=infra-testing-agent, \
  managed-by=foundry-agent

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
    terraform_plan,
    terraform_apply,
    terraform_destroy,
    terraform_show,
    terraform_output,
    terraform_test,
    # Azure
    create_resource_group,
    delete_resource_group,
    check_resource_group_exists,
    get_current_identity,
    check_role_assignments,
    # Git
    clone_repo,
    clone_registry_module,
    # Analysis
    read_upgrade_doc,
    summarise_plan_json,
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
