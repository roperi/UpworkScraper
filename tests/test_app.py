import json
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from upwork_scraper import app
from upwork_scraper import auth as app_auth
from upwork_scraper.browser import BrowserSpec
from upwork_scraper.config import Settings
from upwork_scraper.database import create_db


class FakeLogger(logging.Logger):
    def __init__(self):
        super().__init__("fake")
        self.messages = []

    def info(self, *args, **kwargs):
        self.messages.append(("info", args))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args))

    def error(self, *args, **kwargs):
        self.messages.append(("error", args))

    def exception(self, *args, **kwargs):
        self.messages.append(("exception", args))


class FakeDriver:
    def __init__(self):
        self.closed = False

    def quit(self):
        self.closed = True

    def execute_cdp_cmd(self, _command, _params):
        return {}

    def get_log(self, _log_type):
        return []


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


def test_job_urls_are_built_from_feed_tiles():
    class Driver:
        def execute_script(self, _script):
            return {
                "tiles": [
                    {
                        "uid": "2105718172637605907",
                        "title": "PromptOps Engineer for Deterministic AI Agents",
                    },
                    {"uid": None, "title": "Missing opening UID"},
                    {"uid": "2105364230841134304", "title": "Build HIPAA-Compliant AI system"},
                ],
                "links": [],
            }

    assert app._job_urls(Driver()) == [
        "https://www.upwork.com/jobs/PromptOps-Engineer-for-Deterministic-AI-Agents"
        "_~022105718172637605907",
        "https://www.upwork.com/jobs/Build-HIPAA-Compliant-AI-system_~022105364230841134304",
    ]


def test_job_urls_fall_back_to_feed_links_when_tiles_have_no_hooks():
    class Driver:
        def execute_script(self, _script):
            return {
                "tiles": [{"href": None, "uid": None, "title": None}],
                "links": [
                    "/jobs/Ops-Engineer_~022116093407321073630/?referrer_url_path=find_work_home",
                    "/jobs/Ops-Engineer_~022116093407321073630/?referrer_url_path=find_work_home",
                    "/nx/search/jobs/?ontology_skill_uid=1031626712264720384",
                ],
            }

    assert app._job_urls(Driver()) == [
        "https://www.upwork.com/jobs/Ops-Engineer_~022116093407321073630/"
    ]


def test_load_job_list_waits_for_stable_empty_results(monkeypatch):
    class Driver:
        def __init__(self):
            self.scrolls = 0

        def find_element(self, _by, _name):
            return SimpleNamespace(is_displayed=lambda: True)

        def execute_script(self, script, *arguments):
            if ("scrollTop" in script and arguments) or "scrollBy" in script:
                self.scrolls += 1
                return None
            if "scrollHeight > n.clientHeight" in script:
                return None
            return []

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
    monkeypatch.setattr(app, "FEED_READY_TIMEOUT", 0)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(app, "WebDriverWait", ImmediateWait)

    app._load_job_list(driver, logger)

    assert driver.scrolls == 2
    assert any("Job list loaded" in message[0] for _, message in logger.messages)


def test_wait_for_feed_returns_as_soon_as_tiles_render(monkeypatch):
    class Driver:
        def __init__(self):
            self.tile_probes = 0

        def execute_script(self, script):
            if "job-tile" in script:
                self.tile_probes += 1
                return 0 if self.tile_probes < 2 else 30
            return False

    driver = Driver()
    logger = FakeLogger()
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    app._wait_for_feed(driver, logger)

    assert driver.tile_probes == 2
    assert not any(level == "warning" for level, _ in logger.messages)


def test_job_urls_prefer_the_tile_link_and_strip_tracking_parameters():
    class Driver:
        def execute_script(self, _script):
            return {
                "tiles": [
                    {
                        "href": "/jobs/ML-Ops-Engineer_~022116093407321073630/"
                        "?referrer_url_path=find_work_home",
                        "uid": "2116093407321073630",
                        "title": "ML Ops Engineer for Cloud Features",
                    },
                    {
                        "href": "https://www.upwork.com/jobs/Absolute_~02200?x=1",
                        "uid": None,
                        "title": None,
                    },
                    {"href": None, "uid": None, "title": "Unusable tile"},
                ],
                "links": ["/jobs/ignored_~02999"],
            }

    assert app._job_urls(Driver()) == [
        "https://www.upwork.com/jobs/ML-Ops-Engineer_~022116093407321073630/",
        "https://www.upwork.com/jobs/Absolute_~02200",
    ]


def test_load_job_list_stops_on_cloudflare_block(monkeypatch):
    """A block page must end the run instead of scrolling an empty page."""

    class Driver:
        current_url = "https://www.upwork.com/nx/find-work/best-matches"
        scrolls = 0

        def execute_script(self, script, *arguments):
            if script == app_auth.BLOCKED_PAGE_JS:
                return True
            self.scrolls += 1
            return None

    driver = Driver()
    try:
        app._load_job_list(driver, FakeLogger())
    except app_auth.UpworkBlockedError:
        pass
    else:
        raise AssertionError("expected UpworkBlockedError before scrolling")
    assert driver.scrolls == 0


def test_document_responses_extract_cloudflare_verdicts():
    class Driver:
        def get_log(self, _log_type):
            return [
                {
                    "message": json.dumps(
                        {
                            "message": {
                                "method": "Network.responseReceived",
                                "params": {
                                    "type": "Document",
                                    "response": {
                                        "url": "https://www.upwork.com/nx/s/find-work/best-matches",
                                        "status": 403,
                                        "headers": {
                                            "CF-Ray": "a5296037484ded07-LHR",
                                            "cf-mitigated": "challenge",
                                            "Retry-After": "60",
                                        },
                                    },
                                },
                            }
                        }
                    )
                },
                {
                    "message": json.dumps(
                        {
                            "message": {
                                "method": "Network.responseReceived",
                                "params": {
                                    "type": "XHR",
                                    "response": {"url": "https://api", "status": 200},
                                },
                            }
                        }
                    )
                },
                {"message": "not json"},
            ]

    documents = app._document_responses(Driver())

    assert len(documents) == 1
    assert documents[0]["status"] == 403
    assert documents[0]["cf-ray"] == "a5296037484ded07-LHR"
    assert documents[0]["cf-mitigated"] == "challenge"
    assert documents[0]["retry-after"] == "60"


def test_log_document_responses_reports_status_and_headers():
    logger = FakeLogger()

    class Driver:
        def get_log(self, _log_type):
            return [
                {
                    "message": json.dumps(
                        {
                            "message": {
                                "method": "Network.responseReceived",
                                "params": {
                                    "type": "Document",
                                    "response": {
                                        "url": "https://www.upwork.com/nx/s/find-work/best-matches",
                                        "status": 403,
                                        "headers": {"cf-ray": "abc-LHR", "cf-mitigated": "block"},
                                    },
                                },
                            }
                        }
                    )
                }
            ]

    app._log_document_responses(Driver(), logger)

    message = str(logger.messages[0][1])
    assert "403" in message
    assert "cf-ray=abc-LHR" in message
    assert "cf-mitigated=block" in message

    app._log_document_responses(None, logger)


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
