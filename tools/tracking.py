"""Tracking storage for longitudinal test results.

Tier 3 of the output architecture: persistent, cross-module tracking
of test runs, example results, and findings over time.

This module provides a local SQLite implementation for development.
For production, swap the backend to Azure Table Storage or Cosmos DB
by implementing the same TrackingStore interface.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from agent_framework import tool

# Default DB path -- override via TRACKING_DB_PATH env var
_DEFAULT_DB = "tracking.db"


def _db_path() -> str:
    return os.environ.get("TRACKING_DB_PATH", _DEFAULT_DB)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS test_runs (
            run_id TEXT PRIMARY KEY,
            module_source TEXT NOT NULL,
            module_version TEXT DEFAULT '',
            base_ref TEXT DEFAULT 'main',
            head_ref TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            total_examples INTEGER DEFAULT 0,
            passed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            findings_count INTEGER DEFAULT 0,
            critical_findings INTEGER DEFAULT 0,
            report_path TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS example_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES test_runs(run_id),
            example TEXT NOT NULL,
            status TEXT NOT NULL,
            resources_created INTEGER DEFAULT 0,
            idempotency_status TEXT DEFAULT 'skipped',
            apply_duration_seconds REAL DEFAULT 0,
            errors TEXT DEFAULT '[]',
            plan_summary TEXT DEFAULT '{}',
            UNIQUE(run_id, example)
        );

        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES test_runs(run_id),
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence TEXT DEFAULT '{}',
            upgrade_md_reference TEXT DEFAULT '',
            verdict TEXT DEFAULT '',
            reviewer_notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS module_health (
            module_source TEXT PRIMARY KEY,
            last_run_id TEXT,
            last_status TEXT,
            last_tested_at TEXT,
            total_runs INTEGER DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0,
            last_success_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_module ON test_runs(module_source);
        CREATE INDEX IF NOT EXISTS idx_runs_status ON test_runs(status);
        CREATE INDEX IF NOT EXISTS idx_results_run ON example_results(run_id);
        CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
        CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
    """)


@tool
def store_test_run(
    run_id: str,
    module_source: str,
    module_version: str = "",
    base_ref: str = "main",
    head_ref: str = "",
    report_json: str = "{}",
) -> str:
    """Store a completed test run and its results in the tracking database.

    Parses the JSON report (from generate_test_report) and stores the
    run metadata, per-example results, and findings for longitudinal tracking.

    Args:
        run_id: Unique run identifier.
        module_source: Module source identifier.
        module_version: Version tested.
        base_ref: Base reference for the test.
        head_ref: Head reference (for upgrade testing).
        report_json: Full JSON test report from generate_test_report.

    Returns:
        JSON with storage confirmation and summary.
    """
    try:
        report = json.loads(report_json)
    except json.JSONDecodeError as e:
        return json.dumps({"status": "error", "details": f"Invalid report JSON: {e}"})

    now = datetime.now(timezone.utc).isoformat()
    summary = report.get("summary", {})
    deploy_results = report.get("deploy_results", [])
    findings = report.get("findings", [])

    status = "success" if summary.get("failures", 0) == 0 else "failure"

    conn = _connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO test_runs
               (run_id, module_source, module_version, base_ref, head_ref,
                status, started_at, completed_at,
                total_examples, passed, failed,
                findings_count, critical_findings, report_path, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, module_source, module_version, base_ref, head_ref,
                status, now, now,
                summary.get("total_deploys", 0),
                summary.get("successes", 0),
                summary.get("failures", 0),
                summary.get("total_findings", 0),
                summary.get("critical_findings", 0),
                report.get("json_report_path", ""),
                json.dumps(report.get("metadata", {})),
            ),
        )

        for r in deploy_results:
            conn.execute(
                """INSERT OR REPLACE INTO example_results
                   (run_id, example, status, resources_created,
                    idempotency_status, errors, plan_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    r.get("example", "unknown"),
                    r.get("status", "unknown"),
                    r.get("resources_created", 0),
                    r.get("idempotency", {}).get("status", "skipped")
                    if isinstance(r.get("idempotency"), dict) else "skipped",
                    json.dumps(r.get("errors", [])),
                    json.dumps(r.get("plan_summary", {})),
                ),
            )

        for f in findings:
            conn.execute(
                """INSERT INTO findings
                   (run_id, category, severity, description, evidence,
                    upgrade_md_reference, verdict, reviewer_notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    f.get("category", "unknown"),
                    f.get("severity", "info"),
                    f.get("description", ""),
                    json.dumps(f.get("evidence", {})),
                    f.get("upgrade_md_reference", ""),
                    f.get("verdict", ""),
                    f.get("reviewer_notes", ""),
                ),
            )

        # Update module health
        conn.execute(
            """INSERT INTO module_health
               (module_source, last_run_id, last_status, last_tested_at,
                total_runs, consecutive_failures, last_success_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(module_source) DO UPDATE SET
                 last_run_id = excluded.last_run_id,
                 last_status = excluded.last_status,
                 last_tested_at = excluded.last_tested_at,
                 total_runs = module_health.total_runs + 1,
                 consecutive_failures = CASE
                   WHEN excluded.last_status = 'failure'
                   THEN module_health.consecutive_failures + 1
                   ELSE 0 END,
                 last_success_at = CASE
                   WHEN excluded.last_status = 'success'
                   THEN excluded.last_tested_at
                   ELSE module_health.last_success_at END""",
            (
                module_source, run_id, status, now,
                1 if status == "failure" else 0,
                now if status == "success" else None,
            ),
        )

        conn.commit()
        return json.dumps({
            "status": "stored",
            "run_id": run_id,
            "examples_stored": len(deploy_results),
            "findings_stored": len(findings),
        })
    finally:
        conn.close()


