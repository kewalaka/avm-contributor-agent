"""Fork lifecycle management tools using the gh CLI.

Handles forking upstream AVM repositories, syncing forks with upstream,
and cloning forks into isolated workspace directories.
"""

from __future__ import annotations

import json
import os

from agent_framework import ai_function

from tools.github_ops import _gh


@ai_function
def ensure_fork(upstream_repo: str, fork_owner: str = "") -> str:
    """Ensure a fork of an upstream repository exists under fork_owner.

    If fork_owner is empty, it is inferred from the authenticated gh user.
    The fork is created if it does not already exist.

    Args:
        upstream_repo: Upstream repository in owner/repo format (e.g. 'Azure/terraform-azurerm-avm-res-network-vnet').
        fork_owner: GitHub login to fork into. If empty, uses the authenticated user.

    Returns:
        JSON with status ('exists' or 'created'), fork_repo, upstream_repo, and fork_owner.
    """
    if not fork_owner:
        result = _gh(["api", "user", "--jq", ".login"])
        if result["exit_code"] != 0:
            return json.dumps({"status": "error", "details": result})
        fork_owner = result["stdout"].strip()
        if not fork_owner:
            return json.dumps({"status": "error", "details": "Could not determine authenticated GitHub user"})

    repo_name = upstream_repo.split("/")[-1]
    fork_repo = f"{fork_owner}/{repo_name}"

    check = _gh(["repo", "view", fork_repo, "--json", "name"])
    if check["exit_code"] == 0:
        return json.dumps({
            "status": "exists",
            "fork_repo": fork_repo,
            "upstream_repo": upstream_repo,
            "fork_owner": fork_owner,
        })

    create = _gh(["repo", "fork", upstream_repo, "--clone=false", "--default-branch-only"])
    if create["exit_code"] != 0:
        return json.dumps({"status": "error", "details": create})

    return json.dumps({
        "status": "created",
        "fork_repo": fork_repo,
        "upstream_repo": upstream_repo,
        "fork_owner": fork_owner,
    })


@ai_function
def sync_fork_default_branch(fork_repo: str, upstream_repo: str, branch: str = "main") -> str:
    """Fast-forward sync a fork's branch from upstream.

    Will never force-push or reset. If the fork has diverged, returns an error
    requiring manual resolution.

    Args:
        fork_repo: Fork repository in owner/repo format.
        upstream_repo: Upstream repository in owner/repo format.
        branch: Branch to sync (default 'main').

    Returns:
        JSON with status 'synced' on success, or error with reason 'fork_diverged' if the
        fork has diverged from upstream.
    """
    result = _gh(["repo", "sync", fork_repo, "--source", upstream_repo, "--branch", branch])
    if result["exit_code"] == 0:
        return json.dumps({"status": "synced", "fork_repo": fork_repo, "branch": branch})

    if "diverged" in result["stderr"].lower():
        return json.dumps({
            "status": "error",
            "reason": "fork_diverged",
            "message": "Fork has diverged from upstream; manual resolution required",
        })

    return json.dumps({"status": "error", "details": result})


@ai_function
def clone_fork(fork_repo: str, run_id: str, branch: str = "main") -> str:
    """Clone a fork into the isolated workspace directory ~/.tfdev/ws/<run_id>/<repo_name>/.

    Also configures an 'upstream' remote pointing to the original upstream repository.

    Args:
        fork_repo: Fork repository in owner/repo format.
        run_id: Unique run identifier used to isolate the workspace directory.
        branch: Branch to clone (default 'main').

    Returns:
        JSON with status 'cloned', clone_path, fork_repo, and run_id.
    """
    import subprocess

    repo_name = fork_repo.split("/")[-1]
    ws_dir = os.path.expanduser(f"~/.tfdev/ws/{run_id}")
    clone_path = os.path.join(ws_dir, repo_name)

    os.makedirs(ws_dir, exist_ok=True)

    clone_result = _gh(
        ["repo", "clone", fork_repo, clone_path, "--", "--branch", branch, "--single-branch"],
        timeout=120,
    )
    if clone_result["exit_code"] != 0:
        return json.dumps({"status": "error", "details": clone_result})

    # Resolve upstream from fork metadata
    meta = _gh(["repo", "view", fork_repo, "--json", "parent", "--jq", ".parent.nameWithOwner"])
    if meta["exit_code"] != 0 or not meta["stdout"].strip():
        return json.dumps({
            "status": "error",
            "details": "Cloned successfully but could not resolve upstream repository",
            "clone_path": clone_path,
        })

    upstream_nwo = meta["stdout"].strip()
    upstream_url = f"https://github.com/{upstream_nwo}.git"

    try:
        subprocess.run(
            ["git", "remote", "add", "upstream", upstream_url],
            cwd=clone_path,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return json.dumps({
            "status": "error",
            "details": f"Cloned but failed to add upstream remote: {exc.stderr}",
            "clone_path": clone_path,
        })

    return json.dumps({
        "status": "cloned",
        "clone_path": os.path.abspath(clone_path),
        "fork_repo": fork_repo,
        "run_id": run_id,
    })


@ai_function
def get_fork_info(fork_repo: str) -> str:
    """Get metadata about a forked repository.

    Args:
        fork_repo: Fork repository in owner/repo format.

    Returns:
        JSON with name, owner, parent, defaultBranchRef, and updatedAt fields.
    """
    result = _gh(["repo", "view", fork_repo, "--json", "name,owner,parent,defaultBranchRef,updatedAt"])
    if result["exit_code"] != 0:
        return json.dumps({"status": "error", "details": result})
    try:
        return json.dumps(json.loads(result["stdout"]))
    except json.JSONDecodeError:
        return json.dumps({"status": "error", "details": "Failed to parse gh output"})
