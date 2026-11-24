"""The robot itself: signs in, downloads reports and submits forms like a careful person would.

Built for unreliable portals: explicit waits instead of sleeps, retries with backoff for
temporary errors, automatic re-login when the session expires, and a screenshot of every
failure so someone can see what went wrong.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .config import Settings

log = logging.getLogger("portal_robot")
T = TypeVar("T")

DONE, REJECTED, FAILED = "done", "rejected", "failed"


class TransientError(Exception):
    """Something that is worth trying again: a 503 page, a timeout, an expired session."""


class SessionExpired(TransientError):
    pass


class LoginError(Exception):
    """Wrong credentials. Retrying would only lock the account, so the robot stops."""


@dataclass
class ReportJob:
    company: str
    month: str
    report_type: str

    @property
    def key(self) -> str:
        return f"{self.company} · {self.month} · {self.report_type}"


@dataclass
class InvoiceRequest:
    company: str
    supplier_vat: str
    invoice_number: str
    invoice_date: str
    amount: str
    category: str
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.company} · {self.invoice_number}"


@dataclass
class TaskResult:
    task: str
    key: str
    status: str
    detail: str = ""
    output: str = ""
    attempts: int = 0
    screenshot: str = ""
    seconds: float = 0.0
    errors: dict[str, str] = field(default_factory=dict)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class PortalRobot:
    def __init__(self, settings: Settings, out_dir: str | Path, frames_dir: str | Path | None = None):
        self.settings = settings
        self.out_dir = Path(out_dir)
        self.frames_dir = Path(frames_dir) if frames_dir else None
        self._frame = 0
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    # ----- lifecycle -------------------------------------------------------------------------
    def __enter__(self) -> "PortalRobot":
        (self.out_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        if self.frames_dir:
            self.frames_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        launch = {"headless": self.settings.headless, "slow_mo": self.settings.slow_mo_ms}
        if self.settings.browser_channel:
            launch["channel"] = self.settings.browser_channel
        self._browser = self._pw.chromium.launch(**launch)
        self._context = self._browser.new_context(accept_downloads=True, viewport={"width": 1100, "height": 760})
        self._context.set_default_timeout(self.settings.timeout_ms)
        self.page = self._context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        for closer in (self._context, self._browser):
            try:
                closer and closer.close()
            except Exception:  # noqa: BLE001 - closing must never hide the real error
                pass
        if self._pw:
            self._pw.stop()

    # ----- helpers ---------------------------------------------------------------------------
    def frame(self, label: str) -> None:
        """Saves a numbered screenshot, used to build the demo GIF."""
        if self.frames_dir:
            self._frame += 1
            self.page.screenshot(path=self.frames_dir / f"{self._frame:03d}_{_slug(label)}.png")

    def screenshot(self, name: str) -> str:
        path = self.out_dir / "screenshots" / f"{time.strftime('%H%M%S')}_{_slug(name)}.png"
        try:
            self.page.screenshot(path=path, full_page=True)
            return str(path)
        except Exception:  # noqa: BLE001
            return ""

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url}{path}"

    def _raise_if_unavailable(self) -> None:
        heading = self.page.locator("h1").first
        if heading.count() and re.search(r"Error 5\d\d", heading.inner_text()):
            raise TransientError(self.page.locator("#error-message").inner_text())
        if "/login" in self.page.url:
            raise SessionExpired("Session expired")

    def _with_retries(self, description: str, action: Callable[[], T]) -> tuple[T, int]:
        """Run `action`, retrying temporary failures with exponential backoff.

        An expired session is not the task's fault: the robot signs in again and repeats the
        step without spending an attempt (up to `max_relogins` times, to avoid looping forever).
        """
        attempts, max_relogins = self.settings.retries, 3
        attempt = relogins = 0
        while True:
            attempt += 1
            try:
                return action(), attempt
            except LoginError:
                raise
            except SessionExpired as exc:
                relogins += 1
                if relogins > max_relogins:
                    raise TransientError(f"Session keeps expiring (signed in again {max_relogins} times)") from exc
                attempt -= 1
                log.info("%s: session expired, signing in again", description)
                self.login()
            except (TransientError, PlaywrightTimeout) as exc:
                reason = str(exc).splitlines()[0].rstrip(".")
                if attempt >= attempts:
                    raise TransientError(f"{reason} (gave up after {attempts} attempts)") from exc
                wait = 0.5 * 2 ** (attempt - 1)
                log.warning("%s: %s. Retrying in %.1fs (attempt %d of %d)", description, reason, wait, attempt + 1, attempts)
                time.sleep(wait)

    # ----- actions ---------------------------------------------------------------------------
    def login(self) -> None:
        page = self.page
        page.goto(self._url("/login"))
        page.fill("#username", self.settings.username)
        page.fill("#password", self.settings.password)
        self.frame("login")
        page.click("#login")
        page.wait_for_load_state()
        if page.locator("#login-error").count():
            raise LoginError(page.locator("#login-error").inner_text())
        log.info("Signed in as %s", self.settings.username)
        self.frame("signed in")

    def _select(self, selector: str, label: str) -> None:
        """Select by visible text, failing fast (no retries) when the option does not exist."""
        options = [o.strip() for o in self.page.locator(f"{selector} option").all_inner_texts()]
        if label not in options:
            raise ValueError(f"{label!r} is not an option in {selector} (options: {', '.join(o for o in options if not o.startswith('--'))})")
        self.page.select_option(selector, label=label)

    def _open(self, path: str) -> None:
        self.page.goto(self._url(path))
        self._raise_if_unavailable()

    def download_report(self, job: ReportJob) -> TaskResult:
        started = time.monotonic()

        def attempt() -> Path:
            page = self.page
            self._open("/reports")
            self._select("#company", job.company)
            page.fill("#month", job.month)
            self._select("#report_type", job.report_type)
            self.frame(f"report form {job.company}")
            page.click("#generate")
            page.wait_for_load_state()
            self._raise_if_unavailable()
            # Wait for whichever comes first: the download link or the "session expired" notice.
            page.locator("#download:visible, #session-expired:visible").first.wait_for(timeout=60_000)
            if page.locator("#session-expired").is_visible():
                raise SessionExpired("Session expired while the report was being generated")
            self.frame(f"report ready {job.company}")
            with page.expect_download() as info:
                page.click("#download")
            target = self.out_dir / "downloads" / _slug(job.company) / f"{job.month}_{_slug(job.report_type)}.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            info.value.save_as(target)
            return target

        try:
            path, attempts = self._with_retries(f"Report {job.key}", attempt)
        except (TransientError, PlaywrightTimeout) as exc:
            return TaskResult("report", job.key, FAILED, str(exc), screenshot=self.screenshot(f"failed {job.key}"),
                              attempts=self.settings.retries, seconds=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 - e.g. a company that is not in the list
            return TaskResult("report", job.key, FAILED, f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
                              screenshot=self.screenshot(f"failed {job.key}"), attempts=1,
                              seconds=time.monotonic() - started)
        log.info("Downloaded %s -> %s", job.key, path)
        return TaskResult("report", job.key, DONE, "Downloaded", str(path), attempts, seconds=time.monotonic() - started)

    def submit_invoice(self, req: InvoiceRequest) -> TaskResult:
        started = time.monotonic()

        def attempt() -> tuple[str, dict[str, str]]:
            page = self.page
            self._open("/requests/new")
            self._select("#company", req.company)
            page.fill("#supplier_vat", req.supplier_vat)
            page.fill("#invoice_number", req.invoice_number)
            page.fill("#invoice_date", req.invoice_date)
            page.fill("#amount", req.amount)
            if req.category:
                self._select("#category", req.category)
            page.fill("#notes", req.notes)
            self.frame(f"invoice form {req.invoice_number}")
            page.click("#submit")
            page.wait_for_load_state()
            self._raise_if_unavailable()
            if page.locator("#protocol").count():
                self.frame(f"invoice registered {req.invoice_number}")
                return page.locator("#protocol").inner_text().strip(), {}
            errors = {el.get_attribute("data-field"): el.inner_text() for el in page.locator(".field-error").all()}
            if errors:
                self.frame(f"invoice rejected {req.invoice_number}")
                return "", errors
            raise TransientError("Unexpected page after submitting the form")

        try:
            (protocol, errors), attempts = self._with_retries(f"Invoice {req.key}", attempt)
        except (TransientError, PlaywrightTimeout) as exc:
            return TaskResult("invoice", req.key, FAILED, str(exc), screenshot=self.screenshot(f"failed {req.key}"),
                              attempts=self.settings.retries, seconds=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001
            return TaskResult("invoice", req.key, FAILED, f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
                              screenshot=self.screenshot(f"failed {req.key}"), attempts=1,
                              seconds=time.monotonic() - started)
        if errors:
            detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
            log.warning("Rejected by the portal %s: %s", req.key, detail)
            return TaskResult("invoice", req.key, REJECTED, detail, attempts=attempts, errors=errors,
                              screenshot=self.screenshot(f"rejected {req.key}"), seconds=time.monotonic() - started)
        log.info("Registered %s as %s", req.key, protocol)
        return TaskResult("invoice", req.key, DONE, "Registered", protocol, attempts, seconds=time.monotonic() - started)
