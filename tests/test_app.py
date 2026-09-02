import sqlite3
from pathlib import Path
from types import SimpleNamespace

from upwork_scraper import app
from upwork_scraper.browser import BrowserSpec
from upwork_scraper.config import Settings
from upwork_scraper.database import create_db


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, *args, **kwargs):
        self.messages.append(("info", args))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args))

    def exception(self, *args, **kwargs):
        self.messages.append(("exception", args))


class FakeDriver:
    def __init__(self):
        self.closed = False

    def quit(self):
        self.closed = True


def test_validate_login_does_not_open_database(monkeypatch):
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")
    driver = FakeDriver()

    monkeypatch.setattr(app, "load_settings", lambda: settings)
    monkeypatch.setattr(app, "_logger", lambda level: FakeLogger())
    monkeypatch.setattr(
        app,
        "launch_driver",
        lambda current: (driver, BrowserSpec(Path("/usr/bin/google-chrome"), "151.0.0")),
    )
    monkeypatch.setattr(app, "login", lambda *args: None)

    def unexpected_database_access(*args):
        raise AssertionError("login validation must not open the jobs database")

    monkeypatch.setattr(app, "connect_to_db", unexpected_database_access)

    assert app.main(["--validate-login"]) is True
    assert driver.closed is True


def test_cli_converts_success_to_zero(monkeypatch):
    monkeypatch.setattr(app, "main", lambda: True)

    assert app.cli() == 0


def test_cli_converts_failure_to_one(monkeypatch):
    monkeypatch.setattr(app, "main", lambda: False)

    assert app.cli() == 1


def test_job_urls_are_filtered_and_normalized():
    class Driver:
        def execute_script(self, _script):
            return [
                "https://www.upwork.com/jobs/first_~abc/?foo=bar",
                "https://www.upwork.com/jobs/skill_~def?ontology_skill_uid=123",
                "https://www.upwork.com/jobs/second_~ghi/?foo=bar",
            ]

    assert app._job_urls(Driver()) == [
        "https://www.upwork.com/jobs/first_~abc",
        "https://www.upwork.com/jobs/second_~ghi",
    ]


def test_load_job_list_waits_for_stable_empty_results(monkeypatch):
    class Body:
        def __init__(self):
            self.scrolls = 0

        def send_keys(self, _key):
            self.scrolls += 1

    class Driver:
        def __init__(self):
            self.body = Body()

        def find_element(self, _by, name):
            return self.body if name == "body" else SimpleNamespace(is_displayed=lambda: True)

        def execute_script(self, script):
            if "querySelectorAll" in script:
                return []
            return None

    class ImmediateWait:
        def __init__(self, driver, _timeout, poll_frequency=None):
            self.driver = driver

        def until(self, condition):
            for _ in range(app.JOB_COUNT_STABLE_POLLS):
                if condition(self.driver):
                    return True
            raise AssertionError("condition did not stabilize")

    driver = Driver()
    logger = FakeLogger()
    monkeypatch.setattr(app, "SCROLL_STEPS", 2)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(app, "WebDriverWait", ImmediateWait)

    app._load_job_list(driver, logger)

    assert driver.body.scrolls == 2
    assert any("Job list loaded" in message[0] for _, message in logger.messages)


def test_process_jobs_counts_insert_update_failure_and_skipped(monkeypatch):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    create_db(conn, cursor)
    cursor.execute(
        "INSERT INTO jobs (job_id, job_url, job_title, job_description, job_proposals, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("existing", "old-url", "Existing", "Description", "old proposals", "2020-01-01"),
    )
    cursor.execute(
        "INSERT INTO jobs (job_id, job_url, job_title, job_description, job_proposals, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "unchanged",
            "old-unchanged-url",
            "Unchanged",
            "Description",
            "old proposals",
            "2020-01-02",
        ),
    )
    logger = FakeLogger()

    def fake_parse(_rows, job_url):
        job_id = {
            "existing-url": "existing",
            "unchanged-url": "unchanged",
        }.get(job_url, "new")
        if job_url == "failed-url":
            raise ValueError("invalid job")
        proposals = "old proposals" if job_url == "unchanged-url" else "new proposals"
        return {
            "job_id": job_id,
            "job_url": job_url,
            "job_title": "Title",
            "posted_date": None,
            "job_description": "Description",
            "job_tags": "[]",
            "job_proposals": proposals,
        }

    monkeypatch.setattr(app, "parse_job_details", fake_parse)
    counts = app._process_jobs(
        ["new post", "existing post", "unchanged post", "failed post", "unmatched post"],
        ["new-url", "existing-url", "unchanged-url", "failed-url"],
        cursor,
        logger,
    )
    conn.commit()

    assert counts == app.ScrapeCounts(inserted=1, updated=1, unchanged=1, failed=1, skipped=1)
    assert cursor.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
    assert (
        cursor.execute("SELECT job_proposals FROM jobs WHERE job_id = 'existing'").fetchone()[0]
        == "new proposals"
    )
    assert (
        cursor.execute("SELECT updated_at FROM jobs WHERE job_id = 'existing'").fetchone()[0]
        != "2020-01-01"
    )
    assert (
        cursor.execute("SELECT updated_at FROM jobs WHERE job_id = 'unchanged'").fetchone()[0]
        == "2020-01-02"
    )
    assert any(level == "warning" for level, _ in logger.messages)
