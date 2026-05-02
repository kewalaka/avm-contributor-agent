"""Orchestrator — drives the Developer→Reviewer→CI maker/checker pipeline."""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3

_DEVELOPER_INSTRUCTIONS_FALLBACK = """\
You are the Developer agent in the tf-module-developer-agent pipeline.
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


def _load_module_skill_content(workspace_path: str) -> str | None:
    """Scan the module workspace for an AVM skill file and return its content."""
    ws = Path(workspace_path)
    candidates = [
        ws / ".agents" / "skills" / "AVM-Terraform-Development" / "SKILL.md",
        ws / ".agents" / "skills" / "AVM-Terraform-Development.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            logger.info("Found module skill: %s", candidate)
            return candidate.read_text(encoding="utf-8")
    # Fallback: any .md in the skill directory
    skill_dir = ws / ".agents" / "skills" / "AVM-Terraform-Development"
    if skill_dir.is_dir():
        for md_file in sorted(skill_dir.glob("*.md")):
            logger.info("Found module skill (fallback): %s", md_file)
            return md_file.read_text(encoding="utf-8")
    logger.warning("No module skill found in %s — using additive instructions only", workspace_path)
    return None


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

    # Step 1 — Prepare fork + workspace
    repo_name = request.upstream_repo.split("/")[-1]
    fork_owner = request.fork_owner or ""

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

    # Fetch issue context once for the reviewer
    issue_context = ""
    if request.issue_number is not None:
        issue_context = _get_issue_context(request.upstream_repo, request.issue_number)

    # Build developer instructions once (module skill + additive overlay)
    developer_instructions = _build_developer_instructions(workspace_path)

    # Step 3 — Developer → Reviewer → Push loop (max _MAX_ATTEMPTS)
    attempts: list[FixAttempt] = []
    pr_url: str = ""
    pr_number: int | None = None
    previous_feedback: list[str] = []

    for attempt_number in range(1, _MAX_ATTEMPTS + 1):
        logger.info("Pipeline attempt %d/%d", attempt_number, _MAX_ATTEMPTS)
        attempt = FixAttempt(attempt_number=attempt_number, branch_name=branch_name)

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

            # Open draft PR on first successful push
            if not pr_url:
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
