"""Application orchestration."""

from __future__ import annotations

import logging
import sys
import time
from argparse import ArgumentParser
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .auth import login
from .browser import discover_browser, launch_driver
from .config import ConfigurationError, load_settings, validate_proxy_server
from .database import connect_to_db, create_db
from .job_helpers import parse_job_details

SCROLL_STEPS = 12
SCROLL_PAUSE_SECONDS = 0.5
JOB_COUNT_STABLE_POLLS = 3
JOB_COUNT_STABLE_TIMEOUT = 30


@dataclass(frozen=True)
class ScrapeCounts:
    """Database outcomes for one scrape run."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def processed(self) -> int:
        return self.inserted + self.updated + self.unchanged + self.failed


def _logger(level: str) -> logging.Logger:
    logger = logging.getLogger("upwork_scraper")
    logger.setLevel(getattr(logging, level, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        log_dir = Path(__file__).resolve().parents[1] / "log"
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "upwork_best_matches_scraper.log")
        file_handler.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s %(message)s"))
        logger.addHandler(file_handler)
    return logger


def _arguments(argv: list[str] | None):
    parser = ArgumentParser(description="Scrape Upwork Best Matches once")
    parser.add_argument("--check-config", action="store_true", help="validate settings and browser")
    parser.add_argument("--database", help="SQLite database path")
    parser.add_argument("--browser-path", help="browser executable path")
    parser.add_argument("--proxy-server", help="proxy URL or host:port")
    parser.add_argument(
        "--validate-login",
        action="store_true",
        help="validate live Upwork login without scraping or writing jobs",
    )
    parser.add_argument("--verification-timeout", type=int)
    parser.add_argument("--log-level")
    return parser.parse_args(argv)


def _job_urls(driver) -> list[str]:
    hrefs = driver.execute_script(
        "return Array.from(document.querySelectorAll(\"a[href*='/jobs/']\"), link => link.href);"
    )
    return [href.split("?", 1)[0].rstrip("/") for href in hrefs if "ontology_skill_uid" not in href]


def _load_job_list(driver, logger: logging.Logger) -> None:
    """Scroll through results and wait briefly for lazy-loaded jobs to settle."""

    body = driver.find_element(By.TAG_NAME, "body")
    for step in range(1, SCROLL_STEPS + 1):
        body.send_keys(Keys.PAGE_DOWN)
        time.sleep(SCROLL_PAUSE_SECONDS)
        logger.info(
            "Loading jobs: scroll %d/%d; %d job links visible",
            step,
            SCROLL_STEPS,
            len(_job_urls(driver)),
        )

    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    WebDriverWait(driver, 120).until(EC.visibility_of_element_located((By.TAG_NAME, "footer")))

    previous_count = -1
    stable_polls = 0

    def stable_job_count(current_driver) -> bool:
        nonlocal previous_count, stable_polls
        current_count = len(_job_urls(current_driver))
        if current_count == previous_count:
            stable_polls += 1
        else:
            previous_count = current_count
            stable_polls = 1
        return stable_polls >= JOB_COUNT_STABLE_POLLS

    WebDriverWait(driver, JOB_COUNT_STABLE_TIMEOUT, poll_frequency=1).until(stable_job_count)
    logger.info("Job list loaded: %d job links visible", previous_count)


def _process_jobs(job_posts: list[str], job_urls: list[str], cursor, logger) -> ScrapeCounts:
    """Parse and persist posts, retaining progress when one post fails."""

    if len(job_posts) != len(job_urls):
        logger.warning(
            "Job result mismatch: %d parsed posts and %d job links; processing %d pairs",
            len(job_posts),
            len(job_urls),
            min(len(job_posts), len(job_urls)),
        )

    inserted = 0
    updated = 0
    unchanged = 0
    failed = 0
    skipped = max(0, len(job_posts) - len(job_urls))
    processable = min(len(job_posts), len(job_urls))
    for index in range(processable):
        post = job_posts[index]
        try:
            details = parse_job_details(post.split("\n"), job_url=job_urls[index])
            cursor.execute("SELECT job_proposals FROM jobs WHERE job_id = ?", (details["job_id"],))
            existing = cursor.fetchone()
            if existing is not None:
                stored_proposals = existing[0] or ""
                scraped_proposals = details["job_proposals"] or ""
                if stored_proposals == scraped_proposals:
                    unchanged += 1
                    continue
                cursor.execute(
                    "UPDATE jobs SET job_proposals = ?, updated_at = ? WHERE job_id = ?",
                    (details["job_proposals"], datetime.now(), details["job_id"]),
                )
                updated += 1
            else:
                cursor.execute(
                    "INSERT INTO jobs (job_id, job_url, job_title, posted_date, "
                    "job_description, job_tags, job_proposals) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        details["job_id"],
                        job_urls[index],
                        details["job_title"],
                        details["posted_date"],
                        details["job_description"],
                        details["job_tags"],
                        details["job_proposals"],
                    ),
                )
                inserted += 1
        except Exception as exc:
            failed += 1
            logger.warning("Job %d/%d failed: %s", index + 1, processable, exc)
        if (index + 1) % 5 == 0 or index + 1 == processable:
            logger.info(
                "Processed %d/%d jobs (%d inserted, %d updated, %d unchanged, %d failed)",
                index + 1,
                len(job_posts),
                inserted,
                updated,
                unchanged,
                failed,
            )
    return ScrapeCounts(inserted, updated, unchanged, failed, skipped)


def main(argv: list[str] | None = None) -> bool:
    args = _arguments(argv)
    try:
        settings = load_settings()
        if args.database:
            settings = replace(settings, database_path=Path(args.database))
        if args.browser_path:
            settings = replace(settings, browser_executable_path=args.browser_path)
        if args.proxy_server:
            settings = replace(settings, proxy_server=validate_proxy_server(args.proxy_server))
        if args.verification_timeout is not None:
            if args.verification_timeout <= 0:
                raise ConfigurationError("--verification-timeout must be greater than zero")
            settings = replace(settings, verification_timeout=args.verification_timeout)
        if args.log_level:
            settings = replace(settings, log_level=args.log_level.upper())
        logger = _logger(settings.log_level)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return False

    if args.check_config:
        try:
            browser = discover_browser(settings.browser_executable_path)
            print(f"Configuration is valid; browser={browser.executable} version={browser.version}")
            return True
        except Exception as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return False

    if args.validate_login:
        driver = None
        try:
            driver, browser = launch_driver(settings)
            logger.info("Using %s (%s)", browser.executable, browser.version)
            logger.info("Validating Upwork login")
            login(driver, settings, lambda message: logger.warning(message))
            logger.info("Upwork login validation succeeded")
            return True
        except Exception:
            logger.exception("Login validation failed")
            return False
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    logger.warning("Browser was already closed")

    conn, cursor = connect_to_db(settings.database_path)
    driver = None
    try:
        create_db(conn, cursor)
        driver, browser = launch_driver(settings)
        logger.info("Using %s (%s)", browser.executable, browser.version)
        logger.info("Checking Upwork authentication")
        login(driver, settings, lambda message: logger.warning(message))

        _load_job_list(driver, logger)

        jobs_container = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "/html/body/div[3]/div/div/div[1]/div[2]/div/div/main/div")
            )
        )[-1]
        text = jobs_container.text
        if settings.first_name:
            text = text.split(settings.first_name)[0]
        text = text.split("Ordered by most relevant.")[-1]
        job_posts = text.split("Posted")[1:]
        job_urls = _job_urls(driver)
        logger.info("Parsed %d job posts and found %d job links", len(job_posts), len(job_urls))
        counts = _process_jobs(job_posts, job_urls, cursor, logger)
        conn.commit()
        logger.info(
            "Scraping completed: %d job posts processed (%d inserted, %d updated, "
            "%d unchanged, %d failed, %d skipped); database commit succeeded",
            counts.processed,
            counts.inserted,
            counts.updated,
            counts.unchanged,
            counts.failed,
            counts.skipped,
        )
        return True
    except Exception:
        logger.exception("Scraping failed")
        return False
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                logger.warning("Browser was already closed")
        cursor.close()
        conn.close()


def cli() -> int:
    """Run the packaged command with conventional process exit statuses."""

    return 0 if main() else 1
