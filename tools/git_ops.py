"""Git operations tools for the Infrastructure Testing Agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_framework import tool

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


@tool
def git_switch_ref(
    workspace_id: str,
    ref: str,
    target_dir: str = "module",
    fetch_depth: int = 50,
) -> str:
    """Switch an already-cloned repository to a different branch, tag, or commit.

    Unlike a plain ``git checkout``, this tool fetches the target ref first
    so it works even when the initial clone was shallow or single-branch.
    The checkout is always detached (deterministic, no local branch state).

    Use this to switch between base_ref and head_ref during upgrade testing.

    Args:
        workspace_id: Workspace id from create_workspace.
        ref: Branch name, tag, or commit SHA to switch to.
        target_dir: Directory within the workspace containing the clone.
        fetch_depth: How many commits to fetch (default 50, use 0 for full).

    Returns:
        JSON with fetch and checkout results plus the resolved commit SHA.
    """
    ws_path = _workspace_path(workspace_id) / target_dir

    if not (ws_path / ".git").exists():
        return json.dumps({"error": f"No git repo found at {target_dir}"})

    # Fetch the ref (handles shallow clones that don't have other branches)
    fetch_cmd = ["fetch", "origin", ref]
    if fetch_depth > 0:
        fetch_cmd.extend(["--depth", str(fetch_depth)])
    fetch_result = _git(fetch_cmd, ws_path)

    if fetch_result["exit_code"] != 0:
        # Try fetching tags in case ref is a tag like v0.5.0
        tag_fetch = _git(["fetch", "origin", "--tags"], ws_path)
        if tag_fetch["exit_code"] != 0:
            return json.dumps({
                "error": f"Could not fetch ref '{ref}'",
                "fetch_result": fetch_result,
                "tag_fetch_result": tag_fetch,
            })

    # Clean working tree before switching (preserve external state files)
    _git(["clean", "-fd", "--exclude=*.tfstate", "--exclude=*.tfstate.backup"], ws_path)
    _git(["checkout", "--", "."], ws_path)

    # Checkout detached to the fetched ref
    # Try FETCH_HEAD first (from the direct fetch), then the ref name
    checkout_result = _git(["checkout", "--detach", "FETCH_HEAD"], ws_path)
    if checkout_result["exit_code"] != 0:
        checkout_result = _git(["checkout", "--detach", ref], ws_path)

    if checkout_result["exit_code"] != 0:
        # Last resort: try origin/ref
        checkout_result = _git(["checkout", "--detach", f"origin/{ref}"], ws_path)

    if checkout_result["exit_code"] != 0:
        return json.dumps({
            "error": f"Could not checkout ref '{ref}'",
            "checkout_result": checkout_result,
        })

    # Get the resolved commit SHA
    rev_result = _git(["rev-parse", "HEAD"], ws_path)
    commit_sha = rev_result["stdout"].strip() if rev_result["exit_code"] == 0 else "unknown"

    return json.dumps({
        "status": "switched",
        "ref": ref,
        "commit_sha": commit_sha,
        "detached": True,
    })


@tool
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


@tool
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


@tool
def add_remote(
    workspace_id: str,
    remote_url: str,
    remote_name: str = "upstream",
    target_dir: str = "module",
    fetch_depth: int = 50,
) -> str:
    """Add a second remote to an already-cloned repository and fetch it.

    Useful for cross-repo upgrade testing where the base and head versions
    live in different repositories.  After adding the remote, branches/tags
    from it are available for ``git_switch_ref``.

    Args:
        workspace_id: Workspace id from create_workspace.
        remote_url: HTTPS URL of the remote to add.
        remote_name: Name for the new remote (default "upstream").
        target_dir: Directory within the workspace containing the clone.
        fetch_depth: How many commits to fetch (default 50, use 0 for full).

    Returns:
        JSON with the result of add + fetch.
    """
    if not remote_url.startswith("https://"):
        return json.dumps({"error": "Only HTTPS remote URLs are accepted"})

    ws_path = _workspace_path(workspace_id) / target_dir
    if not (ws_path / ".git").exists():
        return json.dumps({"error": f"No git repo found at {target_dir}"})

    # Remove existing remote with same name if present (ignore errors)
    _git(["remote", "remove", remote_name], ws_path)

    add_result = _git(["remote", "add", remote_name, remote_url], ws_path)
    if add_result["exit_code"] != 0:
        return json.dumps({
            "error": f"Failed to add remote: {add_result['stderr']}",
            "result": add_result,
        })

    fetch_cmd = ["fetch", remote_name]
    if fetch_depth > 0:
        fetch_cmd.extend(["--depth", str(fetch_depth)])
    fetch_result = _git(fetch_cmd, ws_path)
    if fetch_result["exit_code"] != 0:
        return json.dumps({
            "error": f"Added remote but fetch failed: {fetch_result['stderr']}",
            "remote_name": remote_name,
            "fetch_result": fetch_result,
        })

    return json.dumps({
        "status": "ready",
        "remote_name": remote_name,
        "remote_url": remote_url,
    })
