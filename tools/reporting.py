"""Reporting tools for generating structured test reports and GitHub content.

These tools format findings from the analysis phase into actionable
outputs — test reports, issue bodies, and UPGRADE.md suggestions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_framework import ai_function

from tools.terraform import _workspace_path


@ai_function
def generate_test_report(
    workspace_id: str,
    module_source: str,
    module_version: str = "",
    deploy_results_json: str = "[]",
    findings_json: str = "[]",
) -> str:
    """Generate a structured markdown test report.

    Produces a report summarising which examples were tested, deploy
    outcomes, idempotency results, and findings from plan diff analysis.

    Args:
        workspace_id: Workspace id from create_workspace.
        module_source: Module source identifier (registry path or local path).
        module_version: Version tested (if applicable).
        deploy_results_json: JSON array of deploy result objects.
        findings_json: JSON array of analysis finding objects.

    Returns:
        JSON with the markdown report and a summary object.
    """
    try:
        deploy_results = json.loads(deploy_results_json)
        findings = json.loads(findings_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON input: {e}"})

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Module Test Report",
        f"",
        f"**Module:** `{module_source}`",
        f"**Version:** {module_version or 'N/A'}",
        f"**Date:** {timestamp}",
        f"**Workspace:** `{workspace_id}`",
        f"",
    ]

    # Deploy results table
    if deploy_results:
        lines.extend([
            "## Deploy Results",
            "",
            "| Example | Status | Resources | Idempotency | Errors |",
            "|---------|--------|-----------|-------------|--------|",
        ])
        for r in deploy_results:
            idemp = r.get("idempotency", {})
            idemp_status = idemp.get("status", "n/a") if idemp else "n/a"
            errors = "; ".join(r.get("errors", [])) or "—"
            lines.append(
                f"| {r.get('example', '?')} "
                f"| {_status_icon(r.get('status', '?'))} {r.get('status', '?')} "
                f"| {r.get('resources_created', 0)} "
                f"| {_status_icon(idemp_status)} {idemp_status} "
                f"| {errors} |"
            )
        lines.append("")

    # Findings
    if findings:
        lines.extend(["## Findings", ""])
        for i, f in enumerate(findings, 1):
            severity = f.get("severity", "info")
            icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
            lines.extend([
                f"### {icon} Finding {i}: {f.get('category', 'unknown')} ({severity})",
                "",
                f"{f.get('description', 'No description')}",
                "",
            ])
            if f.get("upgrade_md_reference"):
                lines.append(f"> **UPGRADE.md reference:** {f['upgrade_md_reference']}")
                lines.append("")

    # Summary
    total_deploys = len(deploy_results)
    successes = sum(1 for r in deploy_results if r.get("status") == "success")
    failures = sum(1 for r in deploy_results if r.get("status") == "failure")
    idemp_fails = sum(
        1 for r in deploy_results
        if r.get("idempotency", {}).get("status") == "fail"
    )
    critical_findings = sum(1 for f in findings if f.get("severity") == "critical")

    lines.extend([
        "## Summary",
        "",
        f"- **Deploys:** {successes}/{total_deploys} succeeded",
        f"- **Failures:** {failures}",
        f"- **Idempotency failures:** {idemp_fails}",
        f"- **Critical findings:** {critical_findings}",
        f"- **Total findings:** {len(findings)}",
    ])

    report_md = "\n".join(lines)

    # Save report to workspace
    report_path = _workspace_path(workspace_id) / "test-report.md"
    report_path.write_text(report_md)

    return json.dumps({
        "report": report_md,
        "report_path": "test-report.md",
        "summary": {
            "total_deploys": total_deploys,
            "successes": successes,
            "failures": failures,
            "idempotency_failures": idemp_fails,
            "critical_findings": critical_findings,
            "total_findings": len(findings),
        },
    })


@ai_function
def generate_issue_body(
    finding_category: str,
    finding_description: str,
    module_source: str,
    module_version: str = "",
    evidence_json: str = "{}",
) -> str:
    """Format a finding as a GitHub issue body ready for filing.

    Args:
        finding_category: Category (breaking_change, idempotency, missing_doc, suggestion).
        finding_description: Description of the finding.
        module_source: Module source identifier.
        module_version: Version where the issue was found.
        evidence_json: JSON object with supporting evidence.

    Returns:
        JSON with the formatted issue title and body.
    """
    try:
        evidence = json.loads(evidence_json)
    except json.JSONDecodeError:
        evidence = {}

    category_labels = {
        "breaking_change": ("Breaking Change", "bug"),
        "idempotency": ("Idempotency Issue", "bug"),
        "missing_doc": ("Missing Documentation", "documentation"),
        "suggestion": ("Improvement Suggestion", "enhancement"),
    }
    label_info = category_labels.get(finding_category, (finding_category, "bug"))

    title = f"[AVM Testing] {label_info[0]}: {finding_description[:80]}"
    body_lines = [
        f"## {label_info[0]}",
        "",
        f"**Module:** `{module_source}`",
        f"**Version:** {module_version or 'N/A'}",
        f"**Detected by:** AVM Module Testing Agent",
        "",
        "### Description",
        "",
        finding_description,
        "",
    ]

    if evidence:
        body_lines.extend([
            "### Evidence",
            "",
            "```json",
            json.dumps(evidence, indent=2),
            "```",
            "",
        ])

    body_lines.extend([
        "### Reproduction",
        "",
        f"1. Clone the module at version `{module_version or 'latest'}`",
        "2. Run `terraform init` in the affected example",
        "3. Run `terraform apply`",
        "4. Observe the issue described above",
        "",
        "---",
        "*This issue was automatically filed by the AVM Module Testing Agent.*",
    ])

    return json.dumps({
        "title": title,
        "body": "\n".join(body_lines),
        "suggested_labels": label_info[1],
    })


@ai_function
def generate_upgrade_doc_suggestion(
    observed_changes_json: str,
    existing_upgrade_md: str = "",
    module_version_from: str = "",
    module_version_to: str = "",
) -> str:
    """Propose UPGRADE.md additions based on observed plan diffs.

    Compares observed terraform plan changes against existing UPGRADE.md
    content and suggests additions for undocumented breaking changes.

    Args:
        observed_changes_json: JSON array of resource changes from plan diff.
        existing_upgrade_md: Current UPGRADE.md content (empty if none exists).
        module_version_from: Version being upgraded from.
        module_version_to: Version being upgraded to.

    Returns:
        JSON with suggested UPGRADE.md additions and matched/unmatched changes.
    """
    try:
        changes = json.loads(observed_changes_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid observed_changes_json"})

    # Categorise changes
    breaking = [c for c in changes if c.get("actions") in [["delete", "create"], ["create", "delete"]]]
    updates = [c for c in changes if c.get("actions") == ["update"]]
    deletes = [c for c in changes if c.get("actions") == ["delete"]]

    # Check which changes are documented
    documented = []
    undocumented = []
    for change in breaking + deletes:
        addr = change.get("address", "")
        resource_type = change.get("type", "")
        if existing_upgrade_md and (addr in existing_upgrade_md or resource_type in existing_upgrade_md):
            documented.append(change)
        else:
            undocumented.append(change)

    # Generate suggestion
    suggestion_lines = []
    if undocumented:
        version_header = f"{module_version_from} → {module_version_to}" if module_version_from else "Latest"
        suggestion_lines.extend([
            f"## Upgrade from {version_header}",
            "",
            "### Breaking Changes",
            "",
        ])
        for change in undocumented:
            suggestion_lines.append(
                f"- `{change.get('address', '?')}` — action: {change.get('actions', '?')}"
            )
        suggestion_lines.append("")

    return json.dumps({
        "has_suggestions": len(undocumented) > 0,
        "documented_changes": len(documented),
        "undocumented_changes": len(undocumented),
        "suggestion_md": "\n".join(suggestion_lines) if suggestion_lines else "",
        "breaking_changes": len(breaking),
        "updates": len(updates),
        "deletes": len(deletes),
    }, indent=2)


def _status_icon(status: str) -> str:
    """Return an emoji icon for a status value."""
    return {
        "success": "✅",
        "failure": "❌",
        "timeout": "⏱️",
        "skipped": "⏭️",
        "pass": "✅",
        "fail": "❌",
        "error": "⚠️",
    }.get(status, "⚪")
