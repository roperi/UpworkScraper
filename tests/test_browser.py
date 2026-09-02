from pathlib import Path

from upwork_scraper import browser
from upwork_scraper.browser import BrowserSpec
from upwork_scraper.config import Settings


def test_browser_spec_extracts_major_version() -> None:
    spec = BrowserSpec(Path("/usr/bin/google-chrome"), "151.0.7922.173")
    assert spec.major_version == 151


def test_discovery_prefers_google_chrome(monkeypatch) -> None:
    monkeypatch.setattr(
        browser.shutil,
        "which",
        lambda name: {
            "google-chrome": "/opt/google/chrome/chrome",
            "chromium": "/snap/bin/chromium",
        }.get(name),
    )
    monkeypatch.setattr(browser, "_version", lambda path: "151.0.7922.173")

    spec = browser.discover_browser()

    assert spec.executable == Path("/opt/google/chrome/chrome")
    assert spec.major_version == 151


def test_launch_passes_profile_and_proxy_to_chrome(monkeypatch, tmp_path) -> None:
    class FakeOptions:
        def __init__(self):
            self.arguments = []
            self.user_data_dir = None

        def add_argument(self, argument):
            self.arguments.append(argument)

    options = FakeOptions()
    monkeypatch.setattr(browser.uc, "ChromeOptions", lambda: options)
    monkeypatch.setattr(browser.uc, "Chrome", lambda **kwargs: kwargs)
    settings = Settings(
        username="user@example.com",
        password="secret",
        first_name="FirstName",
        proxy_server="socks5://127.0.0.1:1080",
        browser_profile_dir=tmp_path / "profile",
    )
    spec = BrowserSpec(Path("/usr/bin/google-chrome"), "151.0.0")

    result = browser._launch_with_driver(settings, spec)

    assert result["options"] is options
    assert options.page_load_strategy == "eager"
    assert options.user_data_dir == str(tmp_path / "profile")
    assert "--proxy-server=socks5://127.0.0.1:1080" in options.arguments
