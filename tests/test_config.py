from pathlib import Path

import pytest

from upwork_scraper.config import ConfigurationError, load_settings, validate_proxy_server


def test_load_settings_from_environment() -> None:
    settings = load_settings(
        {
            "UPWORK_USERNAME": "user@example.com",
            "UPWORK_PASSWORD": "secret",
            "UPWORK_FIRST_NAME": "FirstName",
            "UPWORK_DATABASE_PATH": "/tmp/jobs.db",
            "UPWORK_VERIFICATION_TIMEOUT": "45",
            "UPWORK_BROWSER_PROFILE_DIR": "/tmp/upwork-profile",
        }
    )
    assert settings.username == "user@example.com"
    assert settings.password == "secret"
    assert settings.database_path == Path("/tmp/jobs.db")
    assert settings.verification_timeout == 45
    assert settings.browser_profile_dir == Path("/tmp/upwork-profile")


def test_missing_username_fails_without_exposing_values() -> None:
    with pytest.raises(ConfigurationError, match="UPWORK_USERNAME must be set"):
        load_settings({})


def test_password_is_optional_for_provider_login() -> None:
    settings = load_settings(
        {"UPWORK_USERNAME": "user@example.com", "UPWORK_FIRST_NAME": "FirstName"}
    )

    assert settings.username == "user@example.com"
    assert settings.password is None


def test_invalid_timeout_fails() -> None:
    with pytest.raises(ConfigurationError, match="must be an integer"):
        load_settings(
            {
                "UPWORK_USERNAME": "user@example.com",
                "UPWORK_PASSWORD": "secret",
                "UPWORK_FIRST_NAME": "FirstName",
                "UPWORK_VERIFICATION_TIMEOUT": "never",
            }
        )


def test_proxy_server_is_normalized_and_loaded() -> None:
    settings = load_settings(
        {
            "UPWORK_USERNAME": "user@example.com",
            "UPWORK_PASSWORD": "secret",
            "UPWORK_FIRST_NAME": "FirstName",
            "UPWORK_PROXY_SERVER": "127.0.0.1:1080",
        }
    )
    assert settings.proxy_server == "http://127.0.0.1:1080"


def test_proxy_server_rejects_embedded_credentials() -> None:
    with pytest.raises(ConfigurationError, match="must not contain proxy credentials"):
        validate_proxy_server("socks5://user:secret@127.0.0.1:1080")


def test_missing_profile_name_fails() -> None:
    with pytest.raises(ConfigurationError, match="UPWORK_FIRST_NAME must be set"):
        load_settings({"UPWORK_USERNAME": "user@example.com", "UPWORK_PASSWORD": "secret"})


BASE_ENV = {"UPWORK_USERNAME": "user@example.com", "UPWORK_FIRST_NAME": "FirstName"}


def test_formatting_is_disabled_by_default() -> None:
    settings = load_settings(BASE_ENV)

    assert settings.openrouter_api_key is None
    assert settings.openrouter_model is None
    assert settings.formatting_enabled is False


@pytest.mark.parametrize(
    "extra",
    [
        {"OPENROUTER_API_KEY": "key-value"},
        {"OPENROUTER_MODEL": "vendor/model"},
        {"OPENROUTER_API_KEY": "  ", "OPENROUTER_MODEL": "vendor/model"},
        {"OPENROUTER_API_KEY": "key-value", "OPENROUTER_MODEL": "  "},
    ],
)
def test_formatting_needs_both_key_and_model(extra) -> None:
    assert load_settings({**BASE_ENV, **extra}).formatting_enabled is False


def test_formatting_enabled_with_both_and_key_is_hidden_from_repr() -> None:
    settings = load_settings(
        {**BASE_ENV, "OPENROUTER_API_KEY": " key-value ", "OPENROUTER_MODEL": " vendor/model "}
    )

    assert settings.formatting_enabled is True
    assert settings.openrouter_model == "vendor/model"
    assert "key-value" not in repr(settings)
