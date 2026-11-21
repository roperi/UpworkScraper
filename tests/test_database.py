import sqlite3

from upwork_scraper.database import create_db

OLD_SCHEMA = """
    CREATE TABLE jobs (
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


def _columns(conn):
    return [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]


def test_new_database_has_formatted_column():
    conn = sqlite3.connect(":memory:")
    create_db(conn, conn.cursor())

    assert "job_description_formatted" in _columns(conn)


def test_existing_database_is_upgraded_in_place_without_losing_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO jobs (job_id, job_title, job_description, updated_at) "
        "VALUES ('1', 'Title', 'Body text', '2026-01-01 00:00:00')"
    )
    conn.commit()

    create_db(conn, conn.cursor())

    assert "job_description_formatted" in _columns(conn)
    row = conn.execute(
        "SELECT job_id, job_title, job_description, updated_at, job_description_formatted FROM jobs"
    ).fetchall()
    assert row == [("1", "Title", "Body text", "2026-01-01 00:00:00", None)]


def test_running_create_db_twice_is_a_no_op():
    conn = sqlite3.connect(":memory:")
    conn.execute(OLD_SCHEMA)
    conn.execute("INSERT INTO jobs (job_id, job_title, job_description) VALUES ('1', 'T', 'B')")
    conn.commit()

    create_db(conn, conn.cursor())
    before = _columns(conn)
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    create_db(conn, conn.cursor())

    assert _columns(conn) == before
    assert conn.execute("SELECT * FROM jobs").fetchall() == rows
    assert before.count("job_description_formatted") == 1
