"""Orchestrator — drives the Developer→Reviewer→CI maker/checker pipeline."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from agents.base import create_specialist
from agents.reviewer import review_diff
from models import DiffReview, FixAttempt
from request import DevRequest
from tools.dispatch_ci import dispatch_module_checks, dispatch_module_e2e
from tools.fork_ops import clone_fork, ensure_fork, sync_fork_default_branch
from tools.git_ops import (
    add_remote,
    clone_repo,
    commit_files,
    create_branch,
    git_switch_ref,
    push_branch,
)
from tools.github_ops import (
    add_issue_comment,
    create_pull_request,
    flip_pr_ready,
    get_latest_release,
    search_github_issues,
    update_pr_body_section,
)
from tools.module_discovery import (
    discover_module_structure,
    ingest_local_module,
    list_module_examples,
    read_module_skill,
)
from tools.analysis import read_upgrade_doc, summarise_plan_json
from tools.session_store import SessionStore, get_session_events

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3

# Agent branch name pattern — must match the guardrail in tools/git_ops.py
_BRANCH_RE = re.compile(r"^agent/(issue-\d+|manual)-[a-z0-9-]+$")

_DEVELOPER_INSTRUCTIONS_FALLBACK = """\
You are the Developer agent in the avm-contributor-agent pipeline.
Your job is to implement a GitHub issue fix on an AVM Terraform module fork.
Follow AVM Terraform conventions: snake_case names, required outputs (id, resource),
no hardcoded locations, azapi preferred for new resources.
Do NOT push — the orchestrator handles pushing after Reviewer approval.
"""


def _load_prompt_file(filename: str) -> str:
    """Load a prompt/skill file from the agents/ directory tree."""
    agents_dir = Path(__file__).parent
    path = agents_dir / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s", path)
        return ""


def _run_avm_precommit(workspace_path: str) -> bool:
    """Run ./avm pre-commit in the module workspace to align and generate the AVM skill file.

    Returns True if the command completed successfully (exit code 0).
    """
    import subprocess

    avm_bin = Path(workspace_path) / "avm"
    if not avm_bin.is_file() or not os.access(avm_bin, os.X_OK):
        logger.warning("./avm not found or not executable in %s — cannot auto-generate skill", workspace_path)
        return False

    logger.info("Running ./avm pre-commit in %s to generate AVM skill file", workspace_path)
    result = subprocess.run(
        ["./avm", "pre-commit"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode == 0:
        logger.info("./avm pre-commit succeeded")
        return True
    logger.warning(
        "./avm pre-commit exited %d:\n%s\n%s",
        result.returncode,
        result.stdout[-2000:],
        result.stderr[-2000:],
    )
    return False


def _load_module_skill_content(workspace_path: str) -> str | None:
    """Scan the module workspace for an AVM skill file and return its content.

    If the skill file is absent, runs ``./avm pre-commit`` to align the module and
    generate it, then re-scans.  Returns None only if the file is still missing after
    that attempt.
    """
    ws = Path(workspace_path)

    def _scan() -> str | None:
        candidates = [
            ws / ".agents" / "skills" / "AVM-Terraform-Development" / "SKILL.md",
            ws / ".agents" / "skills" / "AVM-Terraform-Development.md",
        ]
        for candidate in candidates:
            if candidate.exists():
                logger.info("Found module skill: %s", candidate)
                return candidate.read_text(encoding="utf-8")
        skill_dir = ws / ".agents" / "skills" / "AVM-Terraform-Development"
        if skill_dir.is_dir():
            for md_file in sorted(skill_dir.glob("*.md")):
                logger.info("Found module skill (fallback): %s", md_file)
                return md_file.read_text(encoding="utf-8")
        return None

    content = _scan()
    if content is not None:
        return content

    # Skill absent — run ./avm pre-commit to generate it then retry
    if _run_avm_precommit(workspace_path):
        content = _scan()
        if content is not None:
            return content

    logger.warning(
        "No AVM skill file found in %s after ./avm pre-commit — developer will use additive instructions only",
        workspace_path,
    )
    return None


def _get_pr_details(repo: str, pr_number: int) -> dict | None:
    """Fetch PR details: headRefName and headRepository (nameWithOwner, owner login).

    Args:
        repo: Repository to look up the PR in (owner/repo format).
              For fork PRs pass the fork repo; for upstream PRs pass the upstream repo.
        pr_number: PR number within that repo.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "headRefName,headRepository"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def _clone_local_repo_to_workspace(local_path: str, run_id: str, repo_name: str) -> dict:
    """Clone a local git repository into the isolated workspace directory.

    Uses ``git clone --local`` (hardlinks) so the clone is fast and the user's
    working tree is untouched.  The origin remote is then repointed to the fork's
    GitHub URL (inferred from the local repo's upstream remote or origin).

    Note: only *committed* changes are visible in the clone.  Any uncommitted or
    unstaged work must be committed in the source repository first.

    Returns a dict with ``status`` ('cloned' or 'error') and ``clone_path``.
    """
    import os

    src = Path(local_path).resolve()
    ws_dir = Path.home() / ".tfdev" / "ws" / run_id
    clone_path = ws_dir / repo_name
    ws_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "clone", "--local", str(src), str(clone_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"status": "error", "details": result.stderr}
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"status": "error", "details": str(exc)}

    # Repoint origin to the fork's GitHub URL (try 'origin' first, then 'upstream')
    for remote_name in ("origin", "upstream"):
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", remote_name],
                capture_output=True, text=True, timeout=15,
                cwd=str(src),
            )
            if r.returncode == 0:
                github_url = r.stdout.strip()
                # Only accept actual GitHub URLs; skip local file paths.
                # Validate with urllib.parse to avoid substring-match bypasses.
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(github_url)
                    is_github_https = (
                        parsed.scheme == "https"
                        and parsed.netloc == "github.com"
                    )
                except Exception:
                    is_github_https = False
                is_github_ssh = re.match(r"^git@github\.com:[^/]+/.+\.git$", github_url) is not None
                if is_github_https or is_github_ssh:
                    subprocess.run(
                        ["git", "remote", "set-url", "origin", github_url],
                        cwd=str(clone_path), capture_output=True, text=True, timeout=15,
                    )
                    break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return {"status": "cloned", "clone_path": str(clone_path)}


