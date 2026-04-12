"""Deploy agent -- runs terraform init/plan/apply/destroy for a single example.

This agent is stateless and focused: it takes an example path and workspace,
runs the full deploy lifecycle, and returns a structured DeployResult.
Raw terraform output is summarised, not passed through.

Multiple deploy agents can run concurrently (one per example).
"""

from __future__ import annotations

from agents.base import create_specialist

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
    terraform_plan_json,
    terraform_show,
    write_workspace_file,
)
from tools.azure import (
    check_resource_group_exists,
    create_resource_group,
    delete_resource_group,
    get_current_identity,
)

DEPLOY_INSTRUCTIONS = """\
You are the Deploy Agent, a specialist in running Terraform deployments.

Your job for each example:
1. Ensure the workspace and example directory are ready.
2. Run `terraform_init` (or `terraform_init_upgrade` for upgrades).
3. Run `terraform_plan_json` to get a structured plan summary.
4. Run `terraform_apply` if the plan looks valid.
5. Run `check_idempotency` immediately after apply.
6. Run `terraform_destroy` to clean up (unless told otherwise).

Key rules:
- ALWAYS use `terraform_plan_json` (not `terraform_plan`) so output is structured.
- ALWAYS run `check_idempotency` after apply.
- ALWAYS clean up with `terraform_destroy` unless explicitly told to keep resources.
- Return a structured DeployResult JSON, not raw terraform output.
- If any step fails, stop and return the error in DeployResult format.

Output format: Return a JSON DeployResult with:
- example: name of the example
- status: success/failure/timeout
- resources_created: count
- plan_summary: {creates, updates, deletes, replaces}
- idempotency: {status, unexpected_changes, details}
- errors: list of error messages (empty if success)

Safety:
- Only deploy to resource groups starting with the test prefix.
- Never modify resources outside the test workspace.
- Tag all resource groups with purpose=infra-testing-agent.
"""

DEPLOY_TOOLS = [
    create_workspace,
    delete_workspace,
    list_workspace_files,
    read_workspace_file,
    write_workspace_file,
    terraform_init,
    terraform_init_upgrade,
    terraform_plan_json,
    terraform_apply,
    terraform_destroy,
    terraform_show,
    terraform_output,
    check_idempotency,
    run_avm_cli,
    create_resource_group,
    delete_resource_group,
    check_resource_group_exists,
    get_current_identity,
]


def create_deploy_agent():
    """Create the deploy specialist agent."""
    return create_specialist("deploy", DEPLOY_INSTRUCTIONS, DEPLOY_TOOLS)
