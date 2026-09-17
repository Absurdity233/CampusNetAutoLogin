from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit


APP_NAME = "CampusNetAutoLogin"
PORTAL_ADAPTERS = {"auto", "configured", "eportal", "generic"}


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / APP_NAME


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


def default_log_path() -> Path:
    return app_data_dir() / "campusnet.log"


def normalize_login_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    if "://" not in url:
        url = f"http://{url}"
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("login_url 必须是有效的 HTTP 或 HTTPS 地址")
    return url


@dataclass(slots=True)
class AppConfig:
    login_url: str = ""
    allowed_ssids: list[str] | None = None
    connectivity_url: str = "http://www.msftconnecttest.com/connecttest.txt"
    connectivity_expected: str = "Microsoft Connect Test"
    connectivity_timeout_seconds: float = 5.0
    wifi_wait_seconds: int = 30
    auto_connect_wifi: bool = False
    check_interval_seconds: int = 30
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 300
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.15
    retry_poll_seconds: float = 2.0
    page_timeout_ms: int = 30000
    login_verify_seconds: int = 20
    headless: bool = True
    ignore_https_errors: bool = False
    portal_adapter: str = "auto"
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    agreement_selector: str = ""
    credential_target: str = "CampusNetAutoLogin/default"

    def __post_init__(self) -> None:
        if self.allowed_ssids is None:
            self.allowed_ssids = []

    def validate(self) -> None:
        if not isinstance(self.login_url, str):
            raise ValueError("login_url 必须是字符串")
        self.login_url = normalize_login_url(self.login_url)
        if not isinstance(self.allowed_ssids, list) or any(
            not isinstance(ssid, str) for ssid in self.allowed_ssids
        ):
            raise ValueError("allowed_ssids 必须是字符串列表")
        self.allowed_ssids = list(
            dict.fromkeys(ssid.strip() for ssid in self.allowed_ssids if ssid.strip())
        )
        string_fields = (
            "connectivity_url",
            "connectivity_expected",
            "credential_target",
            "portal_adapter",
            "username_selector",
            "password_selector",
            "submit_selector",
            "agreement_selector",
        )
        for field_name in string_fields:
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"{field_name} 必须是字符串")
        numeric_fields = (
            "connectivity_timeout_seconds",
            "wifi_wait_seconds",
            "check_interval_seconds",
            "retry_initial_seconds",
            "retry_max_seconds",
            "retry_multiplier",
            "retry_jitter_ratio",
            "retry_poll_seconds",
            "page_timeout_ms",
            "login_verify_seconds",
        )
        for field_name in numeric_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} 必须是数字")
        boolean_fields = ("auto_connect_wifi", "headless", "ignore_https_errors")
        for field_name in boolean_fields:
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} 必须是布尔值")
        if not self.credential_target.strip():
            raise ValueError("credential_target 不能为空")
        if self.connectivity_timeout_seconds <= 0:
            raise ValueError("connectivity_timeout_seconds 必须大于 0")
        if self.wifi_wait_seconds < 0:
            raise ValueError("wifi_wait_seconds 不能小于 0")
        if self.check_interval_seconds < 5:
            raise ValueError("check_interval_seconds 不能小于 5")
        if self.retry_initial_seconds < 1:
            raise ValueError("retry_initial_seconds 不能小于 1")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds 不能小于 retry_initial_seconds")
        if self.retry_multiplier < 1:
            raise ValueError("retry_multiplier 不能小于 1")
        if not 0 <= self.retry_jitter_ratio <= 0.5:
            raise ValueError("retry_jitter_ratio 必须在 0 到 0.5 之间")
        if self.retry_poll_seconds <= 0:
            raise ValueError("retry_poll_seconds 必须大于 0")
        if self.page_timeout_ms < 1000:
            raise ValueError("page_timeout_ms 不能小于 1000")
        if self.login_verify_seconds < 1:
            raise ValueError("login_verify_seconds 不能小于 1")
        self.portal_adapter = self.portal_adapter.strip().lower()
        if self.portal_adapter not in PORTAL_ADAPTERS:
            choices = ", ".join(sorted(PORTAL_ADAPTERS))
            raise ValueError(f"portal_adapter 必须是以下值之一：{choices}")


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppConfig()

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    allowed = {field.name for field in fields(AppConfig)}
    config = AppConfig(**{key: value for key, value in raw.items() if key in allowed})
    config.validate()
    return config


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config.validate()
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path
