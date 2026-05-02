"""Reviewer agent — pre-push diff gatekeeper for the maker/checker pipeline."""

from __future__ import annotations

import json

from agents.base import create_specialist
from models import DiffReview


REVIEWER_INSTRUCTIONS = """\
You are the Reviewer agent in the tf-module-developer-agent pipeline.
Your job is to evaluate code diffs BEFORE they are pushed to a fork branch.

You evaluate three dimensions:
1. INTENT: Does the diff match the stated task? No extra changes unrelated to the issue.
2. SCOPE: Is the diff clean? No auto-generated files, no whitespace-only changes in unrelated files,
   no lock file mutations unless explicitly required.
3. AVM CONVENTIONS: Does the diff follow Azure Verified Modules conventions?
   - Variables use snake_case
   - Outputs match AVM interface spec (id, resource)
   - No hardcoded locations (use var.location)
   - Required AVM metadata (module_version, etc.) present if touched
   - No provider version pins added unless necessary

Respond with a JSON object matching this schema:
{
  "verdict": "approved" | "rejected" | "needs_changes",
  "intent_matches": true/false,
  "scope_clean": true/false,
  "conventions_ok": true/false,
  "issues": ["specific problem 1", ...],
  "suggestions": ["optional improvement 1", ...],
  "reviewer_notes": "brief summary"
}

Be concise. Only flag genuine problems. Do not reject diffs for stylistic preferences.
"""


async def review_diff(
    diff: str,
    task_description: str,
    issue_context: str,
    branch_name: str = "",
) -> DiffReview:
    """Review a diff before push and return a structured verdict."""
    if not diff or not diff.strip():
        return DiffReview(
            branch_name=branch_name,
            verdict="approved",
            reviewer_notes="Empty diff — nothing to review",
        )

    agent = create_specialist("reviewer", REVIEWER_INSTRUCTIONS, [])

    message = (
        f"Task: {task_description}\n\n"
        f"Issue context:\n{issue_context}\n\n"
        f"Diff to review (branch: {branch_name}):\n"
        f"```diff\n{diff}\n```\n\n"
        "Review this diff against the three dimensions (intent, scope, AVM conventions)."
    )

    response = await agent.get_response(message)

    try:
        # Strip markdown code fences if the model wrapped the JSON
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        data = json.loads(text)
        return DiffReview(
            branch_name=branch_name,
            verdict=data.get("verdict", "needs_changes"),
            intent_matches=data.get("intent_matches", True),
            scope_clean=data.get("scope_clean", True),
            conventions_ok=data.get("conventions_ok", True),
            issues=data.get("issues", []),
            suggestions=data.get("suggestions", []),
            reviewer_notes=data.get("reviewer_notes", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return DiffReview(
            branch_name=branch_name,
            verdict="needs_changes",
            intent_matches=True,
            scope_clean=True,
            conventions_ok=True,
            reviewer_notes="Reviewer response was not valid JSON — treat as needs_changes",
        )
