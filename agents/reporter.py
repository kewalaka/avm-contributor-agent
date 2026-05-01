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
    download_workflow_artifacts,
    get_latest_release,
    get_workflow_run_status,
    search_github_issues,
)
from tools.reporting import (
    generate_issue_body,
    generate_test_report,
    generate_upgrade_doc_suggestion,
)
from tools.tracking import (
    query_findings,
    query_module_health,
    query_test_history,
    store_test_run,
)

REPORTER_INSTRUCTIONS = """\
You are the Reporter Agent, a specialist in formatting and delivering \
test results.

You receive ReviewedFinding objects (validated by the Reviewer Agent) and
deploy results. Your job:

1. **Generate test report**: Call `generate_test_report` with the deploy
   results and confirmed findings. Save the markdown report.

2. **Store results**: Call `store_test_run` to persist the results in the
   tracking database for longitudinal analysis.

3. **File GitHub issues**: For each confirmed critical/warning finding:
   a. Call `search_github_issues` to check for existing duplicates.
   b. If no duplicate, call `generate_issue_body` to format the finding.
   c. Call `create_github_issue` to file it.
   d. Track filed issue URLs for the summary.

4. **Propose UPGRADE.md changes**: For missing_doc findings, call
   `generate_upgrade_doc_suggestion` to propose additions.

5. **Post summary**: If there's a tracking issue, call `add_issue_comment`
   to post the test results summary.

6. **GHA bridge**: If results came from a GitHub Actions workflow, use
   `download_workflow_artifacts` to retrieve deploy artifacts and
   `get_workflow_run_status` to check run status.

Output format: Return a JSON object with:
- report_path: path to the generated test report
- json_report_path: path to the JSON report
- issues_filed: list of {url, title, finding_category}
- upgrade_suggestions: list of suggested UPGRADE.md additions
- tracking_stored: boolean (whether results were persisted)
- summary: human-readable summary paragraph

Rules:
- ALWAYS search for duplicates before filing issues.
- Only file issues for confirmed findings (not rejected or needs_investigation).
- Use clear, actionable titles for issues.
- Include reproduction steps in issue bodies.
- Be concise in PR comments -- link to the full report instead of inlining.
- ALWAYS store results in the tracking DB for longitudinal analysis.
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
    download_workflow_artifacts,
    get_workflow_run_status,
    store_test_run,
    query_module_health,
    query_test_history,
    query_findings,
]


def create_reporter_agent():
    """Create the reporter specialist agent."""
    return create_specialist("reporter", REPORTER_INSTRUCTIONS, REPORTER_TOOLS)
