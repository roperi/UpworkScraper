from upwork_scraper import auth
from upwork_scraper.auth import (
    BEST_MATCHES_URL,
    LOGIN_URL,
    _authenticated,
    _login_warning,
    _visible_button,
)
from upwork_scraper.config import Settings


class FakeElement:
    def __init__(self, text="", displayed=True, enabled=True):
        self.text = text
        self._displayed = displayed
        self._enabled = enabled

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled


class FakeDriver:
    def __init__(self, url, buttons=None, password_fields=None, redirect_url=None):
        self.current_url = url
        self.buttons = buttons or []
        self.password_fields = password_fields or []
        self.redirect_url = redirect_url
        self.visited_urls = []

    def get(self, url):
        self.visited_urls.append(url)
        if url == LOGIN_URL and self.redirect_url is not None:
            self.current_url = self.redirect_url
        else:
            self.current_url = url

    def find_elements(self, _by, selector):
        if selector == "form#login button":
            return self.buttons
        if selector == "login_password":
            return self.password_fields
        return []


def test_visible_button_ignores_hidden_and_disabled_controls():
    driver = FakeDriver(
        "https://www.upwork.com/ab/account-security/login",
        buttons=[
            FakeElement("Continue", displayed=False),
            FakeElement("Continue", enabled=False),
            FakeElement("Continue"),
        ],
    )

    assert _visible_button(driver) is driver.buttons[2]


def test_authenticated_requires_leaving_login_page():
    login_driver = FakeDriver(
        "https://www.upwork.com/ab/account-security/login",
        password_fields=[FakeElement(displayed=False)],
    )
    authenticated_driver = FakeDriver(
        "https://www.upwork.com/nx/find-work/best-matches",
        password_fields=[],
    )

    assert not _authenticated(login_driver)
    assert _authenticated(authenticated_driver)


def test_authenticated_rejects_external_provider_page():
    provider_driver = FakeDriver("https://accounts.google.com/signin")

    assert not _authenticated(provider_driver)


def test_login_reuses_authenticated_profile_on_best_matches(monkeypatch):
    driver = FakeDriver(BEST_MATCHES_URL)
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")

    auth.login(driver, settings, lambda _message: None)

    assert driver.visited_urls == []


def test_login_navigates_authenticated_profile_to_best_matches():
    driver = FakeDriver("https://www.upwork.com/nx/find-work/home")
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")

    auth.login(driver, settings, lambda _message: None)

    assert driver.visited_urls == [BEST_MATCHES_URL]


def test_login_handles_authenticated_redirect_from_login_url(monkeypatch):
    driver = FakeDriver("about:blank", redirect_url=BEST_MATCHES_URL)
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")
    monkeypatch.setattr(auth, "_dismiss_cookie_consent", lambda _driver: None)

    auth.login(driver, settings, lambda _message: None)

    assert driver.visited_urls == [LOGIN_URL]


def test_login_warning_detects_security_interstitials():
    assert _login_warning("We detected abnormal behavior") == "abnormal behavior"
    assert _login_warning("The account has unusual activity") == "unusual activity"
    assert _login_warning("Normal login page") is None
