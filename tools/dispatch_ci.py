"""CI dispatch tools — POST repository_dispatch to kewalaka/avm-contributions and poll results."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from agent_framework import ai_function

_TARGET_REPO = "kewalaka/avm-contributions"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _dispatch_token() -> str:
    token = os.environ.get("AGENT_DISPATCH_TOKEN", "")
    if not token:
        raise EnvironmentError(
            "AGENT_DISPATCH_TOKEN is not set — set a fine-grained PAT for kewalaka/avm-contributions"
        )
    return token


def _post_repository_dispatch(event_type: str, client_payload: dict) -> dict:
    url = f"https://api.github.com/repos/{_TARGET_REPO}/dispatches"
    body = json.dumps({"event_type": event_type, "client_payload": client_payload}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"token {_dispatch_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 204:
                return {
                    "status": "dispatched",
                    "dispatch_id": client_payload.get("dispatch_id", ""),
                    "timestamp": time.time(),
                }
            body_text = resp.read().decode()
            return {"status": "error", "http_status": resp.status, "body": body_text}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        return {"status": "error", "http_status": e.code, "body": body_text}
    except urllib.error.URLError as e:
        return {"status": "error", "reason": str(e)}


def _find_triggered_run(
    workflow_filename: str, dispatch_timestamp: float, timeout_s: int = 120
) -> str | None:
    deadline = time.time() + timeout_s
    cutoff = dispatch_timestamp - 10  # 10s buffer for clock skew
    while time.time() < deadline:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "--repo", _TARGET_REPO,
                "--workflow", workflow_filename,
                "--limit", "20",
                "--json", "databaseId,status,createdAt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            try:
                runs = json.loads(result.stdout)
                matching = [
                    r for r in runs
                    if datetime.fromisoformat(
                        r["createdAt"].replace("Z", "+00:00")
                    ).timestamp() > cutoff
                ]
                if matching:
                    # Return newest matching run
                    latest = max(
                        matching,
                        key=lambda r: datetime.fromisoformat(
                            r["createdAt"].replace("Z", "+00:00")
                        ).timestamp(),
                    )
                    return str(latest["databaseId"])
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
        time.sleep(5)
    return None


def _wait_for_run(run_id: str, timeout_s: int = 1800) -> dict:
    try:
        subprocess.run(
            [
                "gh", "run", "watch",
                "--repo", _TARGET_REPO,
                run_id,
                "--exit-status",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"run_id": run_id, "conclusion": "timeout"}

    result = subprocess.run(
        [
            "gh", "run", "view",
            "--repo", _TARGET_REPO,
            run_id,
            "--json", "status,conclusion,url",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
        return {
            "run_id": run_id,
            "status": data.get("status"),
            "conclusion": data.get("conclusion"),
            "url": data.get("url"),
        }
    except (json.JSONDecodeError, KeyError):
        return {"run_id": run_id, "conclusion": "unknown", "raw": result.stdout}


def _download_artifacts(run_id: str, artifact_name: str, output_dir: str) -> dict:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "gh", "run", "download",
            "--repo", _TARGET_REPO,
            run_id,
            "--name", artifact_name,
            "--dir", output_dir,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return {
            "status": "error",
            "stderr": result.stderr,
            "stdout": result.stdout,
        }

    files = []
    for root, _, filenames in os.walk(output_dir):
        for f in filenames:
            files.append(os.path.relpath(os.path.join(root, f), output_dir))

    summary = None
    summary_path = Path(output_dir) / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError:
            pass

    return {
        "status": "downloaded",
        "files": files,
        "output_dir": output_dir,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Public @ai_function tools
# ---------------------------------------------------------------------------

@ai_function
def dispatch_module_checks(
    module_repo: str,
    module_ref: str,
    run_id: str = "",
) -> str:
    """Dispatch module-check CI to kewalaka/avm-contributions and wait for results.

    Runs linting and pre-commit checks on the given fork branch.

    Args:
        module_repo: Fork repo in owner/repo format.
        module_ref: Branch or SHA to check.
        run_id: Optional correlation ID (generates UUID if empty).

    Returns:
        JSON with status, dispatch_id, gha_run_id, run_url, conclusion, and artifacts_dir.
    """
    if not run_id:
        run_id = str(uuid.uuid4())
    dispatch_id = str(uuid.uuid4())
    artifacts_dir = str(Path.home() / ".tfdev" / "ws" / run_id / "ci" / "checks")

    dispatch_result = _post_repository_dispatch(
        "module-checks",
        {
            "dispatch_id": dispatch_id,
            "source": module_repo,
            "branch": module_ref,
        },
    )
    if dispatch_result["status"] != "dispatched":
        return json.dumps({"status": "error", "dispatch_id": dispatch_id, **dispatch_result})

    gha_run_id = _find_triggered_run(
        "checks.yml", dispatch_result["timestamp"], timeout_s=120
    )
    if not gha_run_id:
        return json.dumps({
            "status": "error",
            "dispatch_id": dispatch_id,
            "details": "Could not find triggered workflow run within timeout",
        })

    run_result = _wait_for_run(gha_run_id, timeout_s=600)
    artifact_result = _download_artifacts(gha_run_id, "check-report", artifacts_dir)

    conclusion = run_result.get("conclusion", "unknown")
    return json.dumps({
        "status": "success" if conclusion == "success" else "failure",
        "dispatch_id": dispatch_id,
        "gha_run_id": gha_run_id,
        "run_url": run_result.get("url", ""),
        "conclusion": conclusion,
        "artifacts_dir": artifacts_dir,
    })


@ai_function
def dispatch_module_e2e(
    module_repo: str,
    module_ref: str,
    example: str = "",
    run_id: str = "",
) -> str:
    """Dispatch module-e2e CI to kewalaka/avm-contributions and wait for results.

    Runs an end-to-end deployment test of the module.

    Args:
        module_repo: Fork repo in owner/repo format.
        module_ref: Branch or SHA to test.
        example: Optional example directory filter (empty = all).
        run_id: Optional correlation ID (generates UUID if empty).

    Returns:
        JSON with status, dispatch_id, gha_run_id, run_url, conclusion, example, and artifacts_dir.
    """
    if not run_id:
        run_id = str(uuid.uuid4())
    dispatch_id = str(uuid.uuid4())
    artifacts_dir = str(Path.home() / ".tfdev" / "ws" / run_id / "ci" / "e2e")

    dispatch_result = _post_repository_dispatch(
        "module-e2e",
        {
            "dispatch_id": dispatch_id,
            "source": module_repo,
            "branch": module_ref,
            **({"example": example} if example else {}),
        },
    )
    if dispatch_result["status"] != "dispatched":
        return json.dumps({"status": "error", "dispatch_id": dispatch_id, **dispatch_result})

    gha_run_id = _find_triggered_run(
        "e2e-tests.yml", dispatch_result["timestamp"], timeout_s=120
    )
    if not gha_run_id:
        return json.dumps({
            "status": "error",
            "dispatch_id": dispatch_id,
            "details": "Could not find triggered workflow run within timeout",
        })

    run_result = _wait_for_run(gha_run_id, timeout_s=3600)
    artifact_result = _download_artifacts(gha_run_id, "e2e-report", artifacts_dir)

    conclusion = run_result.get("conclusion", "unknown")
    return json.dumps({
        "status": "success" if conclusion == "success" else "failure",
        "dispatch_id": dispatch_id,
        "gha_run_id": gha_run_id,
        "run_url": run_result.get("url", ""),
        "conclusion": conclusion,
        "example": example,
        "artifacts_dir": artifacts_dir,
    })


@ai_function
def dispatch_upgrade_test(
    upstream_repo: str,
    fork_repo: str,
    base_ref: str,
    head_ref: str,
    example: str = "",
    run_id: str = "",
) -> str:
    """Dispatch module-upgrade CI to kewalaka/avm-contributions and wait for results.

    Tests that the module can be upgraded from base_ref to head_ref without breaking existing deployments.

    Args:
        upstream_repo: The upstream/canonical module repo in owner/repo format.
        fork_repo: Fork repo containing the changes in owner/repo format.
        base_ref: Starting ref (the version being upgraded from).
        head_ref: Target ref (the version being upgraded to).
        example: Optional example directory filter (empty = all).
        run_id: Optional correlation ID (generates UUID if empty).

    Returns:
        JSON with status, dispatch_id, gha_run_id, run_url, conclusion, artifacts_dir, and upgrade_summary.
    """
    if not run_id:
        run_id = str(uuid.uuid4())
    dispatch_id = str(uuid.uuid4())
    artifacts_dir = str(Path.home() / ".tfdev" / "ws" / run_id / "ci" / "upgrade")

    dispatch_result = _post_repository_dispatch(
        "module-upgrade",
        {
            "dispatch_id": dispatch_id,
            "upstream_repo": upstream_repo,
            "fork_repo": fork_repo,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "example": example,
        },
    )
    if dispatch_result["status"] != "dispatched":
        return json.dumps({"status": "error", "dispatch_id": dispatch_id, **dispatch_result})

    gha_run_id = _find_triggered_run(
        "upgrade-tests.yml", dispatch_result["timestamp"], timeout_s=120
    )
    if not gha_run_id:
        return json.dumps({
            "status": "error",
            "dispatch_id": dispatch_id,
            "details": "Could not find triggered workflow run within timeout",
        })

    run_result = _wait_for_run(gha_run_id, timeout_s=2400)
    artifact_result = _download_artifacts(gha_run_id, "upgrade-report", artifacts_dir)

    conclusion = run_result.get("conclusion", "unknown")
    return json.dumps({
        "status": "success" if conclusion == "success" else "failure",
        "dispatch_id": dispatch_id,
        "gha_run_id": gha_run_id,
        "run_url": run_result.get("url", ""),
        "conclusion": conclusion,
        "artifacts_dir": artifacts_dir,
        "upgrade_summary": artifact_result.get("summary"),
    })


@ai_function
def check_dispatch_token() -> str:
    """Validate AGENT_DISPATCH_TOKEN by probing the target repository.

    Call at startup to confirm the fine-grained PAT is correctly configured
    before attempting any CI dispatches.

    Returns:
        JSON with status 'valid' and repo name, or an error description.
    """
    try:
        token = _dispatch_token()
    except EnvironmentError as e:
        return json.dumps({"status": "error", "reason": str(e)})

    url = f"https://api.github.com/repos/{_TARGET_REPO}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                return json.dumps({"status": "valid", "repo": _TARGET_REPO})
            return json.dumps({"status": "error", "http_status": resp.status})
    except urllib.error.HTTPError as e:
        return json.dumps({"status": "error", "http_status": e.code, "reason": str(e)})
    except urllib.error.URLError as e:
        return json.dumps({"status": "error", "reason": str(e)})
