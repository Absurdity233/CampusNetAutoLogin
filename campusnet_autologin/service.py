from __future__ import annotations

import ctypes
import logging
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .config import AppConfig, load_config
from .credentials import Credentials, read_credentials
from .network import connect_preferred_wifi, current_wifi_ssid, has_internet, ssid_is_allowed
from .portal import PortalLoginError, login_to_portal


LOGGER = logging.getLogger(__name__)
ERROR_ALREADY_EXISTS = 183


class ServiceState(str, Enum):
    STARTING = "starting"
    CHECKING_WIFI = "checking_wifi"
    CONNECTING_WIFI = "connecting_wifi"
    NO_WIFI = "no_wifi"
    WRONG_WIFI = "wrong_wifi"
    CHECKING_CONNECTIVITY = "checking_connectivity"
    AUTHENTICATING = "authenticating"
    ONLINE = "online"
    AUTHENTICATION_FAILED = "authentication_failed"
    CREDENTIALS_MISSING = "credentials_missing"
    CONFIG_ERROR = "config_error"
    ERROR = "error"
    RETRY_WAIT = "retry_wait"
    STOPPED = "stopped"


_ALLOWED_TRANSITIONS = {
    ServiceState.STARTING: {
        ServiceState.CHECKING_WIFI,
        ServiceState.CREDENTIALS_MISSING,
        ServiceState.CONFIG_ERROR,
        ServiceState.ERROR,
        ServiceState.STOPPED,
    },
    ServiceState.CHECKING_WIFI: {
        ServiceState.CONNECTING_WIFI,
        ServiceState.NO_WIFI,
        ServiceState.WRONG_WIFI,
        ServiceState.CHECKING_CONNECTIVITY,
        ServiceState.ERROR,
    },
    ServiceState.CONNECTING_WIFI: {
        ServiceState.NO_WIFI,
        ServiceState.WRONG_WIFI,
        ServiceState.CHECKING_CONNECTIVITY,
        ServiceState.ERROR,
    },
    ServiceState.NO_WIFI: {ServiceState.RETRY_WAIT, ServiceState.STOPPED},
    ServiceState.WRONG_WIFI: {ServiceState.RETRY_WAIT, ServiceState.STOPPED},
    ServiceState.CHECKING_CONNECTIVITY: {
        ServiceState.ONLINE,
        ServiceState.AUTHENTICATING,
        ServiceState.ERROR,
    },
    ServiceState.AUTHENTICATING: {
        ServiceState.ONLINE,
        ServiceState.AUTHENTICATION_FAILED,
        ServiceState.ERROR,
    },
    ServiceState.ONLINE: {
        ServiceState.CHECKING_WIFI,
        ServiceState.CREDENTIALS_MISSING,
        ServiceState.CONFIG_ERROR,
        ServiceState.ERROR,
        ServiceState.STOPPED,
    },
    ServiceState.AUTHENTICATION_FAILED: {
        ServiceState.RETRY_WAIT,
        ServiceState.STOPPED,
    },
    ServiceState.CREDENTIALS_MISSING: {
        ServiceState.RETRY_WAIT,
        ServiceState.STOPPED,
    },
    ServiceState.CONFIG_ERROR: {ServiceState.RETRY_WAIT, ServiceState.STOPPED},
    ServiceState.ERROR: {ServiceState.RETRY_WAIT, ServiceState.STOPPED},
    ServiceState.RETRY_WAIT: {
        ServiceState.CHECKING_WIFI,
        ServiceState.CREDENTIALS_MISSING,
        ServiceState.CONFIG_ERROR,
        ServiceState.ERROR,
        ServiceState.STOPPED,
    },
    ServiceState.STOPPED: set(),
}


@dataclass(slots=True)
class ServiceStateMachine:
    state: ServiceState = ServiceState.STARTING
    detail: str = ""

    def transition(self, new_state: ServiceState, detail: str = "") -> None:
        if new_state == self.state:
            if detail != self.detail:
                self.detail = detail
                LOGGER.info("服务状态保持 %s: %s", new_state.value, detail)
            return
        allowed = _ALLOWED_TRANSITIONS[self.state]
        if new_state != ServiceState.STOPPED and new_state not in allowed:
            raise RuntimeError(
                f"非法服务状态转换：{self.state.value} -> {new_state.value}"
            )
        suffix = f": {detail}" if detail else ""
        LOGGER.info("服务状态 %s -> %s%s", self.state.value, new_state.value, suffix)
        self.state = new_state
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AttemptResult:
    state: ServiceState
    online: bool
    code: str
    detail: str = ""
    ssid: str | None = None

    def __bool__(self) -> bool:
        return self.online

    @property
    def retry_key(self) -> tuple[str, str | None, str]:
        return self.state.value, self.ssid, self.code


