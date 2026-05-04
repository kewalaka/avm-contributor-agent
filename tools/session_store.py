"""SessionStore — append-only JSONL event log for pipeline crash recovery.

The event log is NOT the model's context window.  It is a durable backing
store written to ``~/.tfdev/ws/<run_id>/events.jsonl`` that the harness
slices on demand.  This decoupling enables crash recovery without re-feeding
the full history to the model.

Key insight (per the long-running-agents pattern): the model sees only the
slice of events it needs for the current decision, while the full log
persists independently and can be replayed to reconstruct pipeline state.

Usage::

    store = SessionStore(run_id)
    store.append("pipeline_started", request=request_dict)
    store.append("workspace_prepared", workspace_path=workspace_path)
    events = store.get_events(start=0, end=10)
    last = store.last_checkpoint()

    # Resume an existing run:
    store = SessionStore.wake(run_id)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_framework import ai_function


class SessionStore:
    """Append-only event log backed by ``~/.tfdev/ws/<run_id>/events.jsonl``.

    Each event is a JSON object with at minimum::

        {"ts": "<ISO-8601>", "type": "<event_type>", ...data}

    The file is opened in append mode for each write so it is safe to
    use from a single process; concurrent multi-process writes are not
    supported (no locking).
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        ws_dir = Path.home() / ".tfdev" / "ws" / run_id
        ws_dir.mkdir(parents=True, exist_ok=True)
        self._path: Path = ws_dir / "events.jsonl"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, event_type: str, **data) -> None:
        """Append a structured event to the log.

        Args:
            event_type: Short identifier, e.g. ``"pipeline_started"``.
            **data: Arbitrary key/value payload serialised as JSON.
        """
        event: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **data,
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_events(self, start: int = 0, end: int | None = None) -> list[dict]:
        """Return events from the log, optionally sliced ``[start:end]``.

        Malformed lines are silently skipped so a partially-written tail
        record does not break recovery.
        """
        if not self._path.exists():
            return []
        events: list[dict] = []
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    try:
                        events.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        pass
        return events[start:end]

    def last_checkpoint(self) -> dict | None:
        """Return the most recent event whose ``type`` is ``"checkpoint"``.

        Returns ``None`` if no checkpoint has been written yet.
        """
        events = [e for e in self.get_events() if e.get("type") == "checkpoint"]
        return events[-1] if events else None

    def find_event(self, event_type: str) -> dict | None:
        """Return the first event of the given type, or ``None``."""
        for e in self.get_events():
            if e.get("type") == event_type:
                return e
        return None

    def last_event(self, event_type: str) -> dict | None:
        """Return the most recent event of the given type, or ``None``."""
        matches = [e for e in self.get_events() if e.get("type") == event_type]
        return matches[-1] if matches else None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def wake(cls, run_id: str) -> "SessionStore":
        """Load (or create) the :class:`SessionStore` for *run_id*.

        If the events file already exists the returned store can be used
        to read prior events and continue appending.  If the file does
        not exist a new empty store is returned, identical to
        ``SessionStore(run_id)``.
        """
        return cls(run_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Absolute path to the ``events.jsonl`` file."""
        return self._path


# ---------------------------------------------------------------------------
# @ai_function tool exposed to the Developer agent
# ---------------------------------------------------------------------------

@ai_function
def get_session_events(run_id: str, start: int = 0, end: int = 50) -> str:
    """Retrieve pipeline events for a run from the durable event log.

    Use this to review what happened in a prior pipeline run without
    re-feeding the full history into the context window.

    Args:
        run_id: The pipeline run identifier (printed at pipeline start).
        start: Start index, 0-based (default 0).
        end: End index, exclusive (default 50).

    Returns:
        JSON object with ``run_id``, ``events`` list, and ``count``.
    """
    store = SessionStore(run_id)
    events = store.get_events(start=start, end=end)
    return json.dumps(
        {"run_id": run_id, "events": events, "count": len(events)},
        indent=2,
    )
