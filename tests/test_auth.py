from selenium.common.exceptions import TimeoutException

from upwork_scraper import auth
from upwork_scraper.auth import (
    BEST_MATCHES_URL,
    LOGIN_URL,
    USERNAME_FIELD_IDS,
    USERNAME_SUBMIT_IDS,
    _authenticated,
    _login_warning,
    _visible_button,
    _visible_element,
    _wait_for_login_field,
    _wait_for_submit,
)
from upwork_scraper.config import Settings


class FakeElement:
    def __init__(self, text="", displayed=True, enabled=True, value=""):
        self.text = text
        self._displayed = displayed
        self._enabled = enabled
        self.value = value

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled

    def get_attribute(self, name):
        return self.value if name == "value" else None


class FakeDriver:
    def __init__(self, url, buttons=None, fields=None, redirect_url=None):
        self.current_url = url
        self.buttons = buttons or []
        self.fields = fields or {}
        self.redirect_url = redirect_url
        self.visited_urls = []
        self.challenge = False
        self.tokens = []
        self.body_text = ""
        self.blocked = False

    def get(self, url):
        self.visited_urls.append(url)
        if url == LOGIN_URL and self.redirect_url is not None:
            self.current_url = self.redirect_url
        else:
            self.current_url = url

    def find_elements(self, by, selector):
        if by == "id":
            element = self.fields.get(selector)
            return [element] if element is not None else []
        if selector == "form button":
            return self.buttons
        if selector == auth.TURNSTILE_RESPONSE_SELECTOR:
            return self.tokens
        return []

    def find_element(self, by, selector):
        if selector == "body":
            return FakeElement(text=self.body_text)
        raise AssertionError(f"unexpected find_element({by!r}, {selector!r})")

    def execute_script(self, script, *_args):
        if script == auth.CLOUDFLARE_CHALLENGE_JS:
            if isinstance(self.challenge, list):
                return self.challenge.pop(0)
            return self.challenge
        if script == auth.BLOCKED_PAGE_JS:
            return self.blocked
        return None


def test_visible_button_ignores_hidden_and_disabled_controls():
    driver = FakeDriver(
        LOGIN_URL,
        buttons=[
            FakeElement("Continue", displayed=False),
            FakeElement("Continue", enabled=False),
            FakeElement("Continue"),
        ],
    )

    assert _visible_button(driver) is driver.buttons[2]


def test_visible_button_prefers_current_upwork_submit_id():
    button = FakeElement("Continue")
    driver = FakeDriver(
        LOGIN_URL,
        buttons=[FakeElement("Continue with Google")],
        fields={"username_password_continue": button},
    )

    assert _visible_button(driver, USERNAME_SUBMIT_IDS) is button


def test_visible_element_accepts_redesigned_username_field():
    username = FakeElement()
    driver = FakeDriver(LOGIN_URL, fields={"username_username": username})

    assert _visible_element(driver, USERNAME_FIELD_IDS) is username


def test_wait_for_login_field_returns_username_after_cloudflare(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)
    username = FakeElement()
    driver = FakeDriver(
        LOGIN_URL,
        fields={"username_username": username},
        redirect_url=None,
    )
    driver.challenge = [True, False]

    assert _wait_for_login_field(driver, timeout=10, logger=lambda _message: None) is username


def test_wait_for_submit_waits_for_turnstile_token(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)
    button = FakeElement("Continue")
    driver = FakeDriver(LOGIN_URL, buttons=[button])
    driver.tokens = [FakeElement(value="")]

    def issue_token(_seconds):
        driver.tokens[0].value = "token"

    monkeypatch.setattr(auth.time, "sleep", issue_token)

    assert _wait_for_submit(driver, USERNAME_SUBMIT_IDS, 10, lambda _message: None) is button


def test_wait_for_submit_raises_without_turnstile_token():
    driver = FakeDriver(LOGIN_URL, buttons=[FakeElement("Continue")])
    driver.tokens = [FakeElement(value="")]

    try:
        _wait_for_submit(driver, USERNAME_SUBMIT_IDS, 0, lambda _message: None)
    except Exception as exc:
        assert "Cloudflare" in str(exc)
    else:
        raise AssertionError("expected a timeout while Turnstile is unresolved")


def test_authenticated_requires_leaving_login_page():
    login_driver = FakeDriver(
        LOGIN_URL,
        fields={"login_password": FakeElement(displayed=False)},
    )
    authenticated_driver = FakeDriver(
        BEST_MATCHES_URL,
        fields={},
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


def test_login_navigates_authenticated_profile_to_best_matches(monkeypatch):
    driver = FakeDriver("https://www.upwork.com/nx/find-work/home")
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")

    auth.login(driver, settings, lambda _message: None)

    assert driver.visited_urls == [BEST_MATCHES_URL]


def test_login_opens_best_matches_directly_for_a_signed_in_profile(monkeypatch):
    """A persistent session must not route through the login page first."""

    driver = FakeDriver("about:blank", redirect_url=BEST_MATCHES_URL)
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")

    def unexpected_cookie_scan(_driver):
        raise AssertionError("authenticated redirects must skip cookie scanning")

    monkeypatch.setattr(auth, "_dismiss_cookie_consent", unexpected_cookie_scan)

    auth.login(driver, settings, lambda _message: None)

    assert driver.visited_urls == [BEST_MATCHES_URL]


def test_login_falls_back_to_the_login_page_when_the_feed_is_unavailable(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)
    driver = FakeDriver("about:blank")
    settings = Settings(
        username="user@example.com",
        password="secret",
        first_name="FirstName",
        verification_timeout=0,
    )

    try:
        auth.login(driver, settings, lambda _message: None)
    except TimeoutException:
        pass
    else:
        raise AssertionError("expected a timeout without a feed or login form")

    assert driver.visited_urls == [BEST_MATCHES_URL, LOGIN_URL]


def test_login_warning_detects_security_interstitials():
    assert _login_warning("We detected abnormal behavior") == "abnormal behavior"
    assert _login_warning("The account has unusual activity") == "unusual activity"
    assert _login_warning("Normal login page") is None


def test_at_best_matches_accepts_the_session_scoped_redirect_url():
    assert auth._at_best_matches(FakeDriver("https://www.upwork.com/nx/find-work/best-matches"))
    assert auth._at_best_matches(FakeDriver("https://www.upwork.com/nx/s/find-work/best-matches"))
    assert not auth._at_best_matches(FakeDriver("https://www.upwork.com/nx/find-work/home"))


def test_blocked_page_is_not_treated_as_authenticated():
    driver = FakeDriver(BEST_MATCHES_URL)
    driver.blocked = True

    assert auth._blocked_page(driver) is True
    assert _authenticated(driver) is False


def test_login_fails_fast_when_cloudflare_blocks_the_login_page():
    driver = FakeDriver(LOGIN_URL)
    driver.blocked = True
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")

    try:
        auth.login(driver, settings, lambda _message: None)
    except auth.UpworkBlockedError as exc:
        assert "bot scoring" in str(exc)
    else:
        raise AssertionError("expected UpworkBlockedError while Cloudflare blocks the network")


def test_go_to_best_matches_fails_fast_when_cloudflare_blocks():
    class BlockingDriver(FakeDriver):
        def get(self, url):
            super().get(url)
            self.blocked = True

    driver = BlockingDriver("https://www.upwork.com/nx/find-work/home")

    try:
        auth._go_to_best_matches(driver)
    except auth.UpworkBlockedError:
        pass
    else:
        raise AssertionError("expected UpworkBlockedError from the block page")
