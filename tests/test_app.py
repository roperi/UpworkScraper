import json
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _check_config(monkeypatch, capsys, argv, **settings_kwargs):
    settings = Settings(
        username="user@example.com", password="secret", first_name="FirstName", **settings_kwargs
    )
    monkeypatch.setattr(app, "load_settings", lambda: settings)
    monkeypatch.setattr(app, "_logger", lambda level: FakeLogger())
    monkeypatch.setattr(
        app, "discover_browser", lambda path: BrowserSpec(Path("/usr/bin/chrome"), "151.0.0")
    )
    assert app.main(argv) is True
    return capsys.readouterr().out


def test_check_config_reports_formatting_disabled(monkeypatch, capsys):
    out = _check_config(monkeypatch, capsys, ["--check-config"])
    assert "description formatting: disabled" in out


def test_check_config_reports_formatting_enabled_without_the_key(monkeypatch, capsys):
    out = _check_config(
        monkeypatch,
        capsys,
        ["--check-config"],
        openrouter_api_key="sk-test-key",
        openrouter_model="vendor/model",
    )
    assert "description formatting: enabled (model=vendor/model)" in out
    assert "sk-test-key" not in out


def test_check_config_reports_disabled_with_no_format_flag(monkeypatch, capsys):
    out = _check_config(
        monkeypatch,
        capsys,
        ["--check-config", "--no-format"],
        openrouter_api_key="sk-test-key",
        openrouter_model="vendor/model",
    )
    assert "description formatting: disabled" in out


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


class SnapshotDriver:
    current_url = "https://www.upwork.com/nx/find-work/best-matches"
    page_source = "<html>feed</html>"

    def execute_script(self, script):
        if "innerText" in script:
            return "Posted 1 hour ago\nJob A\nPosted 2 hours ago\nJob B\nFirstName\nFooter"
        return {
            "tiles": [{"href": "/jobs/Job-A_~021/", "uid": None, "title": None}],
            "links": [],
        }


def test_save_snapshot_writes_json_and_html_before_parsing(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "SNAPSHOT_DIR", tmp_path / "snapshots")

    found = app._save_snapshot(SnapshotDriver(), FakeLogger(), "test")

    assert found["url"] == SnapshotDriver.current_url
    saved_json = list((tmp_path / "snapshots").glob("best-matches-*.json"))
    saved_html = list((tmp_path / "snapshots").glob("best-matches-*.html"))
    assert len(saved_json) == 1 and len(saved_html) == 1
    saved = json.loads(saved_json[0].read_text())
    assert saved["reason"] == "test"
    assert saved["tiles"][0]["href"] == "/jobs/Job-A_~021/"
    assert saved_html[0].read_text() == "<html>feed</html>"


