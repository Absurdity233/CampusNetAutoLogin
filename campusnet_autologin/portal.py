from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .config import AppConfig
from .credentials import Credentials
from .network import has_internet
from .portal_adapters import PortalAdapterError, form_summary, select_portal_adapter


LOGGER = logging.getLogger(__name__)
CAPTIVE_PORTAL_TRIGGER_URL = "http://www.msftconnecttest.com/redirect"

class PortalLoginError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LoginResult:
    success: bool
    page_url: str
    page_title: str
    adapter_name: str = "unknown"


def sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return "<invalid-url>"


def _safe_page_info(page, fallback_url: str) -> tuple[str, str]:
    try:
        page_url = sanitize_url(page.url)
    except Exception:
        page_url = sanitize_url(fallback_url)
    try:
        title = page.title()
    except Exception:
        title = ""
    return page_url, title


def login_to_portal(
    config: AppConfig,
    credentials: Credentials,
    *,
    show_browser: bool = False,
) -> LoginResult:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise PortalLoginError("Playwright is not installed; run install.bat") from error

    target_url = config.login_url.strip() or CAPTIVE_PORTAL_TRIGGER_URL
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="msedge",
            headless=False if show_browser else config.headless,
        )
        context = browser.new_context(ignore_https_errors=config.ignore_https_errors)
        page = context.new_page()
        page.set_default_timeout(config.page_timeout_ms)
        page_closed = False
        try:
            LOGGER.info("Opening portal: %s", sanitize_url(target_url))
            try:
                page.goto(target_url, wait_until="commit")
            except PlaywrightError as error:
                LOGGER.warning("Portal navigation returned an error; inspecting page: %s", error)
            try:
                page.wait_for_timeout(1500)
            except PlaywrightError:
                page_closed = True

            if has_internet(config):
                page_url, page_title = _safe_page_info(page, target_url)
                return LoginResult(True, page_url, page_title, "already-online")
            if page_closed:
                raise PortalLoginError("PORTAL_PAGE_CLOSED")

            try:
                selected = select_portal_adapter(page, config)
            except PortalAdapterError as error:
                LOGGER.error("Portal fields: %s", form_summary(page.frames))
                raise PortalLoginError(str(error)) from error

            form = selected.form
            LOGGER.info("Portal adapter selected: %s", selected.name)
            form.username_input.fill(credentials.username, force=form.username_force)
            form.password_input.fill(credentials.password, force=form.password_force)

            if form.agreement is not None:
                try:
                    if not form.agreement.is_checked():
                        form.agreement.check()
                except Exception:
                    form.agreement.click()

            try:
                if form.submit is not None:
                    form.submit.click()
                else:
                    form.password_input.press("Enter")
            except PlaywrightError as error:
                LOGGER.info("Portal closed or navigated after submit: %s", error)
                page_closed = True

            deadline = time.monotonic() + config.login_verify_seconds
            while time.monotonic() < deadline:
                if has_internet(config):
                    page_url, page_title = _safe_page_info(page, target_url)
                    LOGGER.info("Portal authentication succeeded")
                    return LoginResult(True, page_url, page_title, selected.name)
                if not page_closed:
                    try:
                        page.wait_for_timeout(1000)
                        continue
                    except PlaywrightError:
                        page_closed = True
                        LOGGER.info("Portal page closed; continuing network verification")
                time.sleep(1)

            if not page_closed:
                LOGGER.error("Portal fields after submit: %s", form_summary(page.frames))
            page_url, page_title = _safe_page_info(page, target_url)
            raise PortalLoginError(
                f"NETWORK_NOT_RESTORED page={page_url} title={page_title!r}"
            )
        except PlaywrightTimeoutError as error:
            page_url, _ = _safe_page_info(page, target_url)
            raise PortalLoginError(f"PORTAL_OPERATION_TIMEOUT page={page_url}") from error
        except PlaywrightError as error:
            page_url, _ = _safe_page_info(page, target_url)
            raise PortalLoginError(f"PORTAL_BROWSER_ERROR page={page_url}") from error
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
