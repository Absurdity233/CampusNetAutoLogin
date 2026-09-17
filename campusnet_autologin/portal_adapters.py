from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .config import AppConfig


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
PASSWORD_SELECTORS = (
    "#pwd",
    'input[type="password"]',
    'input[name="pwd" i]',
    'input[id="pwd" i]',
    'input[name*="password" i]',
    'input[id*="password" i]',
)
SUBMIT_SELECTORS = (
    "#loginLink",
    "#loginLink_div",
    '[id*="loginLink" i]',
    'button[type="submit"]',
    'input[type="submit"]',
    '[role="button"][type="submit"]',
)
USERNAME_WAKE_SELECTORS = ("#username_tip", '[id*="username_tip" i]')
PASSWORD_WAKE_SELECTORS = ("#pwd_tip", '[id*="pwd_tip" i]')


class PortalAdapterError(RuntimeError):
    pass


@dataclass(slots=True)
class PortalForm:
    username_input: Any
    password_input: Any
    submit: Any | None
    agreement: Any | None
    username_force: bool = False
    password_force: bool = False


@dataclass(frozen=True, slots=True)
class SelectedPortalAdapter:
    name: str
    form: PortalForm


class PortalAdapter(Protocol):
    name: str

    def can_handle(self, page: Any, config: AppConfig) -> bool: ...

    def locate(self, page: Any, config: AppConfig) -> PortalForm: ...


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


def _agreement_locator(frames, config: AppConfig):
    if not config.agreement_selector:
        return None
    agreement = _visible_locator(frames, (config.agreement_selector,))
    if agreement is None:
        raise PortalAdapterError("AGREEMENT_FIELD_NOT_FOUND")
    return agreement


def _complete_form(
    frames,
    config: AppConfig,
    *,
    username_selectors,
    password_selectors,
    submit_selectors=None,
    force_username_fallback: bool = False,
    force_password_fallback: bool = False,
) -> PortalForm:
    username_input = _visible_locator(frames, username_selectors, editable_only=True)
    password_input = _visible_locator(frames, password_selectors, editable_only=True)
    username_force = False
    password_force = False

    if force_username_fallback and username_input is None:
        username_input = _enabled_locator(frames, ("#username",))
        username_force = username_input is not None
    if force_password_fallback and password_input is None:
        password_input = _enabled_locator(frames, ("#pwd",))
        password_force = password_input is not None

    if username_input is None:
        raise PortalAdapterError("USERNAME_FIELD_NOT_FOUND")
    if password_input is None:
        raise PortalAdapterError("PASSWORD_FIELD_NOT_FOUND")

    submit = (
        _visible_locator(frames, submit_selectors)
        if submit_selectors
        else _visible_login_button(frames)
    )
    return PortalForm(
        username_input=username_input,
        password_input=password_input,
        submit=submit,
        agreement=_agreement_locator(frames, config),
        username_force=username_force,
        password_force=password_force,
    )


class ConfiguredPortalAdapter:
    name = "configured"

    def can_handle(self, _page: Any, config: AppConfig) -> bool:
        return any(
            (
                config.username_selector,
                config.password_selector,
                config.submit_selector,
                config.agreement_selector,
            )
        )

    def locate(self, page: Any, config: AppConfig) -> PortalForm:
        if not self.can_handle(page, config):
            raise PortalAdapterError("CONFIGURED_ADAPTER_REQUIRES_SELECTORS")
        username_selectors = (
            (config.username_selector,) if config.username_selector else USERNAME_SELECTORS
        )
        password_selectors = (
            (config.password_selector,) if config.password_selector else PASSWORD_SELECTORS
        )
        submit_selectors = (config.submit_selector,) if config.submit_selector else None
        return _complete_form(
            page.frames,
            config,
            username_selectors=username_selectors,
            password_selectors=password_selectors,
            submit_selectors=submit_selectors,
            force_username_fallback=not config.username_selector,
            force_password_fallback=not config.password_selector,
        )


class EPortalAdapter:
    name = "eportal"

    def can_handle(self, page: Any, _config: AppConfig) -> bool:
        frames = page.frames
        has_username = any(
            (
                _enabled_locator(frames, ("#username",)) is not None,
                _visible_locator(frames, USERNAME_WAKE_SELECTORS) is not None,
            )
        )
        has_password = any(
            (
                _enabled_locator(frames, ("#pwd",)) is not None,
                _visible_locator(frames, PASSWORD_WAKE_SELECTORS) is not None,
            )
        )
        return has_username and has_password

    def locate(self, page: Any, config: AppConfig) -> PortalForm:
        for selectors in (USERNAME_WAKE_SELECTORS, PASSWORD_WAKE_SELECTORS):
            wake = _visible_locator(page.frames, selectors)
            if wake is None:
                continue
            try:
                wake.click()
                page.wait_for_timeout(300)
            except Exception:
                break
        return _complete_form(
            page.frames,
            config,
            username_selectors=("#username",),
            password_selectors=("#pwd",),
            submit_selectors=("#loginLink", "#loginLink_div", '[id*="loginLink" i]'),
            force_username_fallback=True,
            force_password_fallback=True,
        )


class GenericPortalAdapter:
    name = "generic"

    def can_handle(self, _page: Any, _config: AppConfig) -> bool:
        return True

    def locate(self, page: Any, config: AppConfig) -> PortalForm:
        return _complete_form(
            page.frames,
            config,
            username_selectors=USERNAME_SELECTORS,
            password_selectors=PASSWORD_SELECTORS,
            force_username_fallback=True,
            force_password_fallback=True,
        )


_ADAPTERS: dict[str, PortalAdapter] = {}
_AUTO_ORDER = ("configured", "eportal", "generic")


def register_portal_adapter(adapter: PortalAdapter) -> None:
    name = adapter.name.strip().lower()
    if not name:
        raise ValueError("Portal adapter name cannot be empty")
    _ADAPTERS[name] = adapter


def available_portal_adapters() -> tuple[str, ...]:
    return tuple(_ADAPTERS)


def select_portal_adapter(page: Any, config: AppConfig) -> SelectedPortalAdapter:
    requested = config.portal_adapter.strip().lower()
    if requested != "auto":
        adapter = _ADAPTERS.get(requested)
        if adapter is None:
            raise PortalAdapterError(f"UNKNOWN_PORTAL_ADAPTER adapter={requested}")
        if not adapter.can_handle(page, config):
            raise PortalAdapterError(f"PORTAL_ADAPTER_NOT_MATCHED adapter={requested}")
        return SelectedPortalAdapter(adapter.name, adapter.locate(page, config))

    configured = _ADAPTERS["configured"]
    if configured.can_handle(page, config):
        return SelectedPortalAdapter(configured.name, configured.locate(page, config))

    eportal = _ADAPTERS["eportal"]
    if eportal.can_handle(page, config):
        return SelectedPortalAdapter(eportal.name, eportal.locate(page, config))

    generic = _ADAPTERS["generic"]
    return SelectedPortalAdapter(generic.name, generic.locate(page, config))


def form_summary(frames) -> str:
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


register_portal_adapter(ConfiguredPortalAdapter())
register_portal_adapter(EPortalAdapter())
register_portal_adapter(GenericPortalAdapter())
