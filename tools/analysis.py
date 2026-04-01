"""Analysis tools for comparing plans and checking upgrade documentation."""

from __future__ import annotations

import json

from agent_framework import ai_function

from tools.terraform import _workspace_path


@ai_function
def read_upgrade_doc(
    workspace_id: str,
    file_path: str = "UPGRADE.md",
) -> str:
    """Read an UPGRADE.md (or similar) document from a workspace.

    The agent uses this to cross-reference documented breaking changes
    against what was actually observed in a terraform plan diff.

    Args:
        workspace_id: Workspace id from create_workspace.
        file_path: Path to the upgrade document relative to workspace root.

    Returns:
        JSON with the document content, or an error if not found.
    """
    full_path = _workspace_path(workspace_id) / file_path
    if not full_path.is_file():
        return json.dumps({"error": f"File not found: {file_path}", "hint": "The module may not have an UPGRADE.md yet"})
    try:
        content = full_path.read_text()
        # Truncate very large docs
        if len(content) > 15000:
            content = content[:15000] + "\n\n[... truncated ...]"
        return json.dumps({"content": content, "path": file_path})
    except Exception as e:
        return json.dumps({"error": str(e)})


@ai_function
def summarise_plan_json(
    workspace_id: str,
    plan_file: str = "tfplan",
    working_dir: str = ".",
) -> str:
    """Parse a saved terraform plan (JSON) and return a structured change summary.

    This runs ``terraform show -json <plan_file>`` and extracts the
    resource changes into a concise summary the LLM can reason about.

    Args:
        workspace_id: Workspace id from create_workspace.
        plan_file: Name of the saved plan file.
        working_dir: Relative path to the root module within the workspace.

    Returns:
        JSON summary of creates, updates, deletes and replaces.
    """
    import subprocess

    ws_path = _workspace_path(workspace_id) / working_dir
    cmd = ["terraform", "show", "-json", "-no-color", plan_file]
    try:
        result = subprocess.run(cmd, cwd=str(ws_path), capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "terraform show timed out"})

    if result.returncode != 0:
        return json.dumps({"error": result.stderr[-2000:]})

    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError:
        return json.dumps({"error": "Failed to parse plan JSON"})

    changes = plan.get("resource_changes", [])
    summary = {"create": [], "update": [], "delete": [], "replace": [], "no_op": 0}

    for rc in changes:
        actions = rc.get("change", {}).get("actions", [])
        addr = rc.get("address", "unknown")
        entry = {"address": addr, "type": rc.get("type", ""), "name": rc.get("name", "")}

        if actions == ["no-op"] or actions == ["read"]:
            summary["no_op"] += 1
        elif actions == ["create"]:
            summary["create"].append(entry)
        elif actions == ["delete"]:
            summary["delete"].append(entry)
        elif actions == ["update"]:
            summary["update"].append(entry)
        elif "delete" in actions and "create" in actions:
            summary["replace"].append(entry)
        else:
            summary.setdefault("other", []).append({**entry, "actions": actions})

    summary["total_changes"] = (
        len(summary["create"])
        + len(summary["update"])
        + len(summary["delete"])
        + len(summary["replace"])
    )
    return json.dumps(summary, indent=2)
