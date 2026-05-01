"""Terraform CLI tools for the Infrastructure Testing Agent.

Each public function is decorated with @tool so the Microsoft Agent
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

from agent_framework import tool

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


@tool
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


@tool
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


@tool
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
        except json.JSONDecodeError:
            return json.dumps({"error": "backend_config must be valid JSON"})
        if not isinstance(pairs, dict):
            return json.dumps({"error": "backend_config must be a JSON object"})
        for k, v in pairs.items():
            cmd.append(f"-backend-config={k}={v}")
    return json.dumps(_run(cmd, ws_path))


@tool
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


@tool
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


@tool
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


@tool
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


@tool
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


@tool
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


@tool
def terraform_init_upgrade(
    workspace_id: str,
    working_dir: str = ".",
    backend_config: str = "",
) -> str:
    """Run ``terraform init -upgrade`` inside a workspace.

    This variant uses the ``-upgrade`` flag to update provider and module
    versions, which is required when testing module upgrades or when AVM
    examples pin minimum versions.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        backend_config: Optional JSON object of backend config overrides.

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "init", "-upgrade", "-input=false", "-no-color"]
    if backend_config:
        try:
            pairs = json.loads(backend_config)
        except json.JSONDecodeError:
            return json.dumps({"error": "backend_config must be valid JSON"})
        if not isinstance(pairs, dict):
            return json.dumps({"error": "backend_config must be a JSON object"})
        for k, v in pairs.items():
            cmd.append(f"-backend-config={k}={v}")
    return json.dumps(_run(cmd, ws_path))


@tool
def run_avm_cli(
    workspace_id: str,
    command: str,
    module_dir: str = "module",
) -> str:
    """Run the AVM CLI tool (``./avm``) from a module workspace.

    The AVM template ships an ``avm`` script that wraps common operations
    like pre-commit checks, PR validation, unit tests, and integration tests.
    The agent reads this from the MUT rather than embedding its own copy.

    Args:
        workspace_id: Workspace id from create_workspace.
        command: AVM CLI command to run (e.g. 'pre-commit', 'tf-test-unit').
        module_dir: Directory within the workspace containing the module.

    Returns:
        JSON with exit_code, stdout, stderr.
    """
    allowed_commands = {
        "pre-commit", "pr-check", "tf-test-unit",
        "tf-test-integration", "tf-test-example", "version",
    }
    if command not in allowed_commands:
        return json.dumps({
            "error": f"Command '{command}' not allowed. Allowed: {sorted(allowed_commands)}",
        })

    ws_path = _workspace_path(workspace_id) / module_dir
    avm_path = ws_path / "avm"
    if not avm_path.is_file():
        return json.dumps({"error": "AVM CLI not found in module directory"})
    if not os.access(avm_path, os.X_OK):
        return json.dumps({"error": "AVM CLI exists but is not executable. Run: chmod +x ./avm"})

    cmd = [str(avm_path), command]
    return json.dumps(_run(cmd, ws_path, timeout=1800))


