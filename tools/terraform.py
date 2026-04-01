"""Terraform CLI tools for the Infrastructure Testing Agent.

Each public function is decorated with @ai_function so the Microsoft Agent
Framework exposes it as a callable tool to the LLM.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from agent_framework import ai_function

from config import config

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WORK_ROOT = Path(tempfile.gettempdir()) / "infra-agent-work"
_WORK_ROOT.mkdir(parents=True, exist_ok=True)


def _run(
    cmd: list[str],
    cwd: str | Path,
    *,
    timeout: int = 600,
) -> dict:
    """Run a subprocess and return structured output."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout,
            "stderr": result.stderr[-4000:] if len(result.stderr) > 4000 else result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"Command timed out after {timeout}s"}


def _workspace_path(workspace_id: str) -> Path:
    """Return the on-disk path for a given workspace id."""
    safe_id = workspace_id.replace("/", "_").replace("..", "")
    return _WORK_ROOT / safe_id


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


@ai_function
def create_workspace(name: str = "") -> str:
    """Create a fresh isolated workspace directory and return its id.

    Use this before starting a new test run.  All subsequent terraform and
    git commands reference this workspace id.

    Args:
        name: Optional human-readable label incorporated in the id.

    Returns:
        A JSON object with the workspace id and filesystem path.
    """
    ws_id = f"{name or 'ws'}-{uuid.uuid4().hex[:8]}"
    ws_path = _workspace_path(ws_id)
    ws_path.mkdir(parents=True, exist_ok=True)
    return json.dumps({"workspace_id": ws_id, "path": str(ws_path)})


@ai_function
def delete_workspace(workspace_id: str) -> str:
    """Delete a workspace directory and all its contents.

    Args:
        workspace_id: The workspace id returned by create_workspace.

    Returns:
        Confirmation message.
    """
    ws_path = _workspace_path(workspace_id)
    if ws_path.exists():
        shutil.rmtree(ws_path)
        return json.dumps({"status": "deleted", "workspace_id": workspace_id})
    return json.dumps({"status": "not_found", "workspace_id": workspace_id})


@ai_function
def terraform_init(
    workspace_id: str,
    working_dir: str = ".",
    backend_config: str = "",
) -> str:
    """Run ``terraform init`` inside a workspace.

    Uses a local backend by default (ephemeral state).  Pass
    *backend_config* as a JSON string of key=value pairs to override.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        backend_config: Optional JSON object of backend config overrides.

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "init", "-input=false", "-no-color"]
    if backend_config:
        try:
            pairs = json.loads(backend_config)
            for k, v in pairs.items():
                cmd.append(f"-backend-config={k}={v}")
        except json.JSONDecodeError:
            return json.dumps({"error": "backend_config must be valid JSON"})
    return json.dumps(_run(cmd, ws_path))


@ai_function
def terraform_plan(
    workspace_id: str,
    working_dir: str = ".",
    var_file: str = "",
    out_file: str = "tfplan",
) -> str:
    """Run ``terraform plan`` and save the plan file.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        var_file: Optional path (relative to working_dir) to a .tfvars file.
        out_file: Name for the saved plan file.

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "plan", "-input=false", "-no-color", f"-out={out_file}"]
    if var_file:
        cmd.append(f"-var-file={var_file}")
    return json.dumps(_run(cmd, ws_path))


@ai_function
def terraform_apply(
    workspace_id: str,
    working_dir: str = ".",
    plan_file: str = "",
    var_file: str = "",
) -> str:
    """Run ``terraform apply`` with auto-approve.

    If *plan_file* is provided, applies that saved plan.  Otherwise runs a
    direct apply.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        plan_file: Optional saved plan file name to apply.
        var_file: Optional .tfvars file (ignored when plan_file is set).

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    if plan_file:
        cmd = ["terraform", "apply", "-input=false", "-no-color", plan_file]
    else:
        cmd = ["terraform", "apply", "-input=false", "-no-color", "-auto-approve"]
        if var_file:
            cmd.append(f"-var-file={var_file}")
    return json.dumps(_run(cmd, ws_path, timeout=1200))


@ai_function
def terraform_destroy(
    workspace_id: str,
    working_dir: str = ".",
    var_file: str = "",
) -> str:
    """Run ``terraform destroy`` with auto-approve.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        var_file: Optional .tfvars file.

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "destroy", "-input=false", "-no-color", "-auto-approve"]
    if var_file:
        cmd.append(f"-var-file={var_file}")
    return json.dumps(_run(cmd, ws_path, timeout=1200))