@tool
def query_module_health(
    module_source: str = "",
    limit: int = 20,
) -> str:
    """Query module health status from the tracking database.

    Returns the latest test status for modules. Use without module_source
    to get an overview of all tracked modules.

    Args:
        module_source: Filter to a specific module (empty = all modules).
        limit: Maximum results to return.

    Returns:
        JSON array of module health records.
    """
    conn = _connect()
    try:
        if module_source:
            rows = conn.execute(
                "SELECT * FROM module_health WHERE module_source = ?",
                (module_source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM module_health ORDER BY last_tested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = [dict(row) for row in rows]
        return json.dumps(results, indent=2)
    finally:
        conn.close()


@tool
def query_test_history(
    module_source: str = "",
    limit: int = 10,
    status_filter: str = "",
) -> str:
    """Query historical test runs from the tracking database.

    Args:
        module_source: Filter to a specific module (empty = all).
        limit: Maximum results to return.
        status_filter: Filter by status (success, failure, or empty for all).

    Returns:
        JSON array of test run records.
    """
    conn = _connect()
    try:
        conditions = []
        params: list[Any] = []

        if module_source:
            conditions.append("module_source = ?")
            params.append(module_source)
        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM test_runs{where} ORDER BY started_at DESC LIMIT ?",
            params,
        ).fetchall()

        results = [dict(row) for row in rows]
        return json.dumps(results, indent=2)
    finally:
        conn.close()


@tool
def query_findings(
    module_source: str = "",
    severity: str = "",
    category: str = "",
    limit: int = 20,
) -> str:
    """Query findings across test runs for trend analysis.

    Args:
        module_source: Filter by module source (empty = all).
        severity: Filter by severity (critical, warning, info, or empty).
        category: Filter by category (breaking_change, idempotency, etc.).
        limit: Maximum results to return.

    Returns:
        JSON array of findings with run context.
    """
    conn = _connect()
    try:
        conditions = []
        params: list[Any] = []

        if module_source:
            conditions.append("t.module_source = ?")
            params.append(module_source)
        if severity:
            conditions.append("f.severity = ?")
            params.append(severity)
        if category:
            conditions.append("f.category = ?")
            params.append(category)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        rows = conn.execute(
            f"""SELECT f.*, t.module_source, t.module_version, t.started_at
                FROM findings f
                JOIN test_runs t ON f.run_id = t.run_id
                {where}
                ORDER BY t.started_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

        results = [dict(row) for row in rows]
        return json.dumps(results, indent=2)
    finally:
        conn.close()
