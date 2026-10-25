"""本地任务队列（SQLite）：断网可排队，重启后继续跑，失败可重试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
)
"""


class TaskQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(SCHEMA)
            # 老库升级：补上重试计数字段
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "retry_count" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")

    def add(self, mode: str, payload: dict) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (created_at, updated_at, status, mode, payload) VALUES (?, ?, 'pending', ?, ?)",
                (now, now, mode, json.dumps(payload, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM tasks"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "mode": row["mode"],
                "payload": json.loads(row["payload"]),
                "result": json.loads(row["result"]) if row["result"] else None,
                "error": row["error"],
                "retry_count": row["retry_count"],
            }
            for row in rows
        ]

    def get(self, task_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "mode": row["mode"],
            "payload": json.loads(row["payload"]),
            "error": row["error"],
            "retry_count": row["retry_count"],
        }

    def next_pending(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "mode": row["mode"],
            "payload": json.loads(row["payload"]),
            "retry_count": row["retry_count"],
        }

    def mark(self, task_id: int, status: str, result: dict | None = None, error: str = "") -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, result = ?, error = ? WHERE id = ?",
                (
                    status,
                    now,
                    json.dumps(result, ensure_ascii=False) if result else None,
                    error or None,
                    task_id,
                ),
            )

    def retry(self, task_id: int) -> bool:
        """把失败任务重新放回 pending，并累加重试次数。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = 'pending', updated_at = ?, error = NULL, "
                "retry_count = retry_count + 1 WHERE id = ? AND status IN ('failed', 'running')",
                (now, task_id),
            )
            return cursor.rowcount > 0

    def retry_all_failed(self) -> int:
        """一键把所有失败任务重新排队，返回重置数量。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = 'pending', updated_at = ?, error = NULL, "
                "retry_count = retry_count + 1 WHERE status = 'failed'",
                (now,),
            )
            return int(cursor.rowcount)