def test_save_snapshot_survives_unwritable_directory(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(app, "SNAPSHOT_DIR", blocker / "snapshots")
    logger = FakeLogger()

    found = app._save_snapshot(SnapshotDriver(), logger, "test")

    assert found["tiles"]
    assert any(level == "warning" for level, _ in logger.messages)


def test_posts_and_urls_split_text_and_trim_after_first_name():
    snapshot = {
        "text": "Posted 1 hour ago\nJob A\nPosted 2 hours ago\nJob B\nFirstName\nFooter Posted x",
        "tiles": [{"href": "/jobs/Job-A_~021/", "uid": None, "title": None}],
        "links": [],
    }

    posts, urls = app._posts_and_urls(snapshot, "FirstName")

    assert posts == [" 1 hour ago\nJob A\n", " 2 hours ago\nJob B\n"]
    assert urls == ["https://www.upwork.com/jobs/Job-A_~021/"]


def test_posts_and_urls_handle_empty_snapshot():
    assert app._posts_and_urls({}, "FirstName") == ([], [])


def _snapshot_settings(tmp_path):
    return Settings(
        username="u@example.com",
        password="secret",
        first_name="FirstName",
        database_path=tmp_path / "jobs.db",
    )


def test_from_snapshot_imports_jobs_without_launching_browser(monkeypatch, tmp_path):
    snapshot = tmp_path / "snap.json"
    snapshot.write_text(
        json.dumps(
            {
                "text": "Posted 1 hour ago\nJob A\n",
                "tiles": [{"href": "/jobs/Job-A_~021/", "uid": None, "title": None}],
                "links": [],
            }
        )
    )
    monkeypatch.setattr(app, "load_settings", lambda: _snapshot_settings(tmp_path))
    monkeypatch.setattr(app, "_logger", lambda level: FakeLogger())
    monkeypatch.setattr(
        app,
        "launch_driver",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not launch a browser")),
    )
    monkeypatch.setattr(
        app,
        "parse_job_details",
        lambda _rows, job_url: {
            "job_id": "job-a",
            "job_url": job_url,
            "job_title": "Job A",
            "posted_date": None,
            "job_description": "d",
            "job_tags": "[]",
            "job_proposals": "p",
        },
    )

    assert app.main(["--from-snapshot", str(snapshot)]) is True

    conn = sqlite3.connect(tmp_path / "jobs.db")
    rows = conn.execute("SELECT job_id, job_url FROM jobs").fetchall()
    conn.close()
    assert rows == [("job-a", "https://www.upwork.com/jobs/Job-A_~021/")]


def test_from_snapshot_reports_failure_for_missing_or_invalid_file(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "load_settings", lambda: _snapshot_settings(tmp_path))
    monkeypatch.setattr(app, "_logger", lambda level: FakeLogger())
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")

    assert app.main(["--from-snapshot", str(tmp_path / "missing.json")]) is False
    assert app.main(["--from-snapshot", str(bad)]) is False


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _fixture_snapshot(tmp_path, name, ids):
    """Build a snapshot file from an invented fixture, one job link per post."""

    text = (FIXTURE_DIR / name).read_text()
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "text": text,
                "tiles": [{"href": f"/jobs/Example-Job_~{i}/", "uid": None} for i in ids],
                "links": [],
            }
        )
    )
    return path


NEWER_IDS = ["02a1", "02a2", "02a3", "02a4", "02a5"]


def _jobs_db(path):
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    create_db(conn, cursor)
    return conn, cursor


def _insert(cursor, job_id, tags, proposals):
    cursor.execute(
        "INSERT INTO jobs (job_id, job_url, job_title, job_description, job_tags, "
        "job_proposals, updated_at) VALUES (?, 'u', 'Kept title', 'Kept description', ?, ?, "
        "'2020-01-01')",
        (job_id, tags, proposals),
    )


def test_process_jobs_reports_posts_without_skills_or_proposals():
    conn, cursor = _jobs_db(":memory:")
    logger = FakeLogger()
    text = (FIXTURE_DIR / "best_matches_newer_layout.txt").read_text()
    posts = text.split("Posted")[1:]
    counts = app._process_jobs(posts, [f"/jobs/J_~{i}/" for i in NEWER_IDS], cursor, logger)
    assert counts.inserted == 5
    assert counts.without_skills == 1
    assert counts.without_proposals == 0
    assert not any(level == "warning" for level, _ in logger.messages)


def test_process_jobs_warns_when_most_posts_lack_skills_or_proposals():
    conn, cursor = _jobs_db(":memory:")
    logger = FakeLogger()
    posts = [f" 1 hour ago\nTitle {i}\n" for i in range(4)]
    counts = app._process_jobs(posts, [f"/jobs/J_~0{i}/" for i in range(4)], cursor, logger)
    assert counts.without_skills == 4
    assert counts.without_proposals == 4
    warnings = [str(args) for level, args in logger.messages if level == "warning"]
    assert any("skills" in text for text in warnings)
    assert any("proposal" in text for text in warnings)


