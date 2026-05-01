"""Deploy agent -- runs terraform deployments and upgrade tests for examples.

This agent handles two modes:
  1. **Simple deploy** (no head_ref): init → plan → apply → idempotency → destroy
  2. **Upgrade test** (head_ref set): deploy base_ref → switch to head_ref →
     plan the upgrade diff → destroy.  Uses the deterministic `run_upgrade_test`
     tool which handles the full lifecycle including cleanup in a finally block.

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
from tools.git_ops import git_switch_ref
from tools.upgrade_test import run_upgrade_test

DEPLOY_INSTRUCTIONS = """\
You are the Deploy Agent, a specialist in running Terraform deployments \
and upgrade tests.

You handle TWO modes depending on whether head_ref is set:

## Mode 1: Simple Deploy (no head_ref)

For each example:
1. Ensure the workspace and example directory are ready.
2. Run `terraform_init` (or `terraform_init_upgrade` for provider updates).
3. Run `terraform_plan_json` to get a structured plan summary.
4. Run `terraform_apply` if the plan looks valid.
5. Run `check_idempotency` immediately after apply.
6. Run `terraform_destroy` to clean up (unless told otherwise).
7. Return a structured DeployResult JSON.

## Mode 2: Upgrade Test (head_ref is set)

This is the core workflow. Use `run_upgrade_test` which deterministically:
1. Checks out base_ref (old version)
2. Runs terraform init + apply to deploy the old version
3. Checks idempotency at base_ref
4. Checks out head_ref (new version)
5. Runs terraform init -upgrade + terraform plan (does NOT apply)
6. Captures the upgrade diff (what changes when upgrading)
7. Runs terraform destroy in a finally block (always cleans up)

The upgrade plan diff is the KEY output -- it shows what would change \
when upgrading from base_ref to head_ref. Resource replacements \
(delete+create) are likely breaking changes. The analysis agent will \
cross-reference this with UPGRADE.md.

For upgrade tests, ALWAYS prefer `run_upgrade_test` over manually \
orchestrating the steps. It handles TF_DATA_DIR isolation (so ref \
switching doesn't contaminate state) and cleanup in a finally block \
(so infrastructure isn't leaked on failure).

If you need to switch refs manually for investigation, use \
`git_switch_ref` -- but for the actual test, use `run_upgrade_test`.

Key rules:
- ALWAYS use `terraform_plan_json` (not `terraform_plan`) so output is structured.
- ALWAYS run `check_idempotency` after apply (in simple deploy mode).
- ALWAYS clean up with `terraform_destroy` unless explicitly told to keep resources.
- Return a structured DeployResult JSON, not raw terraform output.
- If any step fails, stop and return the error in DeployResult format.
- For upgrade tests, include the UpgradeTestResult in the DeployResult's \
  `upgrade` field.

Output format for simple deploy: Return a JSON DeployResult with:
- example: name of the example
- status: success/failure/timeout
- resources_created: count
- plan_summary: {creates, updates, deletes, replaces}
- idempotency: {status, unexpected_changes, details}
- errors: list of error messages (empty if success)

Output format for upgrade test: Return a JSON DeployResult with:
- example: name of the example
- status: success/failure (from the upgrade test)
- upgrade: the full UpgradeTestResult including:
  - upgrade_plan_summary: {creates, updates, deletes, replaces}
  - upgrade_resource_changes: list of changed resources with actions
  - upgrade_confidence: high/medium/low
  - base_deploy: whether base version deployed successfully
  - base_idempotency: whether base version was idempotent
  - phases_completed: which steps succeeded
  - timing: duration of each phase

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
    git_switch_ref,
    run_upgrade_test,
]


def create_deploy_agent():
    """Create the deploy specialist agent."""
    return create_specialist("deploy", DEPLOY_INSTRUCTIONS, DEPLOY_TOOLS)
