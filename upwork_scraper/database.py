"""SQLite persistence for scraped job data."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def resolve_database_path(database_path: str | Path) -> Path:
    """Resolve relative database paths from the project root."""

    path = Path(database_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path


def connect_to_db(database_path: str | Path = "upwork_jobs.db"):
    """Open the application database, resolving relative paths from the project root."""

    path = resolve_database_path(database_path)
    conn = sqlite3.connect(path)
    return conn, conn.cursor()


def create_db(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    """Create the jobs table when it does not already exist."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            job_url TEXT,
            job_title TEXT NOT NULL,
            posted_date DATETIME,
            job_description TEXT NOT NULL,
            job_tags TEXT,
            job_proposals TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
