"""Upgrade testing workflow -- the core value proposition of the agent.

This module provides a deterministic, high-level tool that executes
the full upgrade test lifecycle for a single example:

  1. Checkout base_ref (old version)
  2. Deploy (init + apply) at base_ref
  3. Verify idempotency at base_ref
  4. Checkout head_ref (new version)
  5. Run terraform init -upgrade at head_ref
  6. Run terraform plan to capture the upgrade diff (DO NOT apply)
  7. Cleanup (terraform destroy) in a finally block

The tool returns structured execution evidence.  Analysis of the diff
against UPGRADE.md is done by the analysis agent, not here.

Design decisions:
  - TF_DATA_DIR is set outside the repo tree so that switching refs
    does not contaminate .terraform/ state.
  - Each example gets its own workspace -- no shared clones that race.
  - Cleanup always runs (unless cleanup=false) even on partial failure.
  - No UPGRADE.md reading here -- that's the analysis agent's job.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agent_framework import ai_function

from tools.terraform import _workspace_path


def _tf_data_dir(ws_path: Path, example: str) -> Path:
    """Return an external TF_DATA_DIR for the given example.

    Keeping .terraform/ outside the repo checkout prevents contamination
    when switching refs and avoids stale provider/lock artifacts.
    """
    data_dir = ws_path / ".tf-data" / example.replace("/", "_")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _tf_state_path(ws_path: Path, example: str) -> Path:
    """Return an external state file path for the given example.

    Keeping terraform.tfstate outside the repo checkout prevents git clean
    from deleting it when switching between refs.
    """
    state_dir = ws_path / ".tf-state" / example.replace("/", "_")
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "terraform.tfstate"


def _run_tf(
    cmd: list[str],
    cwd: str | Path,
    tf_data_dir: Path,
    timeout: int = 600,
) -> dict:
    """Run a terraform command with TF_DATA_DIR set externally."""
    env = os.environ.copy()
    env["TF_DATA_DIR"] = str(tf_data_dir)
    import subprocess

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout,
            "stderr": result.stderr[-4000:] if len(result.stderr) > 4000 else result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"Command timed out after {timeout}s"}


def _do_destroy(example_dir: Path, tf_data_dir: Path, var_file: str, state_path: Path | None = None) -> dict:
    """Run terraform destroy, best-effort."""
    cmd = ["terraform", "destroy", "-input=false", "-no-color", "-auto-approve"]
    if state_path is not None:
        cmd.append(f"-state={state_path}")
    if var_file:
        cmd.append(f"-var-file={var_file}")
    return _run_tf(cmd, example_dir, tf_data_dir, timeout=1200)


def _parse_plan_json(show_result: dict) -> tuple[dict, list[dict]]:
    """Parse terraform show -json output into summary + resource changes."""
    summary = {"creates": 0, "updates": 0, "deletes": 0, "replaces": 0, "no_ops": 0}
    resource_changes = []

    if show_result["exit_code"] != 0:
        return summary, resource_changes

    try:
        plan_data = json.loads(show_result["stdout"])
    except json.JSONDecodeError:
        return summary, resource_changes

    for rc in plan_data.get("resource_changes", []):
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

    return summary, resource_changes


@ai_function
def run_upgrade_test(
    workspace_id: str,
    module_dir: str,
    example: str,
    base_ref: str,
    head_ref: str,
    var_file: str = "",
    cleanup: bool = True,
    head_module_dir: str = "",
) -> str:
    """Run a full upgrade test for a single module example.

    This is the core workflow: deploy the old version (base_ref), switch
    to the new version (head_ref), and capture what terraform plan shows
    as the upgrade diff.  This detects breaking changes, resource
    replacements, and behavioral shifts.

    The tool does NOT apply the head version -- it only plans.  Analysis
    of the diff against UPGRADE.md is done by the analysis agent.

    TF_DATA_DIR and terraform.tfstate are set outside the repo tree so
    ref switching and git clean do not contaminate provider or state.
    Cleanup (destroy) runs in a finally block to prevent leaked infrastructure.

    Args:
        workspace_id: Workspace id from create_workspace.
        module_dir: Directory within the workspace containing the cloned module.
        example: Example subdirectory name (e.g. "default", "complete").
        base_ref: The old version ref (branch, tag, or commit).
        head_ref: The new version ref to upgrade to.
        var_file: Optional .tfvars file (relative to example dir).
        cleanup: Whether to destroy resources after testing (default true).
        head_module_dir: Optional directory containing a separately-cloned
            head version (for cross-repo upgrade testing).  When set, no git
            ref switching is performed — base_module_dir is deployed, then
            init -upgrade + plan run in head_module_dir.

    Returns:
        JSON UpgradeTestResult with base_deploy, base_idempotency,
        upgrade_plan_summary, upgrade_resource_changes, and destroy_result.
    """
    from tools.git_ops import git_switch_ref

    ws_path = _workspace_path(workspace_id)
    base_example_dir = ws_path / module_dir / "examples" / example
    head_example_dir = (
        ws_path / head_module_dir / "examples" / example
        if head_module_dir
        else base_example_dir
    )
    tf_data_dir = _tf_data_dir(ws_path, example)
    state_path = _tf_state_path(ws_path, example)

    result: dict = {
        "example": example,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "status": "running",
        "phases_completed": [],
        "base_deploy": None,
        "base_idempotency": None,
        "upgrade_plan_summary": None,
        "upgrade_resource_changes": [],
        "upgrade_confidence": "unknown",
        "destroy_result": None,
        "errors": [],
        "timing": {},
    }

    deployed = False
    try:
        # --- Phase 1: Switch to base_ref (skipped in cross-repo mode) ---
        if not head_module_dir:
            t0 = time.time()
            switch_base = json.loads(git_switch_ref(workspace_id, base_ref, module_dir))
            if "error" in switch_base:
                result["status"] = "failure"
                result["errors"].append(f"Failed to checkout base_ref '{base_ref}': {switch_base['error']}")
                return json.dumps(result, indent=2)
            result["base_commit"] = switch_base.get("commit_sha", "unknown")
            result["timing"]["checkout_base"] = round(time.time() - t0, 1)
            result["phases_completed"].append("checkout_base")

        if not base_example_dir.is_dir():
            result["status"] = "failure"
            result["errors"].append(
                f"Example directory 'examples/{example}' not found at base_ref '{base_ref}'"
            )
            return json.dumps(result, indent=2)

        # --- Phase 2: Init + Apply at base_ref ---
        t0 = time.time()
        init_cmd = ["terraform", "init", "-input=false", "-no-color"]
        init_result = _run_tf(init_cmd, base_example_dir, tf_data_dir)
        if init_result["exit_code"] != 0:
            result["status"] = "failure"
            result["errors"].append(f"terraform init failed at base_ref: {init_result['stderr'][:500]}")
            return json.dumps(result, indent=2)
        result["phases_completed"].append("init_base")

        apply_cmd = [
            "terraform", "apply", "-input=false", "-no-color", "-auto-approve",
            f"-state={state_path}",
        ]
        if var_file:
            apply_cmd.append(f"-var-file={var_file}")
        apply_result = _run_tf(apply_cmd, base_example_dir, tf_data_dir, timeout=1200)
        result["timing"]["deploy_base"] = round(time.time() - t0, 1)

        base_deploy = {
            "status": "success" if apply_result["exit_code"] == 0 else "failure",
            "exit_code": apply_result["exit_code"],
        }
        if apply_result["exit_code"] != 0:
            base_deploy["error"] = apply_result["stderr"][:1000]
            result["base_deploy"] = base_deploy
            result["status"] = "failure"
            result["errors"].append("terraform apply failed at base_ref")
            return json.dumps(result, indent=2)

        result["base_deploy"] = base_deploy
        result["phases_completed"].append("apply_base")
        deployed = True

        # --- Phase 3: Idempotency check at base_ref ---
        t0 = time.time()
        idem_cmd = [
            "terraform", "plan", "-input=false", "-no-color",
            "-detailed-exitcode", "-out=idempotency-check",
            f"-state={state_path}",
        ]
        if var_file:
            idem_cmd.append(f"-var-file={var_file}")
        idem_result = _run_tf(idem_cmd, base_example_dir, tf_data_dir)
        result["timing"]["idempotency_base"] = round(time.time() - t0, 1)

        if idem_result["exit_code"] == 0:
            result["base_idempotency"] = {"status": "pass", "unexpected_changes": 0}
        elif idem_result["exit_code"] == 2:
            show_cmd = ["terraform", "show", "-json", "-no-color", "idempotency-check"]
            show_result = _run_tf(show_cmd, base_example_dir, tf_data_dir, timeout=60)
            idem_summary, idem_changes = _parse_plan_json(show_result)
            result["base_idempotency"] = {
                "status": "fail",
                "unexpected_changes": len(idem_changes),
                "changes": idem_changes[:10],
            }
        else:
            result["base_idempotency"] = {
                "status": "error",
                "error": idem_result["stderr"][:500],
            }
        result["phases_completed"].append("idempotency_base")

        # --- Phase 4: Switch to head_ref (skipped in cross-repo mode) ---
        if not head_module_dir:
            t0 = time.time()
            switch_head = json.loads(git_switch_ref(workspace_id, head_ref, module_dir))
            if "error" in switch_head:
                result["status"] = "failure"
                result["errors"].append(f"Failed to checkout head_ref '{head_ref}': {switch_head['error']}")
                return json.dumps(result, indent=2)
            result["head_commit"] = switch_head.get("commit_sha", "unknown")
            result["timing"]["checkout_head"] = round(time.time() - t0, 1)
            result["phases_completed"].append("checkout_head")

        if not head_example_dir.is_dir():
            result["status"] = "failure"
            result["errors"].append(
                f"Example directory 'examples/{example}' not found at head_ref '{head_ref}' "
                "(example may have been renamed or removed -- this is a breaking change)"
            )
            return json.dumps(result, indent=2)

        # --- Phase 5: Init -upgrade + Plan at head_ref (DO NOT APPLY) ---
        t0 = time.time()
        upgrade_init_cmd = ["terraform", "init", "-upgrade", "-input=false", "-no-color"]
        upgrade_init = _run_tf(upgrade_init_cmd, head_example_dir, tf_data_dir)
        if upgrade_init["exit_code"] != 0:
            result["status"] = "failure"
            result["errors"].append(
                f"terraform init -upgrade failed at head_ref: {upgrade_init['stderr'][:500]}"
            )
            return json.dumps(result, indent=2)
        result["phases_completed"].append("init_upgrade_head")

        upgrade_plan_cmd = [
            "terraform", "plan", "-input=false", "-no-color", "-out=upgrade-plan",
            f"-state={state_path}",
        ]
        if var_file:
            upgrade_plan_cmd.append(f"-var-file={var_file}")
        upgrade_plan = _run_tf(upgrade_plan_cmd, head_example_dir, tf_data_dir, timeout=600)
        result["timing"]["plan_upgrade"] = round(time.time() - t0, 1)

        if upgrade_plan["exit_code"] not in (0, 2):
            result["status"] = "failure"
            result["errors"].append(
                f"terraform plan failed at head_ref: {upgrade_plan['stderr'][:500]}"
            )
            return json.dumps(result, indent=2)

        # Parse the upgrade plan
        show_cmd = ["terraform", "show", "-json", "-no-color", "upgrade-plan"]
        show_result = _run_tf(show_cmd, head_example_dir, tf_data_dir, timeout=60)
        upgrade_summary, upgrade_changes = _parse_plan_json(show_result)

        result["upgrade_plan_summary"] = upgrade_summary
        result["upgrade_resource_changes"] = upgrade_changes
        result["phases_completed"].append("plan_head")

        # Determine confidence level
        base_idem_ok = (
            result["base_idempotency"]
            and result["base_idempotency"]["status"] == "pass"
        )
        has_changes = any(
            upgrade_summary.get(k, 0) > 0
            for k in ("creates", "updates", "deletes", "replaces")
        )

        if not base_idem_ok:
            result["upgrade_confidence"] = "low"
            result["errors"].append(
                "Base idempotency failed -- upgrade diff may include noise "
                "from pre-existing drift, not just the version change."
            )
        elif not has_changes:
            result["upgrade_confidence"] = "high"
        else:
            result["upgrade_confidence"] = "medium"

        result["status"] = "success"

    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"Unexpected error: {exc}")

    finally:
        # --- Phase 6: Cleanup (always runs unless cleanup=false or nothing deployed) ---
        if cleanup and deployed:
            t0 = time.time()
            destroy_dir: Path | None = None

            if head_example_dir.is_dir():
                # Prefer head dir: providers already upgraded, consistent with plan state
                destroy_dir = head_example_dir
            elif not head_module_dir:
                # Single-repo mode: example may have been removed at head_ref.
                # Switch back to base_ref to recover the config for destroy.
                try:
                    json.loads(git_switch_ref(workspace_id, base_ref, module_dir))
                except Exception:
                    pass
                if base_example_dir.is_dir():
                    destroy_dir = base_example_dir
            elif base_example_dir.is_dir():
                # Cross-repo mode: fall back to base dir
                destroy_dir = base_example_dir

            if destroy_dir is not None:
                destroy_result = _do_destroy(destroy_dir, tf_data_dir, var_file, state_path)
                result["destroy_result"] = {
                    "status": "success" if destroy_result["exit_code"] == 0 else "failure",
                    "exit_code": destroy_result["exit_code"],
                }
                if destroy_result["exit_code"] != 0:
                    result["destroy_result"]["error"] = destroy_result["stderr"][:500]
                    result["errors"].append("terraform destroy failed -- resources may be leaked")
            else:
                result["destroy_result"] = {
                    "status": "failure",
                    "error": "Example directory not found for destroy -- resources may be leaked",
                }
                result["errors"].append(
                    "terraform destroy skipped -- example dir not found, resources may be leaked"
                )

            result["timing"]["destroy"] = round(time.time() - t0, 1)
            result["phases_completed"].append("destroy")

    return json.dumps(result, indent=2)