def test_repair_fills_only_empty_values_and_never_overwrites(tmp_path):
    conn, cursor = _jobs_db(tmp_path / "jobs.db")
    _insert(cursor, "02a1", "[]", "Proposals:")  # both empty -> filled
    _insert(cursor, "02a2", '["Kept"]', "")  # tags kept, proposals filled
    _insert(cursor, "02a3", "[]", "99 to 100")  # tags filled, proposals kept
    _insert(cursor, "02a4", None, None)  # NULLs are empty too
    _insert(cursor, "unrelated", "[]", "")  # not in snapshot -> untouched
    conn.commit()
    text = (FIXTURE_DIR / "best_matches_newer_layout.txt").read_text()
    posts = text.split("Posted")[1:]
    urls = [f"https://www.upwork.com/jobs/J_~{i}/" for i in NEWER_IDS]
    logger = FakeLogger()

    result = app._repair_from_snapshot(posts, urls, cursor, logger)
    conn.commit()

    rows = {
        row[0]: row[1:]
        for row in cursor.execute(
            "SELECT job_id, job_tags, job_proposals, job_title, job_description, updated_at "
            "FROM jobs"
        )
    }
    assert json.loads(rows["02a1"][0]) == ["Python", "Dashboards", "Data Entry", "Reporting"]
    assert rows["02a1"][1] == "15 to 20"
    assert rows["02a2"][0] == '["Kept"]'
    assert rows["02a2"][1] == "50+"
    assert json.loads(rows["02a3"][0]) == ["Copywriting"]
    assert rows["02a3"][1] == "99 to 100"
    assert json.loads(rows["02a4"][0]) == ["HTML", "CSS"]
    assert rows["02a4"][1] == "Fewer than 5"
    assert rows["unrelated"] == ("[]", "", "Kept title", "Kept description", "2020-01-01")
    assert rows["02a1"][2:4] == ("Kept title", "Kept description")
    assert result.repaired == 4
    assert result.missing == 1  # 02a5 is not stored; repair never inserts
    assert cursor.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 5


def test_repair_is_idempotent(tmp_path):
    conn, cursor = _jobs_db(tmp_path / "jobs.db")
    _insert(cursor, "02a1", "[]", "Proposals:")
    conn.commit()
    posts = (FIXTURE_DIR / "best_matches_newer_layout.txt").read_text().split("Posted")[1:]
    urls = [f"https://www.upwork.com/jobs/J_~{i}/" for i in NEWER_IDS]
    assert app._repair_from_snapshot(posts, urls, cursor, FakeLogger()).repaired == 1
    assert app._repair_from_snapshot(posts, urls, cursor, FakeLogger()).repaired == 0


def test_repair_cli_reads_snapshots_and_commits(monkeypatch, tmp_path):
    conn, cursor = _jobs_db(tmp_path / "jobs.db")
    _insert(cursor, "02a1", "[]", "Proposals:")
    conn.commit()
    conn.close()
    snapshot = _fixture_snapshot(tmp_path, "best_matches_newer_layout.txt", NEWER_IDS)
    monkeypatch.setattr(app, "load_settings", lambda: _snapshot_settings(tmp_path))
    monkeypatch.setattr(app, "_logger", lambda level: FakeLogger())
    monkeypatch.setattr(
        app,
        "launch_driver",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not launch a browser")),
    )

    assert app.main(["--repair-from-snapshot", str(snapshot)]) is True

    conn = sqlite3.connect(tmp_path / "jobs.db")
    row = conn.execute("SELECT job_tags, job_proposals FROM jobs").fetchone()
    conn.close()
    assert json.loads(row[0])[0] == "Python"
    assert row[1] == "15 to 20"


def test_repair_cli_fails_for_missing_snapshot_or_database(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "load_settings", lambda: _snapshot_settings(tmp_path))
    monkeypatch.setattr(app, "_logger", lambda level: FakeLogger())
    assert app.main(["--repair-from-snapshot", str(tmp_path / "missing.json")]) is False
    # A missing database is reported, not silently created.
    snapshot = _fixture_snapshot(tmp_path, "best_matches_newer_layout.txt", NEWER_IDS)
    assert app.main(["--repair-from-snapshot", str(snapshot)]) is False
    assert not (tmp_path / "jobs.db").exists()


def test_repair_leaves_updated_at_unchanged_on_repaired_rows(tmp_path):
    conn, cursor = _jobs_db(tmp_path / "jobs.db")
    _insert(cursor, "02a1", "[]", "Proposals:")
    conn.commit()
    before = cursor.execute("SELECT updated_at FROM jobs WHERE job_id = '02a1'").fetchone()[0]
    posts = (FIXTURE_DIR / "best_matches_newer_layout.txt").read_text().split("Posted")[1:]
    urls = [f"https://www.upwork.com/jobs/J_~{i}/" for i in NEWER_IDS]

    assert app._repair_from_snapshot(posts, urls, cursor, FakeLogger()).repaired == 1
    conn.commit()

    row = cursor.execute(
        "SELECT job_proposals, updated_at FROM jobs WHERE job_id = '02a1'"
    ).fetchone()
    assert row[0] == "15 to 20"
    assert row[1] == before == "2020-01-01"


