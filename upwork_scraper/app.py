"""Application orchestration."""

from __future__ import annotations

import json
import logging
import random
import re
import sys
import time
from argparse import ArgumentParser
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .auth import UpworkBlockedError, ensure_not_blocked, login
from .browser import discover_browser, launch_driver
from .config import ConfigurationError, load_settings, validate_proxy_server
from .database import connect_to_db, create_db
from .job_helpers import parse_job_details

SCROLL_STEPS = 12
SCROLL_PAUSE_SECONDS = 0.5
FEED_READY_TIMEOUT = 45
JOB_COUNT_STABLE_POLLS = 3
JOB_COUNT_STABLE_TIMEOUT = 30
DOCUMENT_HEADERS = ("cf-ray", "cf-mitigated", "server", "retry-after", "location")
DOCUMENT_LOG_LIMIT = 4
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "log" / "snapshots"


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


def _enable_response_logging(driver) -> None:
    """Start collecting document responses from Chrome's performance log.

    The launch options already request performance logging; enabling the
    network domain makes sure every document response is reported even when a
    navigation is short-circuited by a redirect or a refusal.
    """

    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except WebDriverException:
        pass


def _document_responses(driver) -> list[dict]:
    """Return the document responses recorded since the last call."""

    try:
        entries = driver.get_log("performance")
    except WebDriverException:
        return []

    documents = []
    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, TypeError, ValueError):
            continue
        if message.get("method") != "Network.responseReceived":
            continue
        params = message.get("params") or {}
        if params.get("type") != "Document":
            continue
        response = params.get("response") or {}
        headers = {key.lower(): value for key, value in (response.get("headers") or {}).items()}
        document = {
            "url": response.get("url", ""),
            "status": response.get("status"),
        }
        for header in DOCUMENT_HEADERS:
            document[header] = headers.get(header)
        documents.append(document)
    return documents


def _log_document_responses(driver, logger: logging.Logger) -> None:
    """Log the HTTP outcome of each Upwork document load.

    Cloudflare reports its verdict in the response, so these lines say whether
    a run was refused, challenged, or served, without guessing from page text.
    """

    if driver is None:
        return
    for document in _document_responses(driver)[-DOCUMENT_LOG_LIMIT:]:
        details = ", ".join(
            f"{header}={document.get(header) or '-'}"
            for header in DOCUMENT_HEADERS
            if header in {"cf-ray", "cf-mitigated", "retry-after"}
        )
        logger.info("Response %s for %s (%s)", document["status"], document["url"], details)


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
    parser.add_argument(
        "--from-snapshot",
        help="load jobs from a saved log/snapshots/*.json file instead of scraping Upwork",
    )
    parser.add_argument("--verification-timeout", type=int)
    parser.add_argument("--log-level")
    return parser.parse_args(argv)


_JOB_TILES_JS = """
const tiles = Array.from(document.querySelectorAll('[data-test="job-tile"], .job-tile'));
return {
  tiles: tiles.map(tile => {
    const link = tile.querySelector('a[href*="/jobs/"]');
    const tracker = tile.querySelector('[data-ev-opening_uid]');
    const title = tile.querySelector('[data-test="job-title"]');
    return {
      href: link ? link.getAttribute('href') : null,
      uid: tracker ? tracker.getAttribute('data-ev-opening_uid') : null,
      title: title ? title.textContent.trim() : null,
    };
  }),
  // Fallback for variants whose tiles carry no data-test/class hooks: the job
  // links themselves are the canonical URLs and are always rendered.
  links: Array.from(document.querySelectorAll('a[href*="/jobs/"]'))
    .map(anchor => anchor.getAttribute('href'))
    .filter(href => href && /_~02\\d+/.test(href)),
};
"""


def _job_urls(driver) -> list[str]:
    """Collect canonical job URLs from the feed, in document order."""

    return _urls_from_found(driver.execute_script(_JOB_TILES_JS) or {})


def _urls_from_found(found: dict) -> list[str]:
    urls = [
        url for url in (_tile_job_url(tile) for tile in found.get("tiles", [])) if url is not None
    ]
    if urls:
        return urls

    fallback: list[str] = []
    for href in found.get("links", []):
        url = _absolute_job_url(href)
        if url is not None and url not in fallback:
            fallback.append(url)
    return fallback


