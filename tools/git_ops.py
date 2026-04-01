"""Git operations tools for the Infrastructure Testing Agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_framework import ai_function

from tools.terraform import _workspace_path


def _git(args: list[str], cwd: str | Path, timeout: int = 120) -> dict:
    """Run a git command and return structured output."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        stdout = result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout
        stderr = result.stderr[-4000:] if len(result.stderr) > 4000 else result.stderr
        return {"exit_code": result.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"git command timed out after {timeout}s"}


@ai_function
def clone_repo(
    workspace_id: str,
    repo_url: str,
    target_dir: str = "module",
    ref: str = "",
    depth: int = 1,
) -> str:
    """Clone a git repository into a workspace.

    Args:
        workspace_id: Workspace id from create_workspace.
        repo_url: HTTPS URL of the git repository.
        target_dir: Directory name within the workspace to clone into.
        ref: Optional branch, tag or commit to checkout after cloning.
        depth: Clone depth (default 1 for shallow clone).

    Returns:
        JSON with clone result.
    """
    # Basic URL validation - only allow https
    if not repo_url.startswith("https://"):
        return json.dumps({"error": "Only HTTPS repository URLs are accepted"})

    ws_path = _workspace_path(workspace_id)
    clone_target = ws_path / target_dir
    cmd = ["clone", "--depth", str(depth), repo_url, str(clone_target)]
    result = _git(cmd, ws_path)

    if result["exit_code"] == 0 and ref:
        checkout_result = _git(["checkout", ref], clone_target)
        result["checkout"] = checkout_result

    return json.dumps(result)


@ai_function
def clone_registry_module(
    workspace_id: str,
    module_source: str,
    version: str,
    target_dir: str = "module",
) -> str:
    """Download a Terraform module from the public registry by cloning its GitHub source.

    Converts a registry source like ``Azure/avm-res-network-applicationgateway/azurerm``
    to the corresponding GitHub repository URL and checks out the given version tag.

    Args:
        workspace_id: Workspace id from create_workspace.
        module_source: Terraform registry source, e.g. ``Azure/avm-res-network-applicationgateway/azurerm``.
        version: Version tag to checkout (e.g. ``0.5.0``).  The tool tries both ``v{version}`` and ``{version}`` tags.
        target_dir: Directory name within the workspace to clone into.

    Returns:
        JSON with clone result.
    """
    parts = module_source.split("/")
    if len(parts) != 3:
        return json.dumps({"error": f"Expected registry format 'namespace/name/provider', got: {module_source}"})

    namespace, name, provider = parts
    repo_url = f"https://github.com/{namespace}/terraform-{provider}-{name}.git"

    ws_path = _workspace_path(workspace_id)
    clone_target = ws_path / target_dir
    result = _git(["clone", "--depth", "50", repo_url, str(clone_target)], ws_path)

    if result["exit_code"] != 0:
        return json.dumps(result)

    # Try version tag formats
    for tag in [f"v{version}", version]:
        checkout = _git(["checkout", tag], clone_target)
        if checkout["exit_code"] == 0:
            result["checked_out_tag"] = tag
            return json.dumps(result)

    result["warning"] = f"Could not checkout version {version}, staying on default branch"
    return json.dumps(result)