@dataclass(slots=True)
class RetryPolicy:
    initial_seconds: float
    max_seconds: float
    multiplier: float
    jitter_ratio: float
    random_source: Callable[[], float] = field(default=random.random, repr=False)
    _last_key: tuple[str, str | None, str] | None = field(default=None, init=False)
    _next_delay: float = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._next_delay = self.initial_seconds

    @classmethod
    def from_config(cls, config: AppConfig) -> RetryPolicy:
        return cls(
            initial_seconds=config.retry_initial_seconds,
            max_seconds=config.retry_max_seconds,
            multiplier=config.retry_multiplier,
            jitter_ratio=config.retry_jitter_ratio,
        )

    def configure(self, config: AppConfig) -> None:
        self.initial_seconds = config.retry_initial_seconds
        self.max_seconds = config.retry_max_seconds
        self.multiplier = config.retry_multiplier
        self.jitter_ratio = config.retry_jitter_ratio
        self._next_delay = min(
            self.max_seconds,
            max(self.initial_seconds, self._next_delay),
        )

    def reset(self) -> None:
        self._last_key = None
        self._next_delay = self.initial_seconds

    def delay_for(self, result: AttemptResult, *, online_interval: float) -> float:
        if result.online:
            self.reset()
            return max(1.0, online_interval)

        if result.retry_key != self._last_key:
            base_delay = self.initial_seconds
        else:
            base_delay = self._next_delay
        self._last_key = result.retry_key
        self._next_delay = min(
            self.max_seconds,
            max(self.initial_seconds, base_delay * self.multiplier),
        )

        jitter = (self.random_source() * 2 - 1) * base_delay * self.jitter_ratio
        return max(1.0, min(self.max_seconds, base_delay + jitter))


@contextmanager
def single_instance_mutex():
    if not hasattr(ctypes, "WinDLL"):
        yield
        return

    ctypes.set_last_error(0)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, "Local\\CampusNetAutoLogin")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            raise RuntimeError("校园网自动登录程序已经在运行")
        yield
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_ssid(config: AppConfig) -> str | None:
    ssid = current_wifi_ssid()
    if ssid is not None or config.wifi_wait_seconds <= 0:
        return ssid

    LOGGER.info("等待 Wi-Fi 连接，最长 %d 秒", config.wifi_wait_seconds)
    deadline = time.monotonic() + config.wifi_wait_seconds
    while time.monotonic() < deadline:
        time.sleep(1)
        ssid = current_wifi_ssid()
        if ssid is not None:
            return ssid
    return None


def _portal_error_code(error: PortalLoginError) -> str:
    message = str(error).strip()
    return message.split(maxsplit=1)[0] if message else "PORTAL_LOGIN_ERROR"


