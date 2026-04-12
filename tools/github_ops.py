"""GitHub operations tools using the gh CLI.

Thin wrappers around the GitHub CLI for filing issues, creating PRs,
and posting test results.  These serve as the local-dev fallback when
the GitHub MCP server is not available (Foundry-hosted mode uses MCP).
"""

from __future__ import annotations

import json
import subprocess


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


from agent_framework import ai_function


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
