"""Azure resource management tools for the Infrastructure Testing Agent."""

from __future__ import annotations

import json
import subprocess

from agent_framework import ai_function

from config import config


def _az(args: list[str], timeout: int = 120) -> dict:
    """Run an az CLI command and return structured output."""
    cmd = ["az"] + args + ["--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        stdout = result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout
        stderr = result.stderr[-4000:] if len(result.stderr) > 4000 else result.stderr
        return {"exit_code": result.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"az command timed out after {timeout}s"}


@ai_function
def create_resource_group(
    name: str,
    location: str = "",
    tags: str = "",
) -> str:
    """Create an Azure resource group for a test deployment.

    Args:
        name: Resource group name (should follow the TEST_RG_PREFIX convention).
        location: Azure region.  Defaults to the agent's DEFAULT_LOCATION.
        tags: Optional JSON object of tags, e.g. '{"purpose":"avm-test"}'.

    Returns:
        JSON with the az CLI result.
    """
    loc = location or config.default_location
    cmd = ["group", "create", "--name", name, "--location", loc]
    if tags:
        try:
            tag_dict = json.loads(tags)
            tag_pairs = [f"{k}={v}" for k, v in tag_dict.items()]
            cmd.extend(["--tags"] + tag_pairs)
        except json.JSONDecodeError:
            return json.dumps({"error": "tags must be valid JSON"})
    return json.dumps(_az(cmd))


@ai_function
def delete_resource_group(name: str) -> str:
    """Delete an Azure resource group (async, no wait).

    Args:
        name: Resource group name to delete.

    Returns:
        JSON with the az CLI result.
    """
    cmd = ["group", "delete", "--name", name, "--yes", "--no-wait"]
    return json.dumps(_az(cmd))


@ai_function
def check_resource_group_exists(name: str) -> str:
    """Check whether an Azure resource group exists.

    Args:
        name: Resource group name.

    Returns:
        JSON with exists (bool) and details if found.
    """
    cmd = ["group", "exists", "--name", name]
    result = _az(cmd, timeout=30)
    exists = result.get("stdout", "").strip().lower() == "true"
    return json.dumps({"exists": exists, "name": name})


@ai_function
def get_current_identity() -> str:
    """Return the identity the agent is currently authenticated as.

    Useful for verifying which managed identity or user the agent is
    running under and what permissions it may have.

    Returns:
        JSON with account details (subscription, tenant, user).
    """
    result = _az(["account", "show"], timeout=30)
    return json.dumps(result)


@ai_function
def check_role_assignments(
    scope: str,
    assignee: str = "",
) -> str:
    """List RBAC role assignments at a given scope.

    Args:
        scope: Azure resource id scope to check.
        assignee: Optional principal id to filter by.

    Returns:
        JSON with the role assignments.
    """
    cmd = ["role", "assignment", "list", "--scope", scope]
    if assignee:
        cmd.extend(["--assignee", assignee])
    return json.dumps(_az(cmd, timeout=30))
