from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .config import AppConfig
from .credentials import Credentials
from .network import has_internet


LOGGER = logging.getLogger(__name__)
CAPTIVE_PORTAL_TRIGGER_URL = "http://www.msftconnecttest.com/redirect"

USERNAME_SELECTORS = (
    "#username",
    'input[autocomplete="username"]',
    'input[name="username" i]',
    'input[id="username" i]',
    'input[name*="account" i]',
    'input[id*="account" i]',
    'input[type="email"]',
    'input[type="tel"]',
    'input[type="text"]',
)
USERNAME_WAKE_SELECTORS = ("#username_tip", '[id*="username_tip" i]')

PASSWORD_SELECTORS = (
    "#pwd",
    'input[type="password"]',
    'input[name="pwd" i]',
    'input[id="pwd" i]',
    'input[name*="password" i]',
    'input[id*="password" i]',
)
PASSWORD_WAKE_SELECTORS = ("#pwd_tip", '[id*="pwd_tip" i]')

SUBMIT_SELECTORS = (
    "#loginLink",
    "#loginLink_div",
    '[id*="loginLink" i]',
    'button[type="submit"]',
    'input[type="submit"]',
    '[role="button"][type="submit"]',
)


class PortalLoginError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LoginResult:
    success: bool
    page_url: str
    page_title: str


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


def _visible_locator(frames, selectors, *, editable_only: bool = False):
    for selector in selectors:
        if not selector:
            continue
        for frame in frames:
            locator = frame.locator(selector)
            try:
                count = min(locator.count(), 30)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if (
                        candidate.is_visible()
                        and candidate.is_enabled()
                        and (not editable_only or candidate.is_editable())
                    ):
                        return candidate
                except Exception:
                    continue
    return None


def _enabled_locator(frames, selectors):
    for selector in selectors:
        if not selector:
            continue
        for frame in frames:
            locator = frame.locator(selector)
            try:
                count = min(locator.count(), 30)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if candidate.is_enabled():
                        return candidate
                except Exception:
                    continue
    return None


def _visible_login_button(frames):
    button = _visible_locator(frames, SUBMIT_SELECTORS)
    if button is not None:
        return button
    for frame in frames:
        for selector in ("button", "a", '[role="button"]', 'input[type="button"]'):
            locator = frame.locator(selector)
            try:
                count = min(locator.count(), 40)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    label = " ".join(
                        filter(
                            None,
                            [
                                candidate.inner_text(timeout=300).strip(),
                                candidate.get_attribute("value") or "",
                                candidate.get_attribute("aria-label") or "",
                            ],
                        )
                    )
                    if re.search(r"login|sign in|connect|登录|登入|认证|上网", label, re.I):
                        if candidate.is_visible() and candidate.is_enabled():
                            return candidate
                except Exception:
                    continue
    return None


def _form_summary(frames) -> str:
    summary = []
    for frame_index, frame in enumerate(frames):
        for selector in ("input", "button", "a", "select"):
            locator = frame.locator(selector)
            try:
                count = min(locator.count(), 50)
            except Exception:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    summary.append(
                        {
                            "frame": frame_index,
                            "tag": selector,
                            "type": item.get_attribute("type"),
                            "id": item.get_attribute("id"),
                            "name": item.get_attribute("name"),
                            "visible": item.is_visible(),
                        }
                    )
                except Exception:
                    continue
    return json.dumps(summary, ensure_ascii=False)


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

            frames = page.frames if not page_closed else []
            username_wake = _visible_locator(frames, USERNAME_WAKE_SELECTORS)
            if username_wake is not None:
                try:
                    username_wake.click()
                    page.wait_for_timeout(300)
                    frames = page.frames
                except PlaywrightError:
                    page_closed = True
                    frames = []

            password_wake = _visible_locator(frames, PASSWORD_WAKE_SELECTORS)
            if password_wake is not None:
                try:
                    password_wake.click()
                    page.wait_for_timeout(300)
                    frames = page.frames
                except PlaywrightError:
                    page_closed = True
                    frames = []

            username_selectors = (
                (config.username_selector,) if config.username_selector else USERNAME_SELECTORS
            )
            password_selectors = (
                (config.password_selector,) if config.password_selector else PASSWORD_SELECTORS
            )
            username_input = _visible_locator(frames, username_selectors, editable_only=True)
            password_input = _visible_locator(frames, password_selectors, editable_only=True)
            username_force = False
            password_force = False

            if username_input is None and not config.username_selector:
                username_input = _enabled_locator(frames, ("#username",))
                username_force = username_input is not None
            if password_input is None and not config.password_selector:
                password_input = _enabled_locator(frames, ("#pwd",))
                password_force = password_input is not None

            if username_input is None:
                LOGGER.error("Portal fields: %s", _form_summary(frames))
                raise PortalLoginError("USERNAME_FIELD_NOT_FOUND")
            if password_input is None:
                LOGGER.error("Portal fields: %s", _form_summary(frames))
                raise PortalLoginError("PASSWORD_FIELD_NOT_FOUND")

            username_input.fill(credentials.username, force=username_force)
            password_input.fill(credentials.password, force=password_force)

            if config.agreement_selector:
                agreement = _visible_locator(frames, (config.agreement_selector,))
                if agreement is None:
                    raise PortalLoginError("AGREEMENT_FIELD_NOT_FOUND")
                try:
                    if not agreement.is_checked():
                        agreement.check()
                except Exception:
                    agreement.click()

            submit = (
                _visible_locator(frames, (config.submit_selector,))
                if config.submit_selector
                else _visible_login_button(frames)
            )
            try:
                if submit is not None:
                    submit.click()
                else:
                    password_input.press("Enter")
            except PlaywrightError as error:
                LOGGER.info("Portal closed or navigated after submit: %s", error)
                page_closed = True

            deadline = time.monotonic() + config.login_verify_seconds
            while time.monotonic() < deadline:
                if has_internet(config):
                    page_url, page_title = _safe_page_info(page, target_url)
                    LOGGER.info("Portal authentication succeeded")
                    return LoginResult(True, page_url, page_title)
                if not page_closed:
                    try:
                        page.wait_for_timeout(1000)
                        continue
                    except PlaywrightError:
                        page_closed = True
                        LOGGER.info("Portal page closed; continuing network verification")
                time.sleep(1)

            if not page_closed:
                LOGGER.error("Portal fields after submit: %s", _form_summary(page.frames))
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
