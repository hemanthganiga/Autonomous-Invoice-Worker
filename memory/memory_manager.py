"""SQLite-backed task state and append-only execution audit events."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import DATABASE_PATH


class MemoryManager:
    """Persist task summaries and action events in the project's SQLite database."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT,
                    invoice_data_json TEXT,
                    verification_json TEXT,
                    final_report TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id),
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(agent_tasks)")
            }
            if "verification_json" not in columns:
                connection.execute("ALTER TABLE agent_tasks ADD COLUMN verification_json TEXT")

    def create_task(self, task_id: str, user_task: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO agent_tasks (task_id, user_task, status) VALUES (?, ?, ?)",
                (task_id, user_task, "queued"),
            )

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        plan: dict[str, Any] | None = None,
        invoice_data: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        final_report: str | None = None,
    ) -> None:
        values_by_column: dict[str, Any] = {}
        if status is not None:
            values_by_column["status"] = status
        if plan is not None:
            values_by_column["plan_json"] = json.dumps(plan, ensure_ascii=True)
        if invoice_data is not None:
            values_by_column["invoice_data_json"] = json.dumps(invoice_data, ensure_ascii=True)
        if verification is not None:
            values_by_column["verification_json"] = json.dumps(verification, ensure_ascii=True)
        if final_report is not None:
            values_by_column["final_report"] = final_report
        if not values_by_column:
            return

        assignments = [f"{column} = ?" for column in values_by_column]
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        parameters = [*values_by_column.values(), task_id]
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE agent_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown task ID: {task_id}")

    def log_event(
        self,
        task_id: str,
        event_type: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        details_json = json.dumps(details or {}, ensure_ascii=True, default=str)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO execution_logs (task_id, event_type, status, message, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, event_type, status, message, details_json),
            )
            row = connection.execute(
                "SELECT id, created_at FROM execution_logs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return {
            "id": row["id"],
            "task_id": task_id,
            "event_type": event_type,
            "status": status,
            "message": message,
            "details": json.loads(details_json),
            "created_at": row["created_at"],
        }

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None

        task = dict(row)
        task["plan"] = json.loads(task.pop("plan_json")) if task["plan_json"] else None
        raw_invoice = task.pop("invoice_data_json")
        task["invoice_data"] = json.loads(raw_invoice) if raw_invoice else None
        raw_verification = task.pop("verification_json")
        task["verification"] = json.loads(raw_verification) if raw_verification else None
        return task

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent task summaries without loading their full audit logs."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT task_id, user_task, status, final_report, created_at, updated_at
                FROM agent_tasks
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "status": row["status"],
                "message": row["message"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]