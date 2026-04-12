"""Reporter agent -- formats findings and delivers via GitHub.

This agent has the smallest context of all specialists: it receives
only validated findings and formats them into reports, issues, and
PR comments. It handles GitHub interactions.
"""

from __future__ import annotations

from agents.base import create_specialist

from tools.github_ops import (
    add_issue_comment,
    create_github_issue,
    create_pull_request,
    get_latest_release,
    search_github_issues,
)
from tools.reporting import (
    generate_issue_body,
    generate_test_report,
    generate_upgrade_doc_suggestion,
)

REPORTER_INSTRUCTIONS = """\
You are the Reporter Agent, a specialist in formatting and delivering \
test results.

You receive ReviewedFinding objects (validated by the Reviewer Agent) and
deploy results. Your job:

1. **Generate test report**: Call `generate_test_report` with the deploy
   results and confirmed findings. Save the markdown report.

2. **File GitHub issues**: For each confirmed critical/warning finding:
   a. Call `search_github_issues` to check for existing duplicates.
   b. If no duplicate, call `generate_issue_body` to format the finding.
   c. Call `create_github_issue` to file it.
   d. Track filed issue URLs for the summary.

3. **Propose UPGRADE.md changes**: For missing_doc findings, call
   `generate_upgrade_doc_suggestion` to propose additions.

4. **Post summary**: If there's a tracking issue, call `add_issue_comment`
   to post the test results summary.

Output format: Return a JSON object with:
- report_path: path to the generated test report
- issues_filed: list of {url, title, finding_category}
- upgrade_suggestions: list of suggested UPGRADE.md additions
- summary: human-readable summary paragraph

Rules:
- ALWAYS search for duplicates before filing issues.
- Only file issues for confirmed findings (not rejected or needs_investigation).
- Use clear, actionable titles for issues.
- Include reproduction steps in issue bodies.
- Be concise in PR comments -- link to the full report instead of inlining.
"""

REPORTER_TOOLS = [
    generate_test_report,
    generate_issue_body,
    generate_upgrade_doc_suggestion,
    create_github_issue,
    create_pull_request,
    add_issue_comment,
    search_github_issues,
    get_latest_release,
]


def create_reporter_agent():
    """Create the reporter specialist agent."""
    return create_specialist("reporter", REPORTER_INSTRUCTIONS, REPORTER_TOOLS)
