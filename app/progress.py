from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DATA_DIR, DB_PATH


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reads (
                user_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                read_at TEXT NOT NULL,
                PRIMARY KEY (user_id, chapter_id)
            );
            CREATE TABLE IF NOT EXISTS quizzes (
                user_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                detail TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                PRIMARY KEY (user_id, chapter_id)
            );
            """
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def new_user_id() -> str:
    return uuid.uuid4().hex


def mark_read(user_id: str, chapter_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reads (user_id, chapter_id, read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, chapter_id) DO UPDATE SET read_at = excluded.read_at
            """,
            (user_id, chapter_id, now),
        )


def save_quiz(user_id: str, chapter_id: str, score: int, total: int, detail: list) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO quizzes (user_id, chapter_id, score, total, detail, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chapter_id) DO UPDATE SET
                score = excluded.score,
                total = excluded.total,
                detail = excluded.detail,
                submitted_at = excluded.submitted_at
            """,
            (user_id, chapter_id, score, total, json.dumps(detail, ensure_ascii=False), now),
        )


def snapshot(user_id: str) -> dict:
    with _connect() as conn:
        reads = {
            row["chapter_id"]: row["read_at"]
            for row in conn.execute(
                "SELECT chapter_id, read_at FROM reads WHERE user_id = ?",
                (user_id,),
            )
        }
        quizzes = {
            row["chapter_id"]: {
                "score": row["score"],
                "total": row["total"],
                "submitted_at": row["submitted_at"],
            }
            for row in conn.execute(
                "SELECT chapter_id, score, total, submitted_at FROM quizzes WHERE user_id = ?",
                (user_id,),
            )
        }
    return {"reads": reads, "quizzes": quizzes}
