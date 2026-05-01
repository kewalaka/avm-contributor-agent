"""GitHub operations tools using the gh CLI.

Thin wrappers around the GitHub CLI for filing issues, creating PRs,
and posting test results.  These serve as the local-dev fallback when
the GitHub MCP server is not available (Foundry-hosted mode uses MCP).
"""

from __future__ import annotations

import json
import subprocess

from agent_framework import tool as ai_function


def _gh(args: list[str], timeout: int = 60) -> dict:
    """Run a gh CLI command and return structured output."""
    cmd = ["gh"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        stdout = result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout
        stderr = result.stderr[-4000:] if len(result.stderr) > 4000 else result.stderr
        return {"exit_code": result.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"gh command timed out after {timeout}s"}
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": "gh CLI not found — install from https://cli.github.com/"}


@ai_function
def create_github_issue(
    repo: str,
    title: str,
    body: str,
    labels: str = "",
) -> str:
    """Create a GitHub issue to report a finding from module testing.

    Args:
        repo: Repository in owner/repo format (e.g. 'Azure/terraform-azurerm-avm-res-network-applicationgateway').
        title: Issue title.
        body: Issue body in markdown format.
        labels: Comma-separated labels (e.g. 'bug,avm-testing').

    Returns:
        JSON with the created issue URL or error.
    """
    cmd = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    if labels:
        cmd.extend(["--label", labels])
    result = _gh(cmd)
    if result["exit_code"] == 0:
        issue_url = result["stdout"].strip()
        return json.dumps({"status": "created", "url": issue_url})
    return json.dumps({"status": "error", "details": result})


@ai_function
def create_pull_request(
    repo: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
    draft: bool = True,
) -> str:
    """Create a pull request to propose changes to a module.

    Args:
        repo: Repository in owner/repo format.
        branch: Head branch with the changes.
        title: PR title.
        body: PR description in markdown.
        base: Base branch to merge into (default 'main').
        draft: Whether to create as draft PR (default True).

    Returns:
        JSON with the created PR URL or error.
    """
    cmd = [
        "pr", "create",
        "--repo", repo,
        "--head", branch,
        "--base", base,
        "--title", title,
        "--body", body,
    ]
    if draft:
        cmd.append("--draft")
    result = _gh(cmd)
    if result["exit_code"] == 0:
        pr_url = result["stdout"].strip()
        return json.dumps({"status": "created", "url": pr_url})
    return json.dumps({"status": "error", "details": result})


@ai_function
def add_issue_comment(
    repo: str,
    issue_number: int,
    comment: str,
) -> str:
    """Add a comment to an existing GitHub issue or PR.

    Use this to post test results back to a tracking issue.

    Args:
        repo: Repository in owner/repo format.
        issue_number: Issue or PR number.
        comment: Comment body in markdown.

    Returns:
        JSON with the comment URL or error.
    """
    cmd = [
        "issue", "comment",
        "--repo", repo,
        str(issue_number),
        "--body", comment,
    ]
    result = _gh(cmd)
    if result["exit_code"] == 0:
        return json.dumps({"status": "commented", "issue": issue_number})
    return json.dumps({"status": "error", "details": result})


@ai_function
def search_github_issues(
    repo: str,
    query: str,
    limit: int = 10,
) -> str:
    """Search GitHub issues in a repository.

    Use this to find existing issues before filing duplicates.

    Args:
        repo: Repository in owner/repo format.
        query: Search query string.
        limit: Maximum results to return.

    Returns:
        JSON array of matching issues with number, title, state, and URL.
    """
    cmd = [
        "issue", "list",
        "--repo", repo,
        "--search", query,
        "--limit", str(limit),
        "--json", "number,title,state,url,labels",
    ]
    result = _gh(cmd)
    if result["exit_code"] == 0:
        try:
            issues = json.loads(result["stdout"])
            return json.dumps(issues, indent=2)
        except json.JSONDecodeError:
            return json.dumps({"status": "error", "details": "Failed to parse gh output"})
    return json.dumps({"status": "error", "details": result})


@ai_function
def get_latest_release(
    repo: str,
) -> str:
    """Get the latest release version for a repository.

    Useful for determining the current published version of a module.

    Args:
        repo: Repository in owner/repo format.

    Returns:
        JSON with tag name, title, and published date.
    """
    cmd = [
        "release", "view",
        "--repo", repo,
        "--json", "tagName,name,publishedAt",
    ]
    result = _gh(cmd)
    if result["exit_code"] == 0:
        try:
            release = json.loads(result["stdout"])
            return json.dumps(release)
        except json.JSONDecodeError:
            return json.dumps({"status": "error", "details": "Failed to parse release info"})
    return json.dumps({"status": "error", "details": result})


@ai_function
def download_workflow_artifacts(
    repo: str,
    run_id: str = "",
    artifact_name: str = "test-report",
    output_dir: str = "./artifacts",
) -> str:
    """Download artifacts from a GitHub Actions workflow run.

    This bridges GHA deploy results with the agent analysis pipeline.
    The GHA workflow uploads per-example deploy results and plan JSONs
    as artifacts. This tool retrieves them for the agent to analyse.

    Args:
        repo: Repository in owner/repo format.
        run_id: Workflow run ID. If empty, uses the latest completed run.
        artifact_name: Name of the artifact to download (default 'test-report').
        output_dir: Local directory to save artifacts to.

    Returns:
        JSON with download status and list of downloaded files.
    """
    import os

    os.makedirs(output_dir, exist_ok=True)

    if run_id:
        cmd = [
            "run", "download",
            "--repo", repo,
            run_id,
            "--name", artifact_name,
            "--dir", output_dir,
        ]
    else:
        # Find latest completed run of the Module Test workflow
        list_cmd = [
            "run", "list",
            "--repo", repo,
            "--workflow", "run-test.yml",
            "--status", "completed",
            "--limit", "1",
            "--json", "databaseId",
        ]
        list_result = _gh(list_cmd)
        if list_result["exit_code"] != 0:
            return json.dumps({"status": "error", "details": "Could not find workflow runs", "gh": list_result})
        try:
            runs = json.loads(list_result["stdout"])
            if not runs:
                return json.dumps({"status": "error", "details": "No completed workflow runs found"})
            run_id = str(runs[0]["databaseId"])
        except (json.JSONDecodeError, KeyError, IndexError):
            return json.dumps({"status": "error", "details": "Could not parse workflow runs"})

        cmd = [
            "run", "download",
            "--repo", repo,
            run_id,
            "--name", artifact_name,
            "--dir", output_dir,
        ]

    result = _gh(cmd, timeout=120)
    if result["exit_code"] != 0:
        return json.dumps({"status": "error", "details": result})

    # List downloaded files
    downloaded = []
    for root, _, files in os.walk(output_dir):
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), output_dir)
            downloaded.append(rel_path)

    return json.dumps({
        "status": "downloaded",
        "run_id": run_id,
        "artifact": artifact_name,
        "files": downloaded,
        "output_dir": output_dir,
    })


@ai_function
def get_workflow_run_status(
    repo: str,
    run_id: str = "",
    workflow: str = "run-test.yml",
) -> str:
    """Check the status of a GitHub Actions workflow run.

    Args:
        repo: Repository in owner/repo format.
        run_id: Specific run ID to check. If empty, shows recent runs.
        workflow: Workflow filename to filter by.

    Returns:
        JSON with run status, conclusion, and job details.
    """
    if run_id:
        cmd = [
            "run", "view",
            "--repo", repo,
            run_id,
            "--json", "databaseId,status,conclusion,workflowName,createdAt,updatedAt,jobs",
        ]
    else:
        cmd = [
            "run", "list",
            "--repo", repo,
            "--workflow", workflow,
            "--limit", "5",
            "--json", "databaseId,status,conclusion,workflowName,createdAt,headBranch",
        ]

    result = _gh(cmd)
    if result["exit_code"] == 0:
        try:
            return json.dumps(json.loads(result["stdout"]))
        except json.JSONDecodeError:
            return json.dumps({"status": "error", "details": "Failed to parse run info"})
    return json.dumps({"status": "error", "details": result})
