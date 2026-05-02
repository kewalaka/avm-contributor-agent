"""Git operations tools for the Infrastructure Testing Agent."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from agent_framework import ai_function

_WORK_ROOT = Path(tempfile.gettempdir()) / "infra-agent-work"
_AGENT_WS_ROOT = Path.home() / ".tfdev" / "ws"
_STATE_ROOT = Path.home() / ".tfdev" / "state"


def _workspace_path(workspace_id: str) -> Path:
    """Return the on-disk path for a given workspace id."""
    safe_id = workspace_id.replace("/", "_").replace("..", "")
    return _WORK_ROOT / safe_id


def _run_git(args: list[str], cwd: Path, timeout: int = 60) -> dict:
    """Run a git command and return structured output."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        stdout = result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout
        stderr = result.stderr[-4000:] if len(result.stderr) > 4000 else result.stderr
        return {"exit_code": result.returncode, "stdout": stdout.strip(), "stderr": stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"git command timed out after {timeout}s"}


def _parse_remote_owner(url: str) -> str | None:
    """Extract the owner from a GitHub remote URL (https or ssh)."""
    https_match = re.match(r"https?://github\.com/([^/]+)/", url)
    if https_match:
        return https_match.group(1)
    ssh_match = re.match(r"git@github\.com:([^/]+)/", url)
    if ssh_match:
        return ssh_match.group(1)
    return None


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


@ai_function
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


_BRANCH_RE = re.compile(r"^agent/(issue-\d+|manual)-[a-z0-9-]+$")


@ai_function
def create_branch(repo_path: str, branch_name: str, base_branch: str = "main") -> str:
    """Create a new git branch from a base branch.

    Args:
        repo_path: Absolute path to the git repository.
        branch_name: Name for the new branch (must match agent naming convention).
        base_branch: Branch to create from (default "main").

    Returns:
        JSON with status, branch name, and base branch, or an error.
    """
    if not _BRANCH_RE.match(branch_name):
        return json.dumps({
            "error": f"Invalid branch name '{branch_name}'. Must match ^agent/(issue-\\d+|manual)-[a-z0-9-]+$"
        })

    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return json.dumps({"error": f"No git repository found at {repo_path}"})

    fetch_result = _run_git(["fetch", "origin", base_branch], repo)
    if fetch_result["exit_code"] != 0:
        return json.dumps({"error": f"Failed to fetch base branch '{base_branch}': {fetch_result['stderr']}"})

    result = _run_git(["checkout", "-b", branch_name, f"origin/{base_branch}"], repo)
    if result["exit_code"] != 0:
        return json.dumps({"error": f"Failed to create branch: {result['stderr']}"})

    return json.dumps({"status": "created", "branch": branch_name, "base": base_branch})


@ai_function
def commit_files(
    repo_path: str,
    message: str,
    run_id: str,
    files: list[str] | None = None,
) -> str:
    """Stage files and create a commit with standard trailers.

    Args:
        repo_path: Absolute path to the git repository.
        message: Commit message body.
        run_id: Agent run identifier, appended as an ``Agent-Run-Id`` trailer.
        files: Specific files to stage. Stages all changes when None or empty.

    Returns:
        JSON with status, commit SHA, and run_id, or an error.
    """
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return json.dumps({"error": f"No git repository found at {repo_path}"})

    if files:
        for f in files:
            add_result = _run_git(["add", f], repo)
            if add_result["exit_code"] != 0:
                return json.dumps({"error": f"Failed to stage '{f}': {add_result['stderr']}"})
    else:
        add_result = _run_git(["add", "-A"], repo)
        if add_result["exit_code"] != 0:
            return json.dumps({"error": f"Failed to stage changes: {add_result['stderr']}"})

    full_message = (
        f"{message}\n\n"
        f"Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>\n"
        f"Agent-Run-Id: {run_id}"
    )
    commit_result = _run_git(["commit", "-m", full_message], repo)
    if commit_result["exit_code"] != 0:
        return json.dumps({"error": f"Commit failed: {commit_result['stderr']}"})

    sha_result = _run_git(["rev-parse", "HEAD"], repo)
    sha = sha_result["stdout"] if sha_result["exit_code"] == 0 else "unknown"

    return json.dumps({"status": "committed", "sha": sha, "run_id": run_id})


