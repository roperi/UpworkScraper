"""Upwork authentication workflow."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

from selenium.common.exceptions import (
    InvalidSessionIdException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from .config import Settings

LOGIN_URL = "https://www.upwork.com/ab/account-security/login"
BEST_MATCHES_URL = "https://www.upwork.com/nx/find-work/best-matches"
PASSWORD_STEP_TIMEOUT = 30

# Upwork's current login form uses ``#username_username``; older sessions and
# A/B variants still expose ``#login_username``.
USERNAME_FIELD_IDS = ("username_username", "login_username")
PASSWORD_FIELD_IDS = ("login_password",)
USERNAME_SUBMIT_IDS = ("username_password_continue",)
PASSWORD_SUBMIT_IDS = ("login_control_continue",)
SUBMIT_LABELS = {"Continue", "Log in", "Sign in"}
TURNSTILE_RESPONSE_SELECTOR = 'input[name="cf-turnstile-response"]'
CLOUDFLARE_CHALLENGE_JS = """
const interstitial = document.querySelector(
  '.up-challenge-container, #challenge-form, #cf-challenge-running, .cf-browser-verification'
);
if (interstitial) return true;
const title = (document.title || '').toLowerCase();
return title.includes('just a moment') || title === 'challenge - upwork';
"""


def _visible_button(driver, button_ids=()):
    for button_id in button_ids:
        button = _visible_element_by_id(driver, button_id)
        if button is None:
            continue
        try:
            if button.is_enabled():
                return button
        except StaleElementReferenceException:
            continue
    for button in driver.find_elements(By.CSS_SELECTOR, "form button"):
        try:
            if (
                button.is_displayed()
                and button.is_enabled()
                and button.text.strip() in SUBMIT_LABELS
            ):
                return button
        except StaleElementReferenceException:
            continue
    return None


def _visible_element(driver, element_ids):
    for element_id in element_ids:
        element = _visible_element_by_id(driver, element_id)
        if element is not None:
            return element
    return None


def _cloudflare_challenge(driver) -> bool:
    """Report whether Cloudflare is still showing its interstitial page."""

    try:
        return bool(driver.execute_script(CLOUDFLARE_CHALLENGE_JS))
    except InvalidSessionIdException:
        raise
    except WebDriverException:
        # A navigation can destroy the execution context mid-check.
        return False


def _turnstile_token(driver) -> str | None:
    """Return the Turnstile token, or ``None`` when the widget is absent."""

    tokens = driver.find_elements(By.CSS_SELECTOR, TURNSTILE_RESPONSE_SELECTOR)
    if not tokens:
        return None
    try:
        return (tokens[0].get_attribute("value") or "").strip()
    except StaleElementReferenceException:
        # Cloudflare can replace the widget while the check completes.
        return ""


def _submit_ready(driver, button_ids):
    """Return an enabled submit button once Turnstile has issued its token."""

    button = _visible_button(driver, button_ids)
    if button is None or _turnstile_token(driver) == "":
        return None
    return button


def _wait_for_submit(driver, button_ids, timeout: int, logger):
    """Wait for Upwork's invisible Turnstile check before submitting a step.

    The redesigned form keeps its submit button disabled until Cloudflare's
    widget produces ``cf-turnstile-response``. Clicking while the check is
    running leaves the form on the same step and later times out.
    """

    deadline = time.monotonic() + timeout
    challenge_reported = False
    while time.monotonic() < deadline:
        button = _submit_ready(driver, button_ids)
        if button is not None:
            return button
        if _turnstile_token(driver) == "" and not challenge_reported:
            logger("Cloudflare is verifying the browser; waiting for the check to finish")
            challenge_reported = True
        time.sleep(0.5)
    raise TimeoutException(
        "Cloudflare verification did not finish within "
        f"{timeout} seconds; current URL: {driver.current_url}"
    )


def _wait_for_login_field(driver, timeout: int, logger):
    """Wait out Cloudflare and return the first login step.

    Returns ``True`` when the persistent profile is already authenticated and
    the username field element otherwise.
    """

    deadline = time.monotonic() + timeout
    challenge_reported = False
    while time.monotonic() < deadline:
        if _authenticated(driver):
            return True
        if _cloudflare_challenge(driver):
            if not challenge_reported:
                logger("Cloudflare is verifying the browser; waiting for the login page")
                challenge_reported = True
        else:
            field = _visible_element(driver, USERNAME_FIELD_IDS)
            if field is not None:
                return field
        time.sleep(0.5)
    raise TimeoutException(
        "Upwork did not display the login form within "
        f"{timeout} seconds; current URL: {driver.current_url}"
    )


def _dismiss_cookie_consent(driver) -> None:
    """Dismiss the consent overlay on a fresh automation profile."""

    for _ in range(10):
        buttons = driver.find_elements(
            By.XPATH,
            "//button[normalize-space()='Reject All' or normalize-space()='Accept All']",
        )
        consent = next(
            (
                button
                for button in buttons
                if button.is_displayed() and button.text.strip() in {"Reject All", "Accept All"}
            ),
            None,
        )
        if consent is not None:
            # The consent controls can be below the viewport and Selenium's
            # coordinate click can be intercepted by the fixed overlay itself.
            driver.execute_script("arguments[0].click()", consent)
            return
        time.sleep(0.5)


def _retry_on_invalid_state(driver, element_ids, use_element, attempts: int = 4):
    """Run ``use_element(element)`` while Upwork settles the login page.

    A field can be briefly covered by consent or spinner overlays, making it
    visible but not interactable. Refreshing the element and retrying a few
    times recovers from this without failing the whole run.
    """

    last_error: Exception | None = None
    label = "/".join(element_ids)
    for attempt in range(attempts):
        element = _visible_element(driver, element_ids)
        if element is None:
            last_error = RuntimeError(f"{label} is no longer visible")
        else:
            try:
                return use_element(element)
            except Exception as exc:
                last_error = exc
        if attempt < attempts - 1:
            time.sleep(1.5)
    raise last_error  # type: ignore[misc]


def _click_submit(driver, field, button_ids, timeout: int, logger) -> None:
    button = _wait_for_submit(driver, button_ids, timeout, logger)
    if button is not None:
        button.click()
    else:
        field.send_keys(Keys.ENTER)


def _authenticated(driver) -> bool:
    try:
        parsed_url = urlsplit(driver.current_url)
        hostname = parsed_url.hostname or ""
        if hostname != "upwork.com" and not hostname.endswith(".upwork.com"):
            return False
        if "/account-security/login" in parsed_url.path:
            return False
        return not any(e.is_displayed() for e in driver.find_elements(By.ID, "login_password"))
    except StaleElementReferenceException:
        return False
    except InvalidSessionIdException as exc:
        raise RuntimeError("Chrome closed the browser session during authentication") from exc


def _at_best_matches(driver) -> bool:
    parsed_url = urlsplit(driver.current_url)
    return parsed_url.path.rstrip("/") == "/nx/find-work/best-matches"


def _go_to_best_matches(driver) -> None:
    if not _at_best_matches(driver):
        driver.get(BEST_MATCHES_URL)


def _visible_element_by_id(driver, element_id: str):
    try:
        return next((e for e in driver.find_elements(By.ID, element_id) if e.is_displayed()), None)
    except InvalidSessionIdException:
        raise
    except WebDriverException:
        # The document can be replaced while Cloudflare reloads the page.
        return None


def _body_text(driver) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return ""


def _login_warning(text: str) -> str | None:
    markers = (
        "technical difficulties",
        "incorrect",
        "abnormal behavior",
        "unusual activity",
        "suspicious activity",
    )
    return next((marker for marker in markers if marker in text), None)


def login(driver, settings: Settings, logger: Callable[[str], None]) -> None:
    if _authenticated(driver):
        _go_to_best_matches(driver)
        return

    driver.get(LOGIN_URL)
    login_step = _wait_for_login_field(driver, settings.verification_timeout, logger)
    if login_step is True:
        _go_to_best_matches(driver)
        return

    _dismiss_cookie_consent(driver)

    _retry_on_invalid_state(
        driver,
        USERNAME_FIELD_IDS,
        lambda field: (field.clear(), field.send_keys(settings.username)),
    )
    _retry_on_invalid_state(
        driver,
        USERNAME_FIELD_IDS,
        lambda field: _click_submit(
            driver, field, USERNAME_SUBMIT_IDS, settings.verification_timeout, logger
        ),
    )

    password = None
    if settings.password is not None:
        try:
            password_step = WebDriverWait(driver, PASSWORD_STEP_TIMEOUT).until(
                lambda d: _authenticated(d) or _visible_element(d, PASSWORD_FIELD_IDS)
            )
            if password_step is not True:
                password = password_step
        except TimeoutException:
            logger(
                "Upwork did not display a password field; complete Google, Apple, "
                "or another login step in the visible browser"
            )
    else:
        logger(
            "No UPWORK_PASSWORD is configured; complete Google, Apple, password, "
            "or two-step verification in the visible browser"
        )

    if password is not None:
        _retry_on_invalid_state(
            driver,
            PASSWORD_FIELD_IDS,
            lambda field: (field.clear(), field.send_keys(settings.password)),
        )
        _retry_on_invalid_state(
            driver,
            PASSWORD_FIELD_IDS,
            lambda field: _click_submit(
                driver, field, PASSWORD_SUBMIT_IDS, settings.verification_timeout, logger
            ),
        )

    deadline = time.monotonic() + settings.verification_timeout
    reported_warning: str | None = None
    challenge_reported = False
    while time.monotonic() < deadline:
        if _authenticated(driver):
            _go_to_best_matches(driver)
            return
        if not challenge_reported and _cloudflare_challenge(driver):
            logger("Cloudflare is verifying the browser; waiting for it to finish")
            challenge_reported = True
        text = _body_text(driver).lower()
        warning = _login_warning(text)
        if warning is not None and warning != reported_warning:
            logger(
                "Upwork displayed a login/security warning "
                f"({warning}); waiting for manual resolution"
            )
            reported_warning = warning
        time.sleep(1)
    raise TimeoutException(
        f"Authentication did not complete within {settings.verification_timeout} seconds; "
        f"current URL: {driver.current_url}"
    )
