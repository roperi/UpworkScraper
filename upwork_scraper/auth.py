"""Upwork authentication workflow."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

from selenium.common.exceptions import (
    InvalidSessionIdException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from .config import Settings

LOGIN_URL = "https://www.upwork.com/ab/account-security/login"
BEST_MATCHES_URL = "https://www.upwork.com/nx/find-work/best-matches"
PASSWORD_STEP_TIMEOUT = 30


def _visible_button(driver):
    for button in driver.find_elements(By.CSS_SELECTOR, "form#login button"):
        try:
            if (
                button.is_displayed()
                and button.is_enabled()
                and button.text.strip() in {"Continue", "Log in", "Sign in"}
            ):
                return button
        except StaleElementReferenceException:
            continue
    return None


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


def _click_submit(driver, field) -> None:
    button = _visible_button(driver)
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
    except StaleElementReferenceException:
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
    login_step = WebDriverWait(driver, 30).until(
        lambda d: _authenticated(d) or _visible_element_by_id(d, "login_username")
    )
    if login_step is True:
        _go_to_best_matches(driver)
        return

    _dismiss_cookie_consent(driver)
    username = login_step
    username.clear()
    username.send_keys(settings.username)
    _click_submit(driver, username)

    password = None
    if settings.password is not None:
        try:
            password_step = WebDriverWait(driver, PASSWORD_STEP_TIMEOUT).until(
                lambda d: _authenticated(d) or _visible_element_by_id(d, "login_password")
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
        password.clear()
        password.send_keys(settings.password)
        _click_submit(driver, password)

    deadline = time.monotonic() + settings.verification_timeout
    reported_warning: str | None = None
    while time.monotonic() < deadline:
        if _authenticated(driver):
            _go_to_best_matches(driver)
            return
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
