"""
platform_core/data_manager.py

"Data Manager — Logging & Storage (SQLite / Local)" from the platform
diagram. This is the production-grade upgrade over the in-memory-only
event history flagged earlier — violation events now survive a restart
and are queryable, without needing a full external database server.

Tier-agnostic: both Pro and Lite call this identically.
"""

import sqlite3
import json
import time
from contextlib import contextmanager


class DataManager:
    def __init__(self, db_path="data/platform.db"):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    camera_slot TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    person_id TEXT,
                    event_type TEXT NOT NULL,
                    severity TEXT,
                    screenshot_path TEXT,
                    extra_json TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON events(timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_camera
                ON events(camera_slot)
            """)

    def log_event(self, camera_slot, solution, event_type, person_id=None,
                  severity=None, screenshot_path=None, extra=None, timestamp=None):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO events
                   (timestamp, camera_slot, solution, person_id, event_type,
                    severity, screenshot_path, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp or time.time(),
                    str(camera_slot),
                    solution,
                    person_id,
                    event_type,
                    severity,
                    screenshot_path,
                    json.dumps(extra) if extra else None,
                ),
            )

    def get_recent_events(self, limit=100, camera_slot=None, solution=None):
        query = "SELECT * FROM events WHERE 1=1"
        params = []
        if camera_slot is not None:
            query += " AND camera_slot = ?"
            params.append(str(camera_slot))
        if solution is not None:
            query += " AND solution = ?"
            params.append(solution)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def clear_screenshot_paths(self):
        """Detach screenshot references from all events (used by
        DELETE /api/screenshots) without deleting the event rows
        themselves, since /api/alerts and the Dashboard read the same
        table. Returns the list of relative paths that were cleared,
        so the caller can delete the actual files on disk."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT screenshot_path FROM events WHERE screenshot_path IS NOT NULL"
            ).fetchall()
            conn.execute("UPDATE events SET screenshot_path = NULL")
        return [r["screenshot_path"] for r in rows]

    def delete_events_matching_camera_prefix(self, prefix):
        """Deletes events whose camera_slot starts with the given prefix
        (used by api/main.py's upload-file cleanup to purge DB rows for
        upload jobs whose files/screenshots have already been deleted by
        age -- e.g. prefix="upload_" matches camera_slot values like
        "upload_3f9a2b1c" set by VideoUploadProcessor). Returns the
        number of rows deleted."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM events WHERE camera_slot LIKE ?",
                (f"{prefix}%",),
            )
            return cursor.rowcount

    def get_event_counts(self, solution=None, since_timestamp=None):
        query = "SELECT event_type, COUNT(*) as count FROM events WHERE 1=1"
        params = []
        if solution is not None:
            query += " AND solution = ?"
            params.append(solution)
        if since_timestamp is not None:
            query += " AND timestamp >= ?"
            params.append(since_timestamp)
        query += " GROUP BY event_type"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return {r["event_type"]: r["count"] for r in rows}