def _absolute_job_url(href: str) -> str | None:
    """Normalize a rendered job link to a canonical absolute URL."""

    href = (href or "").strip()
    if not href:
        return None
    parsed = urlsplit(href)
    if parsed.netloc and parsed.hostname not in {"upwork.com", "www.upwork.com"}:
        return None
    # Feed markup has used both root-relative URLs and URLs nested under an
    # /nx/... route. Keep the canonical job path and discard tracking data.
    match = re.search(r"(?:^|/)jobs/(.+)$", parsed.path)
    if not match:
        return None
    return f"https://www.upwork.com/jobs/{match.group(1)}"


def _tile_job_url(tile: dict) -> str | None:
    """Return the canonical job URL for one feed tile, or ``None``."""

    url = _absolute_job_url(tile.get("href") or "")
    if url is not None:
        return url

    uid = tile.get("uid")
    title = tile.get("title")
    if not uid or not title:
        return None
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
    return f"https://www.upwork.com/jobs/{slug}_~02{uid}"


def _feed_container(driver):
    """Return the scrollable job-feed container, falling back to window scroll."""

    return driver.execute_script(
        """
        const candidates = Array.from(document.querySelectorAll('div, main, section'))
          .filter(n => n.scrollHeight > n.clientHeight + 100 && n.clientHeight > 300);
        if (!candidates.length) return null;
        candidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
        return candidates[0];
        """
    )


def _scroll_feed(driver, container) -> None:
    """Send a real mouse-wheel event; synthesized scrollTop does not trigger
    the feed's lazy loader, but Chromium's wheel input does."""

    if container is None:
        driver.execute_script("window.scrollBy(0, window.innerHeight);")
        return
    rect = driver.execute_script(
        "const r = arguments[0].getBoundingClientRect();"
        "return {x: r.x + r.width / 2, y: Math.max(r.y + r.height / 2, 0)};",
        container,
    )
    driver.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {"type": "mouseWheel", "x": rect["x"], "y": rect["y"], "deltaX": 0, "deltaY": 600},
    )


def _scroll_offset(driver, container) -> int:
    """Report the real scroll position so the log reflects actual movement."""

    if container is None:
        return driver.execute_script("return Math.round(window.scrollY);")
    return driver.execute_script("return Math.round(arguments[0].scrollTop);", container)


def _job_tile_count(driver) -> int:
    """Count rendered feed entries without parsing their contents."""

    count = driver.execute_script(
        """
        const tiles = document.querySelectorAll('[data-test="job-tile"], .job-tile').length;
        if (tiles) return tiles;
        return Array.from(document.querySelectorAll('a[href*="/jobs/"]'))
          .filter(anchor => /_~02\\d+/.test(anchor.getAttribute('href') || '')).length;
        """
    )
    return count if isinstance(count, int) else 0


def _wait_for_feed(driver, logger: logging.Logger) -> None:
    """Wait for the feed to render before sending any input.

    Sending wheel events into the blank shell that loads first is both useless
    (there is nothing to scroll) and a strong automation signal, so wait for
    the first tile instead of scrolling on a timer.
    """

    deadline = time.monotonic() + FEED_READY_TIMEOUT
    while time.monotonic() < deadline:
        ensure_not_blocked(driver)
        if _job_tile_count(driver):
            return
        time.sleep(0.5)
    logger.warning("No job tiles rendered after %d seconds; scrolling anyway", FEED_READY_TIMEOUT)


def _load_job_list(driver, logger: logging.Logger) -> None:
    """Scroll through results and wait briefly for lazy-loaded jobs to settle."""

    _wait_for_feed(driver, logger)

    previous_offset = -1
    stalled = 0
    for step in range(1, SCROLL_STEPS + 1):
        # Cloudflare can swap in its block page after an initial success, so
        # check every step instead of scrolling twenty seconds of nothing.
        ensure_not_blocked(driver)
        # The feed renders asynchronously, so the scroll container may not
        # exist yet on the first steps. Rediscover it every step instead of
        # caching a None/decoy and silently no-op'ing the rest of the run.
        container = _feed_container(driver)
        _scroll_feed(driver, container)
        time.sleep(random.uniform(SCROLL_PAUSE_SECONDS, SCROLL_PAUSE_SECONDS * 2))
        offset = _scroll_offset(driver, container)
        logger.info(
            "Loading jobs: scroll %d/%d; %d job links visible; offset %d",
            step,
            SCROLL_STEPS,
            len(_job_urls(driver)),
            offset,
        )
        # A wheel event on an already-scrolled-to-bottom feed is pure synthetic
        # input, so stop once two consecutive steps move nothing.
        stalled = stalled + 1 if offset == previous_offset else 0
        previous_offset = offset
        if stalled >= 2:
            logger.info("Feed reached the bottom after %d scrolls", step)
            break

    container = _feed_container(driver)
    if container is not None:
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
    else:
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