def _get_current_branch(workspace_path: str) -> str:
    """Return the current git branch name in workspace_path; empty string on error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=15,
            cwd=workspace_path,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch != "HEAD" else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _get_fork_owner_from_remote(workspace_path: str) -> str:
    """Parse the owner login from the 'origin' remote URL in workspace_path."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
            cwd=workspace_path,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            m = re.match(r"https?://github\.com/([^/]+)/", url)
            if m:
                return m.group(1)
            m = re.match(r"git@github\.com:([^/]+)/", url)
            if m:
                return m.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""



def _build_developer_instructions(workspace_path: str) -> str:
    """Build developer instructions from module skill (if present) + additive overlay."""
    module_skill = _load_module_skill_content(workspace_path)
    additive = _load_prompt_file("prompts/developer-additive.md")

    if module_skill and additive:
        return f"{module_skill}\n\n---\n\n{additive}"
    if module_skill:
        return module_skill
    if additive:
        return f"{_DEVELOPER_INSTRUCTIONS_FALLBACK}\n\n---\n\n{additive}"
    return _DEVELOPER_INSTRUCTIONS_FALLBACK


DEVELOPER_TOOLS = [
    create_branch,
    commit_files,
    git_switch_ref,
    add_remote,
    clone_repo,
    ensure_fork,
    sync_fork_default_branch,
    clone_fork,
    discover_module_structure,
    ingest_local_module,
    list_module_examples,
    read_module_skill,
    read_upgrade_doc,
    summarise_plan_json,
    add_issue_comment,
    search_github_issues,
    get_latest_release,
    get_session_events,
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _gh_json(args: list[str], timeout: int = 30) -> dict | None:
    """Run a gh CLI command and return parsed JSON output, or None on failure."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def _get_issue_title(upstream_repo: str, issue_number: int) -> str:
    """Fetch issue title from GitHub for branch name slug generation."""
    data = _gh_json(
        ["issue", "view", str(issue_number), "--repo", upstream_repo,
         "--json", "title", "--jq", ".title"],
    )
    if isinstance(data, str):
        return data.strip()
    # --jq can return a bare string that json.loads wraps as str
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--repo", upstream_repo,
             "--json", "title", "--jq", ".title"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _get_issue_context(upstream_repo: str, issue_number: int) -> str:
    """Fetch issue title and body for reviewer context."""
    data = _gh_json(
        ["issue", "view", str(issue_number), "--repo", upstream_repo,
         "--json", "title,body"],
    )
    if data and isinstance(data, dict):
        return f"Title: {data.get('title', '')}\n\nBody:\n{data.get('body', '')}"
    return f"Issue #{issue_number} in {upstream_repo}"


def _git_diff_last_commit(workspace_path: str) -> str:
    """Return the diff of HEAD~1..HEAD in the workspace; empty string on error."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD"],
            capture_output=True, text=True, timeout=60,
            cwd=workspace_path,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _git_head_sha(workspace_path: str) -> str:
    """Return HEAD SHA or empty string on error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
            cwd=workspace_path,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _pr_number_from_url(pr_url: str) -> int | None:
    """Extract PR number from a GitHub PR URL."""
    import re
    m = re.search(r"/pull/(\d+)", pr_url)
    return int(m.group(1)) if m else None


def _build_developer_message(
    request: DevRequest,
    workspace_path: str,
    attempt_number: int,
    previous_feedback: list[str],
) -> str:
    """Build the message sent to the Developer agent for each attempt."""
    lines = [
        f"## Developer Task (Attempt {attempt_number}/{_MAX_ATTEMPTS})",
        "",
        request.to_agent_message(),
        "",
        f"**Workspace path**: {workspace_path}",
        "",
    ]
    if previous_feedback:
        lines += [
            "## Issues from Previous Attempt — Please Fix",
            "",
            *[f"- {item}" for item in previous_feedback],
            "",
            "Address ALL of the above before committing.",
        ]
    elif request.mode in ("existing-repo", "existing-pr"):
        lines.append(
            "Review the existing changes in this workspace and continue from the current state."
        )
    else:
        lines.append("Please implement the fix for this issue and commit your changes.")
    return "\n".join(lines)


def _build_pr_body(request: DevRequest) -> str:
    """Build the initial PR body with managed agent regions."""
    issue_ref = (
        f"{request.upstream_repo}#{request.issue_number}"
        if request.issue_number is not None
        else request.upstream_repo
    )
    return (
        f"<!-- agent:summary -->\n"
        f"Automated fix for {issue_ref}\n\n"
        f"Agent run: {request.run_id}\n"
        f"<!-- /agent:summary -->\n\n"
        f"<!-- agent:evidence -->\n"
        f"CI evidence will be added here after checks pass.\n"
        f"<!-- /agent:evidence -->"
    )


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

async def run_developer_pipeline(request: DevRequest) -> dict:
    """Drive the Developer → Reviewer → CI loop.

    Returns a dict with keys:
      outcome: "success" | "escalated" | "error"
      pr_url: str (if PR opened)
      attempts: list[dict]  (one per FixAttempt)
      escalation_reason: str (if escalated)
      errors: list[str] (if outcome is "error")
    """
    # Step 0 — Validate
    try:
        request.validate()
    except ValueError as exc:
        return {"outcome": "error", "errors": [str(exc)]}

    # Initialise durable event log (append-only JSONL backed by ~/.tfdev/ws/<run_id>/)
    session = SessionStore.wake(request.run_id)

    repo_name = request.upstream_repo.split("/")[-1]
    fork_owner = request.fork_owner or ""

    # Log pipeline start (idempotent — safe to log on resume because every run
    # appends a new entry; callers reading the log use find_event/last_event)
    session.append(
        "pipeline_started",
        upstream_repo=request.upstream_repo,
        mode=request.mode,
        issue_number=request.issue_number,
        pr_number=request.pr_number,
        local_path=request.local_path,
        fork_owner=fork_owner,
        base_ref=request.base_ref,
    )

    # ------------------------------------------------------------------
    # Step 1 — Prepare workspace (mode-dependent)
    #
    # On resume: if a workspace_prepared checkpoint already exists for this
    # run_id, skip fork/clone and restore workspace state from the event log.
    # ------------------------------------------------------------------
    wp_event = session.last_event("workspace_prepared")
    if wp_event:
        # Resume path: reuse the previously prepared workspace
        workspace_path = wp_event["workspace_path"]
        branch_name = wp_event["branch_name"]
        fork_owner = wp_event.get("fork_owner", fork_owner)
        fork_repo = wp_event.get("fork_repo", f"{fork_owner}/{repo_name}")
        logger.info(
            "Resuming run %s: workspace=%s branch=%s",
            request.run_id, workspace_path, branch_name,
        )
    elif request.mode == "existing-repo":
        # Clone the user's local checkout into the isolated workspace (~/.tfdev/ws/).
        # Only committed changes are visible in the clone; uncommitted work must be
        # committed locally first.
        src_path = str(Path(request.local_path).resolve())
        if not fork_owner:
            fork_owner = _get_fork_owner_from_remote(src_path)
        fork_repo = f"{fork_owner}/{repo_name}" if fork_owner else repo_name

        clone_result = _clone_local_repo_to_workspace(src_path, request.run_id, repo_name)
        if clone_result.get("status") != "cloned":
            return {"outcome": "error", "errors": [f"local clone failed: {clone_result}"]}
        workspace_path = clone_result["clone_path"]

        branch_name = _get_current_branch(workspace_path)
        if not branch_name or not _BRANCH_RE.match(branch_name):
            # Current branch doesn't match agent convention - create one
            branch_name = request.auto_branch_name()
            branch_result = json.loads(create_branch(workspace_path, branch_name, request.base_ref))
            if branch_result.get("status") != "created":
                return {"outcome": "error", "errors": [f"create_branch failed: {branch_result}"]}

        logger.info("existing-repo mode: workspace=%s branch=%s", workspace_path, branch_name)
        session.append(
            "workspace_prepared",
            workspace_path=workspace_path,
            branch_name=branch_name,
            fork_owner=fork_owner,
            fork_repo=fork_repo,
        )

    elif request.mode == "existing-pr":
        # Determine which repo hosts the PR.
        # With --fork-owner: look in the fork (fork PRs / draft PRs within the fork).
        # Without --fork-owner: look in upstream (upstream-targeted PRs from a fork).
        pr_lookup_repo = (
            f"{fork_owner}/{repo_name}" if fork_owner else request.upstream_repo
        )
        pr_details = _get_pr_details(pr_lookup_repo, request.pr_number)
        if not pr_details:
            return {
                "outcome": "error",
                "errors": [
                    f"Could not fetch PR #{request.pr_number} from {pr_lookup_repo}"
                ],
            }

        head_ref_name: str = pr_details.get("headRefName", "")
        head_repo: dict = pr_details.get("headRepository", {})
        pr_fork_repo: str = head_repo.get("nameWithOwner", "")
        pr_fork_owner: str = (head_repo.get("owner") or {}).get("login", "")

        if not head_ref_name or not pr_fork_repo:
            return {
                "outcome": "error",
                "errors": [
                    f"Incomplete PR details for #{request.pr_number}: "
                    f"headRefName={head_ref_name!r}, headRepository={pr_fork_repo!r}"
                ],
            }

        if not fork_owner:
            fork_owner = pr_fork_owner or pr_fork_repo.split("/")[0]
        fork_repo = pr_fork_repo

        # Clone the fork at the PR head branch
        clone_result = json.loads(clone_fork(fork_repo, request.run_id, head_ref_name))
        if clone_result.get("status") != "cloned":
            return {"outcome": "error", "errors": [f"clone_fork (existing-pr) failed: {clone_result}"]}

        workspace_path = clone_result["clone_path"]

        # Create an agent-compliant branch from the PR head so push_branch guardrails pass.
        # The existing PR is kept as the tracked PR so CI evidence and flip-ready target it.
        branch_name = request.auto_branch_name()
        branch_result = json.loads(create_branch(workspace_path, branch_name, head_ref_name))
        if branch_result.get("status") != "created":
            return {"outcome": "error", "errors": [f"create_branch (existing-pr) failed: {branch_result}"]}

        logger.info(
            "existing-pr mode: workspace=%s new branch=%s (from PR head %s)",
            workspace_path, branch_name, head_ref_name,
        )
        session.append(
            "workspace_prepared",
            workspace_path=workspace_path,
            branch_name=branch_name,
            fork_owner=fork_owner,
            fork_repo=fork_repo,
        )

    else:
        # issue-driven — original fork/clone behaviour
        fork_result = json.loads(ensure_fork(request.upstream_repo, fork_owner))
        if fork_result.get("status") == "error":
            return {"outcome": "error", "errors": [f"ensure_fork failed: {fork_result}"]}

        fork_owner = fork_result.get("fork_owner") or fork_owner
        fork_repo = fork_result.get("fork_repo") or f"{fork_owner}/{repo_name}"

        sync_result = json.loads(
            sync_fork_default_branch(fork_repo, request.upstream_repo, request.base_ref)
        )
        if sync_result.get("status") == "error":
            if sync_result.get("reason") == "fork_diverged":
                return {
                    "outcome": "escalated",
                    "escalation_reason": "fork diverged from upstream — manual intervention required",
                }
            return {"outcome": "error", "errors": [f"sync_fork_default_branch failed: {sync_result}"]}

        clone_result = json.loads(clone_fork(fork_repo, request.run_id, request.base_ref))
        if clone_result.get("status") != "cloned":
            return {"outcome": "error", "errors": [f"clone_fork failed: {clone_result}"]}

        workspace_path = clone_result["clone_path"]

        # Step 2 — Create branch
        issue_title = ""
        if request.issue_number is not None:
            issue_title = _get_issue_title(request.upstream_repo, request.issue_number)

        slug = issue_title.lower().replace(" ", "-")[:40] if issue_title else ""
        branch_name = request.auto_branch_name(slug)

        branch_result = json.loads(create_branch(workspace_path, branch_name, request.base_ref))
        if branch_result.get("status") != "created":
            return {"outcome": "error", "errors": [f"create_branch failed: {branch_result}"]}

        session.append(
            "workspace_prepared",
            workspace_path=workspace_path,
            branch_name=branch_name,
            fork_owner=fork_owner,
            fork_repo=fork_repo,
        )

    # Fetch issue context once for the reviewer
    issue_context = ""
    if request.issue_number is not None:
        issue_context = _get_issue_context(request.upstream_repo, request.issue_number)

    # Build developer instructions once (module skill + additive overlay)
    developer_instructions = _build_developer_instructions(workspace_path)

    # For existing-pr mode, prime pr_url / pr_number from the known PR
    pr_url: str = ""
    pr_number: int | None = None
    if request.mode == "existing-pr" and request.pr_number is not None:
        pr_url = f"https://github.com/{request.upstream_repo}/pull/{request.pr_number}"
        pr_number = request.pr_number

    # On resume, restore any PR that was already opened in a prior run
    if not pr_url:
        pr_event = session.last_event("pr_opened")
        if pr_event and pr_event.get("pr_url"):
            pr_url = pr_event["pr_url"]
            pr_number = pr_event.get("pr_number")
            logger.info("Resuming: restoring PR %s from event log", pr_url)

    # Step 3 — Developer → Reviewer → Push loop (max _MAX_ATTEMPTS)
    attempts: list[FixAttempt] = []
    previous_feedback: list[str] = []

    for attempt_number in range(1, _MAX_ATTEMPTS + 1):
        logger.info("Pipeline attempt %d/%d", attempt_number, _MAX_ATTEMPTS)
        attempt = FixAttempt(attempt_number=attempt_number, branch_name=branch_name)
        session.append("attempt_started", attempt_number=attempt_number)

        # a. Developer turn
        developer = create_specialist("developer", developer_instructions, DEVELOPER_TOOLS)
        dev_message = _build_developer_message(
            request, workspace_path, attempt_number, previous_feedback
        )

        try:
            await developer.get_response(dev_message)
        except Exception as exc:
            logger.error("Developer agent error on attempt %d: %s", attempt_number, exc)
            attempt.reviewer_verdict = "rejected"
            attempt.reviewer_notes = f"Developer agent error: {exc}"
            attempts.append(attempt)
            previous_feedback = [f"Developer agent error: {exc}"]
            continue

        attempt.commit_sha = _git_head_sha(workspace_path)

        # b. Get diff of the last commit
        diff = _git_diff_last_commit(workspace_path)
        if not diff.strip():
            logger.warning("Attempt %d: no diff produced", attempt_number)
            attempt.reviewer_verdict = "rejected"
            attempt.reviewer_notes = "Developer made no changes"
            attempts.append(attempt)
            previous_feedback = [
                "Developer made no changes — write code and commit before responding"
            ]
            continue

        attempt.diff_summary = diff[:500]

        # c. Reviewer turn
        review: DiffReview = await review_diff(
            diff=diff,
            task_description=request.to_agent_message(),
            issue_context=issue_context,
            branch_name=branch_name,
        )
        attempt.reviewer_verdict = review.verdict
        attempt.reviewer_notes = review.reviewer_notes
        logger.info("Reviewer verdict (attempt %d): %s", attempt_number, review.verdict)
        session.append(
            "diff_reviewed",
            attempt_number=attempt_number,
            verdict=review.verdict,
            issues=list(review.issues or []),
        )

        # d. Reviewer approved — push, open PR, dispatch CI
        if review.approved:
            push_result = json.loads(
                push_branch(workspace_path, branch_name, fork_owner, request.run_id)
            )
            if push_result.get("status") != "pushed":
                err = push_result.get("error", str(push_result))
                logger.error("Push failed on attempt %d: %s", attempt_number, err)
                attempt.reviewer_notes += f" | push failed: {err}"
                attempts.append(attempt)
                previous_feedback = [f"Push failed: {err} — resolve workspace state and retry"]
                continue

            # Open draft PR on first successful push (skip if PR already exists)
            if not pr_url:
                issue_title = ""
                if request.issue_number is not None:
                    issue_title = _get_issue_title(request.upstream_repo, request.issue_number)
                pr_title = (
                    f"fix: {issue_title}"
                    if issue_title
                    else f"fix: automated fix for #{request.issue_number}"
                    if request.issue_number is not None
                    else f"chore: automated changes (run {request.run_id})"
                )
                pr_result = json.loads(
                    create_pull_request(
                        repo=request.upstream_repo,
                        branch=f"{fork_owner}:{branch_name}",
                        title=pr_title,
                        body=_build_pr_body(request),
                        base=request.base_ref,
                        draft=True,
                    )
                )
                if pr_result.get("status") == "created":
                    pr_url = pr_result["url"]
                    pr_number = _pr_number_from_url(pr_url)
                    logger.info("Draft PR opened: %s", pr_url)
                    session.append("pr_opened", pr_url=pr_url, pr_number=pr_number)
                else:
                    logger.warning("create_pull_request failed: %s", pr_result)

            # Dispatch module checks (quick CI, timeout 600 s)
            checks_result = json.loads(
                dispatch_module_checks(
                    module_repo=fork_repo,
                    module_ref=branch_name,
                    run_id=request.run_id,
                )
            )
            attempt.ci_dispatched = True
            attempt.ci_run_url = checks_result.get("run_url", "")

            if checks_result.get("conclusion") != "success":
                ci_msg = (
                    f"module-checks failed ({checks_result.get('conclusion', 'unknown')}): "
                    f"{checks_result.get('run_url', '')}"
                )
                logger.warning("Attempt %d: %s", attempt_number, ci_msg)
                attempt.ci_conclusion = checks_result.get("conclusion", "failure")
                attempts.append(attempt)
                previous_feedback = [ci_msg, "Fix lint/pre-commit errors flagged by CI"]
                continue

            # Dispatch e2e tests (timeout 3600 s)
            e2e_result = json.loads(
                dispatch_module_e2e(
                    module_repo=fork_repo,
                    module_ref=branch_name,
                    run_id=request.run_id,
                )
            )
            attempt.ci_run_url = e2e_result.get("run_url", attempt.ci_run_url)

            if e2e_result.get("conclusion") != "success":
                ci_msg = (
                    f"module-e2e failed ({e2e_result.get('conclusion', 'unknown')}): "
                    f"{e2e_result.get('run_url', '')}"
                )
                logger.warning("Attempt %d: %s", attempt_number, ci_msg)
                attempt.ci_conclusion = e2e_result.get("conclusion", "failure")
                attempts.append(attempt)
                previous_feedback = [ci_msg, "Fix e2e deployment failures flagged by CI"]
                continue

            # CI green — update PR body and flip to ready
            attempt.ci_conclusion = "success"
            evidence_content = (
                f"✅ module-checks: {checks_result.get('run_url', 'passed')}\n"
                f"✅ module-e2e: {e2e_result.get('run_url', 'passed')}\n"
                f"Run ID: {request.run_id}"
            )
            if pr_number is not None:
                update_result = json.loads(
                    update_pr_body_section(
                        repo=request.upstream_repo,
                        pr_number=pr_number,
                        section_name="evidence",
                        content=evidence_content,
                    )
                )
                logger.info("PR body updated: %s", update_result.get("status"))
                flip_result = json.loads(
                    flip_pr_ready(repo=request.upstream_repo, pr_number=pr_number)
                )
                logger.info("PR flipped ready: %s", flip_result.get("status"))

            attempts.append(attempt)
            session.append(
                "pipeline_completed",
                outcome="success",
                pr_url=pr_url,
                attempt_number=attempt_number,
            )
            return {
                "outcome": "success",
                "pr_url": pr_url,
                "attempts": [asdict(a) for a in attempts],
            }

        # e. Reviewer rejected / needs_changes — feed issues back for next attempt
        else:
            feedback = list(review.issues or [])
            if review.suggestions:
                feedback.extend(review.suggestions)
            if not feedback:
                feedback = [f"Reviewer verdict: {review.verdict}. {review.reviewer_notes}"]
            previous_feedback = feedback
            attempts.append(attempt)

    # Step 3f — Max attempts exhausted: escalate
    last = attempts[-1] if attempts else None
    escalation_reason = (
        f"Failed to produce an approved, CI-passing implementation after {_MAX_ATTEMPTS} "
        f"attempts. Last reviewer verdict: {last.reviewer_verdict if last else 'none'}. "
        f"Last CI conclusion: {last.ci_conclusion if last else 'none'}."
    )

    escalation_comment = (
        f"## Automated Fix Escalation\n\n"
        f"Agent run `{request.run_id}` could not resolve this issue automatically "
        f"after {_MAX_ATTEMPTS} attempts.\n\n"
        f"**Reason**: {escalation_reason}\n\n"
        f"**Attempts**:\n"
        + "\n".join(
            f"- Attempt {a.attempt_number}: reviewer=`{a.reviewer_verdict}`, "
            f"ci=`{a.ci_conclusion or 'not dispatched'}`"
            for a in attempts
        )
    )

    if pr_number is not None:
        add_issue_comment(
            repo=request.upstream_repo,
            issue_number=pr_number,
            comment=escalation_comment,
        )
    elif request.issue_number is not None:
        add_issue_comment(
            repo=request.upstream_repo,
            issue_number=request.issue_number,
            comment=escalation_comment,
        )

    return {
        "outcome": "escalated",
        "pr_url": pr_url,
        "attempts": [asdict(a) for a in attempts],
        "escalation_reason": escalation_reason,
    }