@ai_function
def push_branch(
    repo_path: str,
    branch_name: str,
    fork_owner: str,
    run_id: str,
    remote: str = "origin",
) -> str:
    """Push a branch to a remote with mandatory safety guardrails.

    Guardrails checked before pushing:
    1. Branch name must match the agent naming convention.
    2. Remote URL owner must equal fork_owner and must not start with ``Azure/``.
    3. No force-push (plain push only).
    4. First-write provenance: records and validates the originating SHA.
    5. Workspace isolation: repo_path must be inside ~/.tfdev or the temp work root.

    Args:
        repo_path: Absolute path to the git repository.
        branch_name: Branch to push.
        fork_owner: Expected GitHub owner of the remote (e.g. your GitHub username).
        run_id: Agent run identifier used for provenance tracking.
        remote: Remote name (default "origin").

    Returns:
        JSON with status, branch, and remote, or a descriptive error.
    """
    repo = Path(repo_path)

    # Guardrail 1: branch prefix allow-list
    if not _BRANCH_RE.match(branch_name):
        return json.dumps({
            "error": f"Branch '{branch_name}' does not match agent naming convention "
                     "^agent/(issue-\\d+|manual)-[a-z0-9-]+"
        })

    # Guardrail 5: workspace isolation (checked early, before any network ops)
    allowed_roots = (str(Path.home() / ".tfdev"), str(_WORK_ROOT))
    if not any(repo_path.startswith(r) for r in allowed_roots):
        return json.dumps({
            "error": f"repo_path '{repo_path}' is outside allowed workspace roots: {allowed_roots}"
        })

    if not (repo / ".git").exists():
        return json.dumps({"error": f"No git repository found at {repo_path}"})

    # Guardrail 2: remote ownership
    url_result = _run_git(["remote", "get-url", remote], repo)
    if url_result["exit_code"] != 0:
        return json.dumps({"error": f"Could not get URL for remote '{remote}': {url_result['stderr']}"})
    remote_url = url_result["stdout"]
    owner = _parse_remote_owner(remote_url)
    if owner is None:
        return json.dumps({"error": f"Could not parse owner from remote URL: {remote_url}"})
    if owner.lower().startswith("azure"):
        return json.dumps({"error": f"Refusing to push: remote owner '{owner}' appears to be an upstream Azure org"})
    if owner != fork_owner:
        return json.dumps({
            "error": f"Remote owner mismatch: expected '{fork_owner}', got '{owner}' (url: {remote_url})"
        })

    # Guardrail 4: first-write provenance
    repo_slug = remote_url.split("github.com/")[-1].split("github.com:")[-1].rstrip(".git").replace("/", "_")
    state_dir = _STATE_ROOT / repo_slug
    state_dir.mkdir(parents=True, exist_ok=True)
    provenance_file = state_dir / f"{branch_name.replace('/', '_')}.json"

    head_result = _run_git(["rev-parse", "HEAD"], repo)
    if head_result["exit_code"] != 0:
        return json.dumps({"error": f"Could not resolve HEAD: {head_result['stderr']}"})
    current_sha = head_result["stdout"]

    if not provenance_file.exists():
        provenance_file.write_text(json.dumps({"first_sha": current_sha, "run_id": run_id}))
    else:
        provenance = json.loads(provenance_file.read_text())
        first_sha = provenance.get("first_sha", "")
        # Check if the remote tip (if any) is a descendant of first_sha
        ls_result = _run_git(["ls-remote", remote, branch_name], repo)
        if ls_result["exit_code"] == 0 and ls_result["stdout"]:
            remote_sha = ls_result["stdout"].split()[0]
            # Fetch enough history to evaluate merge-base
            _run_git(["fetch", remote, branch_name, "--depth", "100"], repo)
            mb_result = _run_git(["merge-base", "--is-ancestor", first_sha, remote_sha], repo)
            if mb_result["exit_code"] != 0:
                return json.dumps({
                    "error": "provenance check failed: remote diverged",
                    "first_sha": first_sha,
                    "remote_sha": remote_sha,
                })

    # Guardrail 3 is enforced by constructing the command without -f flags
    push_result = _run_git(["push", remote, branch_name], repo)
    if push_result["exit_code"] != 0:
        return json.dumps({"error": f"Push failed: {push_result['stderr']}"})

    return json.dumps({"status": "pushed", "branch": branch_name, "remote": remote})


@ai_function
def verify_branch_provenance(repo_path: str, branch_name: str) -> str:
    """Verify that the current HEAD is a descendant of the recorded provenance SHA.

    Args:
        repo_path: Absolute path to the git repository.
        branch_name: Branch name to look up in the provenance store.

    Returns:
        JSON with ``valid`` (bool), ``reason``, and ``first_sha``.
    """
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return json.dumps({"valid": False, "reason": f"No git repository found at {repo_path}", "first_sha": ""})

    # Resolve remote URL to find the repo slug
    url_result = _run_git(["remote", "get-url", "origin"], repo)
    if url_result["exit_code"] != 0:
        return json.dumps({"valid": False, "reason": "Could not determine remote URL", "first_sha": ""})
    remote_url = url_result["stdout"]
    repo_slug = remote_url.split("github.com/")[-1].split("github.com:")[-1].rstrip(".git").replace("/", "_")

    provenance_file = _STATE_ROOT / repo_slug / f"{branch_name.replace('/', '_')}.json"
    if not provenance_file.exists():
        return json.dumps({
            "valid": False,
            "reason": f"No provenance file found at {provenance_file}",
            "first_sha": "",
        })

    provenance = json.loads(provenance_file.read_text())
    first_sha = provenance.get("first_sha", "")
    if not first_sha:
        return json.dumps({"valid": False, "reason": "Provenance file missing first_sha", "first_sha": ""})

    head_result = _run_git(["rev-parse", "HEAD"], repo)
    if head_result["exit_code"] != 0:
        return json.dumps({"valid": False, "reason": "Could not resolve HEAD", "first_sha": first_sha})
    current_sha = head_result["stdout"]

    mb_result = _run_git(["merge-base", "--is-ancestor", first_sha, current_sha], repo)
    if mb_result["exit_code"] == 0:
        return json.dumps({"valid": True, "reason": "HEAD is a descendant of first_sha", "first_sha": first_sha})

    return json.dumps({
        "valid": False,
        "reason": f"HEAD ({current_sha}) is not a descendant of first_sha ({first_sha})",
        "first_sha": first_sha,
    })
