"""Settings come from environment variables or a local .env file. Credentials never live in code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, # comments. Existing environment variables win."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_url: str
    username: str
    password: str
    headless: bool = True
    browser_channel: str = ""
    """Empty for Playwright's bundled Chromium, or "msedge" / "chrome" to use an installed browser."""
    slow_mo_ms: int = 0
    timeout_ms: int = 15_000
    retries: int = 3

    def __repr__(self) -> str:  # never print the password in logs or tracebacks
        return f"Settings(base_url={self.base_url!r}, username={self.username!r}, password='***')"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        missing = [k for k in ("PORTAL_URL", "PORTAL_USER", "PORTAL_PASSWORD") if not os.environ.get(k)]
        if missing:
            raise ValueError(f"Missing setting(s): {', '.join(missing)}. Copy .env.example to .env and fill it in.")
        return cls(
            base_url=os.environ["PORTAL_URL"].rstrip("/"),
            username=os.environ["PORTAL_USER"],
            password=os.environ["PORTAL_PASSWORD"],
            headless=_flag("HEADLESS", True),
            browser_channel=os.environ.get("BROWSER_CHANNEL", ""),
            slow_mo_ms=int(os.environ.get("SLOW_MO_MS", "0")),
            timeout_ms=int(os.environ.get("TIMEOUT_MS", "15000")),
            retries=int(os.environ.get("RETRIES", "3")),
        )
