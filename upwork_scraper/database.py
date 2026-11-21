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
    """Create the jobs table, or add any column an older database is missing."""

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
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            job_description_formatted TEXT
        )
        """
    )
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(jobs)")}
    if "job_description_formatted" not in columns:
        cursor.execute("ALTER TABLE jobs ADD COLUMN job_description_formatted TEXT")
    conn.commit()