def _save_snapshot(driver, logger: logging.Logger, reason: str) -> dict:
    """Capture the loaded feed once and persist it to disk before parsing.

    Everything later parsing needs (tiles, links, text) comes from this single
    capture, so a page that changes or a parser that fails cannot cost a run's
    worth of scrolling. The raw HTML is kept for offline troubleshooting.
    """

    found = driver.execute_script(_JOB_TILES_JS) or {}
    try:
        found["text"] = driver.execute_script(
            "const m = Array.from(document.querySelectorAll('main > div')).pop();"
            "return m ? m.innerText : document.body.innerText;"
        )
        found["url"] = driver.current_url
        html = driver.page_source
    except WebDriverException as exc:
        logger.warning("Could not capture full page for snapshot: %s", exc)
        html = ""
    try:
        directory = SNAPSHOT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (directory / f"best-matches-{stamp}.json").write_text(
            json.dumps({"reason": reason, **found}, indent=2, default=str)
        )
        (directory / f"best-matches-{stamp}.html").write_text(html)
        logger.info("Saved feed snapshot to %s/best-matches-%s.*", directory, stamp)
    except OSError as exc:
        logger.warning("Could not write feed snapshot: %s", exc)
    return found


def _posts_and_urls(snapshot: dict, first_name: str | None) -> tuple[list[str], list[str]]:
    text = snapshot.get("text") or ""
    if first_name:
        text = text.split(first_name)[0]
    return text.split("Posted")[1:], _urls_from_found(snapshot)


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
            _enable_response_logging(driver)
            logger.info("Validating Upwork login")
            login(driver, settings, lambda message: logger.warning(message))
            _log_document_responses(driver, logger)
            logger.info("Upwork login validation succeeded")
            return True
        except UpworkBlockedError as exc:
            _log_document_responses(driver, logger)
            logger.error("%s", exc)
            return False
        except Exception:
            _log_document_responses(driver, logger)
            logger.exception("Login validation failed")
            return False
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    logger.warning("Browser was already closed")

    if args.from_snapshot:
        conn, cursor = connect_to_db(settings.database_path)
        try:
            create_db(conn, cursor)
            snapshot = json.loads(Path(args.from_snapshot).read_text())
            job_posts, job_urls = _posts_and_urls(snapshot, settings.first_name)
            logger.info(
                "Loaded %d job posts and %d job links from snapshot", len(job_posts), len(job_urls)
            )
            counts = _process_jobs(job_posts, job_urls, cursor, logger)
            conn.commit()
            logger.info(
                "Snapshot import completed: %d inserted, %d updated, %d unchanged, %d failed",
                counts.inserted,
                counts.updated,
                counts.unchanged,
                counts.failed,
            )
            return True
        except (OSError, ValueError):
            logger.exception("Snapshot import failed")
            return False
        finally:
            cursor.close()
            conn.close()

    conn, cursor = connect_to_db(settings.database_path)
    driver = None
    try:
        create_db(conn, cursor)
        driver, browser = launch_driver(settings)
        logger.info("Using %s (%s)", browser.executable, browser.version)
        _enable_response_logging(driver)
        logger.info("Checking Upwork authentication")
        login(driver, settings, lambda message: logger.warning(message))
        logger.info("Upwork authentication ready")
        _log_document_responses(driver, logger)

        _load_job_list(driver, logger)

        snapshot = _save_snapshot(driver, logger, "after job list load")
        job_posts, job_urls = _posts_and_urls(snapshot, settings.first_name)
        logger.info("Parsed %d job posts and found %d job links", len(job_posts), len(job_urls))
        if not job_urls:
            # Reporting success here would hide both a block page and a changed
            # feed markup behind "0 jobs processed".
            raise RuntimeError(
                "No job links were found on the Best Matches feed; the page markup may have "
                "changed, or Upwork served an unexpected page"
            )
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
    except UpworkBlockedError as exc:
        _log_document_responses(driver, logger)
        logger.error("%s", exc)
        return False
    except Exception:
        _log_document_responses(driver, logger)
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
