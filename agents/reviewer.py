"""Reviewer agent — pre-push diff gatekeeper for the maker/checker pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agents.base import create_specialist
from models import DiffReview

logger = logging.getLogger(__name__)

_REVIEWER_INSTRUCTIONS_FALLBACK = """\
You are the Reviewer agent in the tf-module-developer-agent pipeline.
Evaluate diffs for intent, scope, and AVM conventions before push.
Respond with a JSON object: verdict, intent_matches, scope_clean,
conventions_ok, issues, suggestions, reviewer_notes.
"""


def _build_reviewer_instructions() -> str:
    """Load the forked AVM review skill + reviewer additive overlay."""
    agents_dir = Path(__file__).parent

    skill_path = agents_dir / "skills" / "avm-review-skill.md"
    additive_path = agents_dir / "prompts" / "reviewer-additive.md"

    parts = []
    for path in (skill_path, additive_path):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning("Reviewer prompt file not found: %s", path)

    if parts:
        return "\n\n---\n\n".join(parts)
    logger.warning("No reviewer skill files found — using fallback instructions")
    return _REVIEWER_INSTRUCTIONS_FALLBACK


# Build once at module load (reviewer instructions are static)
_REVIEWER_INSTRUCTIONS = _build_reviewer_instructions()


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

    agent = create_specialist("reviewer", _REVIEWER_INSTRUCTIONS, [])

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