@tool
def terraform_plan_json(
    workspace_id: str,
    working_dir: str = ".",
    var_file: str = "",
    out_file: str = "tfplan",
) -> str:
    """Run ``terraform plan``, save the plan, and return a structured JSON summary.

    Combines plan + show in one call to produce structured output suitable
    for passing to the analysis phase without raw terraform output in context.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        var_file: Optional path (relative to working_dir) to a .tfvars file.
        out_file: Name for the saved plan file.

    Returns:
        JSON with plan_summary (creates/updates/deletes/replaces counts)
        and resource_changes list, or error details.
    """
    ws_path = _workspace_path(workspace_id) / working_dir

    # Run plan
    plan_cmd = ["terraform", "plan", "-input=false", "-no-color", f"-out={out_file}"]
    if var_file:
        plan_cmd.append(f"-var-file={var_file}")
    plan_result = _run(plan_cmd, ws_path)

    if plan_result["exit_code"] != 0:
        return json.dumps({"status": "error", "phase": "plan", "details": plan_result})

    # Convert to JSON
    show_cmd = ["terraform", "show", "-json", "-no-color", out_file]
    show_result = _run(show_cmd, ws_path, timeout=60)

    if show_result["exit_code"] != 0:
        return json.dumps({"status": "error", "phase": "show", "details": show_result})

    try:
        plan_data = json.loads(show_result["stdout"])
    except json.JSONDecodeError:
        return json.dumps({"status": "error", "phase": "parse", "details": "Failed to parse plan JSON"})

    # Build structured summary
    changes = plan_data.get("resource_changes", [])
    summary = {"creates": 0, "updates": 0, "deletes": 0, "replaces": 0, "no_ops": 0}
    resource_changes = []

    for rc in changes:
        actions = rc.get("change", {}).get("actions", [])
        addr = rc.get("address", "unknown")
        entry = {"address": addr, "type": rc.get("type", ""), "actions": actions}

        if actions == ["no-op"] or actions == ["read"]:
            summary["no_ops"] += 1
        elif actions == ["create"]:
            summary["creates"] += 1
            resource_changes.append(entry)
        elif actions == ["delete"]:
            summary["deletes"] += 1
            resource_changes.append(entry)
        elif actions == ["update"]:
            summary["updates"] += 1
            resource_changes.append(entry)
        elif "delete" in actions and "create" in actions:
            summary["replaces"] += 1
            resource_changes.append(entry)
        else:
            resource_changes.append(entry)

    return json.dumps({
        "status": "success",
        "plan_file": out_file,
        "summary": summary,
        "total_changes": summary["creates"] + summary["updates"] + summary["deletes"] + summary["replaces"],
        "resource_changes": resource_changes,
    }, indent=2)


@tool
def check_idempotency(
    workspace_id: str,
    working_dir: str = ".",
    var_file: str = "",
) -> str:
    """Run a terraform plan after apply to check idempotency.

    A well-written module should produce an empty plan after apply.
    Any unexpected changes indicate an idempotency issue.

    Args:
        workspace_id: Workspace id from create_workspace.
        working_dir: Relative path within the workspace to the root module.
        var_file: Optional .tfvars file.

    Returns:
        JSON with idempotency status (pass/fail), unexpected change count,
        and details of any non-empty plan resources.
    """
    ws_path = _workspace_path(workspace_id) / working_dir
    plan_cmd = ["terraform", "plan", "-input=false", "-no-color", "-detailed-exitcode", "-out=idempotency-check"]
    if var_file:
        plan_cmd.append(f"-var-file={var_file}")

    result = _run(plan_cmd, ws_path)

    # Exit code 0 = empty plan (pass), 2 = changes detected (fail), other = error
    if result["exit_code"] == 0:
        return json.dumps({
            "status": "pass",
            "unexpected_changes": 0,
            "details": [],
            "message": "Idempotency check passed — no changes detected after apply.",
        })
    elif result["exit_code"] == 2:
        # Parse the plan to get details of unexpected changes
        show_cmd = ["terraform", "show", "-json", "-no-color", "idempotency-check"]
        show_result = _run(show_cmd, ws_path, timeout=60)

        details = []
        parse_failed = False
        if show_result["exit_code"] == 0:
            try:
                plan_data = json.loads(show_result["stdout"])
                for rc in plan_data.get("resource_changes", []):
                    actions = rc.get("change", {}).get("actions", [])
                    if actions not in [["no-op"], ["read"]]:
                        details.append({
                            "address": rc.get("address", "unknown"),
                            "actions": actions,
                            "type": rc.get("type", ""),
                        })
            except json.JSONDecodeError:
                parse_failed = True
        else:
            parse_failed = True

        change_count = len(details) if not parse_failed else -1
        message = (
            f"Idempotency check FAILED — {len(details)} unexpected change(s) detected."
            if not parse_failed
            else "Idempotency check FAILED — changes detected but plan details could not be parsed."
        )

        return json.dumps({
            "status": "fail",
            "unexpected_changes": change_count,
            "details": details,
            "parse_failed": parse_failed,
            "message": message,
        })
    else:
        return json.dumps({
            "status": "error",
            "unexpected_changes": 0,
            "details": [],
            "message": f"Idempotency check error: {result['stderr'][-500:]}",
        })


@tool
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


@tool
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


@tool
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
