from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit


APP_NAME = "CampusNetAutoLogin"


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
    page_timeout_ms: int = 30000
    login_verify_seconds: int = 20
    headless: bool = True
    ignore_https_errors: bool = False
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    agreement_selector: str = ""
    credential_target: str = "CampusNetAutoLogin/default"

    def __post_init__(self) -> None:
        if self.allowed_ssids is None:
            self.allowed_ssids = []

    def validate(self) -> None:
        self.login_url = normalize_login_url(self.login_url)
        if not self.credential_target.strip():
            raise ValueError("credential_target 不能为空")
        if self.wifi_wait_seconds < 0:
            raise ValueError("wifi_wait_seconds 不能小于 0")
        if self.check_interval_seconds < 5:
            raise ValueError("check_interval_seconds 不能小于 5")
        if self.retry_initial_seconds < 1:
            raise ValueError("retry_initial_seconds 不能小于 1")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds 不能小于 retry_initial_seconds")
        if self.page_timeout_ms < 1000:
            raise ValueError("page_timeout_ms 不能小于 1000")


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