def attempt_login(
    config: AppConfig,
    credentials: Credentials,
    *,
    show_browser: bool = False,
    state_machine: ServiceStateMachine | None = None,
) -> AttemptResult:
    machine = state_machine or ServiceStateMachine()
    machine.transition(ServiceState.CHECKING_WIFI)
    ssid = _wait_for_ssid(config)
    if ssid is None and config.auto_connect_wifi:
        machine.transition(ServiceState.CONNECTING_WIFI, "未连接 Wi-Fi")
        ssid = connect_preferred_wifi(config)
    if ssid is None:
        machine.transition(ServiceState.NO_WIFI, "尚未连接 Wi-Fi")
        return AttemptResult(ServiceState.NO_WIFI, False, "NO_WIFI")

    if not ssid_is_allowed(config, ssid):
        if not config.auto_connect_wifi:
            detail = f"当前 Wi-Fi {ssid!r} 不在允许列表中"
            machine.transition(ServiceState.WRONG_WIFI, detail)
            return AttemptResult(
                ServiceState.WRONG_WIFI,
                False,
                "WRONG_WIFI",
                detail,
                ssid,
            )
        machine.transition(ServiceState.CONNECTING_WIFI, f"当前 Wi-Fi 为 {ssid!r}")
        ssid = connect_preferred_wifi(config)
        if not ssid_is_allowed(config, ssid):
            detail = f"目标校园 Wi-Fi 尚未连接，当前为 {ssid!r}"
            machine.transition(ServiceState.WRONG_WIFI, detail)
            return AttemptResult(
                ServiceState.WRONG_WIFI,
                False,
                "WRONG_WIFI",
                detail,
                ssid,
            )

    machine.transition(ServiceState.CHECKING_CONNECTIVITY, f"Wi-Fi {ssid!r}")
    if has_internet(config):
        machine.transition(ServiceState.ONLINE, "网络已连通")
        return AttemptResult(ServiceState.ONLINE, True, "ONLINE", ssid=ssid)

    machine.transition(ServiceState.AUTHENTICATING, f"Wi-Fi {ssid!r}")
    try:
        result = login_to_portal(config, credentials, show_browser=show_browser)
        if result.success:
            detail = f"适配器 {result.adapter_name}"
            machine.transition(ServiceState.ONLINE, detail)
            return AttemptResult(
                ServiceState.ONLINE,
                True,
                "AUTHENTICATED",
                detail,
                ssid,
            )
        detail = "Portal 未确认认证成功"
        machine.transition(ServiceState.AUTHENTICATION_FAILED, detail)
        return AttemptResult(
            ServiceState.AUTHENTICATION_FAILED,
            False,
            "AUTHENTICATION_REJECTED",
            detail,
            ssid,
        )
    except PortalLoginError as error:
        detail = str(error)
        machine.transition(ServiceState.AUTHENTICATION_FAILED, detail)
        LOGGER.error("校园网认证失败: %s", error)
        return AttemptResult(
            ServiceState.AUTHENTICATION_FAILED,
            False,
            _portal_error_code(error),
            detail,
            ssid,
        )
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"
        machine.transition(ServiceState.ERROR, detail)
        LOGGER.exception("校园网认证发生未预期错误")
        return AttemptResult(ServiceState.ERROR, False, "UNEXPECTED_ERROR", detail, ssid)


def _sleep_until_network_change(
    seconds: float,
    *,
    expected_ssid: str | None,
    poll_seconds: float,
) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_seconds, remaining))
        current_ssid = current_wifi_ssid()
        if current_ssid != expected_ssid:
            LOGGER.info(
                "检测到 Wi-Fi 状态变化：%r -> %r，提前结束等待",
                expected_ssid,
                current_ssid,
            )
            return True


def _failure_result(
    state: ServiceState,
    code: str,
    detail: str,
) -> AttemptResult:
    return AttemptResult(state, False, code, detail, current_wifi_ssid())


def run_forever() -> None:
    config = AppConfig()
    policy = RetryPolicy.from_config(config)
    machine = ServiceStateMachine()
    with single_instance_mutex():
        LOGGER.info("校园网自动登录服务已启动")
        try:
            while True:
                try:
                    config = load_config()
                    policy.configure(config)
                except (OSError, TypeError, ValueError) as error:
                    detail = f"{type(error).__name__}: {error}"
                    machine.transition(ServiceState.CONFIG_ERROR, detail)
                    result = _failure_result(
                        ServiceState.CONFIG_ERROR,
                        "CONFIG_ERROR",
                        detail,
                    )
                else:
                    try:
                        credentials = read_credentials(config.credential_target)
                    except Exception as error:
                        detail = f"{type(error).__name__}: {error}"
                        machine.transition(ServiceState.ERROR, detail)
                        LOGGER.exception("读取校园网账号密码失败")
                        result = _failure_result(
                            ServiceState.ERROR,
                            "CREDENTIAL_READ_ERROR",
                            detail,
                        )
                    else:
                        if credentials is None:
                            detail = "未找到保存的校园网账号密码"
                            machine.transition(ServiceState.CREDENTIALS_MISSING, detail)
                            LOGGER.error(detail)
                            result = _failure_result(
                                ServiceState.CREDENTIALS_MISSING,
                                "CREDENTIALS_MISSING",
                                detail,
                            )
                        else:
                            result = attempt_login(
                                config,
                                credentials,
                                state_machine=machine,
                            )

                delay = policy.delay_for(
                    result,
                    online_interval=config.check_interval_seconds,
                )
                if result.online:
                    LOGGER.info("将在 %.1f 秒后再次检查网络", delay)
                else:
                    machine.transition(
                        ServiceState.RETRY_WAIT,
                        f"{result.code}，{delay:.1f} 秒后重试",
                    )
                _sleep_until_network_change(
                    delay,
                    expected_ssid=result.ssid,
                    poll_seconds=config.retry_poll_seconds,
                )
        finally:
            machine.transition(ServiceState.STOPPED)