def _process_with_bucket(monkeypatch, stored, scraped):
    conn, cursor = _jobs_db(":memory:")
    _insert(cursor, "02b1", "[]", stored)
    monkeypatch.setattr(
        app,
        "parse_job_details",
        lambda _rows, job_url: {
            "job_id": "02b1",
            "job_url": job_url,
            "job_title": "Title",
            "posted_date": None,
            "job_description": "Description",
            "job_tags": '["Skill"]',
            "job_proposals": scraped,
        },
    )
    counts = app._process_jobs(["post"], ["/jobs/J_~02b1/"], cursor, FakeLogger())
    row = cursor.execute("SELECT job_proposals, updated_at FROM jobs").fetchone()
    return counts, row


def test_process_jobs_never_replaces_stored_bucket_with_empty_value(monkeypatch):
    counts, row = _process_with_bucket(monkeypatch, "5 to 10", "")
    assert row == ("5 to 10", "2020-01-01")
    assert (counts.updated, counts.unchanged) == (0, 1)


@pytest.mark.parametrize("stored", ["", "Proposals:"])
def test_process_jobs_replaces_empty_stored_bucket_with_real_bucket(monkeypatch, stored):
    counts, row = _process_with_bucket(monkeypatch, stored, "10 to 15")
    assert row[0] == "10 to 15"
    assert row[1] != "2020-01-01"
    assert counts.updated == 1


def test_process_jobs_replaces_real_bucket_with_different_real_bucket(monkeypatch):
    counts, row = _process_with_bucket(monkeypatch, "5 to 10", "10 to 15")
    assert row[0] == "10 to 15"
    assert counts.updated == 1


def test_parse_quality_messages_read_naturally():
    logger = FakeLogger()
    app._log_parse_quality(
        app.ScrapeCounts(inserted=4, without_skills=4, without_proposals=4), logger
    )
    texts = [args[0] % args[1:] for _, args in logger.messages]
    assert any("have no skills" in text for text in texts)
    assert any("have no proposal bucket" in text for text in texts)
    assert not any("no a " in text for text in texts)


# --- optional description formatting -------------------------------------------------

FORMAT_KEY = "sk-or-test-SECRET-456"


class RecordingPost:
    """Fake HTTP transport that echoes the user message with a paragraph break added."""

    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body))
        if self.behavior is not None:
            return self.behavior(len(self.calls))
        text = json.loads(body)["messages"][1]["content"]
        content = text.replace(". ", ".\n\n", 1)
        return 200, json.dumps(
            {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 10}}
        ).encode()


def _live_run(monkeypatch, tmp_path, new_count, post, argv=(), enabled=True, existing=()):
    """Run main() through the live path with a fake browser and invented jobs."""

    settings = Settings(
        username="user@example.com",
        password="secret",
        first_name="FirstName",
        database_path=tmp_path / "jobs.db",
        openrouter_api_key=FORMAT_KEY if enabled else None,
        openrouter_model="vendor/model" if enabled else None,
    )
    conn = sqlite3.connect(tmp_path / "jobs.db")
    create_db(conn, conn.cursor())
    for job_id in existing:
        conn.execute(
            "INSERT INTO jobs (job_id, job_url, job_title, job_description, job_proposals) "
            "VALUES (?, 'u', 'T', 'Old text. More old text.', 'old')",
            (job_id,),
        )
    conn.commit()
    conn.close()

    ids = list(existing) + [f"new{i}" for i in range(new_count)]
    driver = FakeDriver()
    monkeypatch.setattr(app, "load_settings", lambda: settings)
    monkeypatch.setattr(app, "_logger", lambda level: logging.getLogger("upwork_scraper"))
    monkeypatch.setattr(
        app, "launch_driver", lambda s: (driver, BrowserSpec(Path("/usr/bin/chrome"), "1"))
    )
    monkeypatch.setattr(app, "login", lambda *a: None)
    monkeypatch.setattr(app, "_load_job_list", lambda *a: None)
    monkeypatch.setattr(app, "_save_snapshot", lambda *a: {})
    monkeypatch.setattr(app, "_posts_and_urls", lambda *a: (ids, [f"/jobs/{i}/" for i in ids]))
    monkeypatch.setattr(
        app,
        "parse_job_details",
        lambda rows, job_url: {
            "job_id": rows[0],
            "job_url": job_url,
            "job_title": "Title",
            "posted_date": None,
            "job_description": f"Invented text for {rows[0]}. Second sentence here.",
            "job_tags": "[]",
            "job_proposals": "new",
        },
    )
    real = app.format_description
    monkeypatch.setattr(app, "format_description", lambda *a, **kw: real(*a, post=post, **kw))
    monkeypatch.setattr(app, "_document_responses", lambda d: [])
    assert app.main(list(argv)) is True
    return sqlite3.connect(tmp_path / "jobs.db")