@ai_function
def terraform_show(
    workspace_id: str,
    working_dir: str = ".",
    plan_file: str = "",
) -> str:
    """Run ``terraform show`` in JSON mode against current state or a saved plan.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        plan_file: Optional plan file to show.  If empty shows current state.

    Returns:
        JSON with exit_code, stdout (the JSON representation), stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "show", "-json", "-no-color"]
    if plan_file:
        cmd.append(plan_file)
    return json.dumps(_run(cmd, ws_path))


@ai_function
def terraform_output(
    workspace_id: str,
    working_dir: str = ".",
) -> str:
    """Return all terraform outputs as JSON.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.

    Returns:
        JSON with exit_code, stdout (outputs), stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "output", "-json", "-no-color"]
    return json.dumps(_run(cmd, ws_path))


@ai_function
def terraform_test(
    workspace_id: str,
    working_dir: str = ".",
) -> str:
    """Run ``terraform test`` (native testing framework) in a module.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "test", "-no-color"]
    return json.dumps(_run(cmd, ws_path, timeout=1800))


@ai_function
def list_workspace_files(
    workspace_id: str,
    relative_path: str = ".",
) -> str:
    """List files and directories in a workspace path.

    Useful for the agent to inspect what was cloned or generated.

    Args:
        workspace_id: Workspace id from create_workspace.
        relative_path: Subdirectory to list (relative to workspace root).

    Returns:
        JSON array of entries with name and type (file/dir).
    """
    ws_path = _workspace_path(workspace_id) / relative_path
    if not ws_path.exists():
        return json.dumps({"error": f"Path not found: {relative_path}"})
    entries = []
    for item in sorted(ws_path.iterdir()):
        if item.name.startswith("."):
            continue
        entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file"})
    return json.dumps(entries)


@ai_function
def read_workspace_file(
    workspace_id: str,
    file_path: str,
    max_lines: int = 200,
) -> str:
    """Read the contents of a file inside a workspace.

    Args:
        workspace_id: Workspace id from create_workspace.
        file_path: Path relative to the workspace root.
        max_lines: Maximum number of lines to return (default 200).

    Returns:
        The file contents (truncated if necessary), or an error message.
    """
    full_path = _workspace_path(workspace_id) / file_path
    # Prevent path traversal
    try:
        full_path.resolve().relative_to(_workspace_path(workspace_id).resolve())
    except ValueError:
        return json.dumps({"error": "Path traversal not allowed"})
    if not full_path.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"})
    try:
        lines = full_path.read_text().splitlines()
        truncated = len(lines) > max_lines
        content = "\n".join(lines[:max_lines])
        return json.dumps({"content": content, "total_lines": len(lines), "truncated": truncated})
    except Exception as e:
        return json.dumps({"error": str(e)})


@ai_function
def write_workspace_file(
    workspace_id: str,
    file_path: str,
    content: str,
) -> str:
    """Write or overwrite a file inside a workspace.

    The agent uses this to create terraform configuration files, tfvars,
    or wrapper modules inside the workspace.

    Args:
        workspace_id: Workspace id from create_workspace.
        file_path: Path relative to the workspace root.
        content: File content to write.

    Returns:
        Confirmation with the file path written.
    """
    full_path = _workspace_path(workspace_id) / file_path
    # Prevent path traversal
    try:
        full_path.resolve().relative_to(_workspace_path(workspace_id).resolve())
    except ValueError:
        return json.dumps({"error": "Path traversal not allowed"})
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)
    return json.dumps({"status": "written", "path": file_path, "bytes": len(content)})
