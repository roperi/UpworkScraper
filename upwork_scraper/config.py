"""Typed runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def validate_proxy_server(value: str | None) -> str | None:
    """Validate a Chrome proxy value and normalize host:port shorthand."""

    if not value:
        return None
    proxy = value if "://" in value else f"http://{value}"
    parsed = urlsplit(proxy)
    if parsed.scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname:
        raise ConfigurationError(
            "UPWORK_PROXY_SERVER must use http[s], socks4, or socks5 and include a host"
        )
    if parsed.username or parsed.password:
        raise ConfigurationError("UPWORK_PROXY_SERVER must not contain proxy credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("UPWORK_PROXY_SERVER must contain a valid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise ConfigurationError("UPWORK_PROXY_SERVER must contain a port from 1 to 65535")
    return proxy


@dataclass(frozen=True)
class Settings:
    username: str
    password: str | None = field(repr=False)
    first_name: str
    browser_executable_path: str | None = None
    proxy_server: str | None = None
    driver_cache_dir: Path = Path.home() / ".cache" / "upwork-scraper"
    browser_profile_dir: Path = (
        Path.home() / ".local" / "state" / "upwork-scraper" / "chrome-profile"
    )
    database_path: Path = Path("upwork_jobs.db")
    verification_timeout: int = 120
    log_level: str = "INFO"
    openrouter_api_key: str | None = field(default=None, repr=False)
    openrouter_model: str | None = None

    @property
    def formatting_enabled(self) -> bool:
        """Description formatting needs both an OpenRouter key and a model."""

        return bool(self.openrouter_api_key and self.openrouter_model)


def _env(name: str, environ: Mapping[str, str]) -> str | None:
    value = environ.get(name)
    return value.strip() if value else None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings, with process environment taking precedence over ``.env``."""

    load_dotenv()
    values = os.environ if environ is None else environ
    username = _env("UPWORK_USERNAME", values)
    password = _env("UPWORK_PASSWORD", values)
    first_name = _env("UPWORK_FIRST_NAME", values)
    if not username:
        raise ConfigurationError("UPWORK_USERNAME must be set in the environment or .env")
    if not first_name:
        raise ConfigurationError("UPWORK_FIRST_NAME must be set in the environment or .env")

    timeout_text = _env("UPWORK_VERIFICATION_TIMEOUT", values) or "120"
    try:
        verification_timeout = int(timeout_text)
    except ValueError as exc:
        raise ConfigurationError("UPWORK_VERIFICATION_TIMEOUT must be an integer") from exc
    if verification_timeout <= 0:
        raise ConfigurationError("UPWORK_VERIFICATION_TIMEOUT must be greater than zero")

    return Settings(
        username=username,
        password=password,
        first_name=first_name,
        browser_executable_path=_env("BROWSER_EXECUTABLE_PATH", values),
        proxy_server=validate_proxy_server(_env("UPWORK_PROXY_SERVER", values)),
        driver_cache_dir=Path(
            _env("UPWORK_DRIVER_CACHE_DIR", values) or Path.home() / ".cache" / "upwork-scraper"
        ).expanduser(),
        browser_profile_dir=Path(
            _env("UPWORK_BROWSER_PROFILE_DIR", values)
            or Path.home() / ".local" / "state" / "upwork-scraper" / "chrome-profile"
        ).expanduser(),
        database_path=Path(_env("UPWORK_DATABASE_PATH", values) or "upwork_jobs.db"),
        verification_timeout=verification_timeout,
        log_level=(_env("LOG_LEVEL", values) or "INFO").upper(),
        openrouter_api_key=_env("OPENROUTER_API_KEY", values),
        openrouter_model=_env("OPENROUTER_MODEL", values),
    )