def _formatted(conn):
    return dict(conn.execute("SELECT job_id, job_description_formatted FROM jobs"))


def test_new_jobs_get_formatted_description(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    post = RecordingPost()
    conn = _live_run(monkeypatch, tmp_path, 2, post)

    assert len(post.calls) == 2
    stored = dict(conn.execute("SELECT job_id, job_description FROM jobs"))
    formatted = _formatted(conn)
    assert stored["new0"] == "Invented text for new0. Second sentence here."
    assert formatted["new0"] == "Invented text for new0.\n\nSecond sentence here."
    assert "2 attempted, 2 formatted, 0 rejected or failed, 0 skipped by cap, 20 tokens" in (
        caplog.text
    )
    assert FORMAT_KEY not in caplog.text


@pytest.mark.parametrize(
    ("enabled", "argv"), [(False, ()), (True, ("--no-format",))], ids=["unconfigured", "flag"]
)
def test_no_http_call_when_disabled(monkeypatch, tmp_path, enabled, argv):
    post = RecordingPost()
    conn = _live_run(monkeypatch, tmp_path, 3, post, argv=argv, enabled=enabled)

    assert post.calls == []
    assert set(_formatted(conn).values()) == {None}
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone() == (3,)


def test_existing_jobs_are_never_sent_to_the_model(monkeypatch, tmp_path):
    post = RecordingPost()
    conn = _live_run(monkeypatch, tmp_path, 1, post, existing=("old1", "old2"))

    assert len(post.calls) == 1
    assert "new0" in post.calls[0]["messages"][1]["content"]
    formatted = _formatted(conn)
    assert formatted["old1"] is None and formatted["old2"] is None
    assert formatted["new0"] is not None
    descriptions = dict(conn.execute("SELECT job_id, job_description FROM jobs"))
    assert descriptions["old1"] == "Old text. More old text."


def test_call_cap_limits_requests_and_logs_skipped(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    post = RecordingPost()
    conn = _live_run(monkeypatch, tmp_path, app.MAX_FORMAT_CALLS + 3, post)

    assert len(post.calls) == app.MAX_FORMAT_CALLS == 50
    values = list(_formatted(conn).values())
    assert sum(v is not None for v in values) == 50
    assert sum(v is None for v in values) == 3
    assert "3 skipped by cap" in caplog.text


def test_failures_leave_null_but_run_still_commits(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)

    def behavior(n):
        if n == 1:
            return 200, b"garbage"
        if n == 2:
            raise RuntimeError(f"transport failed for Bearer {FORMAT_KEY}")
        return 200, json.dumps({"choices": [{"message": {"content": "Changed words"}}]}).encode()

    post = RecordingPost(behavior)
    conn = _live_run(monkeypatch, tmp_path, 3, post)

    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone() == (3,)
    assert set(_formatted(conn).values()) == {None}
    assert "0 formatted, 3 rejected or failed" in caplog.text
    assert FORMAT_KEY not in caplog.text


def test_process_jobs_collects_only_inserted_ids(monkeypatch):
    conn, cursor = _jobs_db(":memory:")
    cursor.execute(
        "INSERT INTO jobs (job_id, job_url, job_title, job_description, job_proposals) "
        "VALUES ('old', 'u', 'T', 'D', 'old')"
    )
    monkeypatch.setattr(
        app,
        "parse_job_details",
        lambda rows, job_url: {
            "job_id": rows[0],
            "job_url": job_url,
            "job_title": "T",
            "posted_date": None,
            "job_description": "D",
            "job_tags": "[]",
            "job_proposals": "new",
        },
    )
    ids: list[str] = []
    counts = app._process_jobs(["old", "fresh"], ["/a/", "/b/"], cursor, FakeLogger(), ids)

    assert (counts.inserted, counts.updated) == (1, 1)
    assert ids == ["fresh"]
