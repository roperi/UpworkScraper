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


def test_login_handles_authenticated_redirect_from_login_url(monkeypatch):
    driver = FakeDriver("about:blank", redirect_url=BEST_MATCHES_URL)
    settings = Settings(username="user@example.com", password="secret", first_name="FirstName")

    def unexpected_cookie_scan(_driver):
        raise AssertionError("authenticated redirects must skip cookie scanning")

    monkeypatch.setattr(auth, "_dismiss_cookie_consent", unexpected_cookie_scan)

    auth.login(driver, settings, lambda _message: None)

    assert driver.visited_urls == [LOGIN_URL]


def test_login_warning_detects_security_interstitials():
    assert _login_warning("We detected abnormal behavior") == "abnormal behavior"
    assert _login_warning("The account has unusual activity") == "unusual activity"
    assert _login_warning("Normal login page") is None
