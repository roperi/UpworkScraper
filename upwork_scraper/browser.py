"""Deterministic browser discovery and WebDriver startup."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import undetected_chromedriver as uc  # type: ignore[import-untyped]

from .config import Settings


class BrowserError(RuntimeError):
    """Raised when a supported browser cannot be discovered or started."""


@dataclass(frozen=True)
class BrowserSpec:
    executable: Path
    version: str

    @property
    def major_version(self) -> int:
        return int(self.version.split(".", 1)[0])


def _version(executable: Path) -> str:
    result = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    match = re.search(r"(\d+(?:\.\d+){2,3})", result.stdout + result.stderr)
    if not match:
        raise BrowserError(f"Could not determine browser version from {executable}")
    return match.group(1)


def discover_browser(explicit_path: str | None = None) -> BrowserSpec:
    if explicit_path:
        executable = Path(explicit_path).expanduser()
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            raise BrowserError(f"Browser executable is not executable: {executable}")
        return BrowserSpec(executable, _version(executable))

    # Keep this ordered list deliberate. The dependency currently searches PATH
    # using an unordered set, which is unsafe when both browsers are installed.
    for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(candidate)
        if path:
            # Preserve snap wrappers such as /snap/bin/chromium; resolving them
            # would turn the browser path into /usr/bin/snap.
            executable = Path(path)
            return BrowserSpec(executable, _version(executable))
    raise BrowserError("No supported browser found; install Chrome or Chromium")


def _cached_driver_path(spec: BrowserSpec, cache_dir: Path) -> Path:
    return cache_dir / f"undetected-chromedriver-{spec.major_version}"


def _cache_driver(driver, destination: Path) -> None:
    source = Path(driver.patcher.executable_path)
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        with source.open("rb") as original:
            shutil.copyfileobj(original, temporary)
    temporary_path.chmod(0o755)
    temporary_path.replace(destination)


def _launch_with_driver(settings: Settings, spec: BrowserSpec, driver_path: Path | None = None):
    options = uc.ChromeOptions()
    options.headless = False
    options.page_load_strategy = "eager"
    options.add_argument("--disable-dev-shm-usage")
    options.user_data_dir = str(settings.browser_profile_dir)
    if settings.proxy_server:
        options.add_argument(f"--proxy-server={settings.proxy_server}")
    arguments = {
        "options": options,
        "browser_executable_path": str(spec.executable),
        "version_main": spec.major_version,
        "use_subprocess": True,
    }
    if driver_path is not None:
        arguments["driver_executable_path"] = str(driver_path)
    return uc.Chrome(**arguments)


def launch_driver(settings: Settings):
    spec = discover_browser(settings.browser_executable_path)
    cached_driver = _cached_driver_path(spec, settings.driver_cache_dir)
    if cached_driver.is_file():
        try:
            return _launch_with_driver(settings, spec, cached_driver), spec
        except Exception:
            # Keep the last known-good file. A fresh attempt may recover from a
            # stale driver, while a failed refresh must never erase the cache.
            pass

    try:
        driver = _launch_with_driver(settings, spec)
    except Exception as exc:
        raise BrowserError(
            f"Could not start ChromeDriver for browser major version {spec.major_version}. "
            "No usable cached driver was available; network access may be required for first setup."
        ) from exc
    _cache_driver(driver, cached_driver)
    return driver, spec
